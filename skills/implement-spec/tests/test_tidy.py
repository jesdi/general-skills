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
from test_walk import fix, plan, tw


class Inside(test_final.Final):
    """The rig of test_final with the driver called in this process."""

    def run(self, steps, *extra, parallel="2", watch=False):
        (self.rig / "plan.json").write_text(json.dumps(steps))
        args = ["--tickets", ".agent/tickets", "--gate", f"TIER=full {self.rig / 'gate'}", "--fast-gate", f"TIER=fast {self.rig / 'gate'}", "--spec", "specs/csv/spec.md",
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
    """The fake `gh` of the rig is on PATH, no git call of an agent opens an editor, and the driver signs no commit."""
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_EDITOR", "true")
    for name, value in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "commit.gpgsign"), ("GIT_CONFIG_VALUE_0", "false")):
        monkeypatch.setenv(name, value)


def stray(rig, text="by hand"):
    """A commit on the PR branch that no agent of the run made."""
    (rig.repo / "stray.txt").write_text(text)
    git(rig.repo, "add", "-A")
    git(rig.repo, "commit", "-q", "-m", text)


# ---------------------------------------------------------------- the story: local stop, rerun, push

def test_a_local_run_stops_before_the_push_and_a_push_rerun_pushes_and_opens_the_pr_once(tmp_path, pr_skill):
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
    assert ref(rig.repo, "feat/csv^{tree}") == ref(rig.repo, "refs/implement-spec/feat%2Fcsv/recovery^{tree}")
    assert rig.led("Recovery point: feat/csv was at " + rig.recovery(), ref(rig.repo, "feat/csv^{tree}"))
    assert rig.led("End of run: final-spec ran on", FABLE) and rig.led("End of run: the full gate is green on " + tip[:7])
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
    assert pr["body"].startswith("## Summary\n\nCSV export.\n\n## Merge Danger\n"), "the pr-writer's body, then the facts"
    assert [f"- {nn}: {row['sha'][:7]} {TITLES[nn]}" for nn, row in zip(order, rows)] == pr["body"].split("## Tickets\n\nOne commit per ticket, in merge order:\n\n")[1].splitlines()[:3]
    (writer,) = rig.final_calls("pr-writer")
    assert f"PR method: {pr_skill}\n" in writer["prompt"]
    assert "Facts of the run, which the driver puts after your body:\n## Tickets" in writer["prompt"]
    assert "## Rulings\n\nNone.\n" in pr["body"] and "Scoped review after the fix batch: not needed" in pr["body"]
    assert f"Pushed feat/csv at {tip} to origin." in rig.out and "PR: https://github.example/pull/1" in rig.out
    assert f"feat/csv is pushed at {tip[:7]}; its PR is https://github.example/pull/1." in rig.out
    assert rig.led("The PR of feat/csv into main is open: https://github.example/pull/1.")

    rig.run(plan(), "--push")
    assert rig.code == 0 and len(rig.pushes()) == 1 and len(rig.prs()) == 1, "a second push rerun sends nothing"
    assert (len(rig.calls()), len(rig.gates())) == (calls + 1, gates), "the one agent of the pushes is the pr-writer"


