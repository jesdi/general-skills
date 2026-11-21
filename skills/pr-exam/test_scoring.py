"""Acceptance tests: `pr_exam.py score --exam --answers --grades`, run as a subprocess.

Spec specs/pr-exam: requirement 6; design.md "Served projection, grades.json, result.json".
Exam: q1 text on a why concept, q2 text, q3-q5 choice (key answer 1).
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "pr_exam.py"
SHA = "a" * 40
CONCEPTS = [("c-why", "why"), ("lease-ownership", "concept"),
            ("head-sha-recheck", "architecture"), ("c-risk", "risk"), ("c-beh", "behaviour")]
TYPES = ["text", "text", "choice", "choice", "choice"]


def exam():
    qs, key = [], {}
    for i, ((slug, _), t) in enumerate(zip(CONCEPTS, TYPES), 1):
        q = {"id": f"q{i}", "concept": slug, "type": t, "prompt": f"Prompt {i}?"}
        k = {"explanation": f"Explain {i}.", "hunk": f"+hunk {i}"}
        if t == "choice":
            q["options"] = ["alpha beta", "gamma delta", "zeta eta"]
            k["answer"] = 1
        else:
            k["rubric"] = {"pass": "Names it.", "partial": "Mentions it."}
        qs.append(q)
        key[q["id"]] = k
    return {"version": 1, "mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
            "concepts": [{"id": s, "title": s, "kind": k} for s, k in CONCEPTS],
            "questions": qs, "key": key}


def score(tmp_path, right=(3, 4, 5), why="pass", other="partial", answers=None, grades=None):
    """`right`: choice questions answered correctly; the others are answered 0 (wrong)."""
    a = {"q1": "because", "q2": "text", **{f"q{i}": 1 if i in right else 0 for i in (3, 4, 5)}}
    g = {"q1": {"grade": why, "feedback": "f1"}, "q2": {"grade": other, "feedback": "f2"}}
    a = a if answers is None else answers
    g = g if grades is None else grades
    for name, data in (("exam", exam()), ("answers", a), ("grades", g)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    return subprocess.run([sys.executable, str(SCRIPT), "score", "--exam", str(tmp_path / "exam.json"),
                           "--answers", str(tmp_path / "answers.json"), "--grades", str(tmp_path / "grades.json")],
                          capture_output=True, text=True)


def ok(r):
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout)


def refused(r, *words):
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    for w in words:
        assert w in r.stderr, (w, r.stderr)


def test_pass_at_90_percent(tmp_path):
    res = ok(score(tmp_path))
    assert (res["score"], res["max"], res["passed"]) == (4.5, 5, True)


def test_pass_at_exactly_80_percent(tmp_path):
    res = ok(score(tmp_path, other="fail"))
    assert (res["score"], res["passed"]) == (4, True)


def test_fail_at_70_percent(tmp_path):
    res = ok(score(tmp_path, right=(3, 4), why="pass", other="partial"))
    assert (res["score"], res["passed"]) == (3.5, False)


def test_fail_when_why_is_partial_above_80_percent(tmp_path):
    res = ok(score(tmp_path, why="partial", other="pass"))
    assert (res["score"], res["passed"]) == (4.5, False)


def test_points_per_question(tmp_path):
    res = ok(score(tmp_path, right=(3,), why="partial", other="fail"))
    assert {q["id"]: q["points"] for q in res["questions"]} == {"q1": 0.5, "q2": 0, "q3": 1, "q4": 0, "q5": 0}


def test_full_points_carry_no_explanation(tmp_path):
    res = ok(score(tmp_path, right=(3, 5), why="pass", other="pass"))
    assert res["missed"] == ["c-risk"]
    qs = {q["id"]: q for q in res["questions"]}
    for qid in ("q1", "q2", "q3", "q5"):
        assert "explanation" not in qs[qid] and "hunk" not in qs[qid]
    assert (qs["q4"]["explanation"], qs["q4"]["hunk"]) == ("Explain 4.", "+hunk 4")


def test_two_named_misses(tmp_path):
    # q2 (lease-ownership) failed, q3 (head-sha-recheck) wrong; everything else right.
    res = ok(score(tmp_path, right=(4, 5), why="pass", other="fail"))
    assert sorted(res["missed"]) == ["head-sha-recheck", "lease-ownership"]
    qs = {q["id"]: q for q in res["questions"]}
    for qid, n in (("q2", 2), ("q3", 3)):
        assert (qs[qid]["explanation"], qs[qid]["hunk"]) == (f"Explain {n}.", f"+hunk {n}")
    for qid in ("q1", "q4", "q5"):
        assert "explanation" not in qs[qid] and "hunk" not in qs[qid]


def test_refuse_unanswered_question(tmp_path):
    a = {"q1": "x", "q2": "y", "q3": 1, "q4": 1}
    refused(score(tmp_path, answers=a), "q5")


def test_refuse_grade_for_choice_question(tmp_path):
    g = {"q1": {"grade": "pass", "feedback": "f"}, "q2": {"grade": "pass", "feedback": "f"},
         "q3": {"grade": "pass", "feedback": "f"}}
    refused(score(tmp_path, grades=g), "q3")


def test_refuse_text_question_without_grade(tmp_path):
    refused(score(tmp_path, grades={"q1": {"grade": "pass", "feedback": "f"}}), "q2")


def test_refuse_unknown_grade(tmp_path):
    g = {"q1": {"grade": "excellent", "feedback": "f"}, "q2": {"grade": "pass", "feedback": "f"}}
    refused(score(tmp_path, grades=g), "q1", "excellent")
