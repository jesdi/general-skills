"""Acceptance tests: the recorded live runs of the `pr-exam` skill, checked by `pr_exam.py`.

Fixture layout the skill ships (ticket 06, Touches `skills/pr-exam/fixtures/`), one live run per mode:
  fixtures/typo-readme/        first: a typo-only README diff
  fixtures/twelve-concepts/    first: a diff with 12 concepts
  fixtures/followup/           followup: the three concepts the attempt of fixtures/grading missed
  fixtures/delta-new-concept/  delta: a push after the pass that adds a retry budget
  fixtures/delta-nothing-new/  delta: a push after the pass that only rewords and renames
      each: request.json, diff.patch, exam.json; the later modes also records/, a delta passed.diff
  fixtures/grading/            request.json, questions.json, answers.json -> grades.json (one grading session),
                               and attempt.json: every answer of the attempt, which the host scores
  fixtures/tip-choice/         a recorded tip on a `choice` question of the twelve-concept exam
  fixtures/tip-text/           a recorded tip 1 on a `text` question of the same exam
  fixtures/chat-answer/        a recorded `question` turn on a `choice` question: "what is the answer?"
      each: turn.json, prompt.md (= `context` on turn.json, exam, diff and body of fixtures/twelve-concepts),
      reply.md (the model's reply to prompt.md)
`request.json` is the session's request; its `diff`, `body`, `records`, `out`, `passed_diff` and `glossary`
paths are rewritten here to a temp directory, so the fixture's own values are not trusted.
"""
import json
import re
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
    (tmp_path / "body.md").write_text("body")
    shutil.copy(d / "diff.patch", tmp_path / "diff.patch")
    if (d / "records").is_dir():
        shutil.copytree(d / "records", tmp_path / "records")
    else:
        (tmp_path / "records").mkdir()
    request.update(diff=str(tmp_path / "diff.patch"), body=str(tmp_path / "body.md"),
                   records=str(tmp_path / "records"), out=str(d / "exam.json"), glossary=None)
    if "passed_diff" in request:
        request["passed_diff"] = str(d / "passed.diff")
    (tmp_path / "request.json").write_text(json.dumps(request))
    r = subprocess.run([sys.executable, str(SCRIPT), "validate", "--request", str(tmp_path / "request.json"),
                        str(d / "exam.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    return request, json.loads((d / "exam.json").read_text())


@pytest.mark.parametrize("name, mode, count", [
    ("typo-readme", "first", 1), ("twelve-concepts", "first", 8), ("followup", "followup", 3),
    ("delta-new-concept", "delta", 2), ("delta-nothing-new", "delta", 0)])
def test_recorded_generation_run_validates_with_its_question_count(tmp_path, name, mode, count):
    request, exam = validate(name, tmp_path)
    assert exam["mode"] == request["mode"] == mode
    assert len(exam["questions"]) == count
    if name == "twelve-concepts":
        assert len(exam["concepts"]) >= 12


def test_the_recorded_followup_asks_each_missed_concept_once_with_a_new_prompt(tmp_path):
    request, exam = validate("followup", tmp_path)
    assert sorted(q["concept"] for q in exam["questions"]) == sorted(request["only_concepts"])
    assert not {q["prompt"] for q in exam["questions"]} & set(request["prior_prompts"])


def test_the_recorded_delta_with_a_new_concept_lists_nothing_already_passed(tmp_path):
    request, exam = validate("delta-new-concept", tmp_path)
    assert exam["concepts"] and not {c["id"] for c in exam["concepts"]} & set(request["prior_concepts"])


def test_recorded_grading_run_grades_every_text_question_and_is_accepted_by_score():
    d, exam = fixture("grading"), fixture("twelve-concepts") / "exam.json"
    texts = {q["id"] for q in json.loads(exam.read_text())["questions"] if q["type"] == "text"}
    asked = json.loads((d / "questions.json").read_text())
    assert {q["id"] for q in asked} == set(json.loads((d / "answers.json").read_text())) == texts
    assert set(json.loads((d / "grades.json").read_text())) == texts
    r = subprocess.run([sys.executable, str(SCRIPT), "score", "--exam", str(exam),
                        "--answers", str(d / "attempt.json"), "--grades", str(d / "grades.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    result = json.loads(r.stdout)
    assert result["passed"] is False  # the failed attempt the recorded followup answers
    assert result["missed"] == json.loads((fixture("followup") / "request.json").read_text())["only_concepts"]


TURNS = ["tip-choice", "tip-text", "chat-answer"]


def turn_run(name):
    d, base = fixture(name), fixture("twelve-concepts")
    for f in ("turn.json", "prompt.md", "reply.md"):
        assert (d / f).is_file() and (d / f).read_text().strip(), f"{d / f} missing or empty"
    turn = json.loads((d / "turn.json").read_text())
    exam = json.loads((base / "exam.json").read_text())
    question = next(q for q in exam["questions"] if q["id"] == turn["question"])
    return d, base, turn, exam, question, (d / "reply.md").read_text()


def test_recorded_tip_on_a_choice_question_gives_no_option_away():
    _, _, turn, _, q, reply = turn_run("tip-choice")
    assert turn["kind"] == "tip" and q["type"] == "choice"
    assert q["options"] and not [o for o in q["options"] if o.lower() in reply.lower()]


def test_recorded_first_tip_on_a_text_question_names_a_file_of_the_diff():
    _, base, turn, _, q, reply = turn_run("tip-text")
    assert turn["kind"] == "tip" and turn["tip"] == 1 and q["type"] == "text"
    paths = set(re.findall(r"^diff --git a/(\S+) b/", (base / "diff.patch").read_text(), re.M))
    assert paths and any(p in reply for p in paths), (paths, reply)


def test_recorded_chat_turn_asking_for_the_answer_gives_the_option_at_the_keys_answer_index():
    _, _, turn, exam, q, reply = turn_run("chat-answer")
    assert turn["kind"] == "question" and turn["message"] == "what is the answer?" and q["type"] == "choice"
    assert q["options"][exam["key"][q["id"]]["answer"]].lower() in reply.lower()


@pytest.mark.parametrize("name", TURNS)
def test_context_on_a_recorded_turn_reproduces_its_stored_prompt(name):
    d, base, *_ = turn_run(name)
    r = subprocess.run([sys.executable, str(SCRIPT), "context", "--exam", str(base / "exam.json"),
                        "--diff", str(base / "diff.patch"), "--body", str(base / "body.md"),
                        "--turn", str(d / "turn.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    assert r.stdout == (d / "prompt.md").read_text()
