"""In-process end-of-run tests: the rig and the scenarios of test_final.py without the launcher, so coverage sees
the driver, plus the cases that only a direct call or a look between two runs reaches."""
import contextlib
import io
import json
import os
from types import SimpleNamespace

import jsonschema
import pytest

import driver
import driver_final
import driver_merge
import driver_report
import driver_state
import driver_summary
import driver_tidy
import test_final
from test_cli import git
from test_final import FILES, FINAL_MODELS, RESOLVE, THREE, TITLES, final, fixer, fn, ref, stop_lines
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_review import FABLE, ONE, finding
from test_stops import UNATTENDED, decisions, stage
from test_walk import plan, tw


class Inside(test_final.Final):
    """The rig of test_final with the driver called in this process."""

    def run(self, steps, *extra, parallel="2", watch=False):
        (self.rig / "plan.json").write_text(json.dumps(steps))
        args = ["--tickets", ".agent/tickets", "--gate", str(self.rig / "gate"), "--spec", "specs/csv/spec.md",
                "--models", str(self.models), "--runner", str(self.rig / "run-agent"), "--parallel", parallel, *extra]
        here, out = os.getcwd(), io.StringIO()
        os.chdir(self.repo)
        try:
            with contextlib.redirect_stdout(out):
                self.code = driver.main(args)
        finally:
            os.chdir(here)
        self.out, self.err = out.getvalue(), ""
        return self

    def final(self):
        return self.state()["run"]["final"]

    def order(self):
        """The ticket numbers in merge order: two tickets that run at once merge in either order."""
        return [number for number, _ in self.state()["run"]["landed"]]


@pytest.fixture(autouse=True)
def tools(monkeypatch, tmp_path):
    """The fake `gh` of the rig is on PATH, and no git call of an agent opens an editor."""
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_EDITOR", "true")


def stray(rig, text="by hand"):
    """A commit on the PR branch that no agent of the run made."""
    (rig.repo / "stray.txt").write_text(text)
    git(rig.repo, "add", "-A")
    git(rig.repo, "commit", "-q", "-m", text)


# ---------------------------------------------------------------- the story: local stop, rerun, push

def test_a_local_run_stops_before_the_push_and_a_push_rerun_pushes_and_opens_the_pr_once(tmp_path):
    rig = Inside(tmp_path, THREE, FINAL_MODELS)
    rig.run(plan())
    assert rig.code == 0, rig.out + "\n".join(rig.ledger())
    tip = rig.branch()
    assert rig.pushes() == [] and not rig.prs() and not ref(rig.origin, "refs/heads/feat/csv"), "nothing is pushed"
    shown_tip, reviewed, push = stop_lines(rig.out)
    assert (shown_tip.group(1), reviewed.group(1)) == (tip, tip) and push.group(0).endswith("--parallel 2 --push")
    rows, order = rig.commits(rig.start), rig.order()
    assert [(r["subject"], r["body"].strip()) for r in rows] == [
        (f"feat({nn}): {TITLES[nn]}", f"Ticket: {FILES[nn]}") for nn in order] and order[2] == "02"
    assert len(rig.commits(rig.start, rig.recovery())) == 6
    assert ref(rig.repo, "feat/csv^{tree}") == ref(rig.repo, "refs/implement/recovery^{tree}")
    assert rig.led("Recovery point: feat/csv was at " + rig.recovery(), ref(rig.repo, "feat/csv^{tree}"))
    assert rig.led("End of run: final-reviewer ran on", FABLE) and rig.led("End of run: the gate is green on " + tip[:7])
    assert {nn: e["tip"] for nn, e in rig.state()["tickets"].items()} == {nn: r["sha"] for nn, r in zip(order, rows)}
    assert f"The final review covers feat/csv up to {tip[:7]}. Nothing is pushed." in rig.out
    assert git(rig.repo, "symbolic-ref", "--short", "HEAD").strip() == "feat/csv" and git(rig.repo, "status", "--short") == ""
    calls, gates, ledger = len(rig.calls()), len(rig.gates()), rig.ledger()

    rig.run(plan())
    assert (rig.code, len(rig.calls()), len(rig.gates()), rig.branch(), rig.ledger()) == (0, calls, gates, tip, ledger)
    assert all(stop_lines(rig.out)) and rig.pushes() == []

    rig.run(plan(), "--push")
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {tip}"], rig.out
    (pr,) = rig.prs()
    assert pr["argv"][:10] == ["pr", "create", "--base", "main", "--head", "feat/csv", "--title", "csv spec",
                               "--body-file", ".agent/implement/final/pr-body.md"]
    assert [f"- {nn}: {row['sha'][:7]} {TITLES[nn]}" for nn, row in zip(order, rows)] == pr["body"].splitlines()[4:7]
    assert "## Rulings\n\nNone.\n" in pr["body"] and "Scoped review after the fix batch: not needed" in pr["body"]
    assert f"Pushed feat/csv at {tip} to origin." in rig.out and "PR: https://github.example/pull/1" in rig.out
    assert f"feat/csv is pushed at {tip[:7]}; its PR is https://github.example/pull/1." in rig.out
    assert rig.led("The PR of feat/csv into main is open: https://github.example/pull/1.")

    rig.run(plan(), "--push")
    assert rig.code == 0 and len(rig.pushes()) == 1 and len(rig.prs()) == 1, "a second push rerun sends nothing"
    assert (len(rig.calls()), len(rig.gates())) == (calls, gates)


