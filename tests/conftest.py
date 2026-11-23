import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE, os.path.join(HERE, "fixtures")]

import harness  # noqa: E402


@pytest.fixture(scope="session")
def sample():
    """합성 샘플을 앱과 같은 경로로 한 번 실행한 원본 결과 (DataFrame 등)"""
    return harness.execute_sample()


@pytest.fixture(scope="session")
def rendered(sample):
    """스냅숏 문자열 (golden 비교용)"""
    out = harness.render(sample)
    harness.save(out, harness.OUT_DIR)      # 실패 시 tests/_out/ 에서 전체 결과 확인
    return out


@pytest.fixture(autouse=True)
def _temp_files_dir(tmp_path, monkeypatch):
    """서약서 저장 폴더는 항상 임시 폴더 (실제 ./data/pledges 를 건드리지 않음)"""
    monkeypatch.setenv("OBI_FILES_DIR", str(tmp_path / "pledges"))


TEST_PASSWORD = "1234"


@pytest.fixture(autouse=True)
def _temp_config(tmp_path, monkeypatch):
    """설정 파일은 항상 임시 obi.ini (로컬 obi.ini 의 비밀번호·키를 쓰지 않음)"""
    path = tmp_path / "obi.ini"
    path.write_text("\n".join([
        "[auth]", f"password = {TEST_PASSWORD}",
        "[ollama]", "base_url = http://127.0.0.1:9", "models = gemma4:31b, qwen3.8:27b",
        "[openai]", "model = gpt-test", ""]), encoding="utf-8")
    monkeypatch.setenv("OBI_CONFIG_PATH", str(path))
    return path
