"""요구사항 완료 기준 (OBI_requirements_2026-11-19.md) — 합성 샘플로 확인한다.

골든은 "바뀌었는지", 여기는 "요구사항대로 동작하는지"를 본다. 항목마다 요구사항 번호를 붙인다.
"""


def _cand_of(cands, eid):
    rows = [r for r in cands.itertuples() if eid in r.근거이벤트.split(", ")]
    assert len(rows) == 1, eid
    return rows[0]


def _tl_row(tl, eid):
    return tl[tl["이벤트ID"] == eid].iloc[0]


def _eid(tl, **match):
    m = tl
    for col, val in match.items():
        m = m[m[col].astype(str).str.contains(val, regex=False, na=False)]
    assert len(m) >= 1, match
    return m.iloc[0]["이벤트ID"]


# --- P-3 -------------------------------------------------------------------
def test_p3_different_sites_not_merged(sample):
    """URL 끝이 같아도('upload') 다른 사이트 접속·업로드는 다른 사건"""
    tl, cands = sample["tl"], sample["cands"]
    mybox = _eid(tl, 대상="mybox.naver.com")
    dropbox = _eid(tl, 대상="dropbox.com")
    assert _cand_of(cands, mybox).후보ID != _cand_of(cands, dropbox).후보ID


def test_p3_same_file_repeated_is_one_case(sample):
    tl, cands = sample["tl"], sample["cands"]
    prints = tl[(tl["채널"] == "출력") & tl["대상"].astype(str).str.contains("대외비_사업계획")]["이벤트ID"].tolist()
    assert len(prints) == 2
    assert _cand_of(cands, prints[0]).후보ID == _cand_of(cands, prints[1]).후보ID


# --- REQ-01 대용량 전송 -------------------------------------------------------
def _codes(tl, eid):
    import obi_criteria as crit
    return crit.codes_of(_tl_row(tl, eid)["점검사유"])


def test_req01_single_and_boundary(sample):
    tl = sample["tl"]
    assert "B10" in _codes(tl, _eid(tl, 대상="youtube.com"))                 # 웹 접속 120MB
    assert "B10" in _codes(tl, _eid(tl, 대상="wetransfer.com"))              # 정확히 100MB (경계 포함)
    assert "B10" not in _codes(tl, _eid(tl, 대상="backup_99.zip"))           # 99.9MB
    big = _eid(tl, 대상="big.iso")                                            # 차단 200MB → R1 만
    assert "B10" not in _codes(tl, big) and "R1" in _codes(tl, big)
    yt = _tl_row(tl, _eid(tl, 대상="youtube.com"))
    assert yt["점검결과"] == "확인 필요" and "120.0MB" in yt["점검사유"]


def test_req01_day_total(sample):
    tl = sample["tl"]
    day = tl[tl["대상"].astype(str).str.contains("현장영상")]
    assert len(day) == 6
    assert all("하루 합계 540.0MB" in r for r in day["점검사유"])


def test_req01_criteria_row(sample):
    c = sample["crit"].set_index("코드")
    assert c.loc["B10", "결과"].startswith("해당")
    assert "2026-08-19" in c.loc["B10", "비고"]
    assert "100MB" in c.loc["B10", "기준"] and "500MB" in c.loc["B10", "기준"]


def test_req01_size_stats(sample):
    import obi_pipeline
    s = obi_pipeline.size_stats(sample["tl"]).set_index("채널")
    assert s.loc["웹", "최대_MB"] == 120.0


# --- REQ-05 개인메일 발송 -----------------------------------------------------
def test_req05_personal_mail_table(sample):
    pm = sample["personal_mail"]
    top = pm.iloc[0]
    assert "friend@gmail.com" in top["수신자"] and "임직원 연락처.xlsx" in top["첨부 파일명"]
    assert top["첨부"] == "첨부 있음" and top["점검결과"] == "확인 필요"
    # 사내 동반 수신(R2)도 같은 영역에 참고로 나온다
    r2 = pm[pm["수신자"].astype(str).str.contains("me@naver.com")].iloc[0]
    assert r2["점검결과"] == "참고" and "R2" in r2["점검 사유"]
    # 차단된 개인메일 발송(R1)도 숨기지 않는다
    assert (pm["차단 여부"] == "차단").any()
    # 첨부 있는 건이 모두 위
    has = (pm["첨부 수"] > 0).tolist()
    assert has == sorted(has, reverse=True)