def test_the_final_review_does_not_start_while_a_ticket_is_not_merged_and_the_ledger_says_so_once(tmp_path):
    rig = Inside(tmp_path, THREE, gate={"red": ["ticket/01"]})
    rig.run(plan())
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1 and not rig.final_calls("final-reviewer") and not rig.recovery()
    assert rig.led("End-of-run work skipped") == ["End-of-run work skipped: these tickets are not merged: 01, 02."]
    assert "The end-of-run work (rebase, tidy, final review, push) did not run: these tickets are not merged: 01, 02." in rig.out
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["01"]}


def test_a_run_that_was_stopped_before_the_branch_moved_finishes_the_move_and_keeps_every_ticket_merged(tmp_path):
    rig = Inside(tmp_path, THREE)
    rig.run(plan())
    tip, calls = rig.branch(), len(rig.calls())
    git(rig.repo, "reset", "-q", "--hard", rig.recovery())  # as if the run had ended right before the move
    rig.run(plan())
    assert (rig.code, rig.branch(), len(rig.calls())) == (0, tip, calls), rig.out
    assert not rig.led("returned to implement") and git(rig.repo, "status", "--short") == ""
    assert {e["phase"] for e in rig.state()["tickets"].values()} == {"merged"}


def test_a_branch_that_cannot_move_to_the_tidied_tip_keeps_the_recorded_ticket_tips(tmp_path, monkeypatch):
    rig = Inside(tmp_path, ONE)
    real = driver_merge.land  # the merge of the ticket moves the branch; the move to the tidied tip fails
    monkeypatch.setattr(driver_merge, "land", lambda pr, new, old: (
        "the ref moved" if driver_tidy.text(new).startswith("feat(01)") else real(pr, new, old)))
    rig.run(plan())
    assert rig.code == 1, rig.out
    (line,) = rig.led("End of run: BLOCKED at the rewrite: cannot move feat/csv to the tidied tip")
    assert line.endswith(f"the ref moved; feat/csv is unrewritten at its recovery point {rig.recovery()[:7]}.")
    assert rig.branch() == rig.recovery() == rig.state()["tickets"]["01"]["tip"] and not rig.final().get("tidied")
    assert not rig.final_calls("final-reviewer") and "End of run: BLOCKED at the rewrite" in rig.out


