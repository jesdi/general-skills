"""Ticket 09 acceptance tests: locked tests are checked on the merge result; one driver per repository.

All through `sh run.sh --runner <script> --parallel 2` on the three-ticket fixture repository (the rigs of test_review
and test_final), and through two worktrees of one fixture repository for the run lock. Tickets are ordered with signal
files and with `wait_pr_moves`, never with a sleep.

Interface choices (ticket 09):
- a ticket that "does not land" ends the run with exit 1 and phase `blocked`; one ledger sentence names the ticket and
  `locked file changed: <path>`; no commit of the ticket is on the PR branch. Whether a fix round ran before is free:
  each plan gives that ticket fixers that write no report.
- an implementer "merges the PR branch" with the `git` hook of the fake runner: `git merge --no-edit feat/csv` in its
  slot, after `wait_pr_moves`, before it writes its files and commits.
- a rejected test-writer report gets the usual one rerun: the second prompt is the first plus a note that names the
  file the report did not list.
- check and ref move as one step: no black-box test can stop the driver between the two. The one window a test can
  hold open is the merge gate, which runs between the merge commit and the ref move. The gate of the first merge
  commit stays open until the reviewer of the other ticket ended, plus a bounded time for that ticket to reach its
  landing. Either order of the two landings is correct, and so is a driver that gives the stale merge commit up;
  a landing after the lock with the weakened file is not, and a landing that is lost is not.
- the lock sentence is the whole stdout of the second driver, one line, and it names the lock by a path that exists
  when it is resolved in the second worktree (an absolute path, or a path relative to that worktree).
- "the second one starts" and "the lock is released": a driver in the other worktree runs its one ticket to exit 0.
- "never reported as an uncommitted change": no line of `git status --short` in either worktree has `lock` in it.
"""
import json
import os
import signal
import subprocess
import time

import pytest

from test_cli import RUN, git, models_file, snapshot, three, ticket
from test_final import Final, ref
from test_review import HOOKED, Review, cli
from test_stops import UNATTENDED, blocked as contradicted
from test_walk import GATE, Rig, impl, make_env, plan, tw

T01, T03, SHARED = "tests/test_01.py", "tests/test_03.py", "tests/test_shared.py"
LOCKED_01, LOCKED_03 = tw("01")["files"][T01], tw("03")["files"][T03]
WEAK = "def test_x():\n    assert True\n"
# A test file of the repository's start. A weaker first test and a new last test merge with no conflict.
SEED = ("def test_first():\n    assert 1 + 1 == 2\n\n\n# one\n# two\n# three\n# four\n# five\n# six\n\n\n"
        "def test_last():\n    assert 2 + 2 == 4\n")
WEAK_SEED = SEED.replace("assert 1 + 1 == 2", "assert True")
MORE_SEED = SEED + "\n\ndef test_more():\n    assert 3 + 3 == 'x'\n"
NO_REPORT = [{}, {}]  # a fixer that writes no report: a fix round, when one starts, ends BLOCKED


def on_pr(rig, path):
    proc = subprocess.run(["git", "show", f"feat/csv:{path}"], cwd=rig.repo, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def phase(rig, nn):
    return rig.state()["tickets"][nn]["phase"]


def holds(rig, sha, commit="feat/csv"):
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, commit], cwd=rig.repo).returncode == 0


def kept_out(rig, nn):
    """Ticket `nn` did not land: the run ends with exit 1 and the PR branch has none of its commits."""
    assert rig.code == 1, rig.output()
    assert phase(rig, nn) == "blocked"
    assert not {f"test: {nn} red", f"feat: {nn} rows"} & set(rig.pr_subjects()), rig.pr_subjects()
    assert not any(holds(rig, c["sha"]) for c in rig.calls(nn) if c["sha"])


def refused(rig, nn, path):
    """The landing of ticket `nn` was refused for the locked file `path` of a merged ticket."""
    kept_out(rig, nn)
    assert rig.led(nn, f"locked file changed: {path}"), rig.ledger()


def writer(nn, path, text, listed=True):
    """The test-writer of `nn` that also writes `path`, and lists it as locked or not."""
    step = tw(nn)
    step["files"][path] = text
    step["report"]["locked"] += [path] * listed
    return step


