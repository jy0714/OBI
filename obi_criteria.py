"""
OBI 점검 기준 (SA팀 기준 반영)

- 이 파일 상단의 키워드 사전과 기준값은 담당자가 직접 추가·삭제할 수 있다.
- 코드가 판단하는 것은 '확인이 필요한 사실'까지다. 이상징후 확정은 담당자가 한다.
- 결과는 삭제하지 않는다. '확인 필요' / '참고 관찰'로 구분만 한다.
"""
import re
from collections import Counter

import pandas as pd

# ===========================================================================
# 키워드 사전 (자유롭게 추가·삭제)
# ===========================================================================
SECURITY_KEYWORDS = ["중요비", "Confidential", "CONFIDENTIAL", "대외비"]          # 무조건 확인
MATERIAL_KEYWORDS = ["도면", "매뉴얼", "AOP", "Plan", "자리배치", "임직원", "컨설팅", "계약서", "사진"]
PERSONAL_DOC_KEYWORDS = ["재직증명서", "원천징수", "경력증명서", "급여명세", "근로소득", "연말정산",
                         "건강보험", "주민등록", "가족관계"]                         # 개인 문서 → 참고
INTERNAL_DOMAINS = ["doosan.com"]
PERSONAL_MAIL_DOMAINS = ["naver.com", "gmail.com", "daum.net", "kakao.com", "hanmail.net",
                         "hotmail.com", "outlook.com", "yahoo.com"]
PUBLIC_DOMAIN_SUFFIXES = [".go.kr", ".ac.kr", ".org"]                                # 업무용 추정 → 참고
PERSONAL_CLOUD_DOMAINS = ["mybox.naver.com", "cloud.naver.com", "onedrive.live.com", "1drv.ms",
                          "drive.google.com", "dropbox.com", "mega.nz", "wetransfer.com", "box.com"]
RECRUIT_DOMAINS = ["saramin.co.kr", "jobkorea.co.kr", "linkedin.com", "wanted.co.kr",
                   "incruit.com", "remember.co.kr", "peoplenjob.com"]                # 채용 사이트 → 참고
APPROVED_SERVICES = ["agiloft.com", "copilotstudio.microsoft.com"]                  # 사내·승인 서비스 → 참고
AMBIGUOUS_NAME_PATTERNS = [r"^document\d*$", r"^문서\d*$", r"^제목\s*없음", r"^untitled", r"^새\s*", r"^scan",
                           r"^image\d*$", r"^img_?\d+$", r"^\d+$", r"^[a-z0-9_\-]{1,4}$", r"^test", r"^temp"]

# ===========================================================================
# 기준값
# ===========================================================================
PRINT_PAGES_MIN = 50        # 단일 출력 매수 기준
PRINT_REPEAT_MIN = 2        # 같은 파일 출력 횟수 기준
PRINT_DAY_MIN = 20          # 특정일 출력 급증: 하루 출력 건수 최소값
PRINT_DAY_RATIO = 3         # 특정일 출력 급증: 출력한 날 평균 대비 배수
USB_REPEAT_MIN = 10         # USB 쓰기·삭제 반복 기준 (요약에 표시)
# [결정 필요] 대용량 전송 기준 (REQ-01). 임시 기본값 — 실제 로그 분포 확인 후 조정
#   python tests/harness.py snapshot 로그.xlsx ... 실행 시 채널별 용량 분포가 출력된다.
SIZE_SINGLE_MIN_MB = 100    # 단일 행위 용량
SIZE_DAY_MIN_MB = 500       # 하루 합계 용량
SIZE_DAY_EVENT_MIN_MB = 1   # 하루 합계 초과일에 B10 을 붙일 개별 행위 최소 용량 (작은 웹 접속까지 표시하지 않도록)
SIZE_CHANNELS = ("이메일", "웹 업로드", "USB", "USB(DLP)", "웹")   # 웹은 허용된 접속의 SentBytes
MB = 1024 * 1024
# 사내 동반 수신 (REQ-11)
# [결정 필요 #7] 발신자 본인의 사내 주소만 수신자에 있으면 동반으로 보지 않는다 (False 면 본인 주소도 동반)
COMPANION_EXCLUDE_SENDER = True
# [결정 필요 #8] 보안등급 키워드(B1) 메일도 사내 동반이면 참고로 내릴지 (False = '확인 필요' 유지, 행위 맥락만 낮음)
COMPANION_DOWNGRADE_B1 = False

