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
import time
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
    # 2026-11-23 요구사항
    ("검토후보건수", "INTEGER"),                   # REQ-15 게시판 '검토 후보 건수' (옛 이력은 NULL → 빈칸)
    ("원본로그_json", "TEXT DEFAULT ''"),          # LLM 전달용 원본 로그 시트 {시트명: [행...]} (옛 이력은 빈칸)
]

LIST_COLUMNS = ["id", "분석일시", "분석자", "회사", "이름", "직급", "부서", "분류", "퇴직일자", "기준일", "이벤트수",
                "검증상태", "인터뷰여부", "보안서약서", "검토상태", "검토자", "코멘트",
                "테스트여부", "최고등급", "확인필요건수", "검토후보건수", "AI생성일시"]

# 보안서약서 PDF 보관 (REQ-12) — 서명·개인정보가 들어 있으므로 동기화 폴더에 두지 않는다
DEFAULT_FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pledges")
PLEDGE_MAX_MB = 20          # [임시] 업로드 상한
# [결정 필요 #9] 보관 기간·파기: 자동 삭제 없음, 삭제는 수동(mark_pledge_deleted = 삭제 표시)
# [결정 필요 #11] 열람 범위: 로그인 없음 → 제한 없이 내려받되 내려받기 이력(누가·언제)을 남긴다


def files_dir():
    """서약서 저장 폴더 (환경 변수 OBI_FILES_DIR, 기본 ./data/pledges). 없으면 만든다."""
    d = os.environ.get("OBI_FILES_DIR") or DEFAULT_FILES_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DBOpenError(RuntimeError):
    """이력 DB 를 열 수 없음. 메시지에 경로와 점검 결과(원인 후보)가 들어 있다."""


def db_path():
    """실제로 열 DB 파일 경로.
    OBI_DB_PATH 를 배치 파일에서 `set OBI_DB_PATH="D:\\x\\a.db"` 처럼 따옴표째 넣거나, 폴더만 적거나,
    상대 경로로 적어도 같은 파일을 가리키게 정리한다."""
    p = str(DB_PATH).strip().strip('"').strip("'").strip()
    p = os.path.abspath(os.path.expandvars(os.path.expanduser(p)))
    if os.path.isdir(p):
        p = os.path.join(p, "obi_history.db")
    return p


def _diagnose(path, err):
    """'unable to open database file' 의 원인 후보를 사람이 읽을 수 있게 정리한다."""
    d = os.path.dirname(path)
    lines = [f"이력 DB 를 열 수 없습니다: {err}", f"- 경로: {path}"]
    if not os.path.isdir(d):
        lines.append(f"- 폴더가 없고 만들 수도 없습니다: {d}")
    elif not os.access(d, os.W_OK):
        lines.append(f"- 폴더에 쓰기 권한이 없습니다: {d} (DB 옆에 -wal/-shm 파일을 만들어야 합니다)")
    if os.path.exists(path):
        if not os.access(path, os.W_OK):
            lines.append("- DB 파일이 읽기 전용입니다 (파일 속성의 '읽기 전용'을 해제하세요).")
        for ext in ("-wal", "-shm"):
            side = path + ext
            if os.path.exists(side) and not os.access(side, os.W_OK):
                lines.append(f"- {os.path.basename(side)} 파일이 읽기 전용이거나 다른 사용자 소유입니다.")
    lines.append("- 다른 프로그램(DB Browser, 백신, 백업·동기화 프로그램 등)이 DB 파일을 잠그고 있으면 닫고 다시 실행하세요.")
    lines.append("- OneDrive/SharePoint 동기화 폴더나 네트워크 드라이브에 두면 열리지 않을 수 있습니다 (OBI_DB_PATH 로 로컬 경로 지정).")
    return "\n".join(lines)


@contextmanager
def _conn():
    """커밋(오류 시 롤백) 후 연결을 반드시 닫는다.
    sqlite3 연결의 기본 with 문은 커밋만 하고 닫지 않아 Windows 에서 DB 파일이 잠긴 채 남는다."""
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=15)   # 다른 사람이 저장 중이면 최대 15초 대기
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db(retries=3, wait=1.0):
    """테이블 생성·마이그레이션. 열지 못하면 잠깐 기다렸다 다시 시도하고(백신·백업의 일시 잠금 대비),
    그래도 안 되면 원인 후보를 담은 DBOpenError 를 낸다."""
    path = db_path()
    for i in range(retries):
        try:
            return _init_db()
        except sqlite3.OperationalError as e:
            if "unable to open" not in str(e) and "readonly" not in str(e):
                raise
            if i == retries - 1:
                raise DBOpenError(_diagnose(path, e)) from e
            time.sleep(wait)
        except OSError as e:          # 폴더를 만들 수 없음 (드라이브 없음, 권한 없음 등)
            raise DBOpenError(_diagnose(path, e)) from e


