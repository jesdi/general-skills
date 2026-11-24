"""Acceptance tests: `pr_exam.py score --exam --answers --grades`, run as a subprocess.

Spec specs/pr-exam: requirement 6; design.md "Served projection, grades.json, result.json".
Exam: q1 text on a why concept, q2 text, q3-q5 choice (key answer 1).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


# --- spec specs/pr-exam-assist: requirements 5-8 (bypassed and assisted questions) ---
# Optional --flags reads {"bypassed": [qid, ...], "assisted": [qid, ...]}.
# Omitted keys mean no flags; output questions use help="bypassed" or "assisted".
# Exam: q1-q3 choice (key answer 1), q4 text on the why concept, q5 and q6 text.
ASSIST_CONCEPTS = [("merge-lease", "concept"), ("retry-budget", "concept"), ("head-recheck", "architecture"),
                   ("lease-owner", "why"), ("fencing-token", "concept"), ("stale-lease", "concept")]


def assist_exam(n=6):
    qs, key = [], {}
    for i, (slug, _) in enumerate(ASSIST_CONCEPTS[:n], 1):
        t = "choice" if i <= 3 else "text"
        q = {"id": f"q{i}", "concept": slug, "type": t, "prompt": f"Prompt {i}?"}
        k = {"explanation": f"Explain {i}.", "hunk": f"+hunk {i}"}
        if t == "choice":
            q["options"] = ["alpha beta", "gamma delta", "zeta eta"]
            k["answer"] = 1
        else:
            k["rubric"] = {"pass": "Names it.", "partial": "Mentions it."}
        qs.append(q)
        key[q["id"]] = k
    concepts = [{"id": s, "title": s, "kind": k} for s, k in ASSIST_CONCEPTS[:n]]
    if n == 1:
        concepts = [{"id": "readme-typo", "title": "README typo", "kind": "behaviour"}]
        qs[0].update(concept="readme-typo", prompt="Which word did the README correct?",
                     options=["merge lease", "retry budget", "fencing token"])
        key["q1"].update(explanation="The README now spells retry budget correctly.",
                         hunk="-retry buget\n+retry budget")
    return {"version": 1, "mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
            "concepts": concepts, "questions": qs, "key": key}


def flagged(tmp_path, flags, wrong=(), grades=None, skip=(), n=6):
    """Everything full points except `wrong` choice ids (answered 0) and `grades` overrides.
    Questions in `skip` get neither an answer nor a grade."""
    g = {"q4": "pass", "q5": "pass", "q6": "pass", **(grades or {})}
    a = {f"q{i}": (0 if f"q{i}" in wrong else 1) for i in (1, 2, 3)}
    a.update({"q4": "t", "q5": "t", "q6": "t"})
    ans = {k: v for k, v in a.items() if k in {f"q{i}" for i in range(1, n + 1)} and k not in skip}
    grs = {k: {"grade": v, "feedback": "f"} for k, v in g.items()
           if k in {f"q{i}" for i in range(1, n + 1)} and k not in skip}
    files = {"exam": assist_exam(n), "answers": ans, "grades": grs}
    if flags is not None:
        files["flags"] = flags
    for name, data in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    cmd = [sys.executable, str(SCRIPT), "score", "--exam", str(tmp_path / "exam.json"),
           "--answers", str(tmp_path / "answers.json"), "--grades", str(tmp_path / "grades.json")]
    if flags is not None:
        cmd += ["--flags", str(tmp_path / "flags.json")]
    return subprocess.run(cmd, capture_output=True, text=True)


def by_id(res):
    return {q["id"]: q for q in res["questions"]}


def test_bypassed_question_leaves_the_score(tmp_path):
    res = ok(flagged(tmp_path, {"bypassed": ["q6"]}, skip=("q6",)))
    assert (res["score"], res["max"], res["passed"]) == (5, 5, True)
    assert by_id(res)["q6"]["help"] == "bypassed"
    assert "stale-lease" not in res["missed"]
    assert "points" not in by_id(res)["q6"]


def test_exactly_80_percent_of_not_bypassed_passes(tmp_path):
    res = ok(flagged(tmp_path, {"bypassed": ["q6"]}, wrong=("q1",), skip=("q6",)))
    assert (res["score"], res["max"], res["passed"]) == (4, 5, True)


def test_under_80_percent_of_not_bypassed_fails(tmp_path):
    res = ok(flagged(tmp_path, {"bypassed": ["q6"]}, wrong=("q1",), grades={"q5": "partial"}, skip=("q6",)))
    assert (res["score"], res["max"], res["passed"]) == (3.5, 5, False)
    assert sorted(res["missed"]) == ["fencing-token", "merge-lease"]


def test_bypassed_why_question_does_not_fail_the_attempt(tmp_path):
    res = ok(flagged(tmp_path, {"bypassed": ["q4"]}, skip=("q4",)))
    assert (res["score"], res["max"], res["passed"]) == (5, 5, True)
    assert "lease-owner" not in res["missed"]


def test_two_bypasses_are_refused(tmp_path):
    r = flagged(tmp_path, {"bypassed": ["q2", "q6"]})
    refused(r, "q2", "q6")
    assert r.stdout == ""


def test_answer_to_bypassed_question_is_ignored(tmp_path):
    res = ok(flagged(tmp_path, {"bypassed": ["q2"]}, wrong=("q2",)))
    q2 = by_id(res)["q2"]
    assert (res["score"], res["max"]) == (5, 5)
    assert q2["help"] == "bypassed" and "points" not in q2


def test_bypassed_question_carries_explanation_and_hunk(tmp_path):
    q6 = by_id(ok(flagged(tmp_path, {"bypassed": ["q6"]}, skip=("q6",))))["q6"]
    assert (q6["explanation"], q6["hunk"]) == ("Explain 6.", "+hunk 6")


def test_assisted_question_scores_as_usual(tmp_path):
    res = ok(flagged(tmp_path, {"assisted": ["q2"]}))
    q2 = by_id(res)["q2"]
    assert (q2["points"], q2["help"]) == (1, "assisted")
    assert (res["score"], res["max"]) == (6, 6)


def test_bypass_outranks_assisted(tmp_path):
    q6 = by_id(ok(flagged(tmp_path, {"bypassed": ["q6"], "assisted": ["q6"]}, skip=("q6",))))["q6"]
    assert q6["help"] == "bypassed"


def test_bypassing_the_only_question_passes(tmp_path):
    res = ok(flagged(tmp_path, {"bypassed": ["q1"]}, skip=("q1",), n=1))
    assert (res["score"], res["max"], res["passed"]) == (0, 0, True)


@pytest.mark.parametrize("flag", ["assisted", "bypassed"])
def test_flags_with_unknown_question_are_refused(tmp_path, flag):
    result = flagged(tmp_path, {flag: ["q9"]})
    refused(result, "q9")
    assert result.stdout == ""


def test_flags_with_unknown_key_are_refused(tmp_path):
    result = flagged(tmp_path, {"skipped": ["q1"]})
    refused(result, "skipped")
    assert result.stdout == ""



@pytest.mark.parametrize("grades,bypassed", [
    ({"q6": "fail"}, "q6"),
    ({"q6": "excellent"}, "q6"),
    ({"q2": "pass"}, "q2"),
])
def test_grade_for_bypassed_question_is_ignored(tmp_path, grades, bypassed):
    res = ok(flagged(tmp_path, {"bypassed": [bypassed]}, grades=grades))
    assert (res["score"], res["max"], res["passed"]) == (5, 5, True)
    assert "stale-lease" not in res["missed"]
    assert by_id(res)[bypassed]["help"] == "bypassed"
    assert "points" not in by_id(res)[bypassed]
    assert res["missed"] == []


def test_flags_are_never_inferred_without_the_flags_file(tmp_path):
    """A follow-up exam reuses ids: stale flag data elsewhere must not mark q6 bypassed."""
    baseline = flagged(tmp_path, None)
    ok(baseline)
    exam_data = json.loads((tmp_path / "exam.json").read_text())
    exam_data["mode"] = "followup"
    exam_data["flags"] = {"bypassed": ["q6"], "assisted": ["q2"]}
    exam_data["questions"][5]["help"] = "bypassed"
    (tmp_path / "exam.json").write_text(json.dumps(exam_data))
    (tmp_path / "flags.json").write_text(json.dumps({"bypassed": ["q6"], "assisted": ["q2"]}))
    r = subprocess.run([sys.executable, str(SCRIPT), "score", "--exam", str(tmp_path / "exam.json"),
                        "--answers", str(tmp_path / "answers.json"), "--grades", str(tmp_path / "grades.json")],
                       capture_output=True, text=True)
    res = ok(r)
    assert (res["score"], res["max"]) == (6, 6)
    assert all("help" not in q for q in res["questions"])
    assert r.stdout == baseline.stdout