# ===========================================================================
# 점검 항목 정의 (화면·보고서에 그대로 표시)
# ===========================================================================
CODE_CHECKS = [
    ("B1", "보안등급 키워드", "파일명·메일 제목에 중요비·Confidential·대외비 포함 (전 채널, 무조건 확인)"),
    ("B2", "개인메일로 첨부 발송", "수신자에 개인메일 도메인이 있고 첨부가 있으며, 사내 도메인 동반 수신이 없음"),
    ("B3", "자료 종류 키워드 반출·출력", "도면·매뉴얼·AOP·Plan·자리배치·임직원·컨설팅·계약서·사진 파일의 외부 발송·업로드·USB·출력"),
    ("B4", "개인 클라우드·개인메일 사이트 이용", "허용된 웹 접속 또는 파일 업로드 중 개인 클라우드·개인메일 사이트"),
    ("B5", "USB 사용 허용", "USB 파일 기록이 허용된 경우 (쓰기·삭제 반복 여부 함께 표시)"),
    ("B6", f"단일 출력 {PRINT_PAGES_MIN}매 이상", f"한 번에 {PRINT_PAGES_MIN}매 이상 출력"),
    ("B7", "같은 파일 반복 출력", f"같은 파일을 {PRINT_REPEAT_MIN}회 이상 출력"),
    ("B8", "특정일 출력 급증", f"하루 {PRINT_DAY_MIN}건 이상이면서 출력한 날 평균의 {PRINT_DAY_RATIO}배 이상"),
    ("B9", "파일명 모호", "출력·반출 파일명이 모호해 내용 확인이 필요"),
    ("B10", "대용량 전송", f"한 번에 {SIZE_SINGLE_MIN_MB}MB 이상 또는 하루 합계 {SIZE_DAY_MIN_MB}MB 이상 전송 "
                         "(이메일·웹 업로드·USB·허용된 웹 접속, 차단 건 제외)"),
]
REFERENCE_CHECKS = [
    ("R1", "차단 완료", "보안 솔루션이 차단한 기록 (차단이 정상 동작한 것으로 보고 우선 검토 제외)"),
    ("R2", "사내 동반 수신", "외부 수신자(개인메일·타사)가 있으나 사내 도메인 주소가 함께 수신 (업무용 추정, "
                         "B2·B3·B9·B10 제외 · B1 은 유지)"),
    ("R3", "공공·학술 기관", ".go.kr·.ac.kr·.org 수신 또는 업로드 (업무용 추정)"),
    ("R4", "개인 문서", "재직증명서·원천징수영수증 등 개인 문서 (공식 문서로 집계하지 않음)"),
    ("R5", "채용 사이트", "채용 사이트 이용"),
    ("R6", "사내·승인 서비스", "사내 도메인 또는 승인 서비스 이용"),
]
AI_CHECKS = [
    ("A1", "특이 URL", "허용된 웹 접속 중 성격이 특이하거나 이상한 URL"),
    ("A2", "내부 자료 추정", "키워드가 없어도 파일명·제목상 내부 자료로 보이는지, 모호한 파일명의 성격"),
    ("A3", "부서 업무 맥락", "부서 업무상 자연스러운 출력·발송인지 (예: DBKR 특정 부서의 Invoice·계약서)"),
    ("A4", "첨부 성격", "회사 사진·매뉴얼 등 회사 자료인지, 외부 AI 이용 목적 전송으로 보이는지"),
    ("A5", "개인정보 대량 전송", "임직원 이메일·연락처 목록, 자리배치도 등 개인정보성 자료"),
    ("A6", "타사 발송 기밀성", "타사 도메인 발송 자료가 업무 범위를 넘는 기밀인지"),
    ("A7", "흐름 연결·추가 탐지", "채널·기간 간 연결된 행위, 위 기준에 없는 이상징후"),
]
ALL_LABELS = {cid: name for cid, name, _ in CODE_CHECKS + REFERENCE_CHECKS + AI_CHECKS}


