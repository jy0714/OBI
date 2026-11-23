"""
OBI AI 분석 - 예시(Mock) 모듈

LLM 연동 전에 결과 화면과 점수 구조를 미리 확인하기 위한 임시 모듈이다.
여기서 만드는 판단은 '예시'이며 실제 AI 판단이 아니다.

연동 시에는 mock_analyze() 만 실제 LLM 호출로 교체하면 되고,
점수 계산(score_candidate)과 결과 형식은 그대로 쓴다.
"""
import json
import math
import re

import pandas as pd
import requests

import obi_criteria as crit
import obi_preprocess as prep

# 위험 점수 배점 (AI 65 : 코드 35)
WEIGHTS = {
    "자료 민감도": ("AI", 25, "회사 자료·기밀로 보이는가"),
    "행위 맥락": ("AI", 25, "부서 업무 범위를 벗어난 행위인가"),
    "흐름 연결": ("AI", 15, "여러 채널에 걸쳐 연결된 행동인가"),
    "반출 경로": ("코드", 20, "회사 밖으로 나간 행위인가"),
    "시점·패턴": ("코드", 15, "업무시간 외·최근 30일·처음 보는 대상인가"),
}
LEVELS = {"높음": 1.0, "중간": 0.5, "낮음": 0.0}
PRIORITY_MAX = 10          # 우선 검토 목록 상한
OUTBOUND = {"이메일", "웹 업로드", "USB", "USB(DLP)", "출력"}

# 등급 (REQ-04). 사용자에게는 등급만 보이고, 위험점수는 내부 정렬용으로만 남긴다 ([결정 필요] #2).
GRADES = ("Critical", "Medium", "Low")      # 높은 순
GRADE_CRITICAL_MIN = 70
GRADE_MEDIUM_MIN = 40
# [결정 필요] #3 개인메일 첨부 발송(B2)의 등급 반영 (REQ-05). False 면 점수 기준 등급만 쓴다.
PERSONAL_MAIL_GRADE_BOOST = True
PERSONAL_MAIL_CRITICAL_WITH = {"B1", "B3", "B10"}   # B2 와 함께 있으면 Critical


def score_candidate(levels):
    """요소별 높음/중간/낮음 → 고정 배점으로 환산 (LLM은 점수를 직접 매기지 않는다).
    반올림은 floor(x + 0.5) — round() 의 은행가 반올림(32.5→32, 37.5→38)을 피한다."""
    return math.floor(sum(WEIGHTS[k][1] * LEVELS.get(v, 0) for k, v in levels.items()) + 0.5)


def grade_of(score, codes=()):
    """내부 점수(+점검 코드) → Critical / Medium / Low"""
    g = "Critical" if score >= GRADE_CRITICAL_MIN else "Medium" if score >= GRADE_MEDIUM_MIN else "Low"
    if PERSONAL_MAIL_GRADE_BOOST and "B2" in codes:
        floor_g = "Critical" if set(codes) & PERSONAL_MAIL_CRITICAL_WITH else "Medium"
        g = min(g, floor_g, key=GRADES.index)
    return g


def grade_rank(g):
    """정렬용: Critical=0, Medium=1, Low=2, 없음=3"""
    return GRADES.index(g) if g in GRADES else len(GRADES)


