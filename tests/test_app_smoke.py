"""화면 스모크 테스트 — Streamlit AppTest 로 브라우저 없이 app.py 를 실행해 예외 없이 그려지는지 확인한다."""
import os

import pytest
from streamlit.testing.v1 import AppTest

import harness
import obi_store as store

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def app(authed=True):
    """메인 화면 테스트는 로그인된 세션으로 시작한다 (로그인 자체는 test_login_* 에서)"""
    at = AppTest.from_file(APP, default_timeout=60)
    if authed:
        at.session_state["obi_authed"] = True
    return at


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    """`ollama list` 대신 고정 목록 (네트워크 없음)"""
    import obi_ai
    monkeypatch.setattr(obi_ai, "list_ollama_models", lambda base, allowed=(), timeout=3: ["gemma4:31b"])


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "app.db"))
    store.init_db()
    return store.DB_PATH


def test_login_required_before_main(temp_db):
    at = app(authed=False).run()
    assert not at.exception, at.exception
    assert not at.sidebar.radio and "분석 실행" not in [b.label for b in at.button]   # 메인 화면 안 보임
    at.text_input(key="login_pw").set_value("0000")
    at.button(key="FormSubmitter:login-확인").click().run()
    assert any("맞지 않습니다" in e.value for e in at.error) and not at.sidebar.radio
    at.text_input(key="login_pw").set_value("1234")
    at.button(key="FormSubmitter:login-확인").click().run()
    assert not at.exception, at.exception
    assert at.sidebar.radio and "분석 실행" in [b.label for b in at.button]


def test_login_locks_after_repeated_failures(temp_db):
    at = app(authed=False).run()
    for _ in range(5):
        at.text_input(key="login_pw").set_value("9999")
        at.button(key="FormSubmitter:login-확인").click().run()
    at.text_input(key="login_pw").set_value("1234")
    at.button(key="FormSubmitter:login-확인").click().run()
    assert any("초 뒤에" in e.value for e in at.error) and not at.sidebar.radio


@pytest.mark.parametrize("ini", [None, "[auth]|password = 12a4", "[auth]|password ="])
def test_login_fails_closed_on_bad_config(temp_db, _temp_config, ini):
    if ini is None:
        _temp_config.unlink()
    else:
        _temp_config.write_text(ini.replace("|", "\n"),encoding="utf-8")
    at = app().run()                                    # 이미 로그인된 세션이어도 막힘
    assert not at.exception, at.exception
    assert at.error and not at.sidebar.radio


def test_ai_engines_and_ollama_models(temp_db):
    at = app().run()
    assert at.sidebar.selectbox(key="ai_provider").options == ["Ollama (로컬)", "OpenAI 형식 API"]
    assert at.sidebar.selectbox(key="ollama_model").options == ["gemma4:31b"]


def test_new_page_renders_empty(temp_db):
    at = app().run()
    assert not at.exception
    assert [b.label for b in at.main.button] == ["분석 실행"]


def test_detail_renders_sample(temp_db, sample):
    r = sample["result"]
    aid = store.save_analysis(r["info"], r["checks"], r["summary"], r["timeline"], r["criteria"])
    at = app()
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
    assert any("주요 검토 파일" in m.value for m in at.markdown)          # REQ-03
    pm = sample["personal_mail"]
    assert metrics["개인메일 발송"] == f"{len(pm)}건 (첨부 {int((pm['첨부 수'] > 0).sum())}건)"   # REQ-05
    assert any("개인메일 발송" in m.value for m in at.markdown)
    labels = [t.label for t in at.tabs]
    for name in ("기준 점검", "통합타임라인", "채널·추이", "AI 분석"):
        assert name in labels


def test_history_page_renders(temp_db, sample):
    r = sample["result"]
    store.save_analysis(r["info"], r["checks"], r["summary"], r["timeline"], r["criteria"])
    at = app().run()
    at.sidebar.radio[0].set_value("이력 조회").run()
    assert not at.exception, at.exception


