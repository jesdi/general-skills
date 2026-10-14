"""Slots 2..N: created once, prepared once, reused only when clean."""
import subprocess

import driver_slots


def test_a_slot_is_created_and_prepared_once_then_reused_off_any_branch(repo, tmp_path):
    repo()
    log = tmp_path.parent / f"{tmp_path.name}-prepare.log"
    assert driver_slots.setup(3, f"pwd >> {log}") == []
    assert len(log.read_text().splitlines()) == 2 and driver_slots.path(1) == "."
    slot = driver_slots.path(3)
    subprocess.run(["git", "-C", slot, "checkout", "-q", "-b", "ticket/07"], check=True)
    assert driver_slots.setup(3, f"pwd >> {log}") == [] and len(log.read_text().splitlines()) == 2
    assert driver_slots.git(slot, "symbolic-ref", "-q", "HEAD").returncode != 0  # detached: the branch is free


def test_a_slot_with_uncommitted_work_stops_the_run_and_keeps_the_work(repo, tmp_path):
    repo()
    assert driver_slots.setup(2, None) == []
    (tmp_path / ".agent/worktrees/slot-2/wip.txt").write_text("half done\n")
    assert driver_slots.setup(3, None) == [
        "slot 2: .agent/worktrees/slot-2 has uncommitted changes: commit or discard them"]
    assert (tmp_path / ".agent/worktrees/slot-2/wip.txt").exists() and not (tmp_path / ".agent/worktrees/slot-3").exists()


def test_a_slot_that_cannot_be_created_is_an_error(repo, tmp_path):
    repo()
    (tmp_path / ".agent/worktrees").mkdir(parents=True)
    (tmp_path / ".agent/worktrees/slot-2").write_text("a file is in the way\n")
    (error,) = driver_slots.setup(2, None)
    assert error.startswith("slot 2: cannot create .agent/worktrees/slot-2: ")
