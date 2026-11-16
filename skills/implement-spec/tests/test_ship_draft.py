"""`ship-draft`: once the driver's retries are spent on an end-of-run stop, a full gate still red after its repair or an
end-of-run agent that failed twice, the driver pushes the branch tip that a full gate ran on and opens the PR as
draft, naming the stop. `--ask` (IMPLEMENT_SPEC_ASK=1, which conftest pins) asks the operator instead; a tip no full
gate ran on, as after a failed rebase conflict agent, still asks.

In process, on the rigs of test_tidy and test_lenses."""
from test_final import RESOLVE, fixer, fn
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_lenses import LENSES, final, rig_of, stopped
from test_review import RISKY, finding, fix, review
from test_stops import UNATTENDED, decisions, stage
from test_tidy import Inside, git, moved, stray
from test_tidy import tools  # noqa: F401  (tools is an autouse fixture)
from test_walk import plan

AUTONOMOUS = ("--no-ask", "--retries", "0", *UNATTENDED)
FIXER_STOPPED = {"report": {**stopped()["report"], "green": []}}
DRAFT = {"stage": "implement", "status": "done", "draft": True, "decisions": 1}


def not_reviewed(rig, since):
    """The coverage line's list of the commits after `since` that no review covered."""
    return "not reviewed: " + "; ".join(f"{c['sha'][:7]} {c['subject']}" for c in rig.commits(since))


def test_a_full_gate_red_after_its_repair_ships_the_repaired_tip_as_draft(tmp_path):
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['feat(01): Export rows', 'fix(01): first repair']})
    rig.run(plan(**{'final/fixer': [fixer('first\n', 'first repair', ticket_of='01')]}), *AUTONOMOUS)
    assert rig.code == 0, rig.output()
    assert len(rig.final_calls('fixer')) == 1 and not any(rig.final_calls(role) for role in LENSES)
    assert rig.pushes() == [f'refs/heads/feat/csv {rig.branch()}'], 'the tip the red gate ran on, repair included'
    (pr,) = rig.prs()
    assert '--draft' in pr['argv']
    assert 'the full integration gate remains red after its repair' in pr['body']
    assert rig.led('open as a draft: the end of run stopped: the full integration gate remains red after its repair')
    assert rig.led('(ship-draft)') and rig.led('Ruled by the driver, not the operator.')
    assert not_reviewed(rig, rig.final()['onto']) in pr['body'], 'no review ran, so every commit is unreviewed'
    assert stage(rig) == DRAFT


def test_a_lens_that_fails_twice_ships_the_gated_tip_as_draft_naming_the_lens(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-structure": [stopped(), stopped()]}), *AUTONOMOUS)
    assert rig.code == 0, rig.out
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.final()['tidied']}"]
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and "the final-structure: it reported BLOCKED" in pr["body"]
    assert not_reviewed(rig, rig.final()["onto"]) in pr["body"]
    assert not rig.final_calls("fixer")
    assert stage(rig) == DRAFT


def test_a_pr_writer_that_fails_twice_ships_a_draft_with_the_facts_of_the_driver(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/pr-writer": [stopped(), stopped()]}), *AUTONOMOUS)
    assert rig.code == 0, rig.out
    assert len(rig.final_calls("pr-writer")) == 2, "no third pr-writer"
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and pr["body"].startswith("## Tickets")
    assert "(ship-draft): the pr-writer: report: " in pr["body"]
    assert stage(rig) == DRAFT


def test_a_final_fixer_that_fails_twice_ships_the_reviewed_tip_as_draft(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-spec": [final(finding("missing --dry-run flag"))],
                    "final/fixer": [FIXER_STOPPED] * 2}), *AUTONOMOUS)
    assert rig.code == 0, rig.out
    reviewed = rig.final()["reviewed"]
    assert rig.pushes() == [f"refs/heads/feat/csv {reviewed}"] and rig.branch() == reviewed
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and "the fixer: it reported BLOCKED" in pr["body"]
    assert f"Branch tip and reviewed tip: {reviewed}" in pr["body"]
    assert rig.led("a finding ships open: missing --dry-run flag")
    assert stage(rig) == DRAFT


