"""형식 계약: 값이 바뀌어도 지켜져야 하는 규칙.

골든 비교는 "무엇이 바뀌었나"를, 이 파일은 "바뀌어도 되는 범위 안인가"를 본다.
LLM 으로 mock_analyze / mock_insights 를 교체해도 이 테스트는 그대로 통과해야 한다.
"""

import pandas as pd

import harness
import obi_ai as ai
import obi_criteria as crit

CAND_COLUMNS = ["후보ID", "등급", "위험점수", "유형", "기간", "채널", "건수", "설명", "근거이벤트", "점검항목", "판단", "근거", "구분"]
EXCEL_SHEETS = ["검증결과", "기준점검", "통합타임라인", "채널요약", "일별건수", "시트상태", "분석조건"]
TIMELINE_COLUMNS = ["이벤트ID", "시간", "점검결과", "점검사유", "기간", "채널", "행위", "대상", "상세", "파일목록", "파일유형",
                    "용량_bytes", "페이지수", "첨부수", "차단여부", "업무시간외", "최초등장", "개인메일", "승인서비스",
                    "동일파일", "중복기록", "당일건수", "수신자", "탐지규칙", "계정", "원본시트", "원본행"]
CHANNELS = {"웹", "USB", "출력", "이메일", "웹 업로드", "USB(DLP)"}


# --- 전처리 결과 엑셀 ------------------------------------------------------
def test_excel_sheets_and_timeline_columns(sample):
    assert list(sample["excel"]) == EXCEL_SHEETS
    assert sample["excel"]["통합타임라인"]["columns"] == TIMELINE_COLUMNS


def test_sheet_row_reconciliation(sample):
    """원본 행 수 = 이벤트에 반영된 행 수 (DLP 병합 후에도 행이 사라지지 않음)"""
    status = pd.DataFrame(sample["result"]["status"])
    assert (status["대사결과"] == "일치").all(), status.to_string()


# --- 타임라인 ---------------------------------------------------------------
def test_timeline_values(sample):
    tl = sample["tl"]
    assert list(tl.columns) == TIMELINE_COLUMNS
    assert tl["이벤트ID"].tolist() == [f"E{i:04d}" for i in range(1, len(tl) + 1)]
    assert set(tl["채널"]) <= CHANNELS
    assert set(tl["점검결과"].fillna("")) <= {"확인 필요", "참고", ""}
    assert set(tl["기간"]) <= {"최근", "평소"}
    times = tl["시간"].dropna()
    assert times.is_monotonic_increasing


def test_check_codes_consistent(sample):
    """점검사유의 코드는 모두 정의된 코드이고, B 코드가 있을 때만 '확인 필요'"""
    for _, r in sample["tl"].iterrows():
        codes = crit.codes_of(r["점검사유"])
        assert all(c in crit.ALL_LABELS for c in codes), r["점검사유"]
        expected = "확인 필요" if any(c.startswith("B") for c in codes) else ("참고" if codes else "")
        assert (r["점검결과"] or "") == expected, (r["이벤트ID"], r["점검사유"])


def test_criteria_table(sample):
    c = sample["crit"]
    assert c["코드"].tolist() == [cid for cid, _, _ in crit.CODE_CHECKS + crit.REFERENCE_CHECKS + crit.AI_CHECKS]
    tl_ids = set(sample["tl"]["이벤트ID"])
    for ids in c["이벤트ID"]:
        assert {x.strip() for x in str(ids).split(",") if x.strip()} <= tl_ids