def test_req05_web_upload_and_no_attachment(sample):
    pm = sample["personal_mail"]
    web = pm[pm["구분"] == "웹 업로드"]
    assert len(web) == 1 and "mail.naver.com" in web.iloc[0]["수신자"]
    last = pm.iloc[-1]
    assert last["첨부 수"] == 0 and "me2@daum.net" in last["수신자"]


# --- REQ-10 인적 사항 자동 입력 (추출) ---------------------------------------
def test_req10_extract_from_sample(tmp_path):
    import obi_pipeline
    import sample_ddi
    src = sample_ddi.build(str(tmp_path / "s.xlsx"))
    xls, person = obi_pipeline.read_log(src)
    assert (person["이름"], person["직급"], person["계정"]) == ("홍길동", "선임", "kildong.hong")
    assert person["부서"] == ""                       # 샘플에는 부서 컬럼 없음 → 직접 입력
    # 같은 workbook 으로 분석 (다시 읽지 않음)
    r = obi_pipeline.analyze_file(xls, str(tmp_path / "o.xlsx"), {"퇴직일자": "2026-09-30"}, "2026-09-02")
    assert len(r["timeline"]) > 0


# --- REQ-03 주요 검토 파일 ----------------------------------------------------
def test_req03_design_drawing_one_row(sample):
    kf = sample["key_files"]
    rows = kf[kf["파일명"].str.contains("설계도면_v2.dwg", regex=False)]
    assert len(rows) == 1
    r = rows.iloc[0]
    assert r["채널"] == "USB, USB(DLP)" and r["횟수"] == 3
    assert "B5" in r["근거"] and "B3" in r["근거"]
    assert r["등급"] in ("Critical", "Medium", "Low")


def test_req03_sorted_by_grade_and_only_flagged(sample):
    import obi_ai as ai
    kf, tl = sample["key_files"], sample["tl"]
    ranks = kf["등급"].map(ai.grade_rank).tolist()
    assert ranks == sorted(ranks)
    flagged = set(tl.loc[tl["점검결과"] == "확인 필요", "이벤트ID"])
    for ids in kf["근거 이벤트ID"]:
        assert set(ids.split(", ")) <= flagged
    assert "현장영상_01.mp4 외 5개" in kf["파일명"].tolist()


# --- REQ-08 Test 모드와 검토 입력 필수 ----------------------------------------
def test_req08_review_required_rules():
    import obi_pipeline as p
    empty = dict(테스트여부=0, 인터뷰여부="미확인", 보안서약서="미확인", 검토자="", 코멘트="")
    assert p.review_missing(empty) == ["인터뷰 여부", "보안서약서 작성 여부", "검토자", "Comment 및 확인 결과"]
    assert p.review_missing(dict(empty, 테스트여부=1)) == []                     # Test 는 요구 안 함
    full = dict(empty, 인터뷰여부="예", 보안서약서="아니오", 검토자="김검토", 코멘트="이상 없음", 서약서수=1)
    assert p.review_missing(full) == []
    assert p.review_missing(dict(full, 코멘트="   ")) == ["Comment 및 확인 결과"]
    assert p.review_missing({"인터뷰여부": "예"}) != []                           # 옛 이력(테스트여부 없음) = 정식


# --- REQ-06 분석 이력 게시판 ---------------------------------------------------
def test_req06_board_filters(tmp_path, monkeypatch, sample):
    from datetime import date
    import obi_pipeline as p
    import obi_store as store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "b.db"))
    store.init_db()
    real = p.save(sample["result"])
    test = p.save(sample["result"], test=True)
    old = store.save_analysis(dict(sample["result"]["info"], 이름="옛이력"), [], sample["result"]["summary"],
                              sample["result"]["timeline"].iloc[0:0])     # 등급·건수 없는 옛 이력 흉내
    df = store.list_analyses()

    v = p.board(df)
    assert list(v.columns) == p.BOARD_COLUMNS
    assert v["이력#"].tolist() == [old, real]                      # 최신순, Test 제외
    assert v.set_index("이력#").loc[real, "입력 완료"] == "입력 필요"
    assert test in p.board(df, include_test=True)["이력#"].tolist()
    top = sample["cands"]["등급"].iloc[0]
    assert p.board(df, grades=[top])["이력#"].tolist() == [real]
    assert p.board(df, grades=[p.NO_GRADE])["이력#"].tolist() == [old]
    assert p.board(df, q="옛이")["이력#"].tolist() == [old]
    assert p.board(df, date_to=date(2000, 1, 1)).empty