def test_req08_review_form_blocks_missing(temp_db, sample):
    """정식 이력: 필수값 없이 저장 안 됨 / Test 이력: 경고 없음"""
    import obi_pipeline as pipeline
    aid = pipeline.save(sample["result"])
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    assert any("입력 필요" in w.value for w in at.warning)
    at.button(key="FormSubmitter:new_review-검토 내용 저장").click().run()
    assert any("입력해야 저장" in e.value for e in at.error)
    assert store.get_analysis(aid)["검토일시"] == ""

    tid = pipeline.save(sample["result"], test=True)
    at2 = app()
    at2.session_state["current_id"] = tid
    at2.run()
    assert not any("입력 필요" in w.value for w in at2.warning)


def test_req09_board_row_opens_detail_below(temp_db, sample):
    """게시판 행 선택 → 게시판은 그대로 두고 아래에 상세. 선택 해제 → 상세 닫힘"""
    import obi_pipeline as pipeline
    aid = pipeline.save(sample["result"])
    at = app().run()
    at.sidebar.radio[0].set_value("이력 조회").run()
    assert not at.exception
    assert not any(m.label == "최고 등급" for m in at.metric)                       # 선택 전: 상세 없음
    assert all(s.label != "상세 보기" for s in at.selectbox)                        # 선택 상자 없음

    at.session_state["board_table"] = {"selection": {"rows": [0], "columns": [], "cells": []}}   # 행 클릭
    at.run()
    assert not at.exception, at.exception
    assert any(m.label == "최고 등급" for m in at.metric)                           # 상세가 아래에 열림
    assert any("행을 누르면 아래에 상세" in c.value for c in at.caption)              # 게시판도 그대로 보임
    assert not any(b.key == "board_back" for b in at.button)                       # 화면 전환·돌아가기 없음

    at.session_state["board_table"] = {"selection": {"rows": [], "columns": [], "cells": []}}    # 선택 해제
    at.run()
    assert not any(m.label == "최고 등급" for m in at.metric)

def test_req10_upload_prefills_and_user_edit_wins(temp_db, tmp_path):
    """업로드 즉시 이름·직급 자동 입력 → 직급을 고쳐 Test 로 실행 → 고친 값과 출처가 저장된다 (REQ-10·08)"""
    import sample_ddi
    data = open(sample_ddi.build(str(tmp_path / "s.xlsx")), "rb").read()
    xmime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    at = app().run()
    at.file_uploader(key="upload_file").set_value(("로그.xlsx", data, xmime)).run()
    assert not at.exception, at.exception
    assert at.text_input(key="f_name").value == "홍길동"
    assert at.text_input(key="f_rank").value == "선임"
    assert at.text_input(key="f_dept").value == ""

    at.text_input(key="f_rank").set_value("책임")
    at.checkbox[0].check()
    at.date_input[0].set_value(__import__("datetime").date(2026, 9, 30))
    at.date_input[1].set_value(__import__("datetime").date(2026, 9, 2))
    at.button(key="FormSubmitter:new_analysis-분석 실행").click().run()
    assert not at.exception, at.exception
    aid = at.session_state["current_id"]
    a = store.get_analysis(aid)
    assert (a["이름"], a["직급"], a["원본파일명"], a["테스트여부"]) == ("홍길동", "책임", "로그.xlsx", 1)
    assert a["인적사항출처"] == {"이름": "로그", "직급": "수정", "부서": "직접"}


def test_req02_dashboard_renders_and_day_pick(temp_db, sample):
    import obi_pipeline as pipeline
    aid = pipeline.save(sample["result"])
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    day = sample["dashboard"]["dates"][0]
    at.selectbox(key="new_day").set_value(day).run()
    assert not at.exception, at.exception
    assert any("일자별 활동" in m.value for m in at.markdown)


def test_req02_dashboard_empty_history(temp_db, sample):
    r = sample["result"]
    import pandas as pd
    aid = store.save_analysis(r["info"], r["checks"], pd.DataFrame(), r["timeline"].iloc[0:0], pd.DataFrame())
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    assert not at.exception, at.exception
    assert any("표시할 추이가 없습니다" in i.value for i in at.info)


