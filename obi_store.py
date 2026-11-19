"""
OBI 이력 저장소 (SQLite)

- 분석을 실행하면 자동으로 1건 저장되고, 검토 내용은 화면에서 수정·저장한다.
- 저장 위치는 서버의 DB_PATH(환경 변수 OBI_DB_PATH) 한 곳이다. 여러 명이 쓰면 이 경로만 공유 위치로 바꾼다.
- 여러 명이 동시에 쓸 때를 대비해 WAL 모드로 연다.
- 주의: OneDrive·SharePoint 동기화 폴더에는 두지 않는다 (동기화 중 DB 파일이 손상될 수 있음).
- 컬럼 추가는 init_db() 의 MIGRATIONS 에만 적는다. 옛 DB 는 열 때 자동으로 컬럼이 붙고, 옛 이력의 새 컬럼은 비어 있다.
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

# 이전 버전 DB 에 없을 수 있는 컬럼 (이름, 정의). 순서대로 ALTER TABLE ADD COLUMN.
MIGRATIONS = [
    ("기준점검_json", "TEXT"),
    # 2026-11-19 요구사항
    ("테스트여부", "INTEGER DEFAULT 0"),          # REQ-08
    ("최고등급", "TEXT DEFAULT ''"),              # REQ-06 목록용 (옛 이력은 빈칸)
    ("확인필요건수", "INTEGER"),                   # REQ-06 목록용 (옛 이력은 NULL)
    ("인적사항출처_json", "TEXT"),                 # REQ-10 {"이름": "로그"|"수정"|"직접", ...}
    ("AI의견", "TEXT DEFAULT ''"),                # REQ-07
    ("AI프로바이더", "TEXT DEFAULT ''"),
    ("AI모델", "TEXT DEFAULT ''"),
    ("AI생성일시", "TEXT DEFAULT ''"),
    ("AI요청자", "TEXT DEFAULT ''"),
    ("AI이전생성일시_json", "TEXT DEFAULT '[]'"),  # '다시 요청' 전 생성 일시 목록
    ("삭제여부", "INTEGER DEFAULT 0"),            # REQ-06 삭제 표시
    ("삭제자", "TEXT DEFAULT ''"),
    ("삭제일시", "TEXT DEFAULT ''"),
]

LIST_COLUMNS = ["id", "분석일시", "분석자", "회사", "이름", "직급", "부서", "분류", "퇴직일자", "기준일", "이벤트수",
                "검증상태", "인터뷰여부", "보안서약서", "검토상태", "검토자", "코멘트",
                "테스트여부", "최고등급", "확인필요건수", "AI생성일시"]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        con.execute("PRAGMA journal_mode=WAL")    # 읽는 동안에도 저장 가능 (DB 파일 옆에 -wal, -shm 생성)
        con.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                분석일시 TEXT, 분석자 TEXT,
                회사 TEXT, 이름 TEXT, 직급 TEXT, 부서 TEXT, 분류 TEXT DEFAULT '퇴직자', 계정 TEXT,
                퇴직일자 TEXT, 기준일 TEXT, 원본파일명 TEXT,
                이벤트수 INTEGER, 검증상태 TEXT,
                인터뷰여부 TEXT DEFAULT '미확인', 보안서약서 TEXT DEFAULT '미확인',
                코멘트 TEXT DEFAULT '', 검토상태 TEXT DEFAULT '검토 전', 검토자 TEXT DEFAULT '', 검토일시 TEXT DEFAULT '',
                검증결과_json TEXT, 채널요약_json TEXT, 타임라인_json TEXT
            )""")
        cols = [r[1] for r in con.execute("PRAGMA table_info(analyses)")]
        for name, decl in MIGRATIONS:
            if name not in cols:
                con.execute(f"ALTER TABLE analyses ADD COLUMN {name} {decl}")
        con.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER, 생성일시 TEXT, 생성자 TEXT, 형식 TEXT, 파일명 TEXT
            )""")


def save_analysis(info, checks, summary, timeline, criteria=None, *, test=False, top_grade="", need_count=None,
                  sources=None):
    """분석 직후 자동 저장. 저장된 이력 id를 돌려준다.

    test: Test 로 실행한 분석 (REQ-08) / top_grade·need_count: 게시판 목록용 (REQ-06)
    sources: 인적 사항 출처 {"이름": "로그"|"수정"|"직접", ...} (REQ-10)
    """
    ok = all("확인 필요" not in str(c[1]) for c in checks)
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO analyses (분석일시, 분석자, 회사, 이름, 직급, 부서, 분류, 계정, 퇴직일자, 기준일, 원본파일명,
                                  이벤트수, 검증상태, 검증결과_json, 채널요약_json, 타임라인_json, 기준점검_json,
                                  테스트여부, 최고등급, 확인필요건수, 인적사항출처_json)
            VALUES (?, ?, ?, ?, ?, ?, '퇴직자', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), info.get("분석자", ""), info["회사"], info["이름"],
             info["직급"], info["부서"], info["계정"], info["퇴직일자"], info["기준일"], info["원본파일명"],
             len(timeline), "정상" if ok else "확인 필요",
             json.dumps(checks, ensure_ascii=False),
             summary.to_json(orient="records", force_ascii=False),
             timeline.to_json(orient="records", force_ascii=False, date_format="iso"),
             (criteria if criteria is not None else pd.DataFrame()).to_json(orient="records", force_ascii=False),
             1 if test else 0, top_grade or "", need_count,
             json.dumps(sources or {}, ensure_ascii=False)))
        return cur.lastrowid


def update_review(analysis_id, 인터뷰여부, 보안서약서, 코멘트, 검토상태, 검토자):
    with _conn() as con:
        con.execute("""UPDATE analyses SET 인터뷰여부=?, 보안서약서=?, 코멘트=?, 검토상태=?, 검토자=?, 검토일시=?
                       WHERE id=?""",
                    (인터뷰여부, 보안서약서, 코멘트, 검토상태, 검토자, _now(), analysis_id))


def save_ai_opinion(analysis_id, text, provider, model, requester):
    """AI 의견 저장 (REQ-07). 다시 요청하면 덮어쓰되 이전 생성 일시는 목록으로 남긴다."""
    with _conn() as con:
        r = con.execute("SELECT AI생성일시, AI이전생성일시_json FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        prev = json.loads((r["AI이전생성일시_json"] if r else None) or "[]")
        if r and r["AI생성일시"]:
            prev.append(r["AI생성일시"])
        con.execute("""UPDATE analyses SET AI의견=?, AI프로바이더=?, AI모델=?, AI생성일시=?, AI요청자=?,
                       AI이전생성일시_json=? WHERE id=?""",
                    (text, provider, model, _now(), requester, json.dumps(prev, ensure_ascii=False), analysis_id))


def mark_deleted(analysis_id, who):
    """삭제 표시 (REQ-06). 실제로 지우지 않고 목록에서만 숨긴다."""
    with _conn() as con:
        con.execute("UPDATE analyses SET 삭제여부=1, 삭제자=?, 삭제일시=? WHERE id=?", (who, _now(), analysis_id))


def delete_analysis(analysis_id):
    """실제 삭제. Test 이력만 허용한다 (정식 이력은 mark_deleted)."""
    with _conn() as con:
        r = con.execute("SELECT 테스트여부 FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if r is None:
            return
        if not r["테스트여부"]:
            raise PermissionError("정식 이력은 실제로 삭제할 수 없습니다. 삭제 표시(mark_deleted)를 사용하세요.")
        con.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
        con.execute("DELETE FROM reports WHERE analysis_id=?", (analysis_id,))


def log_report(analysis_id, who, fmt, filename):
    """리포트 생성 이력 (REQ-07)"""
    with _conn() as con:
        con.execute("INSERT INTO reports (analysis_id, 생성일시, 생성자, 형식, 파일명) VALUES (?, ?, ?, ?, ?)",
                    (analysis_id, _now(), who, fmt, filename))


def list_reports(analysis_id):
    with _conn() as con:
        rows = con.execute("SELECT 생성일시, 생성자, 형식, 파일명 FROM reports WHERE analysis_id=? ORDER BY id DESC",
                           (analysis_id,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows], columns=["생성일시", "생성자", "형식", "파일명"])


def list_analyses(include_deleted=False):
    """게시판 목록. 타임라인 JSON 은 읽지 않는다 (REQ-06)."""
    where = "" if include_deleted else "WHERE COALESCE(삭제여부, 0) = 0"
    with _conn() as con:
        rows = con.execute(f"SELECT {', '.join(LIST_COLUMNS)} FROM analyses {where} ORDER BY id DESC").fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=LIST_COLUMNS)
    df["테스트여부"] = df["테스트여부"].fillna(0).astype(int)
    df["최고등급"] = df["최고등급"].fillna("")
    return df


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
    d["인적사항출처"] = json.loads(d.pop("인적사항출처_json", None) or "{}")
    d["AI이전생성일시"] = json.loads(d.pop("AI이전생성일시_json", None) or "[]")
    for k in ("AI의견", "AI프로바이더", "AI모델", "AI생성일시", "AI요청자", "최고등급", "삭제자", "삭제일시"):
        d[k] = d.get(k) or ""
    d["테스트여부"] = int(d.get("테스트여부") or 0)
    d["삭제여부"] = int(d.get("삭제여부") or 0)
    if "시간" in tl:
        tl["시간"] = pd.to_datetime(tl["시간"], errors="coerce")
    d["타임라인"] = tl
    return d


def to_legacy_frame(df):
    """이력 목록 → 기존 이력관리 엑셀 양식 + 신규 컬럼. Test 이력은 제외한다 (REQ-08)."""
    if not df.empty and "테스트여부" in df:
        df = df[df["테스트여부"] == 0]
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
