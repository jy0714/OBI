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
    full = dict(empty, 인터뷰여부="예", 보안서약서="아니오", 검토자="김검토", 코멘트="이상 없음")
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
    assert p.person_sources(person, {"이름": "홍길동", "직급": "책임", "부서": "보안팀"}) == \
        {"이름": "로그", "직급": "수정", "부서": "직접"}


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