def test_a_scoped_review_that_fails_twice_ships_the_fixed_tip_as_draft_naming_the_unreviewed_fix(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-spec": [final(finding("missing --dry-run flag"))],
                    "final/fixer": [fixer(fn("02"), "add the flag")],
                    "final/final-reviewer": [stopped(), stopped()]}), *AUTONOMOUS)
    assert rig.code == 0, rig.out
    reviewed = rig.final()["reviewed"]
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and rig.branch() != reviewed
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and "the final-reviewer: it reported BLOCKED" in pr["body"]
    assert f"reviewed tip: {reviewed}; {not_reviewed(rig, reviewed)}" in pr["body"]
    assert "fix(02): add the flag" in not_reviewed(rig, reviewed)
    assert stage(rig) == DRAFT


def test_a_critical_finding_open_when_the_final_fixer_fails_parks_for_the_operator_who_may_ship_it(tmp_path):
    """No agent ships a critical finding: the driver does not take `ship-draft` itself while one is open."""
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-correctness": [final(finding("ledger write not atomic", "critical"))],
                    "final/fixer": [FIXER_STOPPED] * 2})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 2, rig.out
    assert not rig.pushes() and not rig.prs()
    (question,) = decisions(rig)["questions"]
    assert question["id"] == "final-ticket-blocked-1" and "ship-draft" in [o["id"] for o in question["options"]]
    assert not rig.led("(ship-draft)") and rig.final()["ship"] is None
    rig.run(steps, "--answer", "final-ticket-blocked-1=ship-draft", *AUTONOMOUS)
    assert rig.code == 0, rig.out
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.final()['reviewed']}"]
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and rig.led("a finding ships open: ledger write not atomic")
    assert not rig.led("Ruled by the driver, not the operator.")
    assert len(rig.final_calls("fixer")) == 2, "the operator's ship-draft runs no more repair"


def test_a_run_stopped_after_the_ruling_pushes_the_recorded_tip_and_refuses_a_branch_that_moved(tmp_path):
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-structure": [stopped(), stopped()]})
    rig.run(steps, "--no-ask", "--retries", "0")
    shipped = rig.final()["ship"]
    assert rig.code == 0 and not rig.pushes(), rig.out
    assert shipped == {"tip": rig.branch(), "stops": [
        {"subject": "the final-structure: it reported BLOCKED: cannot read the spec", "role": "final-structure"}]}
    stray(rig)
    rig.run(steps, "--push")
    assert rig.code == 1 and not rig.pushes(), rig.out
    assert rig.led(f"feat/csv moved from {shipped['tip'][:7]}, the gated tip that the ship-draft ruling ships")
    git(rig.repo, "reset", "-q", "--hard", shipped["tip"])
    rig.run(steps, "--push")
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {shipped['tip']}"], rig.out
    assert len(rig.final_calls("final-structure")) == 2, "the recorded ruling reviews no more"


def test_a_red_gate_after_the_fix_of_a_critical_finding_parks_for_the_operator_who_may_ship_it(tmp_path):
    """The fix batch clears the open findings before any review verifies the fix: the full gate red after its repair
    leaves the critical finding unverified, so the driver does not take `ship-draft` itself."""
    rig = Inside(tmp_path)
    rig.gate_conf(full={'subjects': ['fix(01): review fix', 'fix(01): gate repair']})
    steps = plan(**{'final/final-correctness': [final(finding('ledger write not atomic', 'critical'))],
                    'final/fixer': [fixer('review\n', 'review fix', ticket_of='01'),
                                    fixer('gate\n', 'gate repair', ticket_of='01')]})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 2, rig.out
    assert not rig.pushes() and not rig.prs()
    assert not rig.final_calls('final-reviewer')
    assert rig.final()['open'] == []
    (question,) = decisions(rig)['questions']
    assert question['id'] == 'final-ticket-blocked-1' and not rig.led('(ship-draft)')
    rig.run(steps, '--answer', 'final-ticket-blocked-1=ship-draft', *AUTONOMOUS)
    assert rig.code == 0, rig.out
    assert rig.pushes() == [f'refs/heads/feat/csv {rig.branch()}'] and len(rig.final_calls('fixer')) == 2
    (pr,) = rig.prs()
    assert '--draft' in pr['argv'] and not rig.led('Ruled by the driver, not the operator.')


