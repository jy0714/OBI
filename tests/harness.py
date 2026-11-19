"""
OBI 결과 하네스 — "같은 입력 → 같은 형식·같은 결과" 를 확인한다.

앱과 똑같은 경로(obi_pipeline → SQLite 저장/불러오기 → 후보·요약·프롬프트)를 화면 없이 실행하고,
결과를 사람이 읽을 수 있는 JSON/텍스트 스냅숏으로 만든다.

사용법 (프로젝트 루트에서):
    python tests/harness.py check                 # 합성 샘플 → tests/golden/ 과 비교 (pytest 와 같은 판정)
    python tests/harness.py update                # 의도한 변경 후 골든 갱신 → git diff 로 변경 내용 검토
    python tests/harness.py snapshot 로그.xlsx --retire-date 2026-09-30 [--base-date ..] --name 홍길동
                                                  # 실제 로그로 기준선 저장 (tests/local/, 커밋 안 됨)
    python tests/harness.py compare 로그.xlsx --retire-date 2026-09-30 [--base-date ..] --name 홍길동
                                                  # 코드 수정 후 같은 실제 로그가 기준선과 같은지 비교

스냅숏 구성 (artifact 이름 → 파일):
    schema.json      출력물별 컬럼 목록 — "형식"이 바뀌었는지 가장 먼저 보는 곳
    excel.json       전처리 결과 엑셀 7개 시트 (내보내기 형식)
    app.json         앱 화면 기준 결과 (저장·불러오기 후 타임라인, 검토 후보, 한눈에 보기)
    prompt.txt       LLM 에 전달되는 프롬프트 전문
"""
import argparse
import difflib
import json
import math
import os
import sys
import tempfile
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "fixtures")]

import pandas as pd  # noqa: E402

import obi_ai as ai  # noqa: E402
import obi_pipeline as pipeline  # noqa: E402
import obi_store as store  # noqa: E402

GOLDEN_DIR = os.path.join(ROOT, "tests", "golden", "sample")
LOCAL_DIR = os.path.join(ROOT, "tests", "local")      # 실제 로그 기준선 (gitignore)
OUT_DIR = os.path.join(ROOT, "tests", "_out")         # 마지막 실행 결과 (gitignore)
ARTIFACTS = ["schema.json", "excel.json", "app.json", "prompt.txt"]

SAMPLE_INFO = dict(회사="DBI", 이름="테스트", 직급="선임", 부서="보안기술팀", 원본파일명="sample_ddi.xlsx", 분석자="harness")

# LLM 보고서가 지켜야 할 형식 (build_llm_prompt 의 '분석 의견 작성 요청' 과 일치)
REPORT_SECTIONS = ["1. 종합 위험 평가", "2. 핵심 우려 사항", "3. 추가 확인 필요 사항", "4. 최종 위험 수준"]
REPORT_LEVELS = ("Critical", "Medium", "Low")


# ---------------------------------------------------------------------------
# 정규화: DataFrame·Timestamp·NaN·numpy 값 → JSON 으로 고정 표현
# ---------------------------------------------------------------------------
def _plain(v):
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return None if pd.isna(v) else v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):   # numpy 스칼라
        v = v.item()
    if isinstance(v, float):
        if math.isnan(v):
            return None
        return int(v) if v.is_integer() else round(v, 6)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def frame(df):
    """DataFrame → {'columns': [...], 'rows': [[...], ...]} (행·열 순서 그대로)"""
    if df is None:
        return None
    return {"columns": [str(c) for c in df.columns],
            "rows": [[_plain(v) for v in row] for row in df.itertuples(index=False, name=None)]}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def execute(xlsx, retire_date, base_date=None, account=None, info=None):
    """앱과 같은 경로로 실행하고 원본 객체(DataFrame 등)를 돌려준다. 계약 테스트용."""
    info = dict(info or SAMPLE_INFO, 퇴직일자=retire_date)
    old_db = store.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        store.DB_PATH = os.path.join(tmp, "harness.db")    # 실제 이력 DB 는 건드리지 않는다
        try:
            out = os.path.join(tmp, "result.xlsx")
            r = pipeline.analyze_file(xlsx, out, info, base_date, account)
            excel = {name: frame(df) for name, df in pd.read_excel(out, sheet_name=None).items()}

            store.init_db()
            aid = store.save_analysis(r["info"], r["checks"], r["summary"], r["timeline"], r["criteria"])
            a = store.get_analysis(aid)
        finally:
            store.DB_PATH = old_db

    tl, crit = a["타임라인"], a["기준점검"]
    cands, lead, points = pipeline.derive(tl)
    prompt = ai.build_llm_prompt(a, crit, cands, tl)
    return dict(result=r, excel=excel, analysis=a, tl=tl, crit=crit, cands=cands,
                lead=lead, points=points, prompt=prompt, personal_mail=pipeline.personal_mail(tl))


