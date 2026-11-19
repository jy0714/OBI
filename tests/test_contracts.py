"""형식 계약: 값이 바뀌어도 지켜져야 하는 규칙.

골든 비교는 "무엇이 바뀌었나"를, 이 파일은 "바뀌어도 되는 범위 안인가"를 본다.
LLM 으로 mock_analyze / mock_insights 를 교체해도 이 테스트는 그대로 통과해야 한다.
"""

import pandas as pd

import harness
import obi_ai as ai
import obi_criteria as crit

CAND_COLUMNS = ["후보ID", "위험점수", "유형", "기간", "채널", "건수", "설명", "근거이벤트", "점검항목", "판단", "근거", "구분"]
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
    assert cands["위험점수"].is_monotonic_decreasing
    for r in cands.itertuples():
        assert set(r.판단) == set(ai.WEIGHTS) and set(r.근거) == set(ai.WEIGHTS)
        assert set(r.판단.values()) <= set(ai.LEVELS)
        assert r.위험점수 == ai.score_candidate(r.판단)
        assert 0 <= r.위험점수 <= sum(w for _, w, _ in ai.WEIGHTS.values())
        assert r.건수 == len(r.근거이벤트.split(", "))
        assert r.구분 in ("우선 검토", "추가 확인")


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


def test_report_format_checker():
    ok = "**1. 종합 위험 평가**\n..\n**2. 핵심 우려 사항**\n..\n**3. 추가 확인 필요 사항**\n- a\n**4. 최종 위험 수준**\n높음: 근거"
    assert harness.check_report_format(ok) == []
    assert "섹션 누락: 2. 핵심 우려 사항" in harness.check_report_format(ok.replace("2. 핵심 우려 사항", ""))
    assert harness.check_report_format(ok.replace("높음", "모름"))


# --- 빈 입력 ---------------------------------------------------------------
def test_derive_on_empty_timeline():
    import obi_pipeline
    cands, lead, points = obi_pipeline.derive(pd.DataFrame())
    assert cands.empty and points == [] and lead