def parks_then_ships_on_the_operators_answer(rig, steps):
    """The driver does not take `ship-draft` itself: the stop parks; the operator's `ship-draft` ships a draft."""
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 2, rig.out
    assert not rig.pushes() and not rig.prs() and not rig.led("(ship-draft)")
    (question,) = decisions(rig)["questions"]
    assert question["id"] == "final-ticket-blocked-1"
    rig.run(steps, "--answer", "final-ticket-blocked-1=ship-draft", *AUTONOMOUS)
    assert rig.code == 0, rig.out
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"]
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"] and not rig.led("Ruled by the driver, not the operator.")


def test_a_critical_finding_of_one_lens_when_another_fails_twice_parks_for_the_operator(tmp_path):
    """No review settled, but the accepted report of the correctness lens holds a critical finding."""
    rig = rig_of(tmp_path)
    parks_then_ships_on_the_operators_answer(rig, plan(**{
        "final/final-correctness": [final(finding("ledger write not atomic", "critical"))],
        "final/final-structure": [stopped(), stopped()]}))
    assert rig.final()["round"] == 0


def test_a_carried_must_fix_finding_when_the_first_full_gate_stays_red_parks_for_the_operator(tmp_path):
    """A must-fix finding carried from a ticket that merged at its fix cap is open before any review."""
    rig = rig_of(tmp_path, RISKY)
    rig.gate_conf(full={"subjects": ["feat(02): Quote fields", "fix(02): first repair"]})
    regression = {**finding("QUOTING-REGRESSION"), "basis": "regression"}
    parks_then_ships_on_the_operators_answer(rig, plan(**{
        "02/reviewer": [review("02", [regression])] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
        "final/fixer": [fixer(fn("02"), "first repair", ticket_of="02")]}))
    assert [item["title"] for item in rig.final()["must_fix"]] == ["QUOTING-REGRESSION"]
    assert not any(rig.final_calls(role) for role in LENSES)


def test_the_operators_ship_draft_with_no_gated_tip_is_refused_and_retry_remains(tmp_path):
    """A failed rebase conflict agent leaves no tip that a full gate ran on: `ship-draft` would record nothing to
    push, so the answer is a usage error and the question stays open."""
    stuck = {"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["both sides are needed"]}}
    resolved = {**RESOLVE, "files": {"demo/cli.py": "def main():\n    return 'both'\n"}}
    rig = moved(tmp_path, {"demo/cli.py": "def main():\n    return 'base'\n"}, stuck, stuck)
    assert rig.code == 2, rig.out
    steps = plan(**{"final/conflict": [stuck, stuck, resolved]})
    rig.run(steps, "--answer", "final-ticket-blocked-1=ship-draft", *UNATTENDED)
    assert rig.code == 3 and "no full gate ran on" in rig.out and "`retry` remains" in rig.out, rig.out
    (question,) = decisions(rig)["questions"]
    assert question["id"] == "final-ticket-blocked-1" and rig.final()["ship"] is None
    rig.run(steps, "--answer", "final-ticket-blocked-1=retry", *UNATTENDED)
    assert rig.code == 0, rig.out
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and "--draft" not in rig.prs()[0]["argv"]
