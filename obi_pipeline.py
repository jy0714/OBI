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


def person_sources(person, values):
    """REQ-10 인적 사항 출처: 로그 값 그대로='로그', 로그 값을 고침='수정', 로그에 없어 직접 입력='직접'"""
    out = {}
    for k in ("이름", "직급", "부서"):
        auto, val = str(person.get(k) or "").strip(), str(values.get(k) or "").strip()
        out[k] = ("로그" if val == auto else "수정") if auto else "직접"
    return out


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
    if "이벤트ID" not in tl:
        return tl.iloc[0:0]
    return tl[tl["이벤트ID"].isin(ids)]


def review_missing(rec):
    """REQ-08 정식 이력의 검토 필수값 중 빠진 항목 이름 목록 (Test 이력은 항상 빈 목록).
    rec: 이력 dict 또는 목록 행. [결정 필요 #5] 강제 시점은 검토 저장·리포트 생성 (분석 직후는 '입력 필요' 표시만)."""
    get = rec.get if hasattr(rec, "get") else (lambda k, d=None: getattr(rec, k, d))
    if int(get("테스트여부", 0) or 0):
        return []
    miss = []
    if (get("인터뷰여부", "") or "미확인") == "미확인":
        miss.append("인터뷰 여부")
    if (get("보안서약서", "") or "미확인") == "미확인":
        miss.append("보안서약서 작성 여부")
    if not str(get("검토자", "") or "").strip():
        miss.append("검토자")
    if not str(get("코멘트", "") or "").strip():
        miss.append("Comment 및 확인 결과")
    # REQ-12: 인터뷰를 했으면 서약서 PDF 필수, '작성 여부 = 예'는 파일이 있을 때만
    # [결정 필요 #10] 인터뷰를 안 했으면 서약서는 선택
    no_file = not int(get("서약서수", 0) or 0)
    if no_file and (get("인터뷰여부", "") == "예" or get("보안서약서", "") == "예"):
        miss.append("보안서약서 PDF")
    return miss


def pledge_status(rec):
    """게시판 '서약서' 칸: 첨부됨 / 필요 / 해당 없음"""
    get = rec.get if hasattr(rec, "get") else (lambda k, d=None: getattr(rec, k, d))
    if int(get("서약서수", 0) or 0):
        return "첨부됨"
    if not int(get("테스트여부", 0) or 0) and get("인터뷰여부", "") == "예":
        return "필요"
    return "해당 없음"


BOARD_COLUMNS = ["이력#", "분석일시", "회사", "이름", "직급", "부서", "퇴직일자", "최고 등급", "확인 필요 건수",
                 "검토 상태", "검토자", "입력 완료", "서약서", "테스트"]
NO_GRADE = "(없음)"     # 옛 이력 등 등급이 저장되지 않은 건


def board(df, q="", companies=None, states=None, grades=None, date_from=None, date_to=None, include_test=False):
    """REQ-06 게시판: store.list_analyses() 결과 → 필터·검색 후 표시용 표 (최신순).
    grades 에 NO_GRADE 를 넣으면 등급이 비어 있는 옛 이력도 나온다."""
    if df.empty:
        return pd.DataFrame(columns=BOARD_COLUMNS)
    v = df.copy()
    if not include_test:
        v = v[v["테스트여부"] == 0]
    if companies is not None:
        v = v[v["회사"].isin(companies)]
    if states is not None:
        v = v[v["검토상태"].isin(states)]
    if grades is not None:
        v = v[v["최고등급"].replace("", NO_GRADE).isin(grades)]
    day = pd.to_datetime(v["분석일시"], errors="coerce").dt.date
    if date_from is not None:
        v = v[day >= date_from]
        day = day[v.index]
    if date_to is not None:
        v = v[day <= date_to]
    if q:
        v = v[v["이름"].astype(str).str.contains(q, na=False, regex=False)
              | v["부서"].astype(str).str.contains(q, na=False, regex=False)]
    v = v.sort_values("id", ascending=False)
    out = pd.DataFrame({
        "이력#": v["id"], "분석일시": v["분석일시"], "회사": v["회사"], "이름": v["이름"], "직급": v["직급"],
        "부서": v["부서"], "퇴직일자": v["퇴직일자"], "최고 등급": v["최고등급"],
        "확인 필요 건수": v["확인필요건수"], "검토 상태": v["검토상태"], "검토자": v["검토자"],
        "입력 완료": ["-" if r.테스트여부 else ("입력 필요" if review_missing(r) else "완료") for r in v.itertuples()],
        "서약서": [pledge_status(r) for r in v.itertuples()],
        "테스트": v["테스트여부"].map({1: "TEST", 0: ""}),
    }, columns=BOARD_COLUMNS)
    return out.reset_index(drop=True)


