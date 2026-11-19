"""
OBI 분석 파이프라인 (화면 없이 실행 가능한 공통 경로)

app.py 와 하네스(tests/harness.py)가 모두 이 함수들을 거친다.
→ 화면에서 보는 결과와 하네스가 검증하는 결과가 같은 코드에서 나온다.

    analyze_file()  : 로그 엑셀 → 전처리 → 결과 엑셀을 다시 읽어 저장용 묶음 생성
    derive()        : (저장 후 불러온) 타임라인 → 검토 후보·한눈에 보기 요약
"""
import re

import pandas as pd

import obi_ai as ai
import obi_criteria as crit
import obi_preprocess as prep


def read_log(src_path):
    """업로드 로그를 한 번 읽고 인적 사항을 뽑는다 (REQ-10). 돌려준 workbook 을 analyze_file 에 다시 넘긴다."""
    xls = prep.LogWorkbook(src_path)
    return xls, prep.extract_person(xls)


def analyze_file(src, out_path, info, base_date=None, account=None):
    """전처리 실행 후 store.save_analysis() 에 넘길 값을 돌려준다.

    src 는 파일 경로 또는 read_log() 가 돌려준 workbook (다시 읽지 않음).
    info 는 복사본을 고쳐서 돌려준다(기준일·계정 채움). 원본 dict 는 건드리지 않는다.
    """
    checks, status, timeline = prep.run(src, out_path, info["퇴직일자"], base_date, account)
    summary = pd.read_excel(out_path, sheet_name="채널요약")
    criteria = pd.read_excel(out_path, sheet_name="기준점검").fillna("")
    cond = pd.read_excel(out_path, sheet_name="분석조건")

    info = dict(info)
    base_row = cond[cond["항목"] == "기준일"]["값"]
    info["기준일"] = str(base_row.iloc[0]).split(" ")[0] if len(base_row) else ""
    accounts = timeline["계정"].replace("", pd.NA).dropna() if "계정" in timeline else pd.Series(dtype=str)
    info["계정"] = accounts.mode().iloc[0] if len(accounts) else ""   # 로그에서 가장 많이 나온 계정
    return dict(info=info, checks=checks, status=status, summary=summary, timeline=timeline, criteria=criteria)


def size_stats(tl):
    """채널별 전송 용량 분포 (REQ-01 기준값 결정용). 용량이 있는 행만 센다."""
    cols = ["채널", "건수", "중앙값_MB", "상위5%_MB", "최대_MB", "기준이상_건수"]
    if tl.empty or "용량_bytes" not in tl:
        return pd.DataFrame(columns=cols)
    size = pd.to_numeric(tl["용량_bytes"], errors="coerce")
    rows = []
    for ch, s in size[size > 0].groupby(tl["채널"]):
        mb = s / crit.MB
        rows.append([ch, len(s), round(mb.median(), 1), round(mb.quantile(0.95), 1), round(mb.max(), 1),
                     int((mb >= crit.SIZE_SINGLE_MIN_MB).sum())])
    return pd.DataFrame(rows, columns=cols)


PERSONAL_MAIL_COLUMNS = ["이벤트ID", "시각", "구분", "첨부", "수신자", "제목", "첨부 파일명", "첨부 수", "용량",
                         "차단 여부", "점검결과", "점검 사유"]


def personal_mail(tl):
    """REQ-05 개인메일 발송: 개인메일 도메인 수신자가 있는 메일 + 개인메일 사이트 웹 업로드.
    참고(R1·R2·R4)로 빠진 건도 숨기지 않는다. 정렬: 첨부 있음 → 확인 필요 → 시간순."""
    if tl.empty or "개인메일" not in tl:
        return pd.DataFrame(columns=PERSONAL_MAIL_COLUMNS)
    m = tl[(tl["개인메일"].fillna("") == "Y") & tl["채널"].isin(["이메일", "웹 업로드"])]
    rows = []
    for _, r in m.iterrows():
        files = prep.split_files(r.get("파일목록"))
        mail = r["채널"] == "이메일"
        size = pd.to_numeric(r.get("용량_bytes"), errors="coerce")
        rows.append({
            "이벤트ID": r["이벤트ID"], "시각": r["시간"], "구분": "메일" if mail else "웹 업로드",
            "첨부": "첨부 있음" if files else "",
            "수신자": r.get("수신자") if mail else r.get("대상"),
            "제목": str(r.get("대상") or "").removeprefix("제목: ") if mail else "",
            "첨부 파일명": ", ".join(files), "첨부 수": len(files),
            "용량": crit.fmt_size(size),
            "차단 여부": r.get("차단여부"), "점검결과": r.get("점검결과") or "", "점검 사유": r.get("점검사유") or "",
        })
    df = pd.DataFrame(rows, columns=PERSONAL_MAIL_COLUMNS)
    if df.empty:
        return df
    df["_무첨부"] = df["첨부 수"] == 0
    df["_참고"] = df["점검결과"] != "확인 필요"
    return (df.sort_values(["_무첨부", "_참고", "시각"], kind="stable")
            .drop(columns=["_무첨부", "_참고"]).reset_index(drop=True))