def test_req10_person_sources():
    import obi_pipeline as p
    person = {"이름": "홍길동", "직급": "선임", "부서": ""}
    assert p.person_sources(person, {"이름": "홍길순", "직급": "책임", "부서": "보안팀"}) == \
        {"이름": "수정", "부서": "직접"}                                     # 직급은 출처 없음 (REQ-14)
    assert p.person_sources(person, {"이름": "홍길동", "부서": ""}) == {"이름": "로그", "부서": "직접"}


# --- REQ-02 채널·추이 대시보드 -------------------------------------------------
def test_req02_dashboard_matches_timeline(sample):
    import obi_criteria as crit
    import obi_pipeline as p
    tl, d = sample["tl"], sample["dashboard"]
    timed = tl[tl["시간"].notna()]
    assert d["daily"]["건수"].sum() == len(timed)
    assert d["need"]["확인필요"].sum() == int((timed["점검결과"] == "확인 필요").sum())
    by_ch = d["daily"].groupby("채널")["건수"].sum().to_dict()
    assert by_ch == timed["채널"].value_counts().to_dict()
    assert round(d["bytes"]["MB"].sum(), 0) == round(crit.transfer_bytes(timed).sum() / crit.MB, 0)
    assert d["offhours"]["업무시간외"].sum() == int((timed["업무시간외"] == "Y").sum())
    assert d["markers"].set_index("이름")["날짜"].to_dict() == \
        {"최근 기간 시작": "2026-08-03", "기준일": "2026-09-02", "퇴직일": "2026-09-30"}
    assert set(d["compare"]["기간"]) == {"평소", "최근"}
    day = d["dates"][0]
    assert len(p.events_on(tl, day)) == d["daily"][d["daily"]["날짜"] == day]["건수"].sum()


def test_req02_dashboard_empty():
    import pandas as pd
    import obi_pipeline as p
    d = p.dashboard(pd.DataFrame())
    assert d["daily"].empty and d["dates"] == []


def test_req02_charts_build(sample):
    """차트 사양이 만들어지고, 채널 색이 고정 순서를 따른다"""
    import obi_charts as c
    d = sample["dashboard"]
    for fn in (c.daily_activity, c.recent_vs_usual, c.off_hours_share, c.daily_bytes):
        assert fn(d).to_dict()
    spec = str(c.daily_activity(d).to_dict())
    assert all(col in spec for col in c.CHANNEL_COLORS) and c.NEED_COLOR in spec


# --- REQ-07 리포트 --------------------------------------------------------------
def test_req07_report_content(sample):
    import io
    import pandas as pd
    import obi_report as rep
    a = dict(sample["analysis"], AI의견="**1. 종합 위험 평가** ...", AI프로바이더="ollama", AI모델="m",
             AI생성일시="2026-11-19 12:00:00", AI요청자="u")
    sheets = rep.build_report(a, "검토자A", now="2026-11-19 13:00:00")
    assert list(sheets)[0] == "요약"
    assert list(sheets)[1:3] == ["주요 검토 파일", "개인메일 발송"]
    summary = sheets["요약"].set_index("항목")["내용"]
    assert summary["최고 등급"] == sample["cands"]["등급"].iloc[0]
    assert "종합 위험 평가" in summary["AI 의견"] and "검토자A" in summary["리포트 생성"]
    assert sheets["주요 검토 파일"]["파일명"].str.contains("설계도면_v2.dwg").any()
    # 숫자 점수 없음 (REQ-04)
    data = rep.to_xlsx(sheets)
    book = pd.read_excel(io.BytesIO(data), sheet_name=None)
    for name, df in book.items():
        assert "위험점수" not in df.columns, name
        assert not df.astype(str).apply(lambda c: c.str.contains("위험점수|위험 점수")).any().any(), name


def test_req07_report_without_ai(sample):
    import obi_report as rep
    s = rep.build_report(sample["analysis"], "u", now="x")["요약"]
    assert "AI 의견" not in s["항목"].tolist()


# --- REQ-11 사내 동반 수신 ------------------------------------------------------
def _mail(tl, subject):
    return tl[tl["대상"].astype(str) == f"제목: {subject}"].iloc[0]