def seeded(rig):
    (rig.repo / SHARED).parent.mkdir()
    (rig.repo / SHARED).write_text(SEED)
    git(rig.repo, "add", "-A")
    git(rig.repo, "commit", "-q", "-m", "seed")
    return rig


# ---------------------------------------------------------------- the implementer of a later ticket

def test_an_implementer_that_weakens_the_locked_test_of_a_merged_ticket_does_not_land_and_nothing_is_pushed(tmp_path):
    rig = Final(tmp_path, three())
    weak = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", T01: WEAK})
    rig.run(plan(**{"02/implementer": [weak], "02/fixer": NO_REPORT}), *UNATTENDED)
    refused(rig, "02", T01)
    assert phase(rig, "01") == "merged" and on_pr(rig, T01) == LOCKED_01
    assert rig.pushes() == [] and not rig.prs() and not ref(rig.origin, "refs/heads/feat/csv")


# ---------------------------------------------------------------- a ticket branch that takes the PR branch in

def takes_in(*more):
    """03's implementer in slot 2: it waits until the PR branch moved, merges it into ticket/03, then commits."""
    return {**impl("03", "demo/cli.py"), "wait_pr_moves": True, "git": [["merge", "-q", "--no-edit", "feat/csv"], *more]}


def test_a_ticket_that_merged_the_pr_branch_into_itself_lands_with_the_tests_of_the_merged_ticket(tmp_path):
    rig = Review(tmp_path, three()).run(plan(**{"03/implementer": [takes_in()], "03/fixer": NO_REPORT}))
    (call,) = rig.calls("03", "implementer")
    assert call["cwd"] == str(rig.slot2) and holds(rig, rig.sha("01", "implementer"), call["sha"]), "01 is in ticket/03"
    assert not rig.led("locked file changed"), rig.ledger()
    assert rig.code == 0, rig.output()
    assert phase(rig, "03") == "merged" and on_pr(rig, T01) == LOCKED_01 and on_pr(rig, T03) == LOCKED_03


def test_a_ticket_that_merged_the_pr_branch_and_deleted_a_locked_test_of_the_merged_ticket_does_not_land(tmp_path):
    rig = Review(tmp_path, three())
    rig.run(plan(**{"03/implementer": [takes_in(["rm", "-q", T01])], "03/fixer": NO_REPORT}))
    (call,) = rig.calls("03", "implementer")
    assert holds(rig, rig.sha("01", "implementer"), call["sha"]), "01 is in ticket/03"
    refused(rig, "03", T01)
    assert phase(rig, "01") == "merged" and on_pr(rig, T01) == LOCKED_01


# ---------------------------------------------------------------- a file that no ticket had locked yet

def test_a_ticket_that_weakened_a_test_file_before_a_ticket_locked_it_does_not_land_after_that_ticket(tmp_path):
    """03 passes its gate and starts its review while 01 runs; 01 then locks the file and merges first."""
    rig = seeded(Review(tmp_path, three()))
    weak = impl("03", files={"demo/cli.py": cli("three"), SHARED: WEAK_SEED})
    rig.run(plan(**{"01/test-writer": [writer("01", SHARED, MORE_SEED)],
                    "01/implementer": [{**impl("01"), "wait": ["r3"]}],
                    "03/implementer": [weak], "03/reviewer": [{"touch": ["r3"], "wait_pr_moves": True}],
                    "03/fixer": NO_REPORT}))
    assert phase(rig, "01") == "merged" and SHARED in rig.state()["tickets"]["01"]["locked"], rig.output()
    refused(rig, "03", SHARED)
    assert on_pr(rig, SHARED) == MORE_SEED


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
        assert rig.code == 1, rig.output()
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
        "01/implementer": [{**one, "wait_pr_moves": True}], "01/conflict": [resolve]}))
    assert len(rig.calls("01", "conflict")) == 1 and phase(rig, "03") == "merged", rig.output()
    assert on_pr(rig, T03) == LOCKED_03
    if changes:
        refused(rig, "01", T03)
        assert on_pr(rig, "demo/cli.py") == cli("three") and not rig.calls("02")
    else:
        assert rig.code == 0, rig.output()
        assert on_pr(rig, "demo/cli.py") == cli("resolved") and not rig.led("locked file changed")


# ---------------------------------------------------------------- the check and the ref move are one step