# ===========================================================================
# 유틸
# ===========================================================================
def _has_kw(text, keywords):
    t = str(text or "")
    return [k for k in keywords if k.lower() in t.lower()]


def _domain(addr):
    s = str(addr or "").strip().lower()
    if "@" in s:
        return s.split("@")[-1]
    m = re.match(r"^(?:https?://)?([^/:\s]+)", s)
    return m.group(1) if m else ""


def _match_domain(dom, domains):
    return any(dom == d or dom.endswith("." + d) for d in domains)


def _stem(name):
    s = re.split(r"[\\/]", str(name or "").strip())[-1].lower()
    s = re.sub(r"^microsoft (word|excel|powerpoint) - ", "", s)
    return re.sub(r"\.[a-z][a-z0-9]{0,7}$", "", s).strip()


_CODE_RE = re.compile(r"(?:^|\| )([BRA]\d+) ")


def codes_of(reasons):
    """점검사유 문자열 'B1 보안등급 키워드(…) | R6 …' → 코드 목록 ['B1', 'R6'] (B10 등 두 자리 포함).
    각 사유 항목 맨 앞의 코드만 읽어 파일명 속 'B2' 같은 글자는 무시한다."""
    return list(dict.fromkeys(_CODE_RE.findall(str(reasons or ""))))


def _is_ambiguous(name):
    st = _stem(name)
    return bool(st) and any(re.search(p, st) for p in AMBIGUOUS_NAME_PATTERNS)


def internal_companions(rcpts, sender=""):
    """REQ-11 외부 수신자가 있을 때 함께 받은 사내 주소 목록 (외부가 없으면 빈 목록).
    사내 판단은 INTERNAL_DOMAINS 한 곳 (하위 도메인 포함). 발신자 본인 주소는 COMPANION_EXCLUDE_SENDER 에 따름."""
    rcpts = [str(x).strip().lower() for x in rcpts if str(x).strip()]
    external = [x for x in rcpts if _domain(x) and not _match_domain(_domain(x), INTERNAL_DOMAINS)]
    if not external:
        return []
    me = str(sender or "").strip().lower()
    return [x for x in rcpts if _match_domain(_domain(x), INTERNAL_DOMAINS)
            and not (COMPANION_EXCLUDE_SENDER and me and x == me)]


def fmt_mb(n_bytes):
    return f"{n_bytes / MB:,.1f}MB"


def fmt_size(n_bytes):
    """화면 표시용 용량: 1MB 미만은 KB, 값이 없으면 빈칸"""
    n = pd.to_numeric(n_bytes, errors="coerce")
    if pd.isna(n) or n <= 0:
        return ""
    return fmt_mb(n) if n >= MB else f"{n / 1024:,.0f}KB"


def transfer_bytes(tl):
    """전송 용량으로 셀 값 (Series, 대상 아니면 0).
    허용된 전송 채널만, USB 삭제는 제외, USB로그와 중복 기록된 DLP USB 는 한 번만 센다."""
    size = pd.to_numeric(tl["용량_bytes"], errors="coerce").fillna(0)
    ok = tl["채널"].isin(SIZE_CHANNELS) & (tl["차단여부"] != "차단")
    ok &= ~tl["행위"].astype(str).str.contains("delete", case=False)
    if "중복기록" in tl:
        ok &= ~((tl["채널"] == "USB(DLP)") & (tl["중복기록"].fillna("") != ""))
    return size.where(ok, 0)


