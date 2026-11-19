"""Acceptance tests: the recorded live runs of the `pr-exam` skill, checked by `pr_exam.py`.

Fixture layout the skill ships (ticket 06, Touches `skills/pr-exam/fixtures/`):
  fixtures/typo-readme/        request.json, diff.patch, exam.json   (generation on a typo-only README diff)
  fixtures/twelve-concepts/    request.json, diff.patch, exam.json   (generation on a diff with 12 concepts)
  fixtures/grading/            exam.json, answers.json, grades.json  (grading of one attempt)
`request.json` is a generation request; its `diff`, `body`, `records`, `out` and `glossary` paths are
rewritten here to a temp directory, so the fixture's own values are not trusted.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "pr_exam.py"
FIXTURES = HERE / "fixtures"


def fixture(name):
    d = FIXTURES / name
    assert d.is_dir(), f"missing recorded run {d}"
    return d


def validate(name, tmp_path):
    d = fixture(name)
    request = json.loads((d / "request.json").read_text())
    (tmp_path / "records").mkdir()
    (tmp_path / "body.md").write_text("body")
    shutil.copy(d / "diff.patch", tmp_path / "diff.patch")
    request.update(diff=str(tmp_path / "diff.patch"), body=str(tmp_path / "body.md"),
                   records=str(tmp_path / "records"), out=str(d / "exam.json"), glossary=None)
    (tmp_path / "request.json").write_text(json.dumps(request))
    r = subprocess.run([sys.executable, str(SCRIPT), "validate", "--request", str(tmp_path / "request.json"),
                        str(d / "exam.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads((d / "exam.json").read_text())


@pytest.mark.parametrize("name, count", [("typo-readme", 1), ("twelve-concepts", 8)])
def test_recorded_generation_run_validates_with_its_question_count(tmp_path, name, count):
    exam = validate(name, tmp_path)
    assert len(exam["questions"]) == count
    if name == "twelve-concepts":
        assert len(exam["concepts"]) >= 12


def test_recorded_grading_run_is_accepted_by_score(tmp_path):
    d = fixture("grading")
    r = subprocess.run([sys.executable, str(SCRIPT), "score", "--exam", str(d / "exam.json"),
                        "--answers", str(d / "answers.json"), "--grades", str(d / "grades.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    assert "passed" in json.loads(r.stdout)