def _cluster(tl):
    """확인 필요 이벤트를 사건 단위로 묶는다 (같은 파일 연결 또는 같은 날 같은 대상)"""
    target = tl[tl["점검결과"].fillna("") == "확인 필요"]
    parent = {e: e for e in target["이벤트ID"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for _, r in target.iterrows():
        for other in re.split(r",\s*", str(r.get("동일파일") or "")):
            if other in parent:
                union(r["이벤트ID"], other)
        for other in re.split(r",\s*", str(r.get("중복기록") or "")):
            if other in parent:
                union(r["이벤트ID"], other)
    # 같은 파일을 반복한 행위도 한 사건으로 묶는다.
    # '대상'(URL·메일 제목)이 아니라 실제 파일명 목록을 정규화(file_key)해 비교한다.
    # (예전에는 URL 끝 'upload' 가 같다는 이유로 서로 다른 사이트가 한 사건이 됐다)
    by_file = {}
    for _, r in target.iterrows():
        for f in prep.split_files(r.get("파일목록")):
            k = prep.file_key(f)
            if k:
                by_file.setdefault(k, []).append(r["이벤트ID"])
    for ids in by_file.values():
        for other in ids[1:]:
            union(ids[0], other)

    groups = {}
    for e in target["이벤트ID"]:
        groups.setdefault(find(e), []).append(e)
    return groups


def mock_analyze(tl):
    """예시 AI 판단 생성. 실제 연동 시 이 함수만 LLM 호출로 교체한다."""
    if tl.empty or "점검결과" not in tl:
        return pd.DataFrame()
    rows = []
    for n, (_, ids) in enumerate(sorted(_cluster(tl).items()), start=1):
        g = tl[tl["이벤트ID"].isin(ids)].sort_values("시간")
        codes = {c for x in g["점검사유"].fillna("") for c in crit.codes_of(x)}
        names = " ".join(g["대상"].fillna("").astype(str) + " " + g["상세"].fillna("").astype(str))
        channels = list(dict.fromkeys(g["채널"]))

        # --- AI가 판단할 항목 (예시 규칙으로 대체) ---
        if crit._has_kw(names, crit.SECURITY_KEYWORDS):
            sens, sens_why = "높음", "보안등급 키워드가 포함된 자료"
        elif crit._has_kw(names, crit.MATERIAL_KEYWORDS):
            sens, sens_why = "중간", "업무 자료로 보이는 파일명"
        else:
            sens, sens_why = "낮음", "파일명만으로는 자료 성격을 알기 어려움"

        # REQ-11: 사건의 모든 행위가 사내 동반(R2) 메일일 때만 낮춘다 (다른 행위와 섞이면 낮추지 않음)
        all_companion = all(ch == "이메일" and "R2" in crit.codes_of(rs)
                            for ch, rs in zip(g["채널"], g["점검사유"].fillna("")))
        if all_companion:
            ctx, ctx_why = "낮음", "사내 동반 수신 (사내 주소가 함께 받은 메일)"
        elif codes & {"B2", "B4"}:
            ctx, ctx_why = "높음", "개인메일·개인 클라우드 등 업무 경로가 아닌 곳으로 이동"
        elif codes & {"R3", "R6"}:
            ctx, ctx_why = "낮음", "사내·거래처·공공기관 등 업무 경로"
        else:
            ctx, ctx_why = "중간", "업무 범위인지 부서 확인이 필요"

        if len(channels) >= 2:
            flow, flow_why = "높음", f"{len(channels)}개 채널에 걸친 연속 행위"
        elif len(g) >= 2:
            flow, flow_why = "중간", "같은 대상에 대한 반복 행위"
        else:
            flow, flow_why = "낮음", "단일 행위"

        # --- 코드가 판단하는 항목 ---
        out = "높음" if any(c in OUTBOUND and c != "출력" for c in channels) else (
            "중간" if "출력" in channels else "낮음")
        pat = "높음" if ((g["업무시간외"] == "Y").any() and (g["최초등장"] == "Y").any()) else (
            "중간" if ((g["업무시간외"] == "Y").any() or (g["최초등장"] == "Y").any()
                      or (g["기간"] == "최근").any()) else "낮음")

        levels = {"자료 민감도": sens, "행위 맥락": ctx, "흐름 연결": flow, "반출 경로": out, "시점·패턴": pat}
        why = {"자료 민감도": sens_why, "행위 맥락": ctx_why, "흐름 연결": flow_why,
               "반출 경로": f"채널: {', '.join(channels)}",
               "시점·패턴": ("업무시간 외 " if (g["업무시간외"] == "Y").any() else "")
                          + ("처음 보는 대상 " if (g["최초등장"] == "Y").any() else "")
                          + ("최근 30일" if (g["기간"] == "최근").any() else "")}

        first, last = g["시간"].min(), g["시간"].max()
        span = _fmt_time(first) + (f" ~ {_fmt_time(last)}" if len(g) > 1 else "")
        checks = sorted((c for c in codes if c[0] in "BR"), key=lambda c: (c[0], int(c[1:])))   # B2 < B10
        score = score_candidate(levels)
        rows.append(dict(
            후보ID=f"C{n:02d}", 등급=grade_of(score, codes), 위험점수=score,
            유형=_title(levels, channels, g),
            기간=span, 채널=", ".join(channels), 건수=len(g),
            설명=_summary(g, channels, levels, why),
            근거이벤트=", ".join(g["이벤트ID"]), 점검항목=", ".join(checks),
            판단=levels, 근거=why))
    # 등급 → 내부 점수 순. 동점이면 후보ID 순서 유지 (stable) → 같은 입력에서 항상 같은 순서
    df = pd.DataFrame(rows)
    df["_등급순"] = df["등급"].map(grade_rank)
    df = (df.sort_values(["_등급순", "위험점수"], ascending=[True, False], kind="stable")
          .drop(columns="_등급순").reset_index(drop=True))
    if not df.empty:
        df["구분"] = ["우선 검토" if i < PRIORITY_MAX else "추가 확인" for i in range(len(df))]
    return df


def _fmt_time(t):
    """시간 해석에 실패한 이벤트(NaT)도 화면이 깨지지 않도록 표시"""
    return t.strftime("%m-%d %H:%M") if pd.notna(t) else "시간 미상"


def _title(levels, channels, g):
    ch = " · ".join(channels)
    if levels["자료 민감도"] == "높음":
        return f"보안등급 자료 반출 의심 ({ch})"
    if levels["행위 맥락"] == "높음":
        return f"업무 경로 외 자료 이동 의심 ({ch})"
    if levels["흐름 연결"] == "높음":
        return f"채널 간 연속 행위 ({ch})"
    return f"확인 필요 행위 ({ch})"


def _summary(g, channels, levels, why):
    head = ", ".join(f"{_fmt_time(r['시간'])} {r['채널']} {r['행위']}" for _, r in g.head(3).iterrows())
    tail = f" 외 {len(g) - 3}건" if len(g) > 3 else ""
    return (f"{head}{tail}. {why['자료 민감도']}, {why['행위 맥락']}."
            + (f" {why['흐름 연결']}." if levels["흐름 연결"] != "낮음" else ""))


# ===========================================================================
# 요약 인사이트 (예시) - LLM 연동 시 이 문장을 모델이 직접 작성한다
# ===========================================================================
def mock_insights(tl, cands):
    """검토자가 먼저 읽을 3~4줄 요약과 핵심 포인트를 만든다."""
    if cands.empty:
        return "확인이 필요한 행위가 발견되지 않았습니다.", []
    top = cands.iloc[0]
    need = tl[tl["점검결과"].fillna("") == "확인 필요"]
    recent = need[need["기간"] == "최근"]
    channels = need["채널"].value_counts()
    points = []

    lead = (f"검토 후보 {len(cands)}건(행위 {len(need)}건을 묶음)이 나왔고, "
            f"가장 높은 후보는 '{top.유형}' (등급 {top.등급})입니다.")

    if len(recent):
        share = round(len(recent) / max(len(need), 1) * 100)
        points.append(f"검토 후보에 묶인 행위의 {share}%({len(recent)}건)가 **최근 30일**에 몰려 있습니다.")
    if len(channels):
        points.append("채널별로는 " + ", ".join(f"{c} {n}건" for c, n in channels.head(3).items()) + " 순입니다.")
    off = need[need["업무시간외"] == "Y"]
    if len(off):
        points.append(f"이 중 **업무시간 외** 행위가 {len(off)}건 있습니다.")
    multi = cands[cands["채널"].str.contains(",")]
    if len(multi):
        points.append(f"여러 채널에 걸친 연속 행위가 {len(multi)}건으로, 단일 행위보다 우선 확인이 필요합니다.")
    first = need[need["최초등장"] == "Y"]
    if len(first):
        points.append(f"평소 쓰지 않던 대상(사이트·장치 등)이 {len(first)}건 처음 등장했습니다.")
    return lead, points[:4]


# ===========================================================================
# LLM 연동 (로컬 Ollama · 외부 OpenAI 형식 API)
# ===========================================================================

PROVIDER_DEFAULTS = {
    "ollama": {"model": "gemma4:31b",  "base_url": "http://localhost:11434",    "api_key": ""},
    "openai": {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1", "api_key": ""},
}


def list_ollama_models(base_url, allowed=(), timeout=3):
    """`ollama list` 와 같은 목록(GET /api/tags)에서 allowed 에 든 모델만 돌려준다.

    allowed 가 비면 설치된 모든 모델. 순서는 allowed 순서(없으면 이름순).
    ':latest' 태그는 생략해 적어도 같은 모델로 본다.
    Raises: ConnectionError (서버 연결 불가 · 응답 이상)
    """
    base = (base_url or PROVIDER_DEFAULTS["ollama"]["base_url"]).rstrip("/")
    try:
        resp = requests.get(f"{base}/api/tags", timeout=timeout)
        resp.raise_for_status()
        names = [m.get("name") or m.get("model") or "" for m in resp.json().get("models", [])]
    except (requests.exceptions.RequestException, ValueError, AttributeError) as e:
        raise ConnectionError(f"Ollama 서버({base})에서 모델 목록을 가져올 수 없습니다. "
                              "`ollama serve` 로 실행 중인지 확인하세요.") from e

    def norm(n):
        return n[:-len(":latest")] if n.endswith(":latest") else n
    installed = {norm(n): n for n in names if n}
    if not allowed:
        return sorted(installed.values())
    return [installed[norm(a)] for a in allowed if norm(a) in installed]

_SYS = (
    "당신은 기업 보안팀의 퇴직자 보안 로그 분석 전문가입니다. "
    "주어진 데이터를 바탕으로 정확하고 실용적인 보안 분석 의견을 한국어로 작성합니다."
)


PROMPT_MAX_EVENTS = 30       # 프롬프트에 싣는 확인 필요 이벤트 수
PROMPT_MAX_RECIPIENTS = 5    # 이벤트 한 줄에 적는 수신자 수 (넘으면 "외 N명")
PROMPT_MAX_FILES = 5         # 이벤트 한 줄에 적는 파일 수 (넘으면 "외 N개")
FILE_CHANNELS = ("웹 업로드", "USB", "USB(DLP)", "출력")


def _limited(items, cap, unit):
    return ", ".join(items[:cap]) + (f" 외 {len(items) - cap}{unit}" if len(items) > cap else "")


def recipients_of(r):
    """이벤트 행 → 수신자 주소 목록"""
    return [x.strip() for x in str(_get(r, "수신자") or "").split(",") if x.strip()]


def files_of(r):
    """이벤트 행 → 파일명(경로 제외) 목록"""
    return [prep.file_basename(f) for f in prep.split_files(_get(r, "파일목록"))]


def _get(r, col):
    v = r.get(col, "") if hasattr(r, "get") else getattr(r, col, "")
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else v


def event_line(r):
    """프롬프트 '확인 필요 이벤트 목록'의 한 줄 (REQ-13). 이메일은 제목·수신자(사내/개인메일/타사)·첨부·용량,
    웹 업로드·USB·출력은 파일명을 함께 적는다."""
    ch = _get(r, "채널")
    parts = [str(_get(r, "이벤트ID")), str(_get(r, "시간")), f"{ch} {_get(r, '행위')}"]
    files = files_of(r)
    if ch == "이메일":
        rcpts = [f"{x}({k})" if (k := crit.recipient_kind(x)) else x for x in recipients_of(r)]
        parts.append(str(_get(r, "대상"))[:60] or "제목: (없음)")
        parts.append("수신: " + (_limited(rcpts, PROMPT_MAX_RECIPIENTS, "명") or "(없음)"))
        parts.append(f"첨부 {len(files)}개" + (": " + _limited(files, PROMPT_MAX_FILES, "개") if files else ""))
        size = crit.fmt_size(pd.to_numeric(_get(r, "용량_bytes"), errors="coerce"))
        if size:
            parts.append(f"용량 {size}")
    else:
        parts.append(f"대상: {str(_get(r, '대상'))[:60]}")
        if ch in FILE_CHANNELS and files:
            parts.append("파일: " + _limited(files, PROMPT_MAX_FILES, "개"))
    parts.append(f"사유: {_get(r, '점검사유')}")
    if "R2" in crit.codes_of(_get(r, "점검사유")):
        parts.append("사내 동반")
    return "  " + " | ".join(parts)


def build_llm_prompt(info, crit_df, cands, tl):
    """분석 데이터를 LLM 프롬프트 문자열로 변환한다."""
    need = tl[tl["점검결과"].fillna("") == "확인 필요"] if "점검결과" in tl.columns else tl.iloc[0:0]

    # SA팀 기준 점검
    if not crit_df.empty and "구분" in crit_df.columns:
        code = crit_df[crit_df["구분"] == "기준 점검"]
        hit = code[code["이벤트ID"].fillna("") != ""]
        miss = code[code["이벤트ID"].fillna("") == ""]
        lines = [
            f"  [해당] {r.항목}: {r.결과}" + (f"  ({r.비고})" if str(r.비고).strip() else "")
            for r in hit.itertuples()
        ]
        if not miss.empty:
            lines.append("  [해당 없음] " + " · ".join(miss["항목"]))
        crit_text = "\n".join(lines) or "  (해당 없음)"
    else:
        crit_text = "  (기준점검 데이터 없음)"

    # 위험 후보 사건
    if not cands.empty:
        cand_text = "\n".join(
            f"  [{r.구분}] {r.유형} | 등급 {r.등급} | {r.기간} | {r.건수}건\n    {r.설명}"
            for r in cands.head(10).itertuples()
        )
    else:
        cand_text = "  (확인 필요 행위 없음)"

    # 확인 필요 이벤트 (최대 PROMPT_MAX_EVENTS 건)
    if not need.empty:
        ev_lines = [event_line(r) for _, r in need.head(PROMPT_MAX_EVENTS).iterrows()]
        if len(need) > PROMPT_MAX_EVENTS:
            ev_lines.append(f"  ... 외 {len(need) - PROMPT_MAX_EVENTS}건")
        event_text = "\n".join(ev_lines)
    else:
        event_text = "  (없음)"

    return f"""당신은 기업 보안팀의 퇴직자 보안 로그 분석 전문가입니다.
DLP·웹 필터·USB·출력 모니터링 데이터를 분석하여 내부 자료 유출 위험을 평가합니다.
아래 데이터를 바탕으로 한국어로 분석 의견을 작성하세요.

═══ 분석 대상 ═══
이름: {info.get('이름','')} | 회사: {info.get('회사','')} | 부서: {info.get('부서','')}
퇴직(예정)일: {info.get('퇴직일자','')} | 로그 기준일: {info.get('기준일','')} | 계정: {info.get('계정','')}
전체 이벤트: {len(tl)}건 | 확인 필요: {len(need)}건

═══ SA팀 기준 점검 결과 ═══
{crit_text}

═══ 위험 후보 사건 (코드 분류) ═══
{cand_text}

═══ 확인 필요 이벤트 목록 (최대 {PROMPT_MAX_EVENTS}건) ═══
{event_text}

═══ 판단 기준 ═══
- 외부로 나간 메일이라도 사내 도메인({', '.join(crit.INTERNAL_DOMAINS)}) 주소가 함께 수신한 '사내 동반' 메일은 업무용으로 보고 리스크를 낮게 판단합니다. 단, 보안등급 키워드 자료는 그대로 확인합니다.

═══ 분석 의견 작성 요청 ═══
위 데이터를 종합하여 아래 형식으로 작성하세요.

**1. 종합 위험 평가**
행위 패턴과 전체 위험 수준을 2~3 문단으로 서술하세요.

**2. 핵심 우려 사항**
가장 주의 깊게 검토해야 할 사건·패턴과 그 이유를 서술하세요. 근거가 된 파일명과 수신자를 함께 적으세요.

**3. 추가 확인 필요 사항**
인터뷰 또는 추가 조사로 확인해야 할 사항을 목록으로 작성하세요.

**4. 최종 위험 수준**
Critical / Medium / Low 중 하나를 선택하고 판단 근거를 간략히 서술하세요.
"""


def _make_messages(info, crit_df, cands, tl):
    return [
        {"role": "system", "content": _SYS},
        {"role": "user",   "content": build_llm_prompt(info, crit_df, cands, tl)},
    ]


def stream_analyze(cfg, info, crit_df, cands, tl):
    """LLM 스트리밍 제너레이터.

    cfg = {"provider": "ollama"|"openai",
           "model": str, "base_url": str, "api_key": str}

    Raises:
        ConnectionError: 서버에 연결할 수 없을 때
        RuntimeError:    인증 오류·모델 없음 등 API 오류 시
    """
    provider = cfg.get("provider", "ollama")
    if provider == "ollama":
        yield from _stream_ollama(cfg, info, crit_df, cands, tl)
    elif provider == "openai":
        yield from _stream_openai(cfg, info, crit_df, cands, tl)
    else:
        raise ValueError(f"알 수 없는 provider: {provider}")


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
def _stream_ollama(cfg, info, crit_df, cands, tl):
    base = cfg.get("base_url", "http://localhost:11434").rstrip("/")
    try:
        resp = requests.post(
            f"{base}/api/chat",
            json={"model": cfg["model"],
                  "messages": _make_messages(info, crit_df, cands, tl),
                  "stream": True},
            stream=True, timeout=300,
        )
        if resp.status_code == 404:
            raise RuntimeError(
                f"모델 '{cfg['model']}'을 찾을 수 없습니다. "
                f"`ollama pull {cfg['model']}` 로 먼저 다운로드하세요."
            )
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            chunk = obj.get("message", {}).get("content", "")
            if chunk:
                yield chunk
            if obj.get("done"):
                break
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            f"Ollama 서버({base})에 연결할 수 없습니다. "
            "`ollama serve` 로 실행 중인지 확인하세요."
        )


# ---------------------------------------------------------------------------
# OpenAI 호환 (OpenAI · Groq · Together · 로컬 OpenAI 호환 서버 등)
# ---------------------------------------------------------------------------
def _stream_openai(cfg, info, crit_df, cands, tl):
    base = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.get('api_key', '')}",
    }
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers=headers,
            json={"model": cfg["model"],
                  "messages": _make_messages(info, crit_df, cands, tl),
                  "stream": True},
            stream=True, timeout=300,
        )
        if resp.status_code == 401:
            raise RuntimeError("API 키가 올바르지 않습니다.")
        if resp.status_code == 404:
            raise RuntimeError(
                f"엔드포인트 또는 모델 '{cfg['model']}'을 찾을 수 없습니다."
            )
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode() if isinstance(raw, bytes) else raw
            if line.startswith("data: "):
                line = line[6:]
            if line == "[DONE]":
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk = (obj.get("choices") or [{}])[0].get("delta", {}).get("content", "")
            if chunk:
                yield chunk
    except requests.exceptions.ConnectionError:
        raise ConnectionError(f"API 서버({base})에 연결할 수 없습니다.")