# ===========================================================================
# 점검 실행
# ===========================================================================
def apply_criteria(tl):
    """타임라인(파일목록 컬럼 포함)에 점검결과·점검사유 컬럼을 추가하고 요약표를 돌려준다."""
    hits = {i: [] for i in tl.index}      # (코드, 상세)
    outbound = {"이메일", "웹 업로드", "USB", "USB(DLP)", "출력"}

    # 출력 통계 (반복·급증)
    pr = tl[tl["채널"] == "출력"]
    repeat = Counter(_stem(x) for x in pr["대상"] if _stem(x))
    day_cnt = pr["시간"].dt.date.value_counts() if len(pr) else pd.Series(dtype=int)
    day_avg = day_cnt.mean() if len(day_cnt) else 0
    surge_days = {d for d, c in day_cnt.items() if c >= PRINT_DAY_MIN and c >= day_avg * PRINT_DAY_RATIO}

    # 대용량 전송 (단일 / 하루 합계)
    sent = transfer_bytes(tl)
    sent_day = sent.groupby(tl["시간"].dt.date).sum() if len(tl) else pd.Series(dtype=float)
    big_days = {d: v for d, v in sent_day.items() if v >= SIZE_DAY_MIN_MB * MB}

    for i, r in tl.iterrows():
        ch, blocked = r["채널"], r["차단여부"] == "차단"
        files = [f for f in (r.get("파일목록") or []) if str(f).strip()]
        names = " ".join(map(str, files)) + " " + str(r.get("대상") or "")
        rcpts = [x.strip() for x in str(r.get("수신자") or "").split(",") if x.strip()]
        doms = sorted({_domain(x) for x in rcpts})   # set 순회 순서는 실행마다 달라질 수 있어 정렬
        dom = _domain(r.get("대상")) if ch in ("웹", "웹 업로드") else ""

        personal_doc = _has_kw(names, PERSONAL_DOC_KEYWORDS)
        if personal_doc:
            hits[i].append(("R4", ", ".join(personal_doc)))

        # 차단 완료
        if blocked:
            hits[i].append(("R1", ""))

        # 이메일 수신자 분류 — 사내 동반(REQ-11): 외부 수신자 + 사내 주소(본인 제외) 함께 수신
        personal_rcpt = [d for d in doms if _match_domain(d, PERSONAL_MAIL_DOMAINS)]
        external = [d for d in doms if d and not _match_domain(d, INTERNAL_DOMAINS)]
        companions = internal_companions(rcpts, r.get("_발신자")) if ch == "이메일" else []
        co = bool(companions)        # 사내 동반 메일: B2·B3·B9·B10 을 붙이지 않는다

        # B1 보안등급 (무조건 — 사내 동반이어도 유지, COMPANION_DOWNGRADE_B1 로만 끔)
        sec = _has_kw(names, SECURITY_KEYWORDS)
        if sec and not (co and COMPANION_DOWNGRADE_B1):
            hits[i].append(("B1", ", ".join(sec)))

        if ch == "이메일":
            if co:
                hits[i].append(("R2", ", ".join(companions)))
            elif personal_rcpt and files and not personal_doc and not blocked:
                hits[i].append(("B2", ", ".join(x for x in rcpts if _domain(x) in personal_rcpt)))
            if external and all(any(d.endswith(s) for s in PUBLIC_DOMAIN_SUFFIXES) for d in external):
                hits[i].append(("R3", ", ".join(external)))
            elif not external:
                hits[i].append(("R6", "사내 수신만"))

        # B3 자료 키워드 반출·출력 (사내 전용·사내 동반 메일·개인 문서·차단은 제외)
        mat = _has_kw(names, MATERIAL_KEYWORDS)
        if mat and ch in outbound and not personal_doc and not blocked:
            if ch != "이메일" or (external and not co):
                hits[i].append(("B3", ", ".join(mat)))

        # 웹 / 웹 업로드
        if ch in ("웹", "웹 업로드") and dom:
            if _match_domain(dom, RECRUIT_DOMAINS):
                hits[i].append(("R5", dom))
            elif _match_domain(dom, INTERNAL_DOMAINS + APPROVED_SERVICES):
                hits[i].append(("R6", dom))
            elif any(dom.endswith(s) for s in PUBLIC_DOMAIN_SUFFIXES):
                hits[i].append(("R3", dom))
            elif not blocked and (_match_domain(dom, PERSONAL_CLOUD_DOMAINS) or
                                  (_match_domain(dom, PERSONAL_MAIL_DOMAINS) and "mail" in dom)):
                hits[i].append(("B4", dom))

        # USB 허용
        if ch in ("USB", "USB(DLP)") and not blocked:
            hits[i].append(("B5", str(r.get("행위") or "")))

        # 출력
        if ch == "출력":
            pages = pd.to_numeric(r.get("페이지수"), errors="coerce")
            if pd.notna(pages) and pages >= PRINT_PAGES_MIN:
                hits[i].append(("B6", f"{int(pages)}매"))
            st = _stem(r.get("대상"))
            if st and repeat[st] >= PRINT_REPEAT_MIN:
                hits[i].append(("B7", f"{repeat[st]}회"))
            if pd.notna(r["시간"]) and r["시간"].date() in surge_days:
                hits[i].append(("B8", f"{r['시간'].date()} {day_cnt[r['시간'].date()]}건"))

        # B9 파일명 모호 (사내 동반 메일 제외)
        if ch in outbound and not blocked and not co:
            amb = [f for f in (files or [r.get("대상")]) if _is_ambiguous(f)]
            if amb:
                hits[i].append(("B9", ", ".join(map(str, amb))))

        # B10 대용량 전송 (차단 건은 transfer_bytes 에서 0 → R1 만 남음, 사내 동반 메일 제외)
        b = 0 if co else sent[i]
        day = r["시간"].date() if pd.notna(r["시간"]) else None
        if b >= SIZE_SINGLE_MIN_MB * MB:
            hits[i].append(("B10", fmt_mb(b)))
        elif day in big_days and b >= SIZE_DAY_EVENT_MIN_MB * MB:
            hits[i].append(("B10", f"{fmt_mb(b)}, {day} 하루 합계 {fmt_mb(big_days[day])}"))

    def fmt(lst):
        seen, out = set(), []
        for c, d in lst:
            if (c, d) in seen:
                continue
            seen.add((c, d))
            out.append(f"{c} {ALL_LABELS[c]}" + (f"({d})" if d else ""))
        return " | ".join(out)

    tl["점검결과"] = [("확인 필요" if any(c.startswith("B") for c, _ in hits[i])
                     else "참고" if hits[i] else "") for i in tl.index]
    tl["점검사유"] = [fmt(hits[i]) for i in tl.index]

    # 요약표
    rows = []
    for cid, name, desc in CODE_CHECKS + REFERENCE_CHECKS:
        ids = [tl.at[i, "이벤트ID"] for i in tl.index if any(c == cid for c, _ in hits[i])]
        note = ""
        if cid == "B5" and ids:
            usb = tl[tl["채널"] == "USB"]
            dels = int(usb["행위"].astype(str).str.contains("Delete", case=False).sum())
            writes = len(usb) - dels
            dlp = int((tl["채널"] == "USB(DLP)").sum())
            note = f"USB로그 쓰기 {writes}건 · 삭제 {dels}건 / DLP 반출 {dlp}건" + \
                   (" · 반복 사용: USB 잔존 여부·개인/회사 USB 확인 필요" if writes + dels >= USB_REPEAT_MIN else "")
        if cid == "B10" and big_days:
            note = "하루 합계 초과: " + ", ".join(f"{d} {fmt_mb(v)}" for d, v in sorted(big_days.items()))
        rows.append(dict(구분="기준 점검" if cid.startswith("B") else "참고 관찰", 코드=cid, 항목=name,
                         결과=f"해당 {len(ids)}건" if ids else "해당 없음", 비고=note,
                         이벤트ID=", ".join(ids), 기준=desc))
    for cid, name, desc in AI_CHECKS:
        rows.append(dict(구분="AI 점검 (연동 예정)", 코드=cid, 항목=name, 결과="LLM 연동 후 판단", 비고="",
                         이벤트ID="", 기준=desc))
    return tl, pd.DataFrame(rows)
