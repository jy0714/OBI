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
