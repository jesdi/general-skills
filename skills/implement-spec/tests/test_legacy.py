"""The names of a run, and the take-over of a run that an older driver started (driver_names, driver_legacy)."""
import json
import subprocess

import driver_dispute
import driver_legacy
import driver_state
from conftest import GIT
from driver_names import Names


def sha(ref="HEAD"):
    return subprocess.run(["git", "rev-parse", "-q", "--verify", ref], capture_output=True, text=True).stdout.strip()


def test_the_names_of_a_run_carry_its_pr_branch_as_one_ref_component(repo, tmp_path):
    """Structure review: git refs are a tree, so `ticket/feat/01` and `ticket/feat/01/02` cannot both exist. The PR
    branch is one escaped component, so runs on two PR branches never collide."""
    names = Names("feat/x")
    assert (names.ticket("01"), names.recovery) == ("ticket/feat%2Fx/01", "refs/implement-spec/feat%2Fx/recovery")
    assert names.lock_file(tmp_path) == tmp_path / "implement-spec/feat%2Fx.lock"
    assert Names("a%2Fb").ticket("01") != Names("a/b").ticket("01")
    repo()
    for name in (Names("feat").ticket("01"), Names("feat/01").ticket("02"), Names("x/recovery").ticket("01")):
        subprocess.run(["git", "branch", name], check=True)
    subprocess.run(["git", "update-ref", "refs/implement/recovery", "HEAD"], check=True)  # of an older driver
    for pr in ("feat", "feat/recovery", "recovery"):
        subprocess.run(["git", "update-ref", Names(pr).recovery, "HEAD"], check=True)
    assert sha(Names("feat/recovery").recovery) == sha()


def older_run(repo, tmp_path):
    """A repository with the start of a run, its recorded tip on `ticket/01` and the state.json of an older driver:
    no `schema`."""
    repo()
    start = sha()
    subprocess.run([*GIT, "commit", "-q", "--allow-empty", "-m", "work"], check=True)
    tip = sha()
    subprocess.run(["git", "branch", "ticket/01", tip], check=True)
    state = driver_state.State(tmp_path / ".agent/implement/state.json")
    state.update(lambda s: s.update(
        run={"pr_branch": "feat/x", "start": start, "final": {"recovery": None}},
        tickets={"01": {"phase": "review", "tip": tip}, "02": {"phase": "pending", "tip": None}}))
    return state, start, tip


def resume(state, tmp_path):
    """driver_legacy.resume; (the errors, the ledger lines)."""
    errors = driver_legacy.resume(state)
    ledger = tmp_path / ".agent/implement/ledger.md"
    return errors, ledger.read_text().splitlines() if ledger.exists() else []


def edit(state, ticket=None, **run):
    """Change the record of the run and of ticket 01, and take the `schema` away: the run is the older driver's
    again."""
    state.update(lambda s: (s["run"].update(run), s["run"].pop("schema", None), s["tickets"]["01"].update(ticket or {})))


def test_resume_renames_the_ticket_branches_of_this_run_only_and_marks_the_run(repo, tmp_path):
    """A `ticket/NN` is this run's only when it holds the start of the run and the recorded tip: a `ticket/NN` of
    another run stays as it is."""
    state, start, tip = older_run(repo, tmp_path)
    subprocess.run(["git", "branch", "-f", "ticket/01", start], check=True)
    assert resume(state, tmp_path) == ([], [])  # it does not hold the recorded tip
    subprocess.run([*GIT, "checkout", "-q", "--orphan", "other"], check=True)
    subprocess.run([*GIT, "commit", "-q", "--allow-empty", "-m", "other run"], check=True)
    subprocess.run(["git", "branch", "-f", "ticket/01", "other"], check=True)
    edit(state, {"tip": None})
    assert resume(state, tmp_path) == ([], [])  # it does not hold the start of the run
    subprocess.run(["git", "branch", "-f", "ticket/01", tip], check=True)
    for phase in ("pending", "merged"):
        edit(state, {"tip": tip, "phase": phase})
        assert resume(state, tmp_path) == ([], [])
    edit(state, {"phase": "review"})
    assert resume(state, tmp_path) == (
        [], ["Ticket 01: renamed the branch ticket/01 of an older driver to ticket/feat%2Fx/01."])
    assert (sha("refs/heads/ticket/01"), sha("refs/heads/ticket/feat%2Fx/01")) == ("", tip)
    assert state.read()["run"]["schema"] == driver_state.SCHEMA
    subprocess.run(["git", "branch", "ticket/01", tip], check=True)
    assert resume(state, tmp_path)[0] == [] and sha("refs/heads/ticket/01") == tip  # a marked run is not touched


def test_resume_keeps_a_ticket_branch_that_a_worktree_outside_the_run_has_checked_out(repo, tmp_path):
    """Correctness review: a common start does not prove whose branch it is. A `ticket/NN` that another worktree has
    checked out is that worktree's."""
    state, _, tip = older_run(repo, tmp_path)
    subprocess.run(["git", "worktree", "add", "-q", str(tmp_path.parent / f"{tmp_path.name}-other"), "ticket/01"],
                   check=True)
    assert resume(state, tmp_path) == ([], []) and sha("refs/heads/ticket/01") == tip
    edit(state)
    subprocess.run(["git", "worktree", "remove", "--force", str(tmp_path.parent / f"{tmp_path.name}-other")],
                   check=True)
    subprocess.run(["git", "worktree", "add", "-q", str(tmp_path / ".agent/worktrees/slot-2"), "ticket/01"],
                   check=True)
    assert resume(state, tmp_path)[1] == ["Ticket 01: renamed the branch ticket/01 of an older driver to "
                                          "ticket/feat%2Fx/01."]  # a slot of this run