def test_a_tidied_tip_that_left_the_branch_stops_the_end_of_run(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.run(plan())
    state = rig.state()
    state["run"]["final"]["tidied"] = rig.recovery()  # the history before the tidy is not in the branch
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    rig.run(plan())
    assert rig.code == 1 and rig.led("End of run: BLOCKED at feat/csv does not hold the tidied tip " + rig.recovery()[:7])


def test_a_ticket_whose_merge_left_the_tree_as_it_was_gets_the_commit_before_it(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.run(plan(), "--dry-run")
    os.chdir(rig.repo)
    start = ref(rig.repo, "HEAD")
    stray(rig)
    tip = ref(rig.repo, "HEAD")
    tickets = {nn: driver.driver_tickets.Ticket(nn, f"{nn}-t.md", f"Title {nn}", None, None, None, None, None, [], {})
               for nn in ("01", "02", "03")}
    commits = driver_tidy.tidy(start, [["01", start], ["02", tip], ["03", tip]], tickets)
    assert commits["01"] == start and commits["02"] == commits["03"] != tip
    assert driver_tidy.same_tree(commits["03"], tip) and not driver_tidy.same_tree("0" * 40, "0" * 40)
    assert driver_tidy.text(commits["02"]) == "feat(02): Title 02\n\nTicket: 02-t.md\n\n"
    assert git(rig.repo, "log", "-1", "--format=%an %ae", commits["02"]) == "t t@example.com\n"


# ---------------------------------------------------------------- the fix batch and its report

def test_a_fix_batch_is_named_per_commit_and_a_commit_with_no_owner_is_a_violation(tmp_path):
    """The first report leaves out one of two commits; the rerun names both, one with a ticket that is not there
    in a third attempt that never runs."""
    rig = Inside(tmp_path, THREE)
    first = fixer(fn("02"), "quote commas", "escape quotes")
    first["report"]["commits"].pop()
    retry = {"report": dict(first["report"], commits=[{"sha": "$HEAD~1", "ticket": "02"}, {"sha": "$HEAD", "ticket": "01"}])}
    retry["files"] = {"wip.txt": "not committed\n"}
    rig.run(plan(**{"final/final-reviewer": [final(finding("Q-ONE"), finding("Q-MINOR", "minor")), final()],
                    "final/fixer": [first, retry]}))
    assert rig.code == 1, rig.out
    assert rig.led("End of run: fixer ran on", "is on feat/csv and not in `commits`: each commit of the batch needs its ticket")
    assert rig.led("End of run: BLOCKED at the fix batch: commits.0.sha")  # `$HEAD~1` is no SHA: a second violation
    reviewed = rig.final()["reviewed"]
    assert rig.branch() == reviewed, "the rejected commits are off the branch"
    (line,) = rig.led(f"End of run: the fix batch was rejected, so the driver moved feat/csv back to its reviewed tip {reviewed[:7]}")
    left = line.split("the commits of the batch end at ")[1].split()[0]
    assert git(rig.repo, "log", "--format=%s", f"{reviewed}..{left}").split("\n")[:2] == ["escape quotes", "quote commas"]
    assert line.endswith("The files of the fixer stay in slot 1 as they are.")
    assert (rig.repo / "wip.txt").read_text() == "not committed\n" and "# escape quotes" in (rig.repo / "demo/export.py").read_text()
    prompt = rig.final_calls("fixer")[0]["prompt"]
    assert "- important: Q-ONE (demo/export.py:99)\n" in prompt and "Q-MINOR" not in prompt
    assert ".agent/implement/final/findings-0.json" in prompt and prompt.rstrip().endswith("one ref store.")
    assert "Locked files: tests/test_01.py tests/test_02.py tests/test_03.py\n" in prompt

    rig.run(plan(), "--push")  # as after any committing role that failed: the work in slot 1 stops the rerun
    assert rig.code == 3 and "uncommitted change: wip.txt" in rig.out and rig.pushes() == []
    git(rig.repo, "checkout", "-q", "-f", "feat/csv")  # the operator discards the work
    (rig.repo / "wip.txt").unlink()

    stray(rig, "fix(99): by hand")  # a commit with the name of a fix commit and no validated owner
    rig.run(plan(), "--push")
    assert rig.code == 1 and rig.pushes() == [] and len(rig.final_calls("final-reviewer")) == 1, rig.out
    assert rig.led("End of run: BLOCKED at the tip", f"is not a tip the driver made: only {reviewed[:7]} gets a review")

    git(rig.repo, "reset", "-q", "--hard", reviewed)
    rig.run(plan(**{"final/fixer": [{}, {}, fixer(fn("02"), "quote commas")]}), "--push")
    assert rig.code == 0 and [r["subject"] for r in rig.commits(reviewed)] == ["fix(02): quote commas"], rig.out
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and rig.final()["batch"] == rig.branch()


def test_a_fixer_that_times_out_twice_keeps_its_files_and_the_branch_stays_at_the_reviewed_tip(tmp_path):
    rig = Inside(tmp_path, ONE)
    slow = {"files": {"wip.txt": "half done\n"}, "sleep": 30}
    rig.run(plan(**{"final/final-reviewer": [final(finding("Q-ONE"))], "final/fixer": [slow, slow]}),
            "--timeout", "fixer=0.01", *UNATTENDED)
    reviewed = rig.final()["reviewed"]
    assert rig.code == 1 and rig.pushes() == [] and rig.branch() == reviewed, rig.out
    assert (rig.repo / "wip.txt").read_text() == "half done\n"
    assert rig.led(f"End of run: the fix batch was rejected, so the driver moved feat/csv back to its reviewed tip "
                   f"{reviewed[:7]}; the fixer made no commit. The files of the fixer stay in slot 1 as they are.")


def test_a_fix_commit_for_a_ticket_that_is_not_in_the_run_is_a_violation(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.run(plan(**{"final/final-reviewer": [final(finding("Q-ONE")), final()],
                    "final/fixer": [fixer(fn("01"), "fix(01): named already", ticket_of="09"),
                                    {"report": dict(fixer(fn("01"), "x")["report"], commits=[{"sha": "$HEAD", "ticket": "01"}])}]}))
    assert rig.code == 0, rig.out
    assert rig.led("End of run: fixer ran on", ": 09 is not a ticket of this run")
    (row,) = rig.commits(rig.final()["tidied"])
    assert row["subject"] == "fix(01): named already" and rig.final()["reviewed"] == row["sha"] == rig.branch()
    assert rig.led("Fix batch of the final review: 1 commit on", f"{row['sha'][:7]} fix(01): named already.")
    scoped = rig.final_calls("final-reviewer")[1]["prompt"]
    assert f"Range: {rig.final()['tidied']}..{row['sha']}\n" in scoped and "This is the scoped review after the fix batch" in scoped
    assert scoped.rstrip().endswith("one ref store.") and ".agent/implement/final/findings-0.json" in scoped


def test_a_final_reviewer_that_changes_the_slot_is_restored_and_runs_once_more(tmp_path):
    rig = Inside(tmp_path, ONE)
    dirty = final(finding("DROPPED"), files={"left.txt": "x"})
    moved_head = final(files={"more.txt": "x"}, commit="by the reviewer")
    rig.run(plan(**{"final/final-reviewer": [dirty, final(finding("KEPT", "minor"))]}))
    assert rig.code == 0 and len(rig.final_calls("final-reviewer")) == 2 and not (rig.repo / "left.txt").exists(), rig.out
    assert rig.led("End of run: a read-only role broke its rule: the tree is not as the review found it: left.txt; the "
                   f"driver restored slot 1 to {rig.branch()[:7]} and runs the final review again.")
    assert rig.led("KEPT") and not rig.led("DROPPED") and rig.final()["reviewed"] == rig.branch()


def test_a_final_reviewer_that_changes_the_slot_twice_ends_the_run_with_a_clean_slot(tmp_path):
    rig = Inside(tmp_path, ONE)
    moved_head = final(files={"more.txt": "x"}, commit="by the reviewer")
    rig.run(plan(**{"final/final-reviewer": [final(files={"left.txt": "x"}), moved_head]}), *UNATTENDED)
    tip = rig.final()["tidied"]
    assert rig.code == 1 and rig.final()["reviewed"] is None and rig.branch() == tip, rig.out
    assert rig.led(f"End of run: BLOCKED at the final review: the slot is no longer on feat/csv at {tip[:7]}.")
    assert git(rig.repo, "status", "--short") == "" and git(rig.repo, "symbolic-ref", "--short", "HEAD") == "feat/csv\n"
    rig.run(plan(), *UNATTENDED)  # the rerun is not stopped by what the reviewer left
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {tip}"], rig.out


def test_a_red_gate_on_the_tidied_tip_stops_before_the_final_review(tmp_path):
    rig = Inside(tmp_path, ONE, gate={"red": ["feat/csv"]})
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1 and not rig.final_calls("final-reviewer") and rig.pushes() == [], rig.out
    assert rig.led("End of run: BLOCKED at the gate: the gate is red on " + rig.branch()[:7])
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["final"]}
    assert rig.state()["run"]["gates"] == 2 and "| gates run | 2 |" in rig.out


# ---------------------------------------------------------------- the triage after the second round

def parked(tmp_path, *findings):
    rig = Inside(tmp_path, ONE)
    steps = plan(**{"final/final-reviewer": [final(*findings), final(*findings)],
                    "final/fixer": [fixer(fn("01"), "first try", ticket_of="01")]})
    return rig.run(steps), steps


def test_open_findings_after_the_second_round_park_the_run_on_one_question_each(tmp_path):
    rig, steps = parked(tmp_path, finding("STILL-OPEN"), finding("ALSO"))
    assert rig.code == 2 and rig.pushes() == [], rig.out
    first, second = decisions(rig)["questions"]
    assert (first["id"], second["id"]) == ("final-finding-1", "final-finding-2")
    assert first["title"] == "Final review: a finding is open after the second review round: STILL-OPEN (demo/export.py:99)"
    assert [o["id"] for o in first["options"]] == ["fix", "accept"] and first["recommended"] == "fix"
    assert first["evidence"] == [".agent/implement/final/findings-1.json", ".agent/implement/final/final-reviewer-1.json"]
    assert "Question final-finding-1: " in rig.out and rig.state()["run"]["stops"] == 1
    assert rig.led("End of run: the operator decides on 2 questions of kind final-finding")
    assert len(rig.final_calls("final-reviewer")) == 2 and len(rig.final_calls("fixer")) == 1

    rig.run(steps, "--answer", "final-finding-1=accept")
    assert rig.code == 2 and rig.led("Ruling: final-review finding closed as it is (accept): STILL-OPEN (demo/export.py:99).")
    assert [q["id"] for q in decisions(rig)["questions"]] == ["final-finding-2"] and not any(stop_lines(rig.out))

    rig.run(steps, "--answer", "final-finding-2=accept", "--push")
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"], rig.out
    body = rig.prs()[0]["body"]
    assert "- Ruling: final-review finding closed as it is (accept): ALSO (demo/export.py:99)." in body
    assert f"- {rig.branch()[:7]} fix(01): first try" in body
    assert f"Scoped review after the fix batch: {rig.final()['tidied']}..{rig.branch()}" in body
    assert len(rig.final_calls("final-reviewer")) == 2, "no third round"


def test_the_answer_fix_keeps_the_finding_open_and_nothing_is_pushed(tmp_path):
    rig, steps = parked(tmp_path, finding("STILL-OPEN"))
    rig.run(steps, "--answer", "final-finding-1=fix", *UNATTENDED)
    assert rig.code == 1 and rig.pushes() == [] and not rig.prs(), rig.out
    assert rig.led("Ruling: final-review finding stays open and nothing is pushed (fix): STILL-OPEN")
    assert rig.led("End of run: BLOCKED at the operator keeps 1 finding of the final review open (fix): nothing is pushed.")
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["final"]}


def test_a_commit_after_the_second_review_is_never_reviewed_or_pushed(tmp_path):
    rig, steps = parked(tmp_path, finding("STILL-OPEN"))
    rig.run(steps, "--answer", "final-finding-1=accept")
    assert rig.code == 0 and all(stop_lines(rig.out))
    stray(rig, "fix(01): by hand")
    rig.run(steps, "--push")
    assert rig.code == 1 and rig.pushes() == [] and len(rig.final_calls("final-reviewer")) == 2
    assert rig.led("End of run: BLOCKED at the two review rounds are done and the tip", "is not the reviewed tip")


# ---------------------------------------------------------------- the rebase

def moved(tmp_path, base, *conflict, **kw):
    rig = Inside(tmp_path, THREE, **kw)
    rig.move_base(base)
    return rig.run(plan(**{"final/conflict": list(conflict)}), *UNATTENDED)


def test_a_base_that_moved_with_no_conflict_is_rebased_onto_with_no_agent_and_one_gate(tmp_path):
    rig = moved(tmp_path, {"docs.md": "base docs\n"})
    assert rig.code == 0 and not rig.final_calls("conflict"), rig.out
    base, rows = ref(rig.origin, "refs/heads/main"), rig.commits(ref(rig.origin, "refs/heads/main"))
    order = rig.order()
    assert [r["subject"] for r in rows] == [f"feat({nn}): {TITLES[nn]}" for nn in order] and order[2] == "02"
    assert ref(rig.repo, "feat/csv~3") == base and git(rig.repo, "show", "feat/csv:docs.md") == "base docs\n"
    assert [p.name for p in (rig.repo / ".agent/implement/final").glob("gate-*.log")] == [f"gate-{rig.branch()[:7]}.log"]
    assert rig.final()["commits"] == {nn: row["sha"] for nn, row in zip(order, rows)}
    assert rig.final()["onto"] == base and f"Range: {base}..{rig.branch()}\n" in rig.final_calls("final-reviewer")[0]["prompt"]
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and stage(rig) == {"stage": "implement", "status": "done"}


def test_a_resolved_rebase_keeps_the_ticket_messages_and_gates_the_rebased_tip_and_the_tidied_tip(tmp_path):
    cli = {"demo/cli.py": "def main():\n    return 'base'\n"}
    resolved = {**RESOLVE, "files": {"demo/cli.py": "def main():\n    return 'both'\n"}, "git_end": []}
    rig = moved(tmp_path, cli, resolved)  # the agent commits as "resolve conflict" and the driver goes on with the rebase
    assert rig.code == 0, rig.out + "\n".join(rig.ledger())
    (call,) = rig.final_calls("conflict")
    assert "Files in conflict: demo/cli.py\n" in call["prompt"] and "Base branch: main, at commit " in call["prompt"]
    assert call["branch"] == "HEAD" and call["prompt"].rstrip().endswith("one ref store.")
    rows = rig.commits(ref(rig.origin, "refs/heads/main"))
    assert [r["subject"] for r in rows] == [f"feat({nn}): {TITLES[nn]}" for nn in rig.order()]
    assert git(rig.repo, "show", "feat/csv:demo/cli.py") == "def main():\n    return 'both'\n"
    (line,) = rig.led("Rebase: the conflict agent resolved the conflict with main in demo/cli.py at ")
    rebased = line.rstrip(".").split()[-1]
    records = sorted(p.name for p in (rig.repo / ".agent/implement/final").glob("gate-*.log"))
    assert records == sorted([f"gate-{rebased}.log", f"gate-{rig.branch()[:7]}.log"]) and rebased != rig.branch()[:7]
    assert ref(rig.repo, f"{rebased}^{{tree}}") == ref(rig.repo, "feat/csv^{tree}")


def test_a_second_conflict_leaves_the_branch_at_its_recovery_point_with_all_its_commits(tmp_path):
    step = {**RESOLVE, "files": {"demo/export.py": "resolved = 1\n"}, "report": {**RESOLVE["report"], "files": ["demo/export.py"]}}
    rig = moved(tmp_path, {"demo/export.py": "def export_csv():\n    return 'base'\n"}, step)
    assert rig.code == 1 and len(rig.final_calls("conflict")) == 1 and not rig.final_calls("final-reviewer"), rig.out
    recovery = rig.recovery()
    assert rig.branch() == recovery and len(rig.commits(rig.start, recovery)) == 6, "two commits per ticket, as before"
    assert rig.led("End of run: BLOCKED at the rewrite: the rebase onto main conflicts a second time, in demo/export.py; "
                   f"feat/csv is unrewritten at its recovery point {recovery[:7]}.")
    assert git(rig.repo, "symbolic-ref", "--short", "HEAD").strip() == "feat/csv" and git(rig.repo, "status", "--short") == ""
    assert rig.pushes() == [] and stage(rig)["blocked"] == ["final"] and not rig.final().get("tidied")
    assert {e["phase"] for e in rig.state()["tickets"].values()} == {"merged"}

    rig.run(plan(), *UNATTENDED)  # the rerun starts the rewrite again from the same recovery point
    assert rig.code == 1 and rig.recovery() == recovery == rig.branch()
    assert len(rig.final_calls("conflict")) == 3, "the conflict agent of the rerun, which has no script, and its one rerun"
    assert not rig.led("returned to implement")


def test_a_conflict_agent_that_gives_up_leaves_no_rebase_open(tmp_path):
    stuck = {"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["both sides are needed"]}}
    rig = moved(tmp_path, {"demo/cli.py": "base\n"}, stuck)
    assert rig.code == 1 and rig.branch() == rig.recovery(), rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: the conflict agent: the conflict reported BLOCKED: both sides are needed;")
    assert git(rig.repo, "status", "--short") == "" and git(rig.repo, "symbolic-ref", "--short", "HEAD") == "feat/csv\n"
    assert not (rig.repo / ".git/rebase-merge").exists()


def test_a_red_gate_after_the_rebase_leaves_the_branch_unrewritten(tmp_path):
    rig = Inside(tmp_path, ONE, gate={"red": ["HEAD"]})  # red on a detached commit: the rebased tip
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    rig.run(plan(**{"final/conflict": [{**RESOLVE, "files": {"demo/export.py": "x = 1\n"}}]}))
    assert rig.code == 1 and rig.branch() == rig.recovery(), rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: the gate after the rebase: the gate is red on ")


def test_a_base_that_holds_a_ticket_commit_already_leaves_the_branch_unrewritten(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({**tw("01")["files"], "demo/export.py": fn("01")})  # the same change as the commit of ticket 01
    rig.run(plan())
    assert rig.code == 1 and rig.branch() == rig.recovery(), rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: the rebase onto main left 0 commits for 1;")


@pytest.mark.parametrize("patch, error", [
    (lambda rig: ("ancestor", lambda commit, of, real=driver_merge.ancestor: commit != rig.start and real(commit, of)),
     "feat/csv no longer holds {start}, the commit the run started from"),
    (lambda rig: ("same_tree", lambda one, other: False), "the tidied history does not have the tree of {old}"),
])
def test_a_history_the_tidy_cannot_trust_is_not_rewritten(tmp_path, monkeypatch, patch, error):
    rig = Inside(tmp_path, ONE)
    name, fake = patch(rig)
    monkeypatch.setattr(driver_tidy if name == "same_tree" else driver_merge, name, fake)
    rig.run(plan())
    assert rig.code == 1 and rig.branch() == rig.recovery() and not rig.final().get("tidied"), rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: " + error.format(start=rig.start[:7], old=rig.recovery()[:7]))
    assert len(rig.commits(rig.start)) == 2, "the two commits of the ticket, as they were"


@pytest.mark.parametrize("change, error", [
    (lambda rig: git(rig.repo, "remote", "set-url", "origin", str(rig.tmp / "nowhere.git")), "origin names no default branch: "),
    (lambda rig: git(rig.origin, "symbolic-ref", "HEAD", "refs/heads/feat/csv"),
     "feat/csv is the default branch of origin: the end of run needs a PR branch."),
])
def test_an_origin_with_no_base_for_the_pr_branch_stops_the_end_of_run_before_the_recovery_point(tmp_path, change, error):
    rig = Inside(tmp_path, ONE)
    git(rig.repo, "push", "-q", "origin", "feat/csv")
    change(rig)
    rig.run(plan())
    assert rig.code == 1 and not rig.recovery() and len(rig.commits(rig.start)) == 2, rig.out
    assert rig.led("End of run: BLOCKED at " + error)


# ---------------------------------------------------------------- the push

def test_a_push_or_a_pr_that_fails_is_tried_again_and_nothing_is_sent_twice(tmp_path):
    rig = Inside(tmp_path, ONE)
    gh, hook = tmp_path / "bin/gh", rig.origin / "hooks/pre-receive"
    script, accept = gh.read_text(), hook.read_text()
    gh.unlink()
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1 and rig.pushes() == [], rig.out
    assert rig.led("End of run: BLOCKED at gh is not on PATH: nothing is pushed.")
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["final"]}

    gh.write_text("#!/bin/sh\necho no token >&2\nexit 4\n")
    gh.chmod(0o755)
    hook.write_text("#!/bin/sh\necho REFUSED >&2\nexit 1\n")
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1 and not ref(rig.origin, "refs/heads/feat/csv") and not rig.final().get("pushed")
    assert rig.led("End of run: BLOCKED at git push origin feat/csv failed: ", "REFUSED")

    hook.write_text(accept)
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1 and rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and not rig.final().get("pr")
    assert rig.led("End of run: BLOCKED at gh pr create failed: no token.")
    assert f"feat/csv is pushed at {rig.branch()[:7]}; its PR is not open." in rig.out

    gh.write_text(script)
    calls = len(rig.calls())
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 0 and len(rig.pushes()) == 1 and len(rig.prs()) == 1 and len(rig.calls()) == calls, rig.out
    assert stage(rig) == {"stage": "implement", "status": "done"} and rig.final()["pr"] == "https://github.example/pull/1"


def test_a_reviewed_tip_with_no_green_gate_record_is_not_pushed(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.run(plan())
    state = rig.state()
    state["run"]["final"]["green_gates"] = []
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    rig.run(plan(), "--push")
    assert rig.code == 1 and rig.pushes() == [] and not rig.prs(), rig.out
    assert rig.led(f"End of run: BLOCKED at the reviewed tip {rig.branch()[:7]} is not the tip of feat/csv with a green "
                   "gate record: nothing is pushed.")


def test_the_rerun_command_drops_the_answers_and_the_pr_title_falls_back_to_the_branch():
    command = driver_final.again(["--gate", "make check", "--answer", "final-finding-1=accept", "--answer=x=y", "--spec", "s.md"])
    assert command == f"sh {driver_final.driver_agents.SKILL / 'run.sh'} --gate 'make check' --spec s.md --push"
    assert driver_final._name(SimpleNamespace(args=SimpleNamespace(spec="nowhere.md"), pr="feat/x")) == "feat/x"


# ---------------------------------------------------------------- preconditions and the skip

def test_without_the_review_diff_skill_a_repository_with_an_origin_stops_before_any_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_final, "REVIEW_DIFF", tmp_path / "review-diff/SKILL.md")
    rig = Inside(tmp_path, ONE).run(plan())
    assert rig.code == 3 and not rig.calls(), rig.out
    assert rig.out == (f"{tmp_path}/review-diff/SKILL.md is missing: the final review runs through it; install the "
                       "review-diff skill next to implement-spec\n")


def test_without_an_origin_the_summary_says_that_the_final_review_did_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_final, "REVIEW_DIFF", tmp_path / "review-diff/SKILL.md")  # not needed with no origin
    rig = Inside(tmp_path, ONE, remote=False)
    rig.run(plan())
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 0 and not (rig.repo / ".agent/stage.json").exists(), rig.out
    assert rig.led("End-of-run work skipped") == ["End-of-run work skipped: the repository has no origin remote."]
    assert ("1 of 1 tickets are merged into feat/csv.\nThe end-of-run work (rebase, tidy, final review, push) did not run: "
            "the repository has no origin remote.\n\n| Measure") in rig.out
    fresh = {"pr_branch": "feat/x", "gates": 0, "stops": 0, "agents": [], "final": driver_state._final()}
    assert driver_summary.render({"run": fresh, "tickets": {}}).startswith("0 of 0 tickets are merged into feat/x.\n\n| Measure")


@pytest.mark.parametrize("name", ["final-reviewer", "final-fixer", "rebase-conflict"])
def test_the_templates_of_the_end_of_run_hold_a_valid_schema_and_end_with_the_rule(name):
    jsonschema.Draft202012Validator.check_schema(driver_report.schema(name))
    text = (driver_report.PROMPTS / f"{name}.md").read_text()
    assert text.rstrip().endswith("one ref store.") and text.rfind("{report}") < text.rfind("DONE_WITH_CONCERNS")


# ---------------------------------------------------------------- fix round 1 of ticket 06

@pytest.mark.parametrize("ref_name", ["feat/csv", "other"])
def test_a_history_that_origin_already_has_is_not_rebased_and_not_tidied(tmp_path, ref_name):
    """The PR branch has a commit of its own before the run, and origin has it, as the PR branch or under
    another ref. Then the base moves."""
    rig = Inside(tmp_path, ONE)
    stray(rig, "published before the run")
    published = rig.branch()
    git(rig.repo, "push", "-q", "origin", f"feat/csv:refs/heads/{ref_name}")
    rig.move_base({"docs.md": "base docs\n"})
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 0, rig.out + "\n".join(rig.ledger())
    assert [r["subject"] for r in rig.commits(published)] == ["test: 01 red", "feat: 01 rows"], "no tidy"
    assert ref(rig.repo, "feat/csv~2") == published and not rig.recovery(), "no rebase, so no recovery point"
    assert len(rig.led("End of run: origin has commits of feat/csv, so its history is kept as it is: no tidy and no rebase")) == 1
    assert len(rig.final_calls("final-reviewer")) == 1 and rig.led("End of run: the gate is green on " + rig.branch()[:7])
    assert rig.pushes()[-1] == f"refs/heads/feat/csv {rig.branch()}" and ref(rig.origin, "refs/heads/feat/csv") == rig.branch()
    assert f"- 01: {rig.branch()[:7]} Export rows" in rig.prs()[0]["body"] and stage(rig)["status"] == "done"


def weak(nn="01"):
    return {f"tests/test_{nn}.py": "def test_x():\n    assert True\n"}


def test_a_fix_batch_that_changes_a_locked_test_gives_a_critical_finding_and_is_not_pushed(tmp_path):
    rig = Inside(tmp_path, ONE)
    batch = fixer(fn("01"), "weaken the test", ticket_of="01")
    batch["commits"][0]["files"].update(weak())
    rig.run(plan(**{"final/final-reviewer": [final(finding("Q-ONE")), final()], "final/fixer": [batch]}), *UNATTENDED)
    assert rig.code == 2 and rig.pushes() == [] and not rig.prs(), rig.out
    assert rig.led("Final review round 2: critical finding of kind code at tests/test_01.py:1: locked file changed: tests/test_01.py.")
    (q,) = decisions(rig)["questions"]
    assert q["title"].endswith("locked file changed: tests/test_01.py (tests/test_01.py:1)")


def test_a_rebase_resolution_that_changes_a_locked_test_leaves_the_branch_at_its_recovery_point(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    rig.run(plan(**{"final/conflict": [{**RESOLVE, "files": {"demo/export.py": "x = 1\n", **weak()}}]}), *UNATTENDED)
    assert rig.code == 1 and rig.pushes() == [] and rig.branch() == rig.recovery(), rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: the conflict agent: locked file changed: tests/test_01.py;")
    assert git(rig.repo, "status", "--short") == "" and not rig.final_calls("final-reviewer")


def test_a_rebase_with_no_commit_to_replay_puts_each_ticket_on_the_fetched_base(tmp_path, monkeypatch):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"docs.md": "base docs\n"})
    git(rig.repo, "fetch", "-q", "origin")
    onto = ref(rig.repo, "refs/remotes/origin/main")
    monkeypatch.chdir(rig.repo)
    assert driver_final._rebased(None, {"01": rig.start, "02": rig.start}, "main", onto) == ({"01": onto, "02": onto}, "")