def test_req07_report_only_on_click_and_ai_persisted(temp_db, sample):
    import obi_pipeline as pipeline
    # 정식 이력 + 검토 미입력 → 리포트 버튼 비활성, 화면을 열어도 리포트 이력 없음
    aid = pipeline.save(sample["result"])
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    assert at.button(key="new_report").disabled
    assert store.list_reports(aid).empty

    # 검토 입력 완료 → 버튼을 눌렀을 때만 생성·이력 기록
    store.update_review(aid, "아니오", "아니오", "이상 없음", "검토 완료", "김검토")   # 서약서 규칙(REQ-12) 해당 없음
    at.run()
    assert not at.button(key="new_report").disabled and store.list_reports(aid).empty
    at.button(key="new_report").click().run()
    assert not at.exception, at.exception
    assert len(store.list_reports(aid)) == 1

    # AI 의견은 DB 에 있으므로 새 세션(브라우저 새로 열기)에서도 보인다
    store.save_ai_opinion(aid, "저장된 AI 의견 본문", "ollama", "qwen", "u")
    fresh = app()
    fresh.session_state["current_id"] = aid
    fresh.run()
    assert any("저장된 AI 의견 본문" in m.value for m in fresh.markdown)


def test_req07_ai_request_saves_to_db(temp_db, sample, monkeypatch):
    """분석 요청 → (가짜 LLM) 스트리밍 → DB 저장 → 다시 요청 시 이전 생성 일시 보존"""
    import obi_ai
    import obi_pipeline as pipeline
    monkeypatch.setattr(obi_ai, "stream_analyze", lambda *a, **k: iter(["**4. 최종 위험 수준** ", "Medium"]))
    aid = pipeline.save(sample["result"], test=True)
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    at.button(key="new_llm_run").click().run()
    assert not at.exception, at.exception
    a = store.get_analysis(aid)
    assert a["AI의견"].endswith("Medium") and a["AI프로바이더"] == "ollama"
    at.button(key="new_llm_run").click().run()                 # 다시 요청
    assert len(store.get_analysis(aid)["AI이전생성일시"]) == 1


def test_req12_pledge_upload_flow(temp_db, sample):
    """인터뷰 '예' + 서약서 없음 → 저장 거부 / 잘못된 파일 거부 / PDF 올리면 '예' 자동 → 검토 완료·리포트 가능"""
    import obi_pipeline as pipeline
    aid = pipeline.save(sample["result"])
    store.update_review(aid, "예", "미확인", "인터뷰 완료", "검토 중", "김검토")
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    assert any("보안서약서 PDF" in w.value for w in at.warning)                  # 입력 필요
    assert "예" not in at.selectbox[1].options                                    # 파일 없으면 '예' 불가
    at.button(key="FormSubmitter:new_review-검토 내용 저장").click().run()
    assert any("보안서약서 PDF" in e.value for e in at.error)
    assert at.button(key="new_report").disabled

    at.file_uploader(key="new_pledge_up").set_value(("가짜.pdf", b"PK not pdf", "application/pdf")).run()
    assert any("PDF 파일이 아닙니다" in e.value for e in at.error)
    assert store.current_pledge(aid) is None

    at.file_uploader(key="new_pledge_up").set_value(("서약서.pdf", b"%PDF-1.4 ok", "application/pdf")).run()
    assert not at.exception, at.exception
    assert store.current_pledge(aid)["원본파일명"] == "서약서.pdf"
    assert store.get_analysis(aid)["보안서약서"] == "예"
    at.button(key="FormSubmitter:new_review-검토 내용 저장").click().run()
    assert store.get_analysis(aid)["검토일시"] != ""                             # 저장됨
    assert not at.button(key="new_report").disabled

    at.button(key="new_pledge_read").click().run()                               # 누를 때만 읽음 + 이력
    assert len(store.list_pledge_downloads(aid)) == 1
