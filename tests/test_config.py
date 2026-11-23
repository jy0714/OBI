"""obi.ini 설정 읽기 · 비밀번호 확인"""
import obi_config as config


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return config.load()


def test_loads_test_config(_temp_config):
    c = config.load()
    assert c["오류"] == "" and c["password"] == "1234"
    assert c["ollama_models"] == ["gemma4:31b", "qwen3.8:27b"]
    assert c["openai_model"] == "gpt-test" and c["openai_base_url"] == config.DEFAULTS["openai_base_url"]
    assert config.check_password(c, "1234") and config.check_password(c, " 1234 ")
    assert not config.check_password(c, "1235") and not config.check_password(c, "")


def test_missing_file_fails_closed(_temp_config):
    _temp_config.unlink()
    c = config.load()
    assert "없습니다" in c["오류"] and not config.check_password(c, "")


def test_password_must_be_four_digits(_temp_config):
    for bad in ("", "123", "12345", "abcd", "12 4"):
        c = _write(_temp_config, f"[auth]\npassword = {bad}\n")
        assert c["오류"], bad
        assert not config.check_password(c, bad)


def test_bom_and_empty_models(_temp_config):
    c = _write(_temp_config, "﻿[auth]\npassword = 0007\n[ollama]\nmodels =\n")
    assert c["오류"] == "" and c["ollama_models"] == []      # 비우면 설치된 전체 허용
    assert config.check_password(c, "0007")


def test_broken_file(_temp_config):
    c = _write(_temp_config, "password = 1234\n")             # 섹션 없음
    assert c["오류"]


def test_example_file_is_valid_but_has_no_password(monkeypatch):
    monkeypatch.setenv("OBI_CONFIG_PATH", config.EXAMPLE_PATH)
    c = config.load()
    assert "4자리" in c["오류"]                                 # 견본 그대로는 열리지 않음
    assert c["ollama_models"] == ["gemma4:31b", "qwen3.8:27b"]