# --- 검토 후보 (LLM 교체 지점의 출력 계약) ---------------------------------
def test_candidate_contract(sample):
    cands, tl = sample["cands"], sample["tl"]
    assert list(cands.columns) == CAND_COLUMNS
    order = list(zip(cands["등급"].map(ai.grade_rank), -cands["위험점수"]))
    assert order == sorted(order)                              # 등급 → 내부 점수 순
    for r in cands.itertuples():
        assert set(r.판단) == set(ai.WEIGHTS) and set(r.근거) == set(ai.WEIGHTS)
        assert set(r.판단.values()) <= set(ai.LEVELS)
        assert r.위험점수 == ai.score_candidate(r.판단)
        assert 0 <= r.위험점수 <= sum(w for _, w, _ in ai.WEIGHTS.values())
        assert r.건수 == len(r.근거이벤트.split(", "))
        assert r.구분 in ("우선 검토", "추가 확인")
        codes = [c.strip() for c in r.점검항목.split(",") if c.strip()]
        assert r.등급 == ai.grade_of(r.위험점수, codes)


def test_grade_rules():
    assert [ai.grade_of(s) for s in (70, 69, 40, 39)] == ["Critical", "Medium", "Medium", "Low"]
    assert ai.grade_of(10, ["B2"]) == "Medium"                  # 개인메일 첨부 → 최소 Medium
    assert ai.grade_of(10, ["B2", "B10"]) == "Critical"         # + 대용량 → Critical
    assert ai.grade_of(90, ["B2"]) == "Critical"                # 올리기만 하고 내리지 않음
    assert ai.score_candidate({"자료 민감도": "중간", "흐름 연결": "중간"}) == 20   # 12.5+7.5
    assert ai.score_candidate({"자료 민감도": "중간"}) == 13                        # 12.5 → 13 (floor(x+.5))


def test_no_numeric_score_shown(sample):
    """REQ-04: 사용자에게 보이는 문장·프롬프트에 숫자 점수가 없다"""
    for text in [sample["lead"], *sample["points"], sample["prompt"]]:
        assert "점수" not in text, text


def test_every_flagged_event_in_exactly_one_candidate(sample):
    flagged = set(sample["tl"].loc[sample["tl"]["점검결과"] == "확인 필요", "이벤트ID"])
    used = [e for ids in sample["cands"]["근거이벤트"] for e in ids.split(", ")]
    assert sorted(used) == sorted(flagged)


def test_insights_shape(sample):
    assert isinstance(sample["lead"], str) and sample["lead"]
    assert isinstance(sample["points"], list) and len(sample["points"]) <= 4


# --- LLM 프롬프트·보고서 형식 ---------------------------------------------
def test_prompt_requests_report_sections(sample):
    for s in harness.REPORT_SECTIONS:
        assert s in sample["prompt"]


def test_prompt_lists_mail_recipients_and_attachments(sample):
    """REQ-13: 프롬프트에 실린 '확인 필요' 이메일마다 그 줄에 수신자와 첨부 파일명이 있다 (상한까지)"""
    tl, prompt = sample["tl"], sample["prompt"]
    need = tl[tl["점검결과"] == "확인 필요"].head(ai.PROMPT_MAX_EVENTS)
    lines = {ln.split(" | ")[0].strip(): ln for ln in prompt.splitlines() if ln.startswith("  E")}
    mails = need[need["채널"] == "이메일"]
    assert len(mails)
    for _, r in mails.iterrows():
        line = lines[r["이벤트ID"]]
        for x in ai.recipients_of(r)[:ai.PROMPT_MAX_RECIPIENTS]:
            assert x in line, (r["이벤트ID"], x)
        for f in ai.files_of(r)[:ai.PROMPT_MAX_FILES]:
            assert f in line, (r["이벤트ID"], f)
    for _, r in need[need["채널"].isin(ai.FILE_CHANNELS)].iterrows():
        for f in ai.files_of(r)[:ai.PROMPT_MAX_FILES]:
            assert f in lines[r["이벤트ID"]], (r["이벤트ID"], f)