def board_summary(tl):
    """게시판 목록용 값 (REQ-06): (최고 등급, 확인 필요 건수). 저장할 때 같이 넣는다."""
    cands, _, _ = derive(tl)
    need = int((tl["점검결과"].fillna("") == "확인 필요").sum()) if "점검결과" in tl else 0
    return (cands["등급"].iloc[0] if len(cands) else ""), need


def save(result, *, test=False, sources=None):
    """analyze_file() 결과를 저장하고 이력 id 를 돌려준다 (앱·하네스 공통)."""
    import obi_store as store
    top, need = board_summary(result["timeline"])
    return store.save_analysis(result["info"], result["checks"], result["summary"], result["timeline"],
                               result["criteria"], test=test, top_grade=top, need_count=need, sources=sources)


def dashboard(tl, summary=None, base_date="", retire_date=""):
    """REQ-02 채널·추이 대시보드 집계. 화면(Altair)은 이 값만 그린다.

    돌려주는 dict:
      daily     날짜·채널·구분(확인 필요/일반)·건수  — 채널별 누적 막대
      need      날짜·확인필요                          — 확인 필요 선
      bytes     날짜·채널·MB                           — 일자별 전송 용량 (REQ-01 과 같은 기준)
      compare   채널·기간(평소/최근)·30일환산          — 평소 대비 최근
      offhours  채널·전체·업무시간외·비중(%)
      markers   이름·날짜                              — 최근 기간 시작일·기준일·퇴직일 세로선
      dates     활동이 있는 날짜 목록 (최근 → 과거)
    """
    empty = dict(daily=pd.DataFrame(columns=["날짜", "채널", "구분", "건수"]),
                 need=pd.DataFrame(columns=["날짜", "확인필요"]),
                 bytes=pd.DataFrame(columns=["날짜", "채널", "MB"]),
                 compare=pd.DataFrame(columns=["채널", "기간", "30일환산"]),
                 offhours=pd.DataFrame(columns=["채널", "전체", "업무시간외", "비중(%)"]),
                 markers=pd.DataFrame(columns=["이름", "날짜"]), dates=[])
    if tl.empty or "시간" not in tl or tl["시간"].notna().sum() == 0:
        return empty
    t = tl[tl["시간"].notna()].copy()
    t["날짜"] = t["시간"].dt.strftime("%Y-%m-%d")
    t["구분"] = t["점검결과"].fillna("").map(lambda x: "확인 필요" if x == "확인 필요" else "일반")

    daily = t.groupby(["날짜", "채널", "구분"]).size().reset_index(name="건수")
    need = (t[t["구분"] == "확인 필요"].groupby("날짜").size().reindex(sorted(t["날짜"].unique()), fill_value=0)
            .rename("확인필요").reset_index())
    sent = crit.transfer_bytes(t)
    b = t.assign(MB=sent / crit.MB)[sent > 0]
    by = b.groupby(["날짜", "채널"])["MB"].sum().round(1).reset_index()

    compare = empty["compare"]
    if summary is not None and len(summary) and {"평소_30일환산", "최근_30일환산"} <= set(summary.columns):
        compare = (summary.melt(id_vars="채널", value_vars=["평소_30일환산", "최근_30일환산"],
                                var_name="기간", value_name="30일환산")
                   .assign(기간=lambda d: d["기간"].map({"평소_30일환산": "평소", "최근_30일환산": "최근"})))
    off = t.groupby("채널").agg(전체=("이벤트ID", "count"),
                               업무시간외=("업무시간외", lambda s: int((s.fillna("") == "Y").sum()))).reset_index()
    off["비중(%)"] = (off["업무시간외"] / off["전체"] * 100).round(1)

    marks = []
    base = pd.to_datetime(base_date, errors="coerce")
    if pd.notna(base):
        marks.append(("최근 기간 시작", (base - pd.Timedelta(days=prep.RECENT_DAYS)).strftime("%Y-%m-%d")))
        marks.append(("기준일", base.strftime("%Y-%m-%d")))
    retire = pd.to_datetime(retire_date, errors="coerce")
    if pd.notna(retire):
        marks.append(("퇴직일", retire.strftime("%Y-%m-%d")))
    return dict(daily=daily, need=need, bytes=by, compare=compare, offhours=off,
                markers=pd.DataFrame(marks, columns=["이름", "날짜"]),
                dates=sorted(t["날짜"].unique(), reverse=True))


def events_on(tl, day):
    """'YYYY-MM-DD' 그날의 이벤트"""
    if tl.empty or "시간" not in tl:
        return tl
    return tl[tl["시간"].dt.strftime("%Y-%m-%d") == day]


def derive(tl):
    """검토 후보(점수)와 한눈에 보기 요약. LLM 연동 시 obi_ai 쪽만 교체하면 된다."""
    cands = ai.mock_analyze(tl)
    lead, points = ai.mock_insights(tl, cands)
    return cands, lead, points