def test_req11_vendor_with_internal_is_r2_reference(sample):
    import obi_criteria as crit
    tl, cands = sample["tl"], sample["cands"]
    r = _mail(tl, "도면 검토 요청")                       # 타사 + 사내, 자료 키워드 + 150MB
    assert r["점검결과"] == "참고"
    assert crit.codes_of(r["점검사유"]) == ["R2"] and "colleague@doosan.com" in r["점검사유"]
    assert not any(r["이벤트ID"] in ids.split(", ") for ids in cands["근거이벤트"])   # 후보에 안 올라감


def test_req11_existing_inc1003_still_r2(sample):
    import obi_criteria as crit
    assert crit.codes_of(_mail(sample["tl"], "미팅 자료")["점검사유"]) == ["R2"]


def test_req11_self_address_is_not_companion(sample):
    import obi_criteria as crit
    r = _mail(sample["tl"], "개인 보관")                   # 개인메일 + 본인 사내 주소만
    assert "B2" in crit.codes_of(r["점검사유"]) and "R2" not in crit.codes_of(r["점검사유"])


def test_req11_security_keyword_kept_but_context_low(sample):
    import obi_criteria as crit
    tl, cands = sample["tl"], sample["cands"]
    r = _mail(tl, "견적 회신")                             # 대외비 + 사내 동반 (hq.doosan.com 하위 도메인)
    assert r["점검결과"] == "확인 필요" and crit.codes_of(r["점검사유"]) == ["B1", "R2"]
    c = _cand_of(cands, r["이벤트ID"])
    assert c.판단["행위 맥락"] == "낮음" and "사내 동반" in c.근거["행위 맥락"]


def test_req11_companion_rules_unit():
    import obi_criteria as crit
    me = "kildong.hong@doosan.com"
    assert crit.internal_companions(["a@vendor.com", "b@doosan.com"], me) == ["b@doosan.com"]
    assert crit.internal_companions(["a@gmail.com", me], me) == []                      # 본인만 → 동반 아님
    assert crit.internal_companions(["b@doosan.com"], me) == []                         # 외부 없음 → 사내 메일
    assert crit.internal_companions(["a@vendor.com", "x@sub.doosan.com"], me) == ["x@sub.doosan.com"]
    assert crit.internal_companions(["a@vendor.com", "x@evildoosan.com"], me) == []     # 비슷한 도메인 아님


def test_req11_prompt_mentions_companion(sample):
    assert "사내 동반" in sample["prompt"] and "판단 기준" in sample["prompt"]


# --- REQ-12 보안서약서 필수 규칙 ------------------------------------------------
def test_req12_pledge_rules():
    import obi_pipeline as p
    full = dict(테스트여부=0, 인터뷰여부="예", 보안서약서="예", 검토자="김", 코멘트="확인", 서약서수=0)
    assert p.review_missing(full) == ["보안서약서 PDF"]                     # 인터뷰 '예' → PDF 필수
    assert p.review_missing(dict(full, 서약서수=1)) == []
    assert p.review_missing(dict(full, 인터뷰여부="아니오", 보안서약서="아니오")) == []   # 미진행자는 선택
    assert p.review_missing(dict(full, 인터뷰여부="아니오")) == ["보안서약서 PDF"]       # '예'는 파일이 있어야
    assert p.review_missing(dict(full, 테스트여부=1)) == []                            # Test 는 요구 안 함
    assert p.pledge_status(dict(full, 서약서수=1)) == "첨부됨"
    assert p.pledge_status(full) == "필요"
    assert p.pledge_status(dict(full, 인터뷰여부="아니오")) == "해당 없음"
    assert p.pledge_status(dict(full, 테스트여부=1)) == "해당 없음"


def test_req12_board_column(tmp_path, monkeypatch, sample):
    import obi_pipeline as p
    import obi_store as store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "b.db"))
    store.init_db()
    a = p.save(sample["result"])
    b = p.save(sample["result"])
    store.update_review(a, "예", "미확인", "c", "검토 중", "r")
    store.update_review(b, "예", "미확인", "c", "검토 중", "r")
    store.save_pledge(b, "s.pdf", b"%PDF-1.4 x", "r")
    v = p.board(store.list_analyses()).set_index("이력#")
    assert v.loc[a, "서약서"] == "필요" and v.loc[a, "입력 완료"] == "입력 필요"
    assert v.loc[b, "서약서"] == "첨부됨" and v.loc[b, "입력 완료"] == "완료"