GOOD_REPORT = ("**1. 종합 위험 평가**\n최근 30일에 반출이 몰림.\n"
               "**2. 핵심 우려 사항**\n- [E0029] 임직원 연락처.xlsx → friend@gmail.com\n"
               "**3. 기준 외 추가 발견**\n- [원본 USB로그 5행] 새벽 USB 사용\n"
               "**4. 추가 확인 필요 사항**\n- 발송 목적\n"
               "**5. 최종 위험 수준**\n**Critical**\n개인메일로 개인정보 발송")


def test_report_format_checker(sample):
    """LLM 보고서 틀: 내용은 자유, 제목·순서·최종 등급·근거 이벤트ID 만 고정"""
    tl = sample["tl"]
    assert harness.check_report_format(GOOD_REPORT, tl) == []
    assert ai.report_level(GOOD_REPORT) == "Critical"
    assert "섹션 누락: 3. 기준 외 추가 발견" in harness.check_report_format(GOOD_REPORT.replace("3. 기준 외 추가 발견", ""))
    swapped = GOOD_REPORT.replace("1. 종합 위험 평가", "TMP").replace("2. 핵심 우려 사항", "1. 종합 위험 평가") \
        .replace("TMP", "2. 핵심 우려 사항")
    assert "섹션 순서가 다름" in harness.check_report_format(swapped)
    assert any("Critical/Medium/Low" in p for p in harness.check_report_format(GOOD_REPORT.replace("**Critical**", "높음")))
    assert any("E9999" in p for p in harness.check_report_format(GOOD_REPORT + " E9999", tl))   # 지어낸 이벤트ID
    no_id = GOOD_REPORT.replace("[E0029] ", "")
    assert "핵심 우려 사항에 근거 이벤트ID 가 없음" in harness.check_report_format(no_id, tl)
    assert harness.check_report_format(no_id) == []                                        # tl 없으면 틀만


def test_prompt_carries_full_timeline_and_raw_logs(sample):
    """LLM 이 직접 분석하도록 통합타임라인 전체와 원본 로그 시트를 함께 넘긴다 (글자 상한 안일 때 전부)"""
    prompt, tl, raw = sample["prompt"], sample["tl"], sample["analysis"]["원본로그"]
    assert raw and len(prompt) <= ai.PROMPT_MAX_CHARS
    for eid in tl["이벤트ID"]:
        assert f"\n{eid}," in prompt, eid
    for name, df in raw.items():
        assert f"[원본 {name}] 전체 {len(df)}행\n" in prompt, name
    for s in ai.REPORT_SECTIONS:
        assert f"**{s}**" in prompt
    assert "생략" not in prompt


def test_prompt_budget_keeps_flagged_first(sample):
    """글자 상한을 넘으면 확인 필요 → 참고 → 그 원본 행 → 나머지 순. 같은 입력 → 같은 프롬프트"""
    a, tl = sample["analysis"], sample["tl"]
    small = ai.build_llm_prompt(a, sample["crit"], sample["cands"], tl, a["원본로그"], max_chars=len(sample["prompt"]) - 3000)
    assert small == ai.build_llm_prompt(a, sample["crit"], sample["cands"], tl, a["원본로그"],
                                        max_chars=len(sample["prompt"]) - 3000)
    assert "글자 상한으로 일부 생략" in small
    part2 = small.split("═══ 2부.")[1].split("═══ 3부.")[0]
    for eid in tl[tl["점검결과"] == "확인 필요"]["이벤트ID"]:
        assert f"\n{eid}," in part2, eid
    tiny = ai.build_llm_prompt(a, sample["crit"], sample["cands"], tl, a["원본로그"], max_chars=1)
    assert "5. 최종 위험 수준" in tiny                        # 틀은 항상 남는다
    old = ai.build_llm_prompt(a, sample["crit"], sample["cands"], tl, None)
    assert "원본 로그가 저장되지 않은 이력" in old             # 옛 이력


# --- 빈 입력 ---------------------------------------------------------------
def test_derive_on_empty_timeline():
    import obi_pipeline
    cands, lead, points = obi_pipeline.derive(pd.DataFrame())
    assert cands.empty and points == [] and lead