def test_resume_stops_on_a_rename_that_git_refuses_and_loses_nothing(repo, tmp_path):
    state, _, _ = older_run(repo, tmp_path)
    subprocess.run(["git", "branch", "ticket/feat%2Fx"], check=True)  # a branch where the folder of the new name goes
    errors, ledger = resume(state, tmp_path)
    assert ledger == [] and len(errors) == 1 and "schema" not in state.read()["run"]
    assert errors[0].startswith("cannot rename the branch ticket/01 of an older driver to ticket/feat%2Fx/01: ")
    assert sha("refs/heads/ticket/01")


def test_resume_moves_the_recovery_ref_that_this_run_recorded(repo, tmp_path):
    state, start, tip = older_run(repo, tmp_path)
    subprocess.run(["git", "branch", "-D", "-q", "ticket/01"], check=True)
    subprocess.run(["git", "update-ref", "refs/implement/recovery", start], check=True)
    edit(state, final={"recovery": tip})
    assert resume(state, tmp_path) == ([], [])  # it holds another commit: not this run's
    edit(state, final={"recovery": start})
    assert resume(state, tmp_path) == (
        [], ["Recovery point: moved refs/implement/recovery of an older driver to refs/implement-spec/feat%2Fx/recovery."])
    assert (sha("refs/implement/recovery"), sha("refs/implement-spec/feat%2Fx/recovery")) == ("", start)


def test_resume_keeps_the_recovery_ref_when_git_refuses_the_new_one(repo, tmp_path):
    state, start, _ = older_run(repo, tmp_path)
    subprocess.run(["git", "branch", "-D", "-q", "ticket/01"], check=True)
    subprocess.run(["git", "update-ref", "refs/implement/recovery", start], check=True)
    subprocess.run(["git", "update-ref", "refs/implement-spec/feat%2Fx", start], check=True)  # a file where a folder goes
    edit(state, final={"recovery": start})
    errors, ledger = resume(state, tmp_path)
    assert ledger == [] and errors[0].startswith("cannot move refs/implement/recovery of an older driver to ")
    assert sha("refs/implement/recovery") == start


def test_the_lock_of_an_older_driver_is_shared_by_newer_drivers_and_excludes_an_older_one(tmp_path):
    """Correctness review: an older driver holds `implement-spec.lock` alone for its whole run."""
    (one, _), (two, _) = driver_legacy.lock(tmp_path), driver_legacy.lock(tmp_path)
    with one, two:
        assert driver_state.lock(tmp_path / "implement-spec.lock") is None
    older = driver_state.lock(tmp_path / "implement-spec.lock")
    assert older is not None and driver_legacy.lock(tmp_path) == (None, (
        f"a driver of an older version runs in this repository: it holds the lock {tmp_path / 'implement-spec.lock'}. "
        "Wait until it ends, then run the driver again."))
    older.close()


def test_resume_keys_the_rulings_and_corrections_of_a_schema_4_run_by_their_test(repo, tmp_path):
    """Schema 4 keyed them by the locked file; each record names its test, so the key moves to that test. A review
    correction recorded only its file and is keyed by it, as a fixture finding that names no test is. The evidence
    of each `spec-ambiguous` question of the ticket gets the key of its file's record, which `keep-test` closes."""
    state, _, _ = older_run(repo, tmp_path)
    ruled = {"test": "test_01.py::test_x[a]", "requirement": "R3", "ruling": "test-wrong", "reason": "r"}
    by_file = {"test": "tests/test_02.py", "requirement": "", "reason": "STALE"}
    evidence = tmp_path / ".agent/implement/01/spec-ambiguous-1.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps([{"file": "tests/test_01.py", "title": "t"}, {"file": "tests/test_02.py"}]))
    question = {"ticket": "01", "kind": "spec-ambiguous", "answer": "keep-test", "evidence": [str(evidence)],
                "subject": "s"}
    gone = {**question, "evidence": [str(tmp_path / "gone.json")]}
    edit(state, {"disputes": {"tests/test_01.py": ruled},
                 "corrected": {"tests/test_01.py": ruled, "tests/test_02.py": by_file}}, questions=[question, gone])
    assert resume(state, tmp_path)[0] == []
    entry = state.read()["tickets"]["01"]
    assert entry["disputes"] == {"tests/test_01.py::test_x": ruled}
    assert entry["corrected"] == {"tests/test_01.py::test_x": ruled, "tests/test_02.py": by_file}
    assert entry["settled"] == 2  # one per record of schema 4: each was an arbitration or a correction
    assert [one["key"] for one in json.loads(evidence.read_text())] == ["tests/test_01.py::test_x", "tests/test_02.py"]
    assert driver_dispute.kept(state.read()["run"]["questions"][:1], "01") == {"tests/test_01.py::test_x",
                                                                             "tests/test_02.py"}
