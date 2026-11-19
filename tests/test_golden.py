"""골든 비교: 같은 입력(합성 샘플) → tests/golden/sample/ 과 글자 하나까지 같은 결과.

실패하면 diff 가 출력된다. 의도한 변경이면 `python tests/harness.py update` 후
`git diff tests/golden` 으로 바뀐 내용을 검토하고 코드와 함께 커밋한다.
"""
import os
import subprocess
import sys

import pytest

import harness


@pytest.mark.parametrize("artifact", harness.ARTIFACTS)
def test_matches_golden(rendered, artifact):
    golden = harness.load(harness.GOLDEN_DIR)
    assert artifact in golden, f"골든 없음: python tests/harness.py update 로 생성"
    report = harness.diff({artifact: golden[artifact]}, {artifact: rendered[artifact]})
    assert not report, f"\n{report.get(artifact)}\n\n의도한 변경이면: python tests/harness.py update"


def test_deterministic_across_processes():
    """해시 시드가 달라도(=다른 프로세스·다른 날 실행) 결과가 같아야 한다.
    set 순회 순서에 의존하는 코드가 생기면 여기서 잡힌다."""
    code = ("import sys, hashlib; sys.path[:0]=['tests','tests/fixtures']; import harness;"
            "r=harness.run_sample(); print(hashlib.sha256(''.join(r[k] for k in harness.ARTIFACTS)"
            ".encode()).hexdigest())")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    digests = set()
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                             capture_output=True, text=True, encoding="utf-8", check=True)
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1, "PYTHONHASHSEED 에 따라 결과가 달라짐 (set/dict 순서 의존 코드 확인)"
