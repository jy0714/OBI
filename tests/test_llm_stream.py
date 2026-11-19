"""LLM 스트리밍 파서 테스트 — 네트워크 없이 가짜 응답으로 프로바이더별 파싱·오류 처리를 고정한다."""
import json

import pandas as pd
import pytest
import requests

import obi_ai as ai

ARGS = ({"이름": "t"}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


class FakeResp:
    def __init__(self, lines, status=200):
        self.lines, self.status_code = lines, status

    def iter_lines(self):
        yield from (x.encode() if isinstance(x, str) else x for x in self.lines)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _patch(monkeypatch, resp=None, exc=None):
    sent = {}

    def fake_post(url, **kw):
        sent.update(url=url, **kw)
        if exc:
            raise exc
        return resp
    monkeypatch.setattr(ai.requests, "post", fake_post)
    return sent


STREAMS = {
    "ollama": [json.dumps({"message": {"content": "안녕"}}), "", "깨진줄",
               json.dumps({"message": {"content": "하세요"}, "done": True}),
               json.dumps({"message": {"content": "무시"}})],
    "openai": ['data: {"choices":[{"delta":{"content":"안녕"}}]}', "",
               'data: {"choices":[{"delta":{}}]}', 'data: {"choices":[{"delta":{"content":"하세요"}}]}',
               "data: [DONE]", 'data: {"choices":[{"delta":{"content":"무시"}}]}'],
    "anthropic": ["event: message_start", 'data: {"type":"message_start"}',
                  'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"안녕"}}',
                  'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"하세요"}}',
                  'data: {"type":"message_stop"}',
                  'data: {"type":"content_block_delta","delta":{"text":"무시"}}'],
}
URLS = {"ollama": "/api/chat", "openai": "/chat/completions", "anthropic": "/v1/messages"}


@pytest.mark.parametrize("provider", list(STREAMS))
def test_stream_parsing(monkeypatch, provider):
    sent = _patch(monkeypatch, FakeResp(STREAMS[provider]))
    cfg = dict(ai.PROVIDER_DEFAULTS[provider], provider=provider, api_key="k")
    assert "".join(ai.stream_analyze(cfg, *ARGS)) == "안녕하세요"
    assert sent["url"].endswith(URLS[provider])
    body = sent["json"]
    assert body["stream"] is True and body["model"] == cfg["model"]
    if provider == "anthropic":
        assert body["system"] and all(m["role"] != "system" for m in body["messages"])
        assert sent["headers"]["x-api-key"] == "k"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_auth_error(monkeypatch, provider):
    _patch(monkeypatch, FakeResp([], status=401))
    with pytest.raises(RuntimeError, match="API 키"):
        list(ai.stream_analyze(dict(ai.PROVIDER_DEFAULTS[provider], provider=provider), *ARGS))


@pytest.mark.parametrize("provider", list(STREAMS))
def test_connection_error(monkeypatch, provider):
    _patch(monkeypatch, exc=requests.exceptions.ConnectionError("down"))
    with pytest.raises(ConnectionError):
        list(ai.stream_analyze(dict(ai.PROVIDER_DEFAULTS[provider], provider=provider), *ARGS))


def test_unknown_provider():
    with pytest.raises(ValueError):
        list(ai.stream_analyze({"provider": "x"}, *ARGS))
