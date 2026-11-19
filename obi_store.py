"""
OBI 이력 저장소 (SQLite)

- 분석을 실행하면 자동으로 1건 저장되고, 검토 내용은 화면에서 수정·저장한다.
- 저장 위치는 DB_PATH 하나로 관리한다. 여러 명이 쓸 경우 이 경로만 공유 위치로 바꾸면 된다.
- 주의: OneDrive·SharePoint 동기화 폴더에는 두지 않는다 (동기화 중 DB 파일이 손상될 수 있음).
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import pandas as pd

DB_PATH = os.environ.get("OBI_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "obi_history.db"))

# 기존 이력관리 엑셀 컬럼 순서 (내보내기 시 그대로 사용)
LEGACY_COLUMNS = ["회사", "이름", "직급", "부서", "분류", "퇴직일자", "인터뷰 여부", "보안서약서 작성 여부", "Comment 및 확인 결과"]


@contextmanager
def _conn():
    """커밋(오류 시 롤백) 후 연결을 반드시 닫는다.
    sqlite3 연결의 기본 with 문은 커밋만 하고 닫지 않아 Windows 에서 DB 파일이 잠긴 채 남는다."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)   # 다른 사람이 저장 중이면 최대 15초 대기
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                분석일시 TEXT, 분석자 TEXT,
                회사 TEXT, 이름 TEXT, 직급 TEXT, 부서 TEXT, 분류 TEXT DEFAULT '퇴직자', 계정 TEXT,
                퇴직일자 TEXT, 기준일 TEXT, 원본파일명 TEXT,
                이벤트수 INTEGER, 검증상태 TEXT,
                인터뷰여부 TEXT DEFAULT '미확인', 보안서약서 TEXT DEFAULT '미확인',
                코멘트 TEXT DEFAULT '', 검토상태 TEXT DEFAULT '검토 전', 검토자 TEXT DEFAULT '', 검토일시 TEXT DEFAULT '',
                검증결과_json TEXT, 채널요약_json TEXT, 타임라인_json TEXT, 기준점검_json TEXT
            )""")
        cols = [r[1] for r in con.execute("PRAGMA table_info(analyses)")]
        if "기준점검_json" not in cols:   # 이전 버전 DB 호환
            con.execute("ALTER TABLE analyses ADD COLUMN 기준점검_json TEXT")


def save_analysis(info, checks, summary, timeline, criteria=None):
    """분석 직후 자동 저장. 저장된 이력 id를 돌려준다."""
    ok = all("확인 필요" not in str(c[1]) for c in checks)
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO analyses (분석일시, 분석자, 회사, 이름, 직급, 부서, 분류, 계정, 퇴직일자, 기준일, 원본파일명,
                                  이벤트수, 검증상태, 검증결과_json, 채널요약_json, 타임라인_json, 기준점검_json)
            VALUES (?, ?, ?, ?, ?, ?, '퇴직자', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), info.get("분석자", ""), info["회사"], info["이름"],
             info["직급"], info["부서"], info["계정"], info["퇴직일자"], info["기준일"], info["원본파일명"],
             len(timeline), "정상" if ok else "확인 필요",
             json.dumps(checks, ensure_ascii=False),
             summary.to_json(orient="records", force_ascii=False),
             timeline.to_json(orient="records", force_ascii=False, date_format="iso"),
             (criteria if criteria is not None else pd.DataFrame()).to_json(orient="records", force_ascii=False)))
        return cur.lastrowid


def update_review(analysis_id, 인터뷰여부, 보안서약서, 코멘트, 검토상태, 검토자):
    with _conn() as con:
        con.execute("""UPDATE analyses SET 인터뷰여부=?, 보안서약서=?, 코멘트=?, 검토상태=?, 검토자=?, 검토일시=?
                       WHERE id=?""",
                    (인터뷰여부, 보안서약서, 코멘트, 검토상태, 검토자,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"), analysis_id))


def delete_analysis(analysis_id):
    with _conn() as con:
        con.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))


def list_analyses():
    with _conn() as con:
        rows = con.execute("""SELECT id, 분석일시, 회사, 이름, 직급, 부서, 분류, 퇴직일자, 기준일, 이벤트수, 검증상태,
                                     인터뷰여부, 보안서약서, 검토상태, 검토자, 코멘트
                              FROM analyses ORDER BY id DESC""").fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def get_analysis(analysis_id):
    with _conn() as con:
        r = con.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["검증결과"] = json.loads(d.pop("검증결과_json") or "[]")
    d["채널요약"] = pd.DataFrame(json.loads(d.pop("채널요약_json") or "[]"))
    tl = pd.DataFrame(json.loads(d.pop("타임라인_json") or "[]"))
    d["기준점검"] = pd.DataFrame(json.loads(d.pop("기준점검_json", None) or "[]"))
    if "시간" in tl:
        tl["시간"] = pd.to_datetime(tl["시간"], errors="coerce")
    d["타임라인"] = tl
    return d


def to_legacy_frame(df):
    """이력 목록 → 기존 이력관리 엑셀 양식 + 신규 컬럼"""
    if df.empty:
        return pd.DataFrame(columns=LEGACY_COLUMNS)
    out = pd.DataFrame({
        "회사": df["회사"], "이름": df["이름"], "직급": df["직급"], "부서": df["부서"], "분류": df["분류"],
        "퇴직일자": df["퇴직일자"], "인터뷰 여부": df["인터뷰여부"], "보안서약서 작성 여부": df["보안서약서"],
        "Comment 및 확인 결과": df["코멘트"],
        "분석일시": df["분석일시"], "이벤트수": df["이벤트수"], "검증상태": df["검증상태"],
        "검토상태": df["검토상태"], "검토자": df["검토자"],
    })
    return out
