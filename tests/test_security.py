"""보안 회귀 테스트 (2026-11-26 점검 조치)

1. 암호화 엑셀을 Excel COM 으로 열 때 매크로를 끈다 (가짜 COM 객체로 확인 — 실제 매크로 파일은 쓰지 않음)
2. API 키는 그 키를 입력한(설정한) 엔드포인트에만 보낸다
3. HTML 레이아웃에 넣는 동적 텍스트를 이스케이프한다
4. XLSX 출력에서 '=...' 문자열이 수식이 아닌 텍스트로 저장된다
"""
import io
import sys
import types

import pandas as pd
import pytest
from openpyxl import load_workbook

import obi_ai as ai
import obi_pipeline as pipeline
import obi_preprocess as prep
import obi_report as report
import obi_store as store
from test_app_smoke import app
from test_llm_stream import STREAMS, FakeResp


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "sec.db"))
    store.init_db()
    return store.DB_PATH


# ---------------------------------------------------------------------------
# 1. Excel COM 매크로 비활성화
# ---------------------------------------------------------------------------
class FakeExcel:
    def __init__(self, log, fail_open=False):
        object.__setattr__(self, "log", log)
        object.__setattr__(self, "fail_open", fail_open)
        object.__setattr__(self, "Workbooks", types.SimpleNamespace(Open=self._open))

    def __setattr__(self, name, value):
        self.log.append(("set", name, value))
        object.__setattr__(self, name, value)

    def _open(self, path, **kw):
        self.log.append(("open", getattr(self, "AutomationSecurity", None), kw))
        if self.fail_open:
            raise RuntimeError("open failed")
        ws = types.SimpleNamespace(Name="USB로그", UsedRange=types.SimpleNamespace(
            Value=(("a", "b"), (1, 2)), Row=1, Column=1))
        log = self.log
        return types.SimpleNamespace(Worksheets=[ws], Close=lambda **k: log.append(("close", k)))

    def Quit(self):
        self.log.append(("quit",))


def _fake_com(monkeypatch, fail_open=False):
    log = []
    pythoncom = types.SimpleNamespace(CoInitialize=lambda: log.append(("coinit",)),
                                      CoUninitialize=lambda: log.append(("couninit",)))
    client = types.SimpleNamespace(DispatchEx=lambda name: FakeExcel(log, fail_open))
    pkg = types.ModuleType("win32com")
    pkg.client = client
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", pkg)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    return log


def _encrypted_like(tmp_path):
    p = tmp_path / "drm.xlsx"
    p.write_bytes(b"\xd0\xcf\x11\xe0not-a-real-workbook")      # PK 아님 + xlrd 실패 → Excel COM 경로
    return str(p)


def test_excel_com_disables_macros_before_open(monkeypatch, tmp_path):
    log = _fake_com(monkeypatch)
    wb = prep.LogWorkbook(_encrypted_like(tmp_path))
    assert wb.mode == "암호화 파일 (엑셀 연동)" and wb.sheet_names == ["USB로그"]
    opens = [e for e in log if e[0] == "open"]
    assert len(opens) == 1 and opens[0][1] == prep.MSO_AUTOMATION_SECURITY_FORCE_DISABLE == 3
    assert opens[0][2]["ReadOnly"] is True and opens[0][2]["UpdateLinks"] == 0
    assert log.index(("set", "AutomationSecurity", 3)) < log.index(opens[0])
    assert log[-3:] == [("close", {"SaveChanges": False}), ("quit",), ("couninit",)]


def test_excel_com_cleans_up_when_open_fails(monkeypatch, tmp_path):
    log = _fake_com(monkeypatch, fail_open=True)
    with pytest.raises(RuntimeError, match="open failed"):
        prep.LogWorkbook(_encrypted_like(tmp_path))
    assert ("open", 3, {"UpdateLinks": 0, "ReadOnly": True}) in log
    assert log[-2:] == [("quit",), ("couninit",)] and not any(e[0] == "close" for e in log)


# ---------------------------------------------------------------------------
# 2. API 키는 그 주소에만
# ---------------------------------------------------------------------------
INI_URL, OTHER_URL, THIRD_URL = "https://api.ini.example/v1", "https://other.example/v1", "https://third.example/v1"
INI_KEY, TYPED_KEY = "ini-key-for-test", "typed-key-for-test"     # 가짜 값
KEY_NAME = {f"Bearer {INI_KEY}": "obi.ini 키", f"Bearer {TYPED_KEY}": "입력 키", None: "없음"}


