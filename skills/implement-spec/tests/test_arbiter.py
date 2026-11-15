"""The arbiter: when the driver's own retries are spent on a finding the scoped review leaves open, one read-only
agent on another provider than the implementer rules `accept` or `follow-up` on it, and the PR opens: draft when a
critical finding stays open, ready otherwise. `--ask` (IMPLEMENT_SPEC_ASK=1, which conftest pins) asks the operator
instead.

In process, on the rig of test_scoped."""
import json

from test_final import final, fixer, fn
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_lenses import ASTRA, MODELS, OPUS, rig_of
from test_review import finding
from test_scoped import ATOMIC
from test_stops import UNATTENDED, decisions, stage
from test_tidy import tools  # noqa: F401  (tools is an autouse fixture)
from test_walk import plan

Q = "final-finding-open-1"
LEAK = finding("token logged in clear", severity="critical", file="auth.py", line=7)
ARBITER = {**MODELS, "arbiter": [OPUS, ASTRA]}  # the implementer is on anthropic, so the arbiter runs on ASTRA


def ruled(*rulings):
    """An arbiter step: one (question, ruling) pair per open question."""
    return {"report": {"status": "DONE", "files": [], "concerns": [], "rulings": [
        {"question": question, "ruling": ruling, "reason": f"why {ruling}", "cost": f"cost of {ruling}"}
        for question, ruling in rulings]}}


def open_after_batch(tmp_path, found=ATOMIC, arbiter=(), *extra):
    """The scoped review after the one fix batch leaves `found` open, with no retry left for the driver."""
    rig = rig_of(tmp_path, models=ARBITER)
    steps = plan(**{"final/final-spec": [final(found)], "final/final-reviewer": [final(found)],
                    "final/fixer": [fixer(fn("02"), "fix it")], "final/arbiter": list(arbiter)})
    return rig.run(steps, "--no-ask", "--retries", "0", *UNATTENDED, *extra), steps


def gh(rig, verb):
    rows = [json.loads(line) for line in rig.gh_log.read_text().splitlines()] if rig.gh_log.exists() else []
    return [row for row in rows if row["argv"][:2] == verb]


def test_the_arbiter_accepts_an_important_finding_and_the_pr_opens_ready_with_its_ruling(tmp_path):
    rig, _ = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "accept"))])
    assert rig.code == 0 and rig.pushes(), rig.out
    (arbiter,) = rig.final_calls("arbiter")
    assert Q in arbiter["prompt"] and "findings-1.json" in arbiter["prompt"]
    assert rig.led(f"End of run: arbiter ran on {ASTRA}")
    (pr,) = rig.prs()
    assert "--draft" not in pr["argv"]
    line = ("Ruling: final ships with the finding open (accept): ledger write not atomic (ledger.py:12). Ruled by "
            "the arbiter, not the operator: why accept. Cost if wrong: cost of accept.")
    assert f"- {line}" in pr["body"] and line in (rig.repo / ".agent/implement/summary.md").read_text()
    assert stage(rig) == {"stage": "implement", "status": "done", "draft": False, "decisions": 1}
    assert len(rig.final_calls("fixer")) == 1, "an accepted finding gets no fix batch"


def test_an_accepted_critical_finding_opens_the_pr_as_draft(tmp_path):
    rig, _ = open_after_batch(tmp_path, LEAK, [ruled(("final-finding-open-1", "accept"))])
    assert rig.code == 0, rig.out
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and stage(rig)["draft"] is True
    assert rig.led("The PR of feat/csv into main is open as a draft: a critical or must-fix finding is open")


def test_a_follow_up_is_filed_as_an_issue_before_the_pr_and_the_pr_body_links_it(tmp_path):
    rig, steps = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "follow-up"))])
    assert rig.code == 0, rig.out
    (issue,) = gh(rig, ["issue", "create"])
    assert issue["argv"][2:4] == ["--title", "ledger write not atomic (ledger.py:12)"]
    assert "feat/csv" in issue["body"] and "why follow-up" in issue["body"]
    (pr,) = rig.prs()
    assert "(follow-up): ledger write not atomic (ledger.py:12)." in pr["body"]
    assert "Follow-up issue: https://github.example/pull/1" in pr["body"]
    rig.run(steps, "--no-ask", *UNATTENDED)
    assert len(gh(rig, ["issue", "create"])) == 1, "a rerun files no second issue"


def test_a_local_run_without_push_files_no_issue(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-spec": [final(ATOMIC)], "final/final-reviewer": [final(ATOMIC)],
                    "final/fixer": [fixer(fn("02"), "fix it")], "final/arbiter": [ruled((Q, "follow-up"))]}),
            "--no-ask")
    assert rig.code == 0 and not gh(rig, ["issue", "create"]) and not rig.prs(), rig.out
    assert "(follow-up)" in (rig.repo / ".agent/implement/summary.md").read_text()


def test_the_arbiter_rules_only_after_the_retries_of_the_driver(tmp_path):
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-spec": [final(ATOMIC)], "final/final-reviewer": [final(ATOMIC), final(ATOMIC)],
                    "final/fixer": [fixer(fn("02"), "fix it"), fixer(fn("02"), "fix it again")],
                    "final/arbiter": [ruled(("final-finding-open-2", "accept"))]})
    rig.run(steps, "--no-ask", "--retries", "1", *UNATTENDED)
    assert rig.code == 0, rig.out
    assert len(rig.final_calls("fixer")) == 2 and len(rig.final_calls("arbiter")) == 1
    assert rig.led("Ruling: final runs again (retry)") and rig.led("Ruled by the arbiter")


def test_an_arbiter_that_fails_twice_leaves_the_question_to_the_operator(tmp_path):
    rig, _ = open_after_batch(tmp_path, ATOMIC, [{"report": {"status": "DONE"}}] * 2)
    assert rig.code == 2 and not rig.pushes(), rig.out
    assert len(rig.final_calls("arbiter")) == 2
    (question,) = decisions(rig)["questions"]
    assert question["id"] == Q
    assert rig.led("End of run: the arbiter could not rule, so the operator decides")


def test_an_arbiter_report_that_skips_a_question_is_rejected(tmp_path):
    rig, _ = open_after_batch(tmp_path, ATOMIC, [ruled(), ruled((Q, "accept"))])
    assert rig.code == 0, rig.out
    first, _ = rig.led("End of run: arbiter ran on")
    assert f"no ruling on {Q}" in first
