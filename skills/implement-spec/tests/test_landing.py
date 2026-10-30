"""Ticket 09 acceptance tests: the locked files of merged tickets on a ticket branch; one driver per PR branch.

All through `sh run.sh --runner <script> --parallel 2` on the three-ticket fixture repository (the rig of test_review),
and through two worktrees of one fixture repository, on two PR branches, for the run lock. Tickets are ordered with
signal files and with `wait_pr_moves`, never with a sleep.

Interface choices:
- a ticket agent whose commits change a locked file of a merged ticket is rejected naming the file; its rerun does the
  same, so the ticket ends `blocked` with a `ticket-blocked` question (exit 2) and no commit of it on the PR branch.
- an implementer "merges the PR branch" with the `git` hook of the fake runner: `git merge --no-edit feat/csv` in its
  slot, after `wait_pr_moves`, before it writes its files and commits.
- a rejected test-writer report gets the usual one rerun: the second prompt is the first plus a note that names the
  file the report did not list.
- a driver on another PR branch in another worktree runs its one ticket to exit 0 while the first driver runs, and
  changes no file and no ref of the first run. The lock of the same PR branch is in test_driver.
- "never reported as an uncommitted change": no line of `git status --short` in either worktree has `lock` in it.
"""
import json
import os
import signal
import subprocess
import time

import pytest

from driver_names import Names

from test_cli import RUN, git, models_file, snapshot, three, ticket
from test_review import HOOKED, Review, cli
from test_stops import blocked as contradicted
from test_walk import GATE, Rig, fix, impl, make_env, plan, tw

T01, T03 = "tests/test_01.py", "tests/test_03.py"
LOCKED_01, LOCKED_03 = tw("01")["files"][T01], tw("03")["files"][T03]
WEAK = "def test_x():\n    assert True\n"
NO_REPORT = [{}, {}]  # a fixer that writes no report: a fix round, when one starts, ends BLOCKED


def on_pr(rig, path):
    proc = subprocess.run(["git", "show", f"feat/csv:{path}"], cwd=rig.repo, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def phase(rig, nn):
    return rig.state()["tickets"][nn]["phase"]


def holds(rig, sha, commit="feat/csv"):
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, commit], cwd=rig.repo).returncode == 0


def kept_out(rig, nn, codes=(1,)):
    """Ticket `nn` did not land: the run ends with one of `codes` and the PR branch has none of its commits."""
    assert rig.code in codes, rig.output()
    assert phase(rig, nn) == "blocked"
    assert not {f"test: {nn} red", f"feat: {nn} rows"} & set(rig.pr_subjects()), rig.pr_subjects()
    assert not any(holds(rig, c["sha"]) for c in rig.calls(nn) if c["sha"])


def writer(nn, path, text, listed=True):
    """The test-writer of `nn` that also writes `path`, and lists it as locked or not."""
    step = tw(nn)
    step["files"][path] = text
    step["report"]["locked"] += [path] * listed
    return step


# ---------------------------------------------------------------- a ticket branch that takes the PR branch in

def takes_in(*more):
    """03's implementer in slot 2: it waits until the PR branch moved, merges it into ticket/feat%2Fcsv/03, then commits."""
    return {**impl("03", "demo/cli.py"), "wait_pr_moves": True, "git": [["merge", "-q", "--no-edit", "feat/csv"], *more]}


def test_a_ticket_that_merged_the_pr_branch_into_itself_lands_with_the_tests_of_the_merged_ticket(tmp_path):
    rig = Review(tmp_path, three()).run(plan(**{"03/implementer": [takes_in()], "03/fixer": NO_REPORT}))
    (call,) = rig.calls("03", "implementer")
    assert call["cwd"] == str(rig.slot2) and holds(rig, rig.sha("01", "implementer"), call["sha"]), "01 is in ticket/feat%2Fcsv/03"
    assert not rig.led("locked file changed"), rig.ledger()
    assert rig.code == 0, rig.output()
    assert phase(rig, "03") == "merged" and on_pr(rig, T01) == LOCKED_01 and on_pr(rig, T03) == LOCKED_03


def test_a_ticket_that_merged_the_pr_branch_and_deleted_a_locked_test_of_the_merged_ticket_is_rejected(tmp_path):
    rig = Review(tmp_path, three())
    rig.run(plan(**{"03/implementer": [takes_in(["rm", "-q", T01]), {}]}))
    call = rig.calls("03", "implementer")[0]
    assert holds(rig, rig.sha("01", "implementer"), call["sha"]), "01 is in ticket/feat%2Fcsv/03"
    assert rig.code == 2 and rig.led("Ticket 03: implementer ran on", f"locked file changed: {T01}"), rig.output()
    assert phase(rig, "01") == "merged" and phase(rig, "03") == "blocked" and on_pr(rig, T01) == LOCKED_01


