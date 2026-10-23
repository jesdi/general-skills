"""state.json: no lost update between threads, atomic publish, and the resume check against git."""
import json
import subprocess
import threading

import driver_state
from conftest import GIT


def test_updates_from_many_threads_are_all_kept_and_no_scratch_file_stays(tmp_path):
    state = driver_state.State(tmp_path / "implement/state.json")
    numbers = [f"{n:02d}" for n in range(1, 9)]
    assert state.start("feat/x", numbers)["run"]["pr_branch"] == "feat/x"

    def work(number):
        for step in range(40):
            state.update(lambda s: s["run"].update(gates=s["run"]["gates"] + 1))
            state.update(lambda s: s["tickets"][number]["green_gates"].append(step))
            state.ticket(number, round=step)

    threads = [threading.Thread(target=work, args=(number,)) for number in numbers]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    final = json.loads(state.path.read_text())
    assert final["run"]["gates"] == 320
    assert all(final["tickets"][n]["green_gates"] == list(range(40)) and final["tickets"][n]["round"] == 39 for n in numbers)
    assert [p.name for p in state.path.parent.iterdir()] == ["state.json"]


def test_start_keeps_a_recorded_run_and_adds_only_the_new_tickets(tmp_path):
    state = driver_state.State(tmp_path / "state.json")
    revision = state.start("feat/x", ["01"])["run"]["revision"]
    state.update(lambda record: record["tickets"]["01"].pop("fixtures"))  # a run of an older driver
    state.ticket("01", phase="merged")
    again = state.start("other", ["01", "02"])
    assert (again["run"]["revision"], again["run"]["pr_branch"]) == (revision, "feat/x")
    assert again["tickets"]["01"]["phase"] == "merged" and again["tickets"]["01"]["fixtures"] == []
    assert again["tickets"]["02"] == {
        "phase": "pending", "round": 0, "tip": None, "test_writer_sha": None, "locked": [], "green_gates": [], "slot": None,
        "note": "", "held": {}, "rejected": {}, "fixtures": []}


def test_verified_trusts_a_tip_only_while_it_is_on_the_ticket_branch(repo, tmp_path):
    repo()
    tests = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run([*GIT, "commit", "-q", "--allow-empty", "-m", "work"], check=True)
    tip = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "branch", "ticket/01", tip], check=True)
    entry = {"phase": "review", "tip": tip, "test_writer_sha": tests}
    assert driver_state.verified(entry, "ticket/01", "feat/x") == ({}, "")
    assert driver_state.verified({**entry, "tip": tests}, "ticket/01", "feat/x") == ({}, "")  # an earlier commit of the branch
    back = ({"phase": "implement", "tip": tests}, f"the recorded tip {tip[:7]} is not on ticket/02")
    assert driver_state.verified(entry, "ticket/02", "feat/x") == back  # no such branch
    subprocess.run(["git", "branch", "-f", "ticket/01", tests], check=True)
    assert driver_state.verified({**entry, "phase": "merge"}, "ticket/01", "feat/x")[0] == {"phase": "implement", "tip": tests}
    # No accepted implementation yet, or finished: nothing to check.
    for phase in ("pending", "test-writer", "implement", "blocked"):
        assert driver_state.verified({**entry, "phase": phase, "tip": "1" * 40}, "ticket/01", "feat/x") == ({}, "")


def test_verified_trusts_merged_only_while_the_tip_is_in_the_pr_branch(repo):
    repo()
    tests = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run([*GIT, "commit", "-q", "--allow-empty", "-m", "work"], check=True)
    tip = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    entry = {"phase": "merged", "tip": tip, "test_writer_sha": tests}
    assert driver_state.verified(entry, "ticket/01", "feat/x") == ({}, "")
    subprocess.run(["git", "branch", "old", tests], check=True)
    assert driver_state.verified(entry, "ticket/01", "old") == (
        {"phase": "implement", "tip": tests}, f"the recorded tip {tip[:7]} is not in old")
