"""
OBI 분석 파이프라인 (화면 없이 실행 가능한 공통 경로)

app.py 와 하네스(tests/harness.py)가 모두 이 함수들을 거친다.
→ 화면에서 보는 결과와 하네스가 검증하는 결과가 같은 코드에서 나온다.

    analyze_file()  : 로그 엑셀 → 전처리 → 결과 엑셀을 다시 읽어 저장용 묶음 생성
    derive()        : (저장 후 불러온) 타임라인 → 검토 후보·한눈에 보기 요약
"""
import pandas as pd

import obi_ai as ai
import obi_criteria as crit
import obi_preprocess as prep


def analyze_file(src_path, out_path, info, base_date=None, account=None):
    """전처리 실행 후 store.save_analysis() 에 넘길 값을 돌려준다.

    info 는 복사본을 고쳐서 돌려준다(기준일·계정 채움). 원본 dict 는 건드리지 않는다.
    """
    checks, status, timeline = prep.run(src_path, out_path, info["퇴직일자"], base_date, account)
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


def derive(tl):
    """검토 후보(점수)와 한눈에 보기 요약. LLM 연동 시 obi_ai 쪽만 교체하면 된다."""
    cands = ai.mock_analyze(tl)
    lead, points = ai.mock_insights(tl, cands)
    return cands, lead, points