def _init_db():
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS pledges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER, 저장파일명 TEXT, 원본파일명 TEXT, 크기 INTEGER, sha256 TEXT,
                올린사람 TEXT, 올린일시 TEXT,
                삭제여부 INTEGER DEFAULT 0, 삭제자 TEXT DEFAULT '', 삭제일시 TEXT DEFAULT ''
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS pledge_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pledge_id INTEGER, analysis_id INTEGER, 내려받은사람 TEXT, 일시 TEXT
            )""")


def save_analysis(info, checks, summary, timeline, criteria=None, *, test=False, top_grade="", need_count=None,
                  sources=None, cand_count=None, raw=None):
    """분석 직후 자동 저장. 저장된 이력 id를 돌려준다.

    test: Test 로 실행한 분석 (REQ-08) / top_grade·need_count: 게시판 목록용 (REQ-06)
    cand_count: 검토 후보 건수 (REQ-15 게시판)
    raw: 원본 로그 시트 {시트명: DataFrame} (prep.raw_sheets) — LLM 분석에 함께 보낸다
    sources: 인적 사항 출처 {"이름": "로그"|"수정"|"직접", ...} (REQ-10)
    """
    ok = all("확인 필요" not in str(c[1]) for c in checks)
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO analyses (분석일시, 분석자, 회사, 이름, 직급, 부서, 분류, 계정, 퇴직일자, 기준일, 원본파일명,
                                  이벤트수, 검증상태, 검증결과_json, 채널요약_json, 타임라인_json, 기준점검_json,
                                  테스트여부, 최고등급, 확인필요건수, 인적사항출처_json, 검토후보건수, 원본로그_json)
            VALUES (?, ?, ?, ?, ?, ?, '퇴직자', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), info.get("분석자", ""), info["회사"], info["이름"],
             info["직급"], info["부서"], info["계정"], info["퇴직일자"], info["기준일"], info["원본파일명"],
             len(timeline), "정상" if ok else "확인 필요",
             json.dumps(checks, ensure_ascii=False),
             summary.to_json(orient="records", force_ascii=False),
             timeline.to_json(orient="records", force_ascii=False, date_format="iso"),
             (criteria if criteria is not None else pd.DataFrame()).to_json(orient="records", force_ascii=False),
             1 if test else 0, top_grade or "", need_count,
             json.dumps(sources or {}, ensure_ascii=False), cand_count,
             json.dumps({n: {"columns": [str(c) for c in df.columns], "data": df.values.tolist()}
                         for n, df in (raw or {}).items()}, ensure_ascii=False)))
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
    """실제 삭제. Test 이력만 허용한다 (정식 이력은 mark_deleted). 서약서 파일도 함께 지운다 (REQ-12)."""
    with _conn() as con:
        r = con.execute("SELECT 테스트여부 FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if r is None:
            return
        if not r["테스트여부"]:
            raise PermissionError("정식 이력은 실제로 삭제할 수 없습니다. 삭제 표시(mark_deleted)를 사용하세요.")
        files = [x["저장파일명"] for x in
                 con.execute("SELECT 저장파일명 FROM pledges WHERE analysis_id=?", (analysis_id,)).fetchall()]
        con.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
        con.execute("DELETE FROM reports WHERE analysis_id=?", (analysis_id,))
        con.execute("DELETE FROM pledges WHERE analysis_id=?", (analysis_id,))
        con.execute("DELETE FROM pledge_downloads WHERE analysis_id=?", (analysis_id,))
    for f in files:                      # DB 삭제가 확정된 뒤 파일 정리
        p = os.path.join(files_dir(), f)
        if os.path.exists(p):
            os.remove(p)


# ---------------------------------------------------------------------------
# 보안서약서 PDF (REQ-12)
# ---------------------------------------------------------------------------
PLEDGE_COLUMNS = ["id", "analysis_id", "저장파일명", "원본파일명", "크기", "sha256", "올린사람", "올린일시",
                  "삭제여부", "삭제자", "삭제일시"]


def validate_pledge(filename, data):
    """PDF 만 받는다. 문제가 있으면 이유를 담은 ValueError."""
    if not str(filename or "").lower().endswith(".pdf"):
        raise ValueError("PDF 파일만 올릴 수 있습니다 (확장자가 .pdf 가 아님).")
    if not data:
        raise ValueError("빈 파일입니다.")
    if len(data) > PLEDGE_MAX_MB * 1024 * 1024:
        raise ValueError(f"파일이 너무 큽니다 ({len(data) / 1024 / 1024:,.1f}MB > {PLEDGE_MAX_MB}MB).")
    if not bytes(data[:5]) == b"%PDF-":
        raise ValueError("PDF 파일이 아닙니다 (확장자만 .pdf 이고 내용이 PDF 형식이 아님).")


def save_pledge(analysis_id, filename, data, who):
    """서약서 저장. 서버 파일명은 서버가 만들고(원본 파일명은 DB 에만), 이전 파일은 지우지 않고 새 행 추가.
    파일을 먼저 쓰고 DB 에 기록하며, DB 기록이 실패하면 쓴 파일을 지운다. 올리면 보안서약서 = '예'."""
    import hashlib
    validate_pledge(filename, data)
    sha = hashlib.sha256(data).hexdigest()
    now = datetime.now()
    stored = f"{int(analysis_id)}_{now.strftime('%Y%m%d_%H%M%S_%f')}_{sha[:8]}.pdf"
    path = os.path.join(files_dir(), stored)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    try:
        with _conn() as con:
            if con.execute("SELECT 1 FROM analyses WHERE id=?", (analysis_id,)).fetchone() is None:
                raise LookupError(f"이력 #{analysis_id} 가 없습니다.")
            cur = con.execute("""INSERT INTO pledges (analysis_id, 저장파일명, 원본파일명, 크기, sha256, 올린사람, 올린일시)
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              (analysis_id, stored, os.path.basename(str(filename)), len(data), sha, who,
                               now.strftime("%Y-%m-%d %H:%M:%S")))
            con.execute("UPDATE analyses SET 보안서약서='예' WHERE id=?", (analysis_id,))
            pid = cur.lastrowid
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise
    return pid


