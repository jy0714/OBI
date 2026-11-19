"""화면 스모크 테스트 — Streamlit AppTest 로 브라우저 없이 app.py 를 실행해 예외 없이 그려지는지 확인한다."""
import os

import pytest
from streamlit.testing.v1 import AppTest

import harness
import obi_store as store

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "app.db"))
    store.init_db()
    return store.DB_PATH


def test_new_page_renders_empty(temp_db):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert [b.label for b in at.button] == ["분석 실행"]


def test_detail_renders_sample(temp_db, sample):
    r = sample["result"]
    aid = store.save_analysis(r["info"], r["checks"], r["summary"], r["timeline"], r["criteria"])
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["current_id"] = aid
    at.run()
    assert not at.exception, at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["검토 후보"] == f"{len(sample['cands'])}건"
    assert metrics["전체 이벤트"] == f"{len(sample['tl'])}건"
    # REQ-04: 숫자 점수 대신 등급
    assert metrics["최고 등급"] == sample["cands"]["등급"].iloc[0]
    assert not any("점수" in label for label in metrics)
    shown = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "점수" not in shown
    labels = [t.label for t in at.tabs]
    for name in ("기준 점검", "통합타임라인", "채널·추이", "AI 분석"):
        assert name in labels


def test_history_page_renders(temp_db, sample):
    r = sample["result"]
    store.save_analysis(r["info"], r["checks"], r["summary"], r["timeline"], r["criteria"])
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("이력 조회").run()
    assert not at.exception, at.exception