def render(x):
    """execute() 결과 → artifact 이름 → 문자열(스냅숏 파일 내용)"""
    a, tl, crit, cands, excel = x["analysis"], x["tl"], x["crit"], x["cands"], x["excel"]
    lead, points, prompt = x["lead"], x["points"], x["prompt"]
    stored_info = {k: a[k] for k in ("회사", "이름", "직급", "부서", "퇴직일자", "기준일", "계정",
                                     "원본파일명", "이벤트수", "검증상태")}
    app = {
        "info": stored_info,
        "검증결과": _plain(a["검증결과"]),
        "타임라인": frame(tl),
        "검토후보": frame(cands),
        "한눈에보기": {"lead": lead, "points": points},
        "개인메일발송": frame(x["personal_mail"]),
    }
    schema = {
        **{f"excel:{n}": v["columns"] for n, v in excel.items()},
        "app:타임라인": [str(c) for c in tl.columns],
        "app:기준점검": [str(c) for c in crit.columns],
        "app:채널요약": [str(c) for c in a["채널요약"].columns],
        "app:검토후보": [str(c) for c in cands.columns],
        "app:개인메일발송": [str(c) for c in x["personal_mail"].columns],
        "app:검토후보.판단_keys": sorted({k for d in cands.get("판단", []) for k in d}) if len(cands) else [],
    }
    return {"schema.json": dumps(schema), "excel.json": dumps(excel),
            "app.json": dumps(app), "prompt.txt": prompt}


def run_case(xlsx, retire_date, base_date=None, account=None, info=None):
    return render(execute(xlsx, retire_date, base_date, account, info))


def execute_sample():
    import sample_ddi
    with tempfile.TemporaryDirectory() as tmp:
        src = sample_ddi.build(os.path.join(tmp, "sample_ddi.xlsx"))
        return execute(src, sample_ddi.RETIRE_DATE, sample_ddi.BASE_DATE, sample_ddi.ACCOUNT)


def run_sample():
    return render(execute_sample())


# ---------------------------------------------------------------------------
# 비교·저장
# ---------------------------------------------------------------------------
def save(results, folder):
    os.makedirs(folder, exist_ok=True)
    for name, text in results.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def load(folder):
    out = {}
    for name in ARTIFACTS:
        p = os.path.join(folder, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out[name] = f.read()
    return out


def diff(expected, actual, max_lines=60):
    """artifact 별 unified diff. 같으면 빈 dict."""
    report = {}
    for name in ARTIFACTS:
        e, a = expected.get(name), actual.get(name)
        if e == a:
            continue
        if e is None:
            report[name] = "(기준선에 없음 — update 필요)"
            continue
        lines = list(difflib.unified_diff(e.splitlines(), (a or "").splitlines(),
                                          f"기준선/{name}", f"현재/{name}", lineterm="", n=2))
        more = f"\n... ({len(lines) - max_lines}줄 더, 전체는 tests/_out/ 참고)" if len(lines) > max_lines else ""
        report[name] = "\n".join(lines[:max_lines]) + more
    return report


def check_report_format(text):
    """LLM 보고서 형식 점검. 문제 목록을 돌려준다 (빈 목록 = 통과)."""
    problems = [f"섹션 누락: {s}" for s in REPORT_SECTIONS if s not in (text or "")]
    tail = (text or "").split(REPORT_SECTIONS[-1], 1)[-1]
    if REPORT_SECTIONS[-1] in (text or "") and not any(lv in tail for lv in REPORT_LEVELS):
        problems.append("최종 위험 수준에 Critical/Medium/Low 중 하나가 없음")
    return problems


def _print_diff(report):
    for name, d in report.items():
        print(f"\n===== {name} =====\n{d}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="OBI 결과 하네스")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="합성 샘플 결과를 골든과 비교")
    sub.add_parser("update", help="합성 샘플 골든 갱신")
    for c in ("snapshot", "compare"):
        p = sub.add_parser(c, help="실제 로그 기준선 " + ("저장" if c == "snapshot" else "비교"))
        p.add_argument("input")
        p.add_argument("--retire-date", required=True)
        p.add_argument("--base-date")
        p.add_argument("--account")
        p.add_argument("--name", required=True, help="기준선 이름 (tests/local/<name>/)")
    a = ap.parse_args(argv)

    if a.cmd in ("check", "update"):
        results, folder = run_sample(), GOLDEN_DIR
    else:
        x = execute(a.input, a.retire_date, a.base_date, a.account)
        results = render(x)
        folder = os.path.join(LOCAL_DIR, a.name)
        crit = sys.modules["obi_criteria"]
        print(f"\n[채널별 전송 용량 분포] 현재 기준: 단일 {crit.SIZE_SINGLE_MIN_MB}MB / 하루 {crit.SIZE_DAY_MIN_MB}MB")
        print(pipeline.size_stats(x["tl"]).to_string(index=False) + "\n")
    save(results, OUT_DIR)

    if a.cmd in ("update", "snapshot"):
        save(results, folder)
        print(f"기준선 저장: {folder}")
        return 0
    report = diff(load(folder), results)
    if not report:
        print(f"일치: {folder} 와 결과가 같습니다 ({', '.join(ARTIFACTS)})")
        return 0
    _print_diff(report)
    print(f"\n불일치 {len(report)}개. 의도한 변경이면 "
          + ("`python tests/harness.py update`" if a.cmd == "check" else "snapshot 다시 실행") + " 으로 기준선 갱신")
    return 1


if __name__ == "__main__":
    sys.exit(main())
