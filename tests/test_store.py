"""이력 저장소: 옛 DB 호환, 삭제 표시, AI 의견, 리포트 이력"""
import sqlite3

import pytest

import obi_pipeline as pipeline
import obi_store as store

OLD_SCHEMA = """CREATE TABLE analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, 분석일시 TEXT, 분석자 TEXT, 회사 TEXT, 이름 TEXT, 직급 TEXT, 부서 TEXT,
    분류 TEXT DEFAULT '퇴직자', 계정 TEXT, 퇴직일자 TEXT, 기준일 TEXT, 원본파일명 TEXT, 이벤트수 INTEGER, 검증상태 TEXT,
    인터뷰여부 TEXT DEFAULT '미확인', 보안서약서 TEXT DEFAULT '미확인', 코멘트 TEXT DEFAULT '',
    검토상태 TEXT DEFAULT '검토 전', 검토자 TEXT DEFAULT '', 검토일시 TEXT DEFAULT '',
    검증결과_json TEXT, 채널요약_json TEXT, 타임라인_json TEXT)"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "t.db"))
    return store.DB_PATH


def test_old_db_opens_and_lists(db):
    """기준점검_json 도 없던 첫 버전 DB → init_db 후 목록·상세가 열린다. 새 컬럼은 비어 있음."""
    con = sqlite3.connect(db)
    con.execute(OLD_SCHEMA)
    con.execute("""INSERT INTO analyses (분석일시, 회사, 이름, 직급, 부서, 계정, 퇴직일자, 기준일, 원본파일명, 이벤트수,
                   검증상태, 검증결과_json, 채널요약_json, 타임라인_json)
                   VALUES ('2026-10-06 15:00:00', 'DBI', '옛이력', '', '', 'a', '2026-09-30', '2026-09-02', 'x.xlsx', 0,
                           '정상', '[]', '[]', '[]')""")
    con.commit()
    con.close()
    store.init_db()
    df = store.list_analyses()
    assert df.loc[0, "이름"] == "옛이력" and df.loc[0, "최고등급"] == "" and df.loc[0, "테스트여부"] == 0
    a = store.get_analysis(int(df.loc[0, "id"]))
    assert a["AI의견"] == "" and a["인적사항출처"] == {} and a["기준점검"].empty
    with sqlite3.connect(db) as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_save_sets_board_values_and_delete_rules(db, sample):
    store.init_db()
    real = pipeline.save(sample["result"])
    test = pipeline.save(sample["result"], test=True)
    df = store.list_analyses().set_index("id")
    assert df.loc[real, "최고등급"] == sample["cands"]["등급"].iloc[0]
    assert df.loc[real, "확인필요건수"] == int((sample["tl"]["점검결과"] == "확인 필요").sum())
    assert df.loc[test, "테스트여부"] == 1

    with pytest.raises(PermissionError):
        store.delete_analysis(real)                  # 정식 이력은 실제 삭제 불가
    store.mark_deleted(real, "검토자A")
    assert real not in store.list_analyses()["id"].tolist()
    assert real in store.list_analyses(include_deleted=True)["id"].tolist()
    assert store.get_analysis(real)["삭제자"] == "검토자A"

    store.delete_analysis(test)                      # Test 이력은 실제 삭제
    assert store.get_analysis(test) is None


def test_ai_opinion_keeps_previous_times(db, sample):
    store.init_db()
    aid = pipeline.save(sample["result"])
    store.save_ai_opinion(aid, "첫 의견", "ollama", "m1", "u")
    first = store.get_analysis(aid)["AI생성일시"]
    store.save_ai_opinion(aid, "두 번째", "openai", "m2", "u2")
    a = store.get_analysis(aid)
    assert a["AI의견"] == "두 번째" and a["AI모델"] == "m2" and a["AI요청자"] == "u2"
    assert a["AI이전생성일시"] == [first]


def test_report_log(db, sample):
    store.init_db()
    aid = pipeline.save(sample["result"])
    store.log_report(aid, "u", "xlsx", "r.xlsx")
    rep = store.list_reports(aid)
    assert rep.loc[0, "생성자"] == "u" and rep.loc[0, "형식"] == "xlsx"


def test_legacy_export_excludes_test(db, sample):
    store.init_db()
    pipeline.save(sample["result"])
    pipeline.save(sample["result"], test=True)
    assert len(store.to_legacy_frame(store.list_analyses())) == 1