# ---------------------------------------------------------------- the test-writer of a later ticket

def test_a_test_writer_that_extends_a_locked_file_of_a_merged_ticket_and_lists_it_lands_with_the_extension(tmp_path):
    more = LOCKED_01 + "\n\ndef test_y():\n    assert '02' == 'y'\n"
    rig = Review(tmp_path, three()).run(plan(**{"02/test-writer": [writer("02", T01, more)]}))
    assert rig.code == 0, rig.output()
    assert not rig.led("locked file changed") and on_pr(rig, T01) == more
    assert phase(rig, "02") == "merged" and rig.state()["tickets"]["02"]["locked"] == ["tests/test_02.py", T01]


@pytest.mark.parametrize("second_lists", [False, True])
def test_a_test_writer_that_changes_a_locked_file_of_a_merged_ticket_and_does_not_list_it_is_rerun_once(tmp_path, second_lists):
    one = LOCKED_01 + "\n\ndef test_y():\n    assert '02' == 'y'\n"
    two = one + "\n\ndef test_z():\n    assert '02' == 'z'\n"
    again = {**writer("02", T01, two, listed=second_lists), "commit": "test: 02 red again"}
    rig = Review(tmp_path, three()).run(plan(**{"02/test-writer": [writer("02", T01, one, listed=False), again]}))
    calls = rig.calls("02", "test-writer")
    assert len(calls) == 2, rig.output()
    note = calls[1]["prompt"].replace(calls[0]["prompt"].strip(), "")
    assert calls[0]["prompt"].strip() in calls[1]["prompt"] and T01 in note and "locked" in note
    if second_lists:
        assert rig.code == 0, rig.output()
        assert phase(rig, "02") == "merged" and on_pr(rig, T01) == two and not rig.led("locked file changed")
    else:
        assert rig.code == 2, rig.output()
        assert rig.led("02", "BLOCKED", T01) and phase(rig, "02") == "blocked"
        assert not rig.calls("02", "implementer") and on_pr(rig, T01) == LOCKED_01


# ---------------------------------------------------------------- the conflict agent

@pytest.mark.parametrize("changes", [True, False])
def test_a_conflict_resolution_lands_only_when_it_leaves_the_locked_files_of_the_merged_tickets_alone(tmp_path, changes):
    """01 and 03 both add demo/cli.py; 03 merges first, so the merge of 01 goes to the conflict agent."""
    resolve = {"files": {"demo/cli.py": cli("resolved"), **({T03: WEAK} if changes else {})}, "commit": "merge: resolve cli",
               "report": {"status": "DONE", "commits": ["$SHA"], "files": ["demo/cli.py"], "concerns": []}}
    one = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "demo/cli.py": cli("one")})
    rig = Review(tmp_path, three()).run(plan(**{
        "01/test-writer": [{**tw("01"), "touch": ["s1"]}],
        "03/implementer": [{**impl("03", "demo/cli.py", cli("three")), "wait": ["s1"]}],
        "01/implementer": [{**one, "wait_pr_moves": True}], "01/conflict": [resolve, resolve]}))
    assert len(rig.calls("01", "conflict")) == 1 + changes and phase(rig, "03") == "merged", rig.output()
    assert on_pr(rig, T03) == LOCKED_03
    if changes:
        kept_out(rig, "01", codes=(2,))
        (question,) = rig.state()["run"]["questions"]
        assert question["kind"] == "ticket-blocked" and f"locked file changed: {T03}" in question["subject"]
        assert on_pr(rig, "demo/cli.py") == cli("three") and not rig.calls("02")
    else:
        assert rig.code == 0, rig.output()
        assert on_pr(rig, "demo/cli.py") == cli("resolved") and not rig.led("locked file changed")


# ---------------------------------------------------------------- one driver for each repository

OTHER = {"04/test-writer": [tw("04")], "04/implementer": [impl("04", "demo/other.py")]}
PATIENT = HOOKED.replace("time.time() + 6", "time.time() + 60")  # an agent that waits for the test, not for 6 s
assert PATIENT != HOOKED