# --- REQ-17 분석가에게 필요한 컬럼만 표시 ------------------------------------------
def test_req17_view_columns_hide_event_id(sample):
    import obi_pipeline as p
    assert "근거 이벤트ID" in sample["key_files"].columns                      # 데이터에는 그대로
    assert "이벤트ID" in sample["personal_mail"].columns
    assert p.KEY_FILE_VIEW == ["파일명", "등급", "근거", "채널", "횟수", "나간 곳", "용량", "최초 시각", "최종 시각"]
    assert set(p.KEY_FILE_VIEW) <= set(p.KEY_FILE_COLUMNS)
    assert set(p.PERSONAL_MAIL_VIEW) <= set(p.PERSONAL_MAIL_COLUMNS)
    assert not any("이벤트ID" in c for c in p.KEY_FILE_VIEW + p.PERSONAL_MAIL_VIEW)


def test_req17_report_sheets_without_event_id(sample):
    import obi_report as report
    sheets = report.build_report(sample["analysis"], "t", now="-")
    for name in ("주요 검토 파일", "개인메일 발송"):
        assert not any("이벤트ID" in c for c in sheets[name].columns), name
    assert "이벤트ID" in sheets["통합타임라인"].columns                          # 타임라인 시트는 유지
    assert len(sheets["주요 검토 파일"]) == len(sample["key_files"])


# --- REQ-15 '검토 후보'만 표시 ('확인 필요 행위' 지표 삭제) -------------------------
def test_req15_one_indicator_board_and_report(tmp_path, monkeypatch, sample):
    import obi_pipeline as p
    import obi_report as report
    import obi_store as store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "b.db"))
    store.init_db()
    aid = p.save(sample["result"])
    assert "확인 필요 건수" not in p.BOARD_COLUMNS
    assert p.board(store.list_analyses()).set_index("이력#").loc[aid, "검토 후보 건수"] == len(sample["cands"])
    items = report.build_report(store.get_analysis(aid), "t", now="-")["요약"]["항목"].tolist()
    assert "검토 후보" in items and "확인 필요 행위" not in items
    assert "확인 필요 행위" not in sample["lead"] and "검토 후보" in sample["lead"]


# --- REQ-14 직급 칸 정리 ------------------------------------------------------
def test_req14_rank_hidden_but_legacy_export_kept(tmp_path, monkeypatch, sample):
    import obi_pipeline as p
    import obi_report as report
    import obi_store as store
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "b.db"))
    store.init_db()
    aid = p.save(sample["result"])
    a = store.get_analysis(aid)
    assert a["직급"] == "선임"                                                  # DB 에는 저장
    assert "직급" not in p.BOARD_COLUMNS and "직급" not in p.board(store.list_analyses()).columns
    assert "직급" not in sample["prompt"] and "선임" not in sample["prompt"]
    summary = report.build_report(a, "t", now="-")["요약"]
    assert "선임" not in summary.set_index("항목").loc["대상자", "내용"]
    legacy = store.to_legacy_frame(store.list_analyses())                       # 기존 양식은 그대로
    assert list(legacy.columns)[:len(store.LEGACY_COLUMNS)] == store.LEGACY_COLUMNS
    assert legacy["직급"].tolist() == ["선임"]


# --- REQ-16 일자별 활동을 항목별로 걸러 보기 -----------------------------------------
def test_req16_channel_filter_matches_timeline(sample):
    import obi_pipeline as p
    tl, a = sample["tl"], sample["analysis"]
    full = p.dashboard(tl, a["채널요약"], a["기준일"], a["퇴직일자"])
    for ch in tl["채널"].dropna().unique():
        d = p.dashboard(tl, a["채널요약"], a["기준일"], a["퇴직일자"], channels=[ch])
        timed = tl[(tl["채널"] == ch) & tl["시간"].notna()]
        assert set(d["daily"]["채널"]) == {ch}
        assert d["daily"]["건수"].sum() == len(timed)
        assert set(d["bytes"]["채널"]) <= {ch}
        assert len(p.events_on(tl, channels=[ch])) == int((tl["채널"] == ch).sum())      # 표 건수 = 타임라인 건수
        assert d["compare"].equals(full["compare"]) and d["offhours"].equals(full["offhours"])   # 전체 기준 유지
    need = p.dashboard(tl, channels=None, need_only=True)
    assert set(need["daily"]["구분"]) == {"확인 필요"}
    assert need["daily"]["건수"].sum() == int(((tl["점검결과"] == "확인 필요") & tl["시간"].notna()).sum())
    assert len(p.events_on(tl, need_only=True)) == int((tl["점검결과"] == "확인 필요").sum())
    # 날짜 + 채널
    day = full["dates"][0]
    ch = full["daily"][full["daily"]["날짜"] == day]["채널"].iloc[0]
    want = full["daily"][(full["daily"]["날짜"] == day) & (full["daily"]["채널"] == ch)]["건수"].sum()
    assert len(p.events_on(tl, day, [ch])) == want
    assert p.dashboard(tl).keys() == full.keys() and p.events_on(tl).equals(tl)      # 필터 없음 = 기존과 같음


