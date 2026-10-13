"""Slots: the worktrees agents run in. Slot 1 is the PR worktree, slots 2..N live under .agent/worktrees/. Stdlib only.

Every path is relative to the PR worktree, which is the driver's working directory for the whole run.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git(slot: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", slot, *args], capture_output=True, text=True)


def head(slot: str) -> str:
    return git(slot, "rev-parse", "HEAD").stdout.strip()


def path(number: int) -> str:
    return "." if number == 1 else f".agent/worktrees/slot-{number}"


def create(number: int, prepare: str | None, keep: bool = False) -> str:
    """Make slot 2..N once and prepare it once; a slot that exists is reused, off any branch. Returns "" or the error.
    `keep`: the slot holds the work of an interrupted ticket, which runs there again; it stays as it is."""
    slot = path(number)
    git(".", "worktree", "prune")  # a slot directory that was removed by hand
    if Path(slot, ".git").exists():
        # The slot of an earlier run. Like the PR worktree, it must be clean: the driver drops no work.
        if not (keep or release(slot)):
            return f"slot {number}: {slot} has uncommitted changes: commit or discard them"
        return ""
    added = git(".", "worktree", "add", "-q", "--detach", slot)
    if added.returncode:
        return f"slot {number}: cannot create {slot}: {added.stderr.strip()}"
    if not prepare:
        return ""
    done = subprocess.run(prepare, shell=True, cwd=slot, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode:
        # An unprepared slot must not look ready to the next run.
        git(".", "worktree", "remove", "--force", slot)
        return (f"slot {number}: --prepare `{prepare}` exited {done.returncode} in {slot}: "
                f"{(done.stderr or done.stdout).strip()}")
    return ""


def setup(count: int, prepare: str | None, keep: set[int] = frozenset()) -> list[str]:
    """Slots 2..count, before any agent (requirement 4). The first failure stops the run: [] or [the error]."""
    for number in range(2, count + 1):
        if error := create(number, prepare, number in keep):
            return [error]
    return []


def release(slot: str) -> bool:
    """Take a clean slot off its branch, so the branch can be deleted and the slot reused. False: the slot has
    uncommitted work, which stays where it is."""
    if git(slot, "status", "--porcelain").stdout.strip():
        return False
    git(slot, "checkout", "-q", "--detach")
    return True