def test_a_base_branch_takes_the_place_of_the_default_branch_for_the_rebase_and_the_pr(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"docs.md": "main docs\n"})
    clone = rig.tmp / "base-clone"
    git(clone, "checkout", "-q", "-b", "integration", rig.start)
    (clone / "integration.md").write_text("integration\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "integration moves")
    git(clone, "push", "-q", "origin", "integration")
    onto = ref(rig.origin, "refs/heads/integration")
    rig.run(plan(), "--push", "--base", "integration")
    assert rig.code == 0, rig.out + "\n".join(rig.ledger())
    assert (rig.final()["base"], rig.final()["onto"]) == ("integration", onto)
    assert git(rig.repo, "merge-base", "--is-ancestor", onto, "feat/csv") == ""
    assert not (rig.repo / "docs.md").exists(), "the default branch is not under the history"
    (pr,) = rig.prs()
    assert pr["argv"][2:6] == ["--base", "integration", "--head", "feat/csv"]


@pytest.mark.parametrize("base, error", [
    ("nowhere", "git fetch origin did not give refs/remotes/origin/nowhere: "),
    ("feat/csv", "feat/csv is the base branch: the end of run needs a PR branch."),
])
def test_a_base_branch_that_cannot_hold_the_pr_stops_the_end_of_run_before_the_recovery_point(tmp_path, base, error):
    rig = Inside(tmp_path, ONE)
    git(rig.repo, "push", "-q", "origin", "feat/csv")
    rig.run(plan(), "--base", base)
    assert rig.code == 1 and not rig.recovery(), rig.out
    assert rig.led("End of run: BLOCKED at " + error)


def test_a_base_branch_needs_an_origin(tmp_path):
    rig = Inside(tmp_path, ONE, remote=False)
    rig.run(plan(), "--base", "integration")
    assert rig.code == 3 and "--base integration names a branch of origin, and the repository has no origin" in rig.out


def test_the_final_review_does_not_start_while_a_ticket_is_not_merged_and_the_ledger_says_so_once(tmp_path):
    rig = Inside(tmp_path, THREE, gate={"red": ["ticket/feat%2Fcsv/01"]})
    rig.run(plan(**{"01/fixer": [fix("01", n) for n in (1, 2)]}))
    rig.run(plan(**{"01/fixer": [fix("01", n) for n in (1, 2)]}), *UNATTENDED)
    assert rig.code == 1 and not rig.final_calls("final-spec") and not rig.recovery()
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
    assert not rig.final_calls("final-spec") and "End of run: BLOCKED at the rewrite" in rig.out


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
    rig.run(plan(**{"final/final-spec": [final(finding("Q-ONE"), finding("Q-MINOR", "minor"))],
                    "final/fixer": [first, retry]}))
    assert rig.code == 2, rig.out
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
    rig.run(plan(), "--answer", "final-ticket-blocked-1=retry", "--push")
    assert rig.code == 1 and rig.pushes() == [] and len(rig.final_calls("final-spec")) == 1, rig.out
    assert rig.led("End of run: BLOCKED at the tip", f"is not a tip the driver made: only {reviewed[:7]} gets a review")

    git(rig.repo, "reset", "-q", "--hard", reviewed)
    rig.run(plan(**{"final/fixer": [{}, {}, fixer(fn("02"), "quote commas")]}), "--push")
    assert rig.code == 0 and [r["subject"] for r in rig.commits(reviewed)] == ["fix(02): quote commas"], rig.out
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and rig.final()["batch"] == rig.branch()


def test_a_fixer_that_times_out_twice_keeps_its_files_and_the_branch_stays_at_the_reviewed_tip(tmp_path):
    rig = Inside(tmp_path, ONE)
    slow = {"files": {"wip.txt": "half done\n"}, "sleep": 30}
    rig.run(plan(**{"final/final-spec": [final(finding("Q-ONE"))], "final/fixer": [slow, slow]}),
            "--timeout", "fixer=0.01", *UNATTENDED)
    reviewed = rig.final()["reviewed"]
    assert rig.code == 1 and rig.pushes() == [] and rig.branch() == reviewed, rig.out
    assert (rig.repo / "wip.txt").read_text() == "half done\n"
    assert rig.led(f"End of run: the fix batch was rejected, so the driver moved feat/csv back to its reviewed tip "
                   f"{reviewed[:7]}; the fixer made no commit. The files of the fixer stay in slot 1 as they are.")


def test_a_fix_commit_for_a_ticket_that_is_not_in_the_run_is_a_violation(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.run(plan(**{"final/final-spec": [final(finding("Q-ONE"))],
                    "final/fixer": [fixer(fn("01"), "fix(01): named already", ticket_of="09"),
                                    {"report": dict(fixer(fn("01"), "x")["report"], commits=[{"sha": "$HEAD", "ticket": "01"}])}]}))
    assert rig.code == 0, rig.out
    assert rig.led("End of run: fixer ran on", ": 09 is not a ticket of this run")
    (row,) = rig.commits(rig.final()["tidied"])
    assert row["subject"] == "fix(01): named already" and rig.final()["reviewed"] == row["sha"] == rig.branch()
    assert rig.led("Fix batch of the final review: 1 commit on", f"{row['sha'][:7]} fix(01): named already.")
    (scoped,) = [call["prompt"] for call in rig.final_calls("final-reviewer")]
    assert f"Range: {rig.final()['tidied']}..{row['sha']}\n" in scoped and "This is the scoped review after the fix batch" in scoped
    assert scoped.rstrip().endswith("one ref store.") and ".agent/implement/final/findings-0.json" in scoped


def test_a_lens_that_changes_the_slot_is_restored_and_every_lens_runs_once_more(tmp_path):
    rig = Inside(tmp_path, ONE)
    dirty = final(finding("DROPPED"), files={"left.txt": "x"})
    moved_head = final(files={"more.txt": "x"}, commit="by the reviewer")
    rig.run(plan(**{"final/final-spec": [dirty, final(finding("KEPT", "minor"))]}))
    assert [len(rig.final_calls(role)) for role in ("final-spec", "final-correctness", "final-structure")] == [2, 2, 2]
    assert rig.code == 0 and not (rig.repo / "left.txt").exists(), rig.out
    assert rig.led("End of run: a read-only role broke its rule: the tree is not as the review found it: left.txt; the "
                   f"driver restored slot 1 to {rig.branch()[:7]} and runs the final review again.")
    assert rig.led("KEPT") and not rig.led("DROPPED") and rig.final()["reviewed"] == rig.branch()


def test_a_lens_that_changes_the_slot_twice_ends_the_run_with_a_clean_slot(tmp_path):
    rig = Inside(tmp_path, ONE)
    moved_head = final(files={"more.txt": "x"}, commit="by the reviewer")
    rig.run(plan(**{"final/final-spec": [final(files={"left.txt": "x"}), moved_head]}), *UNATTENDED)
    tip = rig.final()["tidied"]
    assert rig.code == 1 and rig.final()["reviewed"] is None and rig.branch() == tip, rig.out
    assert rig.led(f"End of run: BLOCKED at the final review: the slot is no longer on feat/csv at {tip[:7]}.")
    assert git(rig.repo, "status", "--short") == "" and git(rig.repo, "symbolic-ref", "--short", "HEAD") == "feat/csv\n"
    rig.run(plan(), *UNATTENDED)  # the rerun is not stopped by what the reviewer left
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {tip}"], rig.out


def test_a_red_gate_on_the_tidied_tip_stops_before_the_final_review(tmp_path):
    rig = Inside(tmp_path, ONE, gate={"red": ["feat/csv"]})
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1 and not rig.final_calls("final-spec") and rig.pushes() == [], rig.out
    assert rig.led("End of run: BLOCKED at the gate: the full gate is red on " + rig.branch()[:7])
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["final"]}
    # The fast gate of the ticket tip, the full gate of its fast-forward, and the full gate of the tidied tip.
    assert rig.state()["run"]["gates"] == {"fast": 1, "full": 2} and "| full gates run | 2 |" in rig.out


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
    assert [p.name for p in (rig.repo / ".agent/implement/final").glob("gate-*.log")] == [f"gate-full-{rig.branch()[:7]}.log"]
    assert rig.final()["commits"] == {nn: row["sha"] for nn, row in zip(order, rows)}
    assert rig.final()["onto"] == base and f"Range: {base}..{rig.branch()}\n" in rig.final_calls("final-spec")[0]["prompt"]
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and stage(rig) == {"stage": "implement", "status": "done"}


def test_a_resolved_rebase_keeps_the_ticket_messages_and_gates_the_rebased_tip_and_the_tidied_tip(tmp_path):
    cli = {"demo/cli.py": "def main():\n    return 'base'\n"}
    resolved = {**RESOLVE, "files": {"demo/cli.py": "def main():\n    return 'both'\n"}}
    rig = moved(tmp_path, cli, resolved)  # the agent commits as "resolve conflict" and goes on with the rebase
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
    assert records == sorted([f"gate-full-{rebased}.log", f"gate-full-{rig.branch()[:7]}.log"]) and rebased != rig.branch()[:7]
    assert ref(rig.repo, f"{rebased}^{{tree}}") == ref(rig.repo, "feat/csv^{tree}")


def test_a_second_conflict_leaves_the_branch_at_its_recovery_point_with_all_its_commits(tmp_path):
    step = {**RESOLVE, "files": {"demo/export.py": "resolved = 1\n"}, "report": {**RESOLVE["report"], "files": ["demo/export.py"]}}
    rig = moved(tmp_path, {"demo/export.py": "def export_csv():\n    return 'base'\n"}, step)
    assert rig.code == 1 and len(rig.final_calls("conflict")) == 1 and not rig.final_calls("final-spec"), rig.out
    recovery = rig.recovery()
    assert rig.branch() == recovery and len(rig.commits(rig.start, recovery)) == 6, "two commits per ticket, as before"
    assert rig.led("End of run: BLOCKED at the rewrite: the rebase onto main conflicts a second time, in demo/export.py; "
                   f"feat/csv is unrewritten at its recovery point {recovery[:7]}.")
    assert git(rig.repo, "symbolic-ref", "--short", "HEAD").strip() == "feat/csv" and git(rig.repo, "status", "--short") == ""
    assert rig.pushes() == [] and stage(rig)["blocked"] == ["final"] and not rig.final().get("tidied")
    assert {e["phase"] for e in rig.state()["tickets"].values()} == {"merged"}

    rig.run(plan(), *UNATTENDED)  # the rerun starts the rewrite again from the same recovery point
    assert rig.code == 2 and rig.recovery() == recovery == rig.branch()
    assert len(rig.final_calls("conflict")) == 3, "the conflict agent of the rerun, which has no script, and its one rerun"
    (question,) = decisions(rig)["questions"]
    assert question["id"] == "final-ticket-blocked-1" and "End of run: an agent stopped" in question["title"]
    assert "the conflict: report .agent/implement/final/conflict-0.json is not readable JSON" in question["title"]
    assert not rig.led("returned to implement")


def test_a_conflict_agent_that_gives_up_leaves_no_rebase_open(tmp_path):
    stuck = {"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["both sides are needed"]}}
    rig = moved(tmp_path, {"demo/cli.py": "base\n"}, stuck, stuck)
    assert rig.code == 2 and rig.branch() == rig.recovery() and len(rig.final_calls("conflict")) == 2, rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: the conflict agent: it reported BLOCKED: both sides are needed;")
    assert git(rig.repo, "status", "--short") == "" and git(rig.repo, "symbolic-ref", "--short", "HEAD") == "feat/csv\n"
    assert not (rig.repo / ".git/rebase-merge").exists()


def test_a_red_gate_after_the_rebase_leaves_the_branch_unrewritten(tmp_path):
    rig = Inside(tmp_path, ONE, gate={"unbranched": True})  # red on a commit that no branch holds: the rebased tip
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    rig.run(plan(**{"final/conflict": [{**RESOLVE, "files": {"demo/export.py": "x = 1\n"}}]}))
    assert rig.code == 1 and rig.branch() == rig.recovery(), rig.out
    assert rig.led("End of run: BLOCKED at the rewrite: the gate after the rebase: the full gate is red on ")


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
    state["run"]["final"]["green_gates"] = {"fast": [], "full": []}
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    rig.run(plan(), "--push")
    assert rig.code == 1 and rig.pushes() == [] and not rig.prs(), rig.out
    assert rig.led(f"End of run: BLOCKED at the reviewed tip {rig.branch()[:7]} is not the tip of feat/csv with a green "
                   "full gate record: nothing is pushed.")


def test_the_rerun_command_drops_the_answers_and_the_pr_title_falls_back_to_the_branch():
    command = driver_final.again(["--gate", "make check", "--answer", "final-ticket-blocked-1=retry", "--answer=x=y", "--spec", "s.md"])
    assert command == f"sh {driver_final.driver_agents.SKILL / 'run.sh'} --gate 'make check' --spec s.md --push"
    assert driver_final._name(SimpleNamespace(args=SimpleNamespace(spec="nowhere.md"), pr="feat/x")) == "feat/x"


# ---------------------------------------------------------------- preconditions and the skip

@pytest.mark.parametrize("remote", [True, False], ids=["origin", "no-origin"])
def test_without_the_review_diff_skill_every_repository_stops_before_any_agent(tmp_path, monkeypatch, remote):
    monkeypatch.setattr(driver_final, "REVIEW_DIFF", tmp_path / "review-diff/SKILL.md")
    rig = Inside(tmp_path, ONE, remote=remote).run(plan())
    assert rig.code == 3 and not rig.calls(), rig.out
    assert rig.out == "".join(f"the review-diff skill is missing its {name}: the {lens} lens of the final review "
                              "follows it; install it next to implement-spec with `npx skills add jesdi/general-skills "
                              "--skill review-diff`\n" for name, lens in (("spec.md", "spec"), ("correctness.md", "correctness")))


def test_a_push_without_the_pr_skill_stops_before_any_agent_and_a_local_run_does_not_need_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-claude-home"))
    rig = Inside(tmp_path, ONE).run(plan(), "--push")
    assert rig.code == 3 and not rig.calls(), rig.out
    assert rig.out.startswith("the pr skill is missing: the PR body follows it; install it with ")

    rig.run(plan())
    assert rig.code == 0 and not rig.final_calls("pr-writer"), rig.out

    (tmp_path / "home/.agents/skills/pr").mkdir(parents=True)
    (tmp_path / "home/.agents/skills/pr/SKILL.md").write_text("---\nname: pr\n---\n")
    rig.run(plan(), "--push")
    assert rig.code == 0 and len(rig.prs()) == 1, rig.out
    (writer,) = rig.final_calls("pr-writer")
    assert f"PR method: {tmp_path}/home/.agents/skills/pr/SKILL.md\n" in writer["prompt"]


def test_without_an_origin_the_run_ends_reviewed_and_a_push_is_refused_at_the_start_and_after_the_tidy(tmp_path):
    rig = Inside(tmp_path, ONE, remote=False).run(plan())
    assert rig.code == 0, rig.out
    tip, final = rig.branch(), rig.final()
    assert (final["base"], final["onto"], final["reviewed"]) == ("", rig.state()["run"]["start"], tip)
    assert rig.led("no origin") == ["End of run: the repository has no origin remote, so the rebase and the push are "
                                    "skipped; the tidy, the gate and the final review run."]
    assert (f"1 of 1 tickets are merged into feat/csv.\nThe final review covers feat/csv up to {tip[:7]}. Nothing is "
            "pushed.\n\n| Measure") in rig.out
    assert rig.out.endswith(f"Reviewed tip: {tip}\nNothing is pushed. The repository has no origin remote.\n")
    # A run that is to push needs the remote: it stops before the lock of a new run and before any agent.
    calls = len(rig.calls())
    for flags in (UNATTENDED, ("--push",)):
        rig.run(plan(), *flags)
        assert rig.code == 3 and len(rig.calls()) == calls and not (rig.repo / ".agent/stage.json").exists()
        assert rig.out == ("the repository has no origin remote: --unattended and --push push the PR branch to origin; "
                           "add the remote, or run without them to stop before the push\n")
    # An origin that comes after the tidy does not make the branch a PR branch: it is on no base of that origin.
    git(rig.repo, "remote", "add", "origin", str(tmp_path / "late.git"))
    rig.run(plan(), "--push")
    assert rig.code == 1 and not rig.pushes() and not rig.prs() and rig.branch() == tip
    assert rig.led("BLOCKED") == [
        "End of run: BLOCKED at the end of run tidied feat/csv when the repository had no origin remote, so it is on "
        "no base branch of origin: nothing is pushed."]
    fresh = {"pr_branch": "feat/x", "gates": {"fast": 0, "full": 0}, "stops": 0, "agents": [], "final": driver_state._final()}
    assert driver_summary.render({"run": fresh, "tickets": {}}).startswith("0 of 0 tickets are merged into feat/x.\n\n| Measure")


@pytest.mark.parametrize("name", ["final-reviewer", "final-lens", "final-fixer", "rebase-conflict"])
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
    assert len(rig.final_calls("final-spec")) == 1 and rig.led("End of run: the full gate is green on " + rig.branch()[:7])
    assert rig.pushes()[-1] == f"refs/heads/feat/csv {rig.branch()}" and ref(rig.origin, "refs/heads/feat/csv") == rig.branch()
    assert f"- 01: {rig.branch()[:7]} Export rows" in rig.prs()[0]["body"] and stage(rig)["status"] == "done"


def weak(nn="01"):
    return {f"tests/test_{nn}.py": "def test_x():\n    assert True\n"}


def test_a_rebase_resolution_that_changes_a_locked_test_is_rejected_and_the_conflict_agent_runs_again(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    # The agent does not go on with the rebase: the driver does, then checks the rebased tip.
    weakens = {"commit": "resolve conflict", "files": {"demo/export.py": "x = 1\n", **weak()}, "report": RESOLVE["report"]}
    puts_back = {"files": tw("01")["files"], "git_end": [["commit", "-q", "-a", "--amend", "--no-edit"]],
                 "report": RESOLVE["report"]}
    rig.run(plan(**{"final/conflict": [weakens, puts_back]}), *UNATTENDED)
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"], rig.out
    first, second = rig.led("End of run: conflict ran on")
    assert "locked file changed: tests/test_01.py" in first and second.endswith("report accepted.")
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == tw("01")["files"]["tests/test_01.py"]
    assert git(rig.repo, "show", "feat/csv:demo/export.py") == "x = 1\n"


def test_a_rebase_the_agent_left_open_with_no_conflict_is_continued_by_the_driver(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    committed = {"commit": "resolve conflict", "files": {"demo/export.py": "x = 1\n"}, "report": RESOLVE["report"]}
    rig.run(plan(**{"final/conflict": [committed]}), *UNATTENDED)
    assert rig.code == 0, rig.out
    (only,) = rig.led("End of run: conflict ran on")
    assert only.endswith("report accepted.") and git(rig.repo, "show", "feat/csv:demo/export.py") == "x = 1\n"


def test_a_rebase_resolution_the_agent_never_staged_is_rejected_with_the_git_error_and_the_agent_runs_again(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    unstaged = {"files": {"demo/export.py": "x = 1\n"}, "git_end": [["rev-parse", "HEAD"]], "report": RESOLVE["report"]}
    stages = {"git_end": [["add", "-A"], ["rebase", "--continue"]], "report": RESOLVE["report"]}
    rig.run(plan(**{"final/conflict": [unstaged, stages]}), *UNATTENDED)
    assert rig.code == 0, rig.out + "\n".join(rig.ledger())
    first, second = rig.led("End of run: conflict ran on")
    assert "git rebase --continue failed at the stop you resolved: " in first and second.endswith("report accepted.")
    assert git(rig.repo, "show", "feat/csv:demo/export.py") == "x = 1\n"


def test_a_staged_resolution_reported_at_the_stop_is_checked_on_the_tip_the_driver_rebased(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    staged = {"files": {"demo/export.py": "x = 1\n"}, "git_end": [["add", "-A"]], "report": RESOLVE["report"]}
    rig.run(plan(**{"final/conflict": [staged]}), *UNATTENDED)
    assert rig.code == 0, rig.out + "\n".join(rig.ledger())
    (only,) = rig.led("End of run: conflict ran on")
    assert only.endswith("report accepted.") and git(rig.repo, "show", "feat/csv:demo/export.py") == "x = 1\n"


def test_a_rebase_with_no_commit_to_replay_puts_each_ticket_on_the_fetched_base(tmp_path, monkeypatch):
    rig = Inside(tmp_path, ONE)
    rig.move_base({"docs.md": "base docs\n"})
    git(rig.repo, "fetch", "-q", "origin")
    onto = ref(rig.repo, "refs/remotes/origin/main")
    monkeypatch.chdir(rig.repo)
    assert driver_final._rebased(None, {"01": rig.start, "02": rig.start}, "main", onto) == {"01": onto, "02": onto}