class Other(Rig):
    """A second worktree of the repository of `first`: its own PR branch feat/other, ticket 04, runner and gate."""

    def __init__(self, first):
        self.tmp = first.tmp.parent / "other"
        self.tmp.mkdir()
        self.env, self.repo, self.rig = make_env(self.tmp), self.tmp / "demo", self.tmp / "rig"
        git(first.repo, "worktree", "add", "-q", "-b", "feat/other", str(self.repo), "feat/csv")
        (self.repo / ".agent/tickets").mkdir(parents=True)
        (self.repo / ".agent/tickets/04-other.md").write_text(ticket("04", "Other", touches="`demo/other.py: other`"))
        self.rig.mkdir()
        for name, text in (("run-agent", HOOKED.replace("feat/csv", "feat/other")), ("gate", GATE)):
            (self.rig / name).write_text(text)
            (self.rig / name).chmod(0o755)
        self.models = models_file(self.tmp)
        self.gate_conf()


def pair(tmp_path):
    (tmp_path / "first").mkdir()
    first = Review(tmp_path / "first", three())
    (first.rig / "run-agent").write_text(PATIENT)
    return first, Other(first)


def start(rig, steps, *extra):
    """The driver of `rig` in the background: `Rig.run` returns only when the driver ended."""
    (rig.rig / "plan.json").write_text(json.dumps(steps))
    args = ["--tickets", ".agent/tickets", "--gate", f"TIER=full {rig.rig / 'gate'}", "--fast-gate", f"TIER=fast {rig.rig / 'gate'}", "--spec", "specs/csv/spec.md",
            "--models", str(rig.models), "--runner", str(rig.rig / "run-agent"), "--parallel", "2", *extra]
    return subprocess.Popen(["sh", str(RUN), *args], cwd=rig.repo, env=rig.env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)


def ended(proc):
    """No driver outlives its test."""
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()


def until(done, seconds=30):
    end = time.time() + seconds
    while not done():
        assert time.time() < end, "the first driver did not reach its agent"
        time.sleep(0.02)


def no_lock_is_dirty(*rigs):
    for rig in rigs:
        status = git(rig.repo, "status", "--short")
        assert "lock" not in status, status


def first_run_refs(rig):
    """The refs of the run on feat/csv: its PR branch, its ticket branches and its recovery ref."""
    names = Names("feat/csv")
    refs = git(rig.repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    return [ref for ref in refs if ref.split()[0] in ("refs/heads/feat/csv", names.recovery)
            or ref.startswith(f"refs/heads/{names.ticket('')}")]


def test_a_driver_on_another_pr_branch_in_another_worktree_runs_while_the_first_runs(tmp_path):
    """Runs on different PR branches share no state.json, no slot and no ref: the second one ends while the first
    holds both its slots, and leaves each file and each ref of the first as it was."""
    first, second = pair(tmp_path)
    held = {f"{nn}/test-writer": [{**tw(nn), "wait": ["go"]}] for nn in ("01", "03")}
    proc = start(first, plan(**held))
    try:
        until(lambda: len(first.calls(role="test-writer")) == 2)  # the first driver holds both its slots in a role
        before = (snapshot(first.repo), first_run_refs(first))
        assert [ref for ref in before[1] if "/ticket/" in ref], before[1]  # the first run has its ticket branches
        second.run(OTHER)
        assert second.code == 0 and second.calls("04", "implementer"), second.output()
        assert (snapshot(first.repo), first_run_refs(first)) == before
        assert not git(second.repo, "branch", "--list", "ticket/feat%2Fother/*")  # the merge deleted ticket 04's branch
        no_lock_is_dirty(first, second)
        (first.rig / "sig/go").touch()
        out, _ = proc.communicate(timeout=120)
    finally:
        ended(proc)
    assert proc.returncode == 0, out
    no_lock_is_dirty(first, second)


ENDS = {
    0: (plan(), {}, ()),
    1: (plan(**{"01/fixer": [fix("01", n) for n in (1, 2)]}), {"red": ["ticket/feat%2Fcsv/01"]}, ()),
    2: (plan(**{"01/implementer": [contradicted()]}), {}, ()),
    3: (plan(), {}, ("--prepare", "false")),  # slot 2 cannot be made: the run has started and holds the lock
    130: (plan(**{"01/test-writer": [{**tw("01"), "wait": ["never"]}]}), {}, ()),
}


@pytest.mark.parametrize("code", ENDS)
def test_the_lock_is_free_after_each_end_of_a_run_and_is_never_an_uncommitted_change(tmp_path, code):
    first, second = pair(tmp_path)
    steps, gate, extra = ENDS[code]
    first.gate_conf(**gate)
    proc = start(first, steps, *extra)
    try:
        if code == 130:
            until(lambda: first.calls("01", "test-writer"))
            no_lock_is_dirty(first, second)
            proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=120)
    finally:
        ended(proc)
    assert proc.returncode == code, out
    no_lock_is_dirty(first, second)
    second.run(OTHER)
    assert second.code == 0 and second.calls("04", "implementer"), second.output()
    no_lock_is_dirty(first, second)