def test_pick_api_key_is_bound_to_endpoint():
    assert ai.pick_api_key(INI_URL + "/ ", {}, INI_URL, INI_KEY) == INI_KEY           # 끝 / · 공백만 무시
    assert ai.pick_api_key(OTHER_URL, {}, INI_URL, INI_KEY) == ""
    assert ai.pick_api_key("https://api.ini.example.evil/v1", {}, INI_URL, INI_KEY) == ""
    assert ai.pick_api_key("https://api.ini.example/v1/x", {}, INI_URL, INI_KEY) == ""
    assert ai.pick_api_key(OTHER_URL, {OTHER_URL: TYPED_KEY}, INI_URL, INI_KEY) == TYPED_KEY
    assert ai.pick_api_key(THIRD_URL, {OTHER_URL: TYPED_KEY}, INI_URL, INI_KEY) == ""
    assert ai.pick_api_key("", {"": TYPED_KEY}, "", INI_KEY) == ""


def test_openai_stream_omits_authorization_without_key(monkeypatch):
    sent = {}
    monkeypatch.setattr(ai.requests, "post", lambda url, **kw: sent.update(kw) or FakeResp(STREAMS["openai"]))
    list(ai.stream_analyze(dict(ai.PROVIDER_DEFAULTS["openai"], provider="openai", api_key=" "), "p"))
    assert "Authorization" not in sent["headers"]


def test_app_sends_key_only_to_its_endpoint(temp_db, sample, _temp_config, monkeypatch):
    """화면 흐름: ① obi.ini 주소 + 빈 키 → obi.ini 키 ② 다른 주소 → 키 없음 ③ 그 주소에 직접 입력 → 입력 키
    ④ 또 다른 주소 → 키 없음 ⑤ obi.ini 주소로 돌아오면 → obi.ini 키 (입력 키는 따라오지 않음)"""
    _temp_config.write_text(_temp_config.read_text(encoding="utf-8")
                            + f"base_url = {INI_URL}\napi_key = {INI_KEY}\n", encoding="utf-8")
    calls = []

    def fake_post(url, **kw):
        calls.append((url.rsplit("/chat/completions", 1)[0], KEY_NAME.get(kw["headers"].get("Authorization"), "?")))
        return FakeResp(STREAMS["openai"])
    monkeypatch.setattr(ai.requests, "post", fake_post)

    aid = pipeline.save(sample["result"], test=True)
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    at.sidebar.selectbox(key="ai_provider").set_value("OpenAI 형식 API").run()

    def ask():
        at.button(key="new_llm_run").click().run()
        assert not at.exception, at.exception
        return calls[-1]

    assert ask() == (INI_URL, "obi.ini 키")
    at.sidebar.text_input(key="openai_base_url").set_value(OTHER_URL).run()
    assert ask() == (OTHER_URL, "없음")
    at.sidebar.text_input(key="openai_api_key").set_value(TYPED_KEY).run()
    assert ask() == (OTHER_URL, "입력 키")
    at.sidebar.text_input(key="openai_base_url").set_value(THIRD_URL).run()
    assert at.sidebar.text_input(key="openai_api_key").value == ""          # 키 칸도 비워진다
    assert ask() == (THIRD_URL, "없음")
    at.sidebar.text_input(key="openai_base_url").set_value(INI_URL + "/").run()
    assert ask() == (INI_URL, "obi.ini 키")
    at.sidebar.text_input(key="openai_base_url").set_value(OTHER_URL).run()
    assert ask() == (OTHER_URL, "입력 키")                                   # 그 주소에 입력한 키는 그 주소에서만