def test_req16_empty_selection():
    import pandas as pd
    import obi_pipeline as p
    tl = pd.DataFrame({"이벤트ID": ["E1"], "시간": pd.to_datetime(["2026-09-01 10:00"]), "채널": ["웹"],
                       "점검결과": [""], "업무시간외": [""], "용량_bytes": [None], "행위": ["접속"]})
    d = p.dashboard(tl, need_only=True)
    assert d["daily"].empty and d["bytes"].empty and d["dates"] == []
    assert p.events_on(tl, channels=["USB"]).empty and p.events_on(tl, "2026-09-02").empty


def test_req16_chart_selectable(sample):
    import obi_charts as c
    spec = c.daily_activity(sample["dashboard"], selectable=True).to_dict()
    assert c.PICK in str(spec) and "일자" in str(spec)
    assert "'name': 'pick'" not in str(c.daily_activity(sample["dashboard"]).to_dict())   # 기본은 선택 없음


# --- REQ-13 LLM 프롬프트에 메일·첨부·수신자 정보 ----------------------------------
def test_req13_prompt_has_recipient_and_attachment_on_one_line(sample):
    line = [ln for ln in sample["prompt"].splitlines() if "friend@gmail.com" in ln and ln.startswith("  E")]
    assert len(line) == 1
    assert "임직원 연락처.xlsx" in line[0] and "friend@gmail.com(개인메일)" in line[0]
    assert "근거가 된 파일명과 수신자를 함께 적으세요" in sample["prompt"]


def test_req13_event_line_caps_and_kinds():
    import pandas as pd
    import obi_ai as ai
    rcpts = [f"u{i}@gmail.com" for i in range(ai.PROMPT_MAX_RECIPIENTS + 2)] + ["a@doosan.com", "b@vendor.co.kr"]
    files = "\n".join(f"C:\\docs\\f{i}.xlsx" for i in range(ai.PROMPT_MAX_FILES + 3))
    r = pd.Series({"이벤트ID": "E1", "시간": "t", "채널": "이메일", "행위": "외부 메일 발송", "대상": "제목: x",
                   "수신자": ", ".join(rcpts), "파일목록": files, "용량_bytes": 2048, "점검사유": "B2 개인메일"})
    line = ai.event_line(r)
    assert "u0@gmail.com(개인메일)" in line and f"외 {len(rcpts) - ai.PROMPT_MAX_RECIPIENTS}명" in line
    assert "a@doosan.com" not in line                                              # 상한 밖
    assert f"첨부 {ai.PROMPT_MAX_FILES + 3}개: f0.xlsx" in line and "외 3개" in line and "docs" not in line
    assert "용량 2KB" in line
    import obi_criteria as crit
    assert [crit.recipient_kind(x) for x in ("a@hq.doosan.com", "x@naver.com", "b@vendor.co.kr", "")] == \
        ["사내", "개인메일", "타사", ""]
    usb = pd.Series({"이벤트ID": "E2", "시간": "t", "채널": "USB", "행위": "USB Write", "대상": "E:\\a.zip",
                     "파일목록": "E:\\a.zip", "점검사유": "B5"})
    assert "파일: a.zip" in ai.event_line(usb)
    web = pd.Series({"이벤트ID": "E3", "시간": "t", "채널": "웹", "행위": "웹 접속", "대상": "https://x", "점검사유": "B4"})
    assert "파일:" not in ai.event_line(web) and "수신:" not in ai.event_line(web)