KEY_FILE_COLUMNS = ["파일명", "등급", "근거", "채널", "횟수", "최초 시각", "최종 시각", "나간 곳", "용량", "근거 이벤트ID"]


def _destination(r):
    ch = r["채널"]
    if ch == "이메일":
        return str(r.get("수신자") or "")
    if ch in ("웹 업로드", "USB(DLP)"):
        return str(r.get("대상") or "")
    m = re.search(r"(장치|프린터): ([^|]*)", str(r.get("상세") or ""))
    return m.group(2).strip() if m else ""


def key_files(tl, cands):
    """REQ-03 주요 검토 파일: '확인 필요' 이벤트에 나온 파일을 file_key() 로 묶은 파일 단위 표 (등급 순)."""
    if tl.empty or "점검결과" not in tl:
        return pd.DataFrame(columns=KEY_FILE_COLUMNS)
    grade_of_event = {}
    for r in cands.itertuples() if len(cands) else []:
        for e in r.근거이벤트.split(", "):
            grade_of_event[e] = r.등급

    groups = {}
    for _, r in tl[tl["점검결과"].fillna("") == "확인 필요"].iterrows():
        files = prep.split_files(r.get("파일목록"))
        for f in files:
            name = prep.file_basename(f)
            k = prep.file_key(f) or name.lower()          # 너무 짧아 키가 없으면 파일명 그대로
            g = groups.setdefault(k, dict(names={}, events=[]))
            g["names"][name] = g["names"].get(name, 0) + 1
            g["events"].append((r, len(files)))

    rows = []
    for g in groups.values():
        evs = [r for r, _ in g["events"]]
        ids = list(dict.fromkeys(r["이벤트ID"] for r in evs))
        times = pd.Series([r["시간"] for r in evs]).dropna()
        reasons = list(dict.fromkeys(seg.strip() for r in evs for seg in str(r["점검사유"] or "").split("|")
                                     if seg.strip()))
        grades = [grade_of_event.get(e, "") for e in ids]
        # 파일 1개짜리 이벤트의 용량만 파일 용량으로 본다 (첨부 여러 개인 메일 합계는 제외)
        sizes = [pd.to_numeric(r.get("용량_bytes"), errors="coerce") for r, n in g["events"] if n == 1]
        sizes = [s for s in sizes if pd.notna(s) and s > 0]
        rows.append({
            # 버전·번호만 다른 이름(현장영상_01~06)도 같은 파일로 묶이므로 개수를 함께 보여 준다
            "파일명": max(g["names"].items(), key=lambda kv: kv[1])[0]
                     + (f" 외 {len(g['names']) - 1}개" if len(g["names"]) > 1 else ""),
            "등급": min(grades, key=ai.grade_rank) if grades else "",
            "근거": " | ".join(reasons),
            "채널": ", ".join(dict.fromkeys(r["채널"] for r in evs)),
            "횟수": len(ids),
            "최초 시각": times.min() if len(times) else pd.NaT,
            "최종 시각": times.max() if len(times) else pd.NaT,
            "나간 곳": ", ".join(d for d in dict.fromkeys(_destination(r) for r in evs) if d),
            "용량": crit.fmt_size(max(sizes)) if sizes else "",
            "근거 이벤트ID": ", ".join(ids),
        })
    df = pd.DataFrame(rows, columns=KEY_FILE_COLUMNS)
    if df.empty:
        return df
    df["_등급순"] = df["등급"].map(ai.grade_rank)
    return (df.sort_values(["_등급순", "최초 시각"], kind="stable", na_position="last")
            .drop(columns="_등급순").reset_index(drop=True))


def events_of(tl, ids):
    """'E0001, E0002' 또는 list → 해당 이벤트 원본 로그 행"""
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.split(",") if x.strip()]
    return tl[tl["이벤트ID"].isin(ids)]


def derive(tl):
    """검토 후보(점수)와 한눈에 보기 요약. LLM 연동 시 obi_ai 쪽만 교체하면 된다."""
    cands = ai.mock_analyze(tl)
    lead, points = ai.mock_insights(tl, cands)
    return cands, lead, points
