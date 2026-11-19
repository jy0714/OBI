"""REQ-12 보안서약서 PDF 저장소 — 임시 폴더(OBI_FILES_DIR)·임시 DB(OBI_DB_PATH) 사용"""
import hashlib
import os
import sqlite3

import pytest

import obi_pipeline as pipeline
import obi_store as store

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


@pytest.fixture
def env(tmp_path, monkeypatch, sample):
    monkeypatch.setenv("OBI_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "p.db"))
    store.init_db()
    return dict(real=pipeline.save(sample["result"]), test=pipeline.save(sample["result"], test=True))


@pytest.mark.parametrize("name, data, reason", [
    ("서약서.docx", PDF, "확장자"),
    ("서약서.pdf", b"PK\x03\x04 not a pdf", "PDF 형식이 아님"),
    ("서약서.pdf", b"%PDF-" + b"0" * (store.PLEDGE_MAX_MB * 1024 * 1024), "너무 큽니다"),
    ("서약서.pdf", b"", "빈 파일"),
])
def test_rejects_with_reason(env, name, data, reason):
    with pytest.raises(ValueError, match=reason):
        store.save_pledge(env["real"], name, data, "u")
    assert store.current_pledge(env["real"]) is None
    assert os.listdir(store.files_dir()) == []                     # 거부된 파일은 남지 않음


def test_save_download_and_hash(env):
    pid = store.save_pledge(env["real"], "../../홍길동 서약서.pdf", PDF, "검토자A")
    cur = store.current_pledge(env["real"])
    assert cur["id"] == pid and cur["원본파일명"] == "홍길동 서약서.pdf" and cur["올린사람"] == "검토자A"
    assert cur["저장파일명"].startswith(f"{env['real']}_") and "홍길동" not in cur["저장파일명"]   # 서버가 만든 이름
    assert os.path.exists(os.path.join(store.files_dir(), cur["저장파일명"]))
    data = store.read_pledge(pid, "검토자B")
    assert hashlib.sha256(data).hexdigest() == cur["sha256"]
    assert store.list_pledge_downloads(env["real"]).iloc[0]["내려받은사람"] == "검토자B"
    assert store.get_analysis(env["real"])["보안서약서"] == "예"                 # 올리면 자동 '예'


def test_replace_keeps_previous(env):
    first = store.save_pledge(env["real"], "a.pdf", PDF, "u")
    second = store.save_pledge(env["real"], "b.pdf", PDF + b"%v2", "u")
    assert store.current_pledge(env["real"])["id"] == second
    assert len(store.list_pledges(env["real"])) == 2
    assert len(os.listdir(store.files_dir())) == 2                  # 이전 파일 보존
    store.mark_pledge_deleted(second, "관리자")
    assert store.current_pledge(env["real"])["id"] == first
    deleted = store.list_pledges(env["real"], include_deleted=True).set_index("id").loc[second]
    assert deleted["삭제자"] == "관리자" and deleted["삭제여부"] == 1
    assert len(os.listdir(store.files_dir())) == 2                  # 삭제 표시는 파일을 지우지 않음
    store.mark_pledge_deleted(first, "관리자")
    assert store.get_analysis(env["real"])["보안서약서"] == "미확인"


def test_db_failure_cleans_file(env):
    with pytest.raises(LookupError):
        store.save_pledge(99999, "a.pdf", PDF, "u")                   # 없는 이력 → DB 기록 실패
    assert os.listdir(store.files_dir()) == []


def test_soft_delete_keeps_file_test_delete_removes(env):
    store.save_pledge(env["real"], "a.pdf", PDF, "u")
    store.save_pledge(env["test"], "b.pdf", PDF, "u")
    store.mark_deleted(env["real"], "u")                          # 정식 이력 삭제 표시 → 파일 유지
    assert len(os.listdir(store.files_dir())) == 2
    store.delete_analysis(env["test"])                            # Test 이력 실제 삭제 → 파일도 삭제
    assert len(os.listdir(store.files_dir())) == 1
    assert store.list_pledges(env["test"], include_deleted=True).empty


def test_survives_restart(env):
    """앱 재시작 = 새 연결·새 모듈 상태에서도 내려받기 가능"""
    import importlib
    pid = store.save_pledge(env["real"], "a.pdf", PDF, "u")
    db = store.DB_PATH
    fresh = importlib.reload(store)
    fresh.DB_PATH = db
    assert fresh.read_pledge(pid, "u") == PDF


def test_old_db_without_pledges_table_opens(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    from test_store import OLD_SCHEMA                             # 실제 첫 버전 스키마 (서약서 관련 없음)
    con.execute(OLD_SCHEMA)
    con.execute("INSERT INTO analyses (이름, 회사, 검증결과_json, 채널요약_json, 타임라인_json) "
                "VALUES ('옛', 'DBI', '[]', '[]', '[]')")
    con.commit()
    con.close()
    monkeypatch.setattr(store, "DB_PATH", str(db))
    store.init_db()
    df = store.list_analyses()
    assert df.loc[0, "서약서수"] == 0
    assert store.get_analysis(int(df.loc[0, "id"]))["서약서"] is None