# The gate of test_walk. The gate of the first merge commit stays open until the call `hold` ended, and 1 s more.
HELD = GATE.replace('print("GATE-OUTPUT-MARK"', '''def ended():
    calls = [json.loads(p.read_text()) for p in (rig / "calls").glob("*.json")]
    return any(c["key"] == conf["hold"] and "t1" in c for c in calls)
merge = len(git("rev-list", "--parents", "-n1", "HEAD").split()) == 3
if conf.get("hold") and merge and not (rig / "sig/held").exists():
    (rig / "sig/held").touch()
    end = time.time() + 30
    while not ended() and time.time() < end:
        time.sleep(0.02)
    time.sleep(1)  # not an order: the time the other ticket gets to try its landing in the open window
    rec["held"] = ended()
print("GATE-OUTPUT-MARK"''', 1)
assert HELD.count("sig/held") == 2


def landed(rig, sha):
    """The place, in the first-parent history the walk made, of the first PR commit that holds `sha`."""
    line = git(rig.repo, "rev-list", "--first-parent", "--reverse", rig.walked()).split()
    return next(i for i, commit in enumerate(line) if holds(rig, sha, commit))


def test_a_ticket_that_lands_while_the_merge_gate_of_another_is_open_does_not_let_that_one_land_unchecked(tmp_path):
    """03 weakens a file that 02 locks. 03 is at its merge gate (after any check on its merge commit, before the ref
    move) when 02 passes its review and wants to land."""
    rig = seeded(Review(tmp_path, three()))
    (rig.rig / "gate").write_text(HELD)
    rig.gate_conf(hold="02/reviewer")
    weak = impl("03", files={"demo/cli.py": cli("three"), SHARED: WEAK_SEED})
    rig.run(plan(**{"02/test-writer": [writer("02", SHARED, MORE_SEED)],
                    "03/implementer": [{**weak, "wait_pr_moves": True}], "03/fixer": NO_REPORT}))
    assert [g for g in rig.gates() if g.get("held")], "the merge gate of 03 was open while 02 ended its review"
    assert phase(rig, "02") == "merged" and "test_more" in on_pr(rig, SHARED), rig.output()
    if phase(rig, "03") == "merged":  # 03 landed first, when no merged ticket had locked the file
        assert rig.code == 0, rig.output()
        assert landed(rig, rig.sha("03", "implementer")) < landed(rig, rig.sha("02", "test-writer")), rig.ledger()
    else:  # 02 landed in the window: the driver looked again, or it gave the stale merge commit up
        kept_out(rig, "03")
        assert on_pr(rig, SHARED) == MORE_SEED


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
    args = ["--tickets", ".agent/tickets", "--gate", str(rig.rig / "gate"), "--spec", "specs/csv/spec.md",
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


def world(*rigs):
    """Every file of each worktree, and every ref and worktree HEAD of the one repository."""
    return ([snapshot(r.repo) for r in rigs], git(rigs[0].repo, "for-each-ref"), git(rigs[0].repo, "worktree", "list"))


def no_lock_is_dirty(*rigs):
    for rig in rigs:
        status = git(rig.repo, "status", "--short")
        assert "lock" not in status, status


def test_a_second_driver_in_another_worktree_exits_3_naming_the_lock_and_starts_when_the_first_ended(tmp_path):
    first, second = pair(tmp_path)
    held = {f"{nn}/test-writer": [{**tw(nn), "wait": ["go"]}] for nn in ("01", "03")}
    proc = start(first, plan(**held))
    try:
        until(lambda: len(first.calls(role="test-writer")) == 2)  # the first driver holds both its slots in a role
        before = world(first, second)
        second.run(OTHER)
        assert second.code == 3, second.output()
        (sentence,) = second.out.splitlines()
        named = [word.rstrip(".,;:") for word in sentence.split() if "/" in word or word.rstrip(".,;:").endswith("lock")]
        assert "lock" in sentence and any((second.repo / word).is_file() for word in named), sentence
        assert world(first, second) == before and not second.calls()
        no_lock_is_dirty(first, second)
        (first.rig / "sig/go").touch()
        out, _ = proc.communicate(timeout=120)
    finally:
        ended(proc)
    assert proc.returncode == 0, out
    second.run(OTHER)
    assert second.code == 0 and second.calls("04", "implementer"), second.output()
    no_lock_is_dirty(first, second)


ENDS = {
    0: (plan(), {}, ()),
    1: (plan(), {"red": ["ticket/01"]}, ()),
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