# ---------------------------------------------------------------------------
# 3. HTML 레이아웃의 동적 텍스트 이스케이프
# ---------------------------------------------------------------------------
def test_detail_header_and_cards_escape_dynamic_text(temp_db, sample, monkeypatch):
    r = dict(sample["result"], info=dict(sample["result"]["info"], 이름="<b>홍&길동</b>",
                                         부서="<img src=x onerror=1>", 계정="kd'\"hong"))
    aid = pipeline.save(r, test=True, sources={"이름": "<i>업로드</i>", "부서": "입력"})
    real = pipeline.derive

    def derive(tl):
        cands, lead, points = real(tl)
        cands = cands.copy()
        cands["유형"] = "<script>t</script>유형"
        cands["기간"] = "<u>기간</u>"
        return cands, "<em>요약</em> & 문장", points
    monkeypatch.setattr(pipeline, "derive", derive)
    at = app()
    at.session_state["current_id"] = aid
    at.run()
    assert not at.exception, at.exception
    html_md = " ".join(m.value for m in at.markdown if m.allow_html)
    for tag in ("<b>", "<img", "<i>업로드", "<script>", "<u>", "<em>"):
        assert tag not in html_md, tag
    for shown in ("&lt;b&gt;홍&amp;길동&lt;/b&gt;", "&lt;img src=x onerror=1&gt;", "kd&#x27;&quot;hong",
                  "&lt;i&gt;업로드&lt;/i&gt;", "&lt;script&gt;t&lt;/script&gt;유형", "&lt;em&gt;요약&lt;/em&gt; &amp; 문장"):
        assert shown in html_md, shown
    assert "&amp;amp;" not in html_md and "&amp;lt;" not in html_md         # 두 번 이스케이프하지 않음
    assert "최고 등급" in html_md and "이력 #" in html_md                    # 일반 한글·기호는 그대로


# ---------------------------------------------------------------------------
# 4. XLSX: '=...' 는 수식이 아닌 텍스트
# ---------------------------------------------------------------------------
def _cells(data_or_path):
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
    wb = load_workbook(src, data_only=False)
    return {ws.title: [c for row in ws.iter_rows() for c in row] for ws in wb.worksheets}


def _assert_no_formula(cells, must_have):
    flat = [c for cs in cells.values() for c in cs]
    assert not [c.coordinate for c in flat if c.data_type == "f"]
    for v in must_have:
        assert any(c.value == v and c.data_type == "s" for c in flat), v


def test_formulas_as_text_keeps_other_cells():
    data = prep.sheets_to_xlsx({"t": pd.DataFrame({
        "글": ["=1+1", "=HYPERLINK(\"http://x\")", "일반 글", "-3", None],
        "수": [1, -2.5, 0, 3, None],
        "날짜": pd.to_datetime(["2026-08-25 19:40:05"] * 5)})})
    cells = _cells(data)["t"]
    _assert_no_formula({"t": cells}, ["=1+1", "=HYPERLINK(\"http://x\")", "일반 글", "-3"])
    by = {c.coordinate: c for c in cells}
    assert (by["B3"].value, by["B3"].data_type) == (-2.5, "n") and by["B2"].value == 1
    assert by["C2"].is_date and str(by["C2"].value) == "2026-08-25 19:40:05"
    assert by["A6"].value is None and by["B6"].value is None


def test_report_and_history_export_store_text(temp_db, sample):
    aid = pipeline.save(sample["result"])                     # 이력 목록 내보내기는 정식 이력만
    store.update_review(aid, "예", "예", "=1+1", "검토 완료", "=2+2")
    a = store.get_analysis(aid)
    tl = a["타임라인"].copy()
    tl.loc[tl.index[0], "대상"] = "=1+1"
    a["타임라인"] = tl
    sheets = report.build_report(a)
    data = report.to_xlsx(sheets)
    cells = _cells(data)
    assert list(cells) == list(sheets)                                      # 시트 구성 그대로
    _assert_no_formula(cells, ["=1+1"])
    legacy = store.to_legacy_frame(store.list_analyses())
    _assert_no_formula(_cells(prep.sheets_to_xlsx({"이력관리": legacy})), ["=1+1", "=2+2"])


def test_cli_preprocess_output_stores_text(tmp_path):
    import openpyxl
    import sample_ddi
    src = sample_ddi.build(str(tmp_path / "in.xlsx"))
    wb = openpyxl.load_workbook(src)
    ws = wb["USB로그"]
    col = [c.value for c in ws[1]].index("File Name") + 1
    ws.cell(2, col).value = "=1+1"
    ws.cell(2, col).data_type = "s"
    wb.save(src)
    out = str(tmp_path / "out.xlsx")
    prep.run(src, out, sample_ddi.RETIRE_DATE, sample_ddi.BASE_DATE, sample_ddi.ACCOUNT)
    cells = _cells(out)
    _assert_no_formula(cells, ["=1+1"])
    assert any(c.is_date for c in cells["통합타임라인"])