def list_pledges(analysis_id, include_deleted=False):
    where = "" if include_deleted else "AND COALESCE(삭제여부, 0) = 0"
    with _conn() as con:
        rows = con.execute(f"SELECT {', '.join(PLEDGE_COLUMNS)} FROM pledges WHERE analysis_id=? {where} "
                           "ORDER BY id DESC", (analysis_id,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows], columns=PLEDGE_COLUMNS)


def current_pledge(analysis_id):
    """가장 최근에 올린(삭제 표시 안 된) 서약서 dict, 없으면 None"""
    df = list_pledges(analysis_id)
    return None if df.empty else df.iloc[0].to_dict()


def read_pledge(pledge_id, who):
    """서약서 파일 bytes (내려받기 버튼을 눌렀을 때만 호출). 무결성 확인 후 내려받기 이력 기록."""
    import hashlib
    with _conn() as con:
        r = con.execute("SELECT * FROM pledges WHERE id=?", (pledge_id,)).fetchone()
    if r is None:
        raise LookupError("서약서 기록이 없습니다.")
    path = os.path.join(files_dir(), r["저장파일명"])
    if not os.path.exists(path):
        raise FileNotFoundError(f"서약서 파일이 저장 위치에 없습니다: {r['저장파일명']}")
    with open(path, "rb") as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != r["sha256"]:
        raise ValueError("서약서 파일이 기록과 다릅니다 (SHA-256 불일치).")
    with _conn() as con:
        con.execute("INSERT INTO pledge_downloads (pledge_id, analysis_id, 내려받은사람, 일시) VALUES (?, ?, ?, ?)",
                    (pledge_id, r["analysis_id"], who, _now()))
    return data


def list_pledge_downloads(analysis_id):
    with _conn() as con:
        rows = con.execute("SELECT pledge_id, 내려받은사람, 일시 FROM pledge_downloads WHERE analysis_id=? "
                           "ORDER BY id DESC", (analysis_id,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows], columns=["pledge_id", "내려받은사람", "일시"])


def mark_pledge_deleted(pledge_id, who):
    """서약서 삭제 표시 (파일은 지우지 않음). 남은 서약서가 없으면 보안서약서를 '미확인'으로 되돌린다."""
    with _conn() as con:
        r = con.execute("SELECT analysis_id FROM pledges WHERE id=?", (pledge_id,)).fetchone()
        if r is None:
            return
        con.execute("UPDATE pledges SET 삭제여부=1, 삭제자=?, 삭제일시=? WHERE id=?", (who, _now(), pledge_id))
        left = con.execute("SELECT COUNT(*) FROM pledges WHERE analysis_id=? AND COALESCE(삭제여부,0)=0",
                           (r["analysis_id"],)).fetchone()[0]
        if not left:
            con.execute("UPDATE analyses SET 보안서약서='미확인' WHERE id=? AND 보안서약서='예'", (r["analysis_id"],))


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
    pledge_count = ("(SELECT COUNT(*) FROM pledges p WHERE p.analysis_id = analyses.id "
                    "AND COALESCE(p.삭제여부, 0) = 0) AS 서약서수")
    with _conn() as con:
        rows = con.execute(f"SELECT {', '.join(LIST_COLUMNS)}, {pledge_count} FROM analyses {where} "
                           "ORDER BY id DESC").fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=LIST_COLUMNS + ["서약서수"])
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
    d["원본로그"] = {n: pd.DataFrame(v["data"], columns=v["columns"])
                    for n, v in json.loads(d.pop("원본로그_json", None) or "{}").items()}
    for k in ("AI의견", "AI프로바이더", "AI모델", "AI생성일시", "AI요청자", "최고등급", "삭제자", "삭제일시"):
        d[k] = d.get(k) or ""
    d["테스트여부"] = int(d.get("테스트여부") or 0)
    d["삭제여부"] = int(d.get("삭제여부") or 0)
    d["서약서"] = current_pledge(analysis_id)              # REQ-12 현재본 (없으면 None)
    d["서약서수"] = 0 if d["서약서"] is None else len(list_pledges(analysis_id))
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
