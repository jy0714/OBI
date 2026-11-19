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
