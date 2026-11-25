"""Acceptance tests: `pr_exam.py context --exam --diff --body --turn`, run as a subprocess.

Spec specs/pr-exam-assist: requirements 1-4, scenarios "a tip prompt ..." to "a draft on the general chat".
turn.json shape: design.md (kind, question, tip, tips, draft, submitted, points, feedback, thread, message).
Exam: q1-q3 choice, q4-q6 text.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "pr_exam.py"
DIFF = "diff --git a/src/lease.py b/src/lease.py\n+token = token + 1\n"
BODY = "PR body: make the lease token monotonic."
CONCEPTS = [("merge-lease", "concept"), ("retry-budget", "risk"), ("head-recheck", "architecture"),
            ("lease-owner", "why"), ("fencing-token", "concept"), ("stale-lease", "behaviour")]


def exam():
    qs, key = [], {}
    for i, (slug, _) in enumerate(CONCEPTS, 1):
        q = {"id": f"q{i}", "concept": slug, "type": "choice" if i <= 3 else "text", "prompt": f"Prompt-{i} about {slug}?"}
        k = {"explanation": f"Explanation-{i} of {slug}.", "hunk": f"+hunk-{i} src/lease.py"}
        if i <= 3:
            q["options"] = [f"opt-{i}-alpha", f"opt-{i}-beta", f"opt-{i}-gamma"]
            k["answer"] = 1
        else:
            k["rubric"] = {"pass": f"Rubric-pass-{i}.", "partial": f"Rubric-partial-{i}."}
        qs.append(q)
        key[q["id"]] = k
    return {"version": 1, "mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": "a" * 40,
            "concepts": [{"id": s, "title": s, "kind": k} for s, k in CONCEPTS], "questions": qs, "key": key}


def context(tmp_path, turn, diff=DIFF, script=SCRIPT, exam_doc=None):
    files = {"exam.json": json.dumps(exam_doc or exam()), "diff.patch": diff, "body.md": BODY, "turn.json": json.dumps(turn)}
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return subprocess.run([sys.executable, str(script), "context", "--exam", str(tmp_path / "exam.json"),
                           "--diff", str(tmp_path / "diff.patch"), "--body", str(tmp_path / "body.md"),
                           "--turn", str(tmp_path / "turn.json")], capture_output=True, text=True)


def ok(r):
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    return r.stdout


def refused(r, *words):
    assert r.returncode not in (0, 2), (r.returncode, r.stdout, r.stderr)  # 2 is argparse: no such command
    for w in words:
        assert w in r.stderr, (w, r.stderr)


def section(name):
    text = (HERE / "SKILL.md").read_text()
    m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, f"SKILL.md has no '## {name}' section"
    return m.group(1).strip()


def has(prompt, *parts):
    for p in parts:
        assert p in prompt, (p, prompt)


def tail(prompt):
    return prompt[prompt.index(BODY) + len(BODY):]


def tip(n, draft, tips=()):
    return {"kind": "tip", "question": "q5", "tip": n, "draft": draft, "tips": list(tips)}


def test_tip_prompt_carries_rules_draft_number_and_earlier_tips(tmp_path):
    p = ok(context(tmp_path, tip(2, "the token only grows", ["Look at src/lease.py, the token line."])))
    q, k = exam()["questions"][4], exam()["key"]["q5"]
    has(p, DIFF, BODY, q["prompt"], k["rubric"]["pass"], k["rubric"]["partial"], k["explanation"], k["hunk"],
        "the token only grows", "2", "Look at src/lease.py, the token line.")
    has(p, section("Tips"))


def test_tip_four_is_refused(tmp_path):
    r = context(tmp_path, tip(4, ""))
    refused(r, "4")
    assert re.search(r"1\s*(-|–|to)\s*3", r.stderr), r.stderr


def test_unknown_question_is_refused(tmp_path):
    refused(context(tmp_path, {"kind": "question", "question": "q9", "draft": "", "thread": [], "message": "hi"}), "q9")


def test_question_thread_sees_draft_key_and_thread(tmp_path):
    thread = [{"role": "user", "text": "earlier question?"}, {"role": "assistant", "text": "earlier answer."}]
    p = ok(context(tmp_path, {"kind": "question", "question": "q2", "draft": "option 1", "thread": thread,
                              "message": "is this right?"}))
    q = exam()["questions"][1]
    has(p, section("Chat"), DIFF, BODY, q["prompt"], *q["options"], "Explanation-2", "+hunk-2", "answer",
        "option 1", "earlier question?", "earlier answer.", "is this right?")


def graded_turn(**extra):
    return {"kind": "question", "question": "q5", "draft": "", "thread": [], "message": "why was I wrong?",
            "submitted": "the token is random", **extra}


def test_graded_thread_sees_answer_points_and_feedback(tmp_path):
    p = ok(context(tmp_path, graded_turn(points=0, feedback="misses that the token only grows")))
    has(p, "the token is random", "misses that the token only grows")
    assert re.search(r"(?i)points?\W{0,6}0\b|\b0\s*points?", tail(p)), tail(p)


def test_grading_thread_sees_submitted_answer_only(tmp_path):
    p = ok(context(tmp_path, graded_turn()))
    has(p, "the token is random")
    assert "point" not in tail(p).lower() and "feedback" not in tail(p).lower(), tail(p)


def test_general_turn_sees_every_question_and_no_draft(tmp_path):
    p = ok(context(tmp_path, {"kind": "general", "thread": [], "message": "what is the riskiest change?"}))
    has(p, section("Chat"), DIFF, BODY, "what is the riskiest change?")
    for i in range(1, 7):
        has(p, f"Prompt-{i} ", f"Explanation-{i} ")
    has(p, "opt-1-beta")
    assert "draft" not in tail(p).lower(), tail(p)


def test_general_turn_with_draft_is_refused(tmp_path):
    refused(context(tmp_path, {"kind": "general", "draft": "x", "thread": [], "message": "hi"}), "draft", "general")


def test_missing_section_is_refused_naming_heading(tmp_path):
    shutil.copy(SCRIPT, tmp_path / "pr_exam.py")
    (tmp_path / "SKILL.md").write_text("# pr-exam\n\n## Chat\nAnswer freely.\n")
    refused(context(tmp_path, tip(1, ""), script=tmp_path / "pr_exam.py"), "Tips")


def test_turns_of_a_kind_share_their_opening(tmp_path):
    a = ok(context(tmp_path, tip(1, "draft one")))
    b = ok(context(tmp_path, tip(2, "another draft", ["first tip text"])))
    end = a.index(BODY) + len(BODY)
    assert a[:end] == b[:end]
    assert a.index(section("Tips")) < a.index(DIFF) < a.index(BODY)
    assert a != b


def test_oversized_prompt_is_refused_with_length_and_limit(tmp_path):
    r = context(tmp_path, tip(1, ""), diff="x" * 450_000)
    refused(r, "limit")
    assert re.search(r"\b(450,?\d{3}|45\d,?\d{3}|4[6-9]\d,?\d{3})\b", r.stderr), r.stderr
    assert re.search(r"400,?000", r.stderr), r.stderr


def test_failed_attempt_turn_uses_the_attempt_exam_not_the_followup(tmp_path):
    attempt, followup = exam(), exam()
    attempt["questions"][4]["prompt"] = "ATTEMPT-prompt q5"
    attempt["key"]["q5"].update(explanation="ATTEMPT-explanation q5", hunk="+ATTEMPT-hunk")
    attempt["key"]["q5"]["rubric"] = {"pass": "ATTEMPT-pass", "partial": "ATTEMPT-partial"}
    followup["mode"] = "followup"
    followup["questions"][4]["prompt"] = "FOLLOWUP-prompt q5"
    followup["key"]["q5"].update(explanation="FOLLOWUP-explanation q5", hunk="+FOLLOWUP-hunk")
    followup["key"]["q5"]["rubric"] = {"pass": "FOLLOWUP-pass", "partial": "FOLLOWUP-partial"}
    (tmp_path / "followup.json").write_text(json.dumps(followup))
    (tmp_path / "exam.followup.json").write_text(json.dumps(followup))
    turn = graded_turn(points=0, feedback="ATTEMPT-feedback")
    p = ok(context(tmp_path, turn, exam_doc=attempt))
    has(p, "ATTEMPT-prompt q5", "ATTEMPT-explanation q5", "+ATTEMPT-hunk", "ATTEMPT-pass", "ATTEMPT-partial",
        "the token is random", "ATTEMPT-feedback")
    assert "FOLLOWUP" not in p, p
    g = ok(context(tmp_path, {"kind": "general", "thread": [], "message": "m"}, exam_doc=attempt))
    assert "FOLLOWUP" not in g and "ATTEMPT-prompt q5" in g, g
