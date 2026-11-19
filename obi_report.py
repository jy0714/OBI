"""
OBI 리포트 (REQ-07) — '리포트 생성'을 눌렀을 때만 만든다.

    build_report()  : 내용 (시트 이름 → DataFrame). 형식과 무관.
    to_xlsx()       : 엑셀 형식. [결정 필요 #4] 임시 기본값 = 엑셀(맨 앞 '요약' 시트 + 기존 시트)
                      Word·PDF 를 추가할 때는 build_report() 결과를 받는 to_docx()/to_pdf() 만 만들면 된다.

숫자 점수(위험점수)는 넣지 않는다 (REQ-04).
"""
import io
from datetime import datetime

import pandas as pd

import obi_pipeline as pipeline

FORMATS = {"xlsx": "엑셀"}
CAND_EXPORT_COLUMNS = ["후보ID", "등급", "구분", "유형", "기간", "채널", "건수", "설명", "점검항목", "판단", "근거", "근거이벤트"]


def _flat(d):
    return " / ".join(f"{k}: {v}" for k, v in d.items()) if isinstance(d, dict) else ("" if d is None else str(d))


def build_report(a, requester="", now=None):
    """이력 dict(store.get_analysis) → {시트 이름: DataFrame}. 첫 시트는 '요약'."""
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tl, crit = a["타임라인"], a["기준점검"]
    cands, lead, points = pipeline.derive(tl)
    files = pipeline.key_files(tl, cands)
    pmail = pipeline.personal_mail(tl)
    need = int((tl["점검결과"].fillna("") == "확인 필요").sum()) if "점검결과" in tl else 0

    rows = [
        ("대상자", f"{a['이름']} · {a['회사']} {a['부서']} {a['직급']}".strip()),
        ("계정", a.get("계정", "")),
        ("퇴직(예정)일", a["퇴직일자"]), ("로그 기준일", a["기준일"]),
        ("이력", f"#{a['id']} · 분석 {a['분석일시']} · {'Test' if a.get('테스트여부') else '정식'}"),
        ("최고 등급", cands["등급"].iloc[0] if len(cands) else "-"),
        ("검토 후보", f"{len(cands)}건"), ("확인 필요 행위", f"{need}건"),
        ("개인메일 발송", f"{len(pmail)}건 (첨부 {int((pmail['첨부 수'] > 0).sum())}건)"),
        ("주요 검토 파일", f"{len(files)}개"),
        ("로그 검증", a["검증상태"]),
        ("한눈에 보기", "\n".join([lead] + [f"- {p}" for p in points]).replace("**", "")),
        ("인터뷰 여부", a["인터뷰여부"]), ("보안서약서 작성 여부", a["보안서약서"]),
        ("검토 상태", a["검토상태"]), ("검토자", a["검토자"]), ("검토일시", a.get("검토일시", "")),
        ("Comment 및 확인 결과", a["코멘트"]),
    ]
    if a.get("AI의견"):
        rows += [("AI 의견 생성", f"{a['AI프로바이더']} · {a['AI모델']} · {a['AI생성일시']} · 요청 {a['AI요청자']}"),
                 ("AI 의견", a["AI의견"])]
    rows += [("리포트 생성", f"{now} · {requester}")]

    cand_out = cands.reindex(columns=CAND_EXPORT_COLUMNS).copy() if len(cands) else \
        pd.DataFrame(columns=CAND_EXPORT_COLUMNS)
    for c in ("판단", "근거"):
        cand_out[c] = cand_out[c].map(_flat)

    return {
        "요약": pd.DataFrame(rows, columns=["항목", "내용"]),
        "주요 검토 파일": files,
        "개인메일 발송": pmail,
        "검토 후보": cand_out,
        "기준 점검": crit,
        "검증결과": pd.DataFrame(a["검증결과"], columns=["확인 항목", "결과", "내용"]),
        "통합타임라인": tl,
        "채널요약": a["채널요약"],
    }


def to_xlsx(sheets):
    """시트 묶음 → 엑셀 bytes (머리글 굵게, 긴 글은 줄바꿈)"""
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
    wb = load_workbook(io.BytesIO(buf.getvalue()))
    for ws in wb:
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 16
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E3E8EF")
        ws.freeze_panes = "A2"
    summary = wb["요약"]
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 110
    for row in summary.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def file_name(a, fmt="xlsx"):
    return f"OBI_리포트_{a['이름']}_{a['퇴직일자']}_#{a['id']}.{fmt}"
