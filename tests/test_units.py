"""전처리·점검 기준의 작은 함수 단위 테스트"""
import pandas as pd
import pytest

import obi_criteria as crit
import obi_preprocess as prep


@pytest.mark.parametrize("raw, expected", [
    ("2026. 7. 30. 오전 2:34:09", "2026-07-30 02:34:09"),
    ("2026. 7. 30. 오후 12:05:00", "2026-07-30 12:05:00"),
    ("2026. 7. 30. 오전 12:05:00", "2026-07-30 00:05:00"),
    ("Begin: 2026-07-29 10:21:50", "2026-07-29 10:21:50"),
    ("End Time : 2026-07-29 10:21:50", "2026-07-29 10:21:50"),
    (pd.Timestamp("2026-01-02 03:04:05", tz="Asia/Seoul").to_pydatetime(), "2026-01-02 03:04:05"),
])
def test_parse_time(raw, expected):
    assert prep.parse_time(raw) == pd.Timestamp(expected)


@pytest.mark.parametrize("raw", [None, float("nan"), "", "알 수 없음"])
def test_parse_time_invalid(raw):
    assert pd.isna(prep.parse_time(raw))


def test_rows_label():
    assert prep.rows_label([3, 4, 5, 9, 10]) == "3-5, 9-10 (5행)"
    assert prep.rows_label([7]) == "7"
    assert prep.rows_label([2, 4, 4]) == "2, 4 (2행)"


@pytest.mark.parametrize("raw, expected", [
    ("홍길동(Kildong.Hong) 선임", "kildong.hong"),
    ("  KILDONG.HONG ", "kildong.hong"),
    (None, ""),
])
def test_norm_account(raw, expected):
    assert prep.norm_account(raw) == expected


def test_file_key_links_variants():
    a = prep.file_key("C:\\work\\Microsoft Word - 사업계획_v2.docx")
    assert a == prep.file_key("사업계획 (1).pdf") == "사업계획"
    assert prep.file_key("a.pdf") == ""          # 너무 짧으면 연결하지 않음


def test_domain_matching():
    assert prep.domain_of("x@Gmail.com") == "gmail.com"
    assert prep.domain_of("https://mail.naver.com/write") == "mail.naver.com"
    assert prep.matches_domain("mail.naver.com", ["naver.com"])
    assert not prep.matches_domain("evilnaver.com", ["naver.com"])


def test_off_hours():
    assert prep.is_off_hours(pd.Timestamp("2026-08-22 10:00"))       # 토요일
    assert prep.is_off_hours(pd.Timestamp("2026-08-24 07:59"))
    assert not prep.is_off_hours(pd.Timestamp("2026-08-24 08:00"))
    assert prep.is_off_hours(pd.Timestamp("2026-08-24 19:00"))
    assert prep.is_off_hours(pd.Timestamp("2026-08-15 10:00"))       # 공휴일


@pytest.mark.parametrize("name, ambiguous", [
    ("scan001.pdf", True), ("Document1.docx", True), ("새 문서.hwp", True), ("1234.pdf", True),
    ("사업계획서.pdf", False), ("Q3_review_final.xlsx", False),
])
def test_ambiguous_names(name, ambiguous):
    assert crit._is_ambiguous(name) is ambiguous


def test_file_list_roundtrip():
    s = prep.join_files(["a, b.xlsx", None, float("nan"), " c.pdf ", "a, b.xlsx", ""])
    assert s == "a, b.xlsx\nc.pdf"                 # 쉼표가 든 파일명도 보존
    assert prep.split_files(s) == ["a, b.xlsx", "c.pdf"]
    assert prep.split_files(None) == [] and prep.split_files(float("nan")) == []
    assert prep.split_files(["x", " "]) == ["x"]


def test_codes_of_two_digit_and_filename_safe():
    s = "B1 보안등급 키워드(대외비) | B10 대용량 전송(150.0MB) | R6 사내·승인 서비스(B2B_계약.pdf)"
    assert crit.codes_of(s) == ["B1", "B10", "R6"]
    assert crit.codes_of("") == [] and crit.codes_of(None) == []


class _FakeBook:
    """LogWorkbook 대신 쓰는 시트 묶음 (header=None 원본 형태)"""
    def __init__(self, sheets):
        self.sheets = {k: pd.DataFrame(v) for k, v in sheets.items()}
        self.sheet_names = list(self.sheets)


def test_extract_person_from_account_and_columns():
    book = _FakeBook({
        "Web Log": [["시간", "URL", "Account", "부서명"],
                    ["2026-08-01 10:00:00", "https://a.com", "홍길동(Kildong.Hong) 선임", "보안기술팀"],
                    ["2026-08-01 11:00:00", "https://b.com", "kildong.hong", "보안기술팀"]],
    })
    p = prep.extract_person(book)
    assert (p["이름"], p["직급"], p["부서"], p["계정"]) == ("홍길동", "선임", "보안기술팀", "kildong.hong")
    assert p["갈림"] == []
    assert prep.person_check(p)[1] == "로그에서 확인"


def test_extract_person_split_values_need_review():
    book = _FakeBook({
        "Web Log": [["시간", "URL", "Account"],
                    ["2026-08-01 10:00:00", "https://a.com", "홍길동(kildong.hong) 선임"],
                    ["2026-08-01 10:00:00", "https://a.com", "홍길동(kildong.hong) 선임"],
                    ["2026-08-01 11:00:00", "https://b.com", "홍길동(kildong.hong) 책임"]],
    })
    p = prep.extract_person(book)
    assert p["직급"] == "선임" and p["갈림"] == ["직급"]
    assert "확인 필요" in prep.person_check(p)[1]
    assert prep.extract_person(_FakeBook({}))["이름"] == ""
