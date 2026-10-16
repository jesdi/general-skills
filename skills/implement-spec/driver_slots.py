"""Slots: the worktrees agents run in. Slot 1 is the PR worktree, slots 2..N live under .agent/worktrees/.

Every path is relative to the PR worktree, which is the driver's working directory for the whole run.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git(slot: str, *args: str) -> subprocess.CompletedProcess[str]:
    # core.quotePath off: git prints a path with a non-ASCII name as it is, so it equals the path that a report names.
    return subprocess.run(["git", "-C", slot, "-c", "core.quotePath=false", *args], capture_output=True, text=True)


def names(listing: str) -> list[str]:
    """The paths of a listing that git made with `-z`."""
    return [name for name in listing.split("\0") if name]


def head(slot: str) -> str:
    return git(slot, "rev-parse", "HEAD").stdout.strip()


def on_branch(slot: str) -> str:
    """The branch the slot has checked out; "" when its HEAD is detached."""
    return git(slot, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()


def ancestor(commit: str, of: str) -> bool:
    return git(".", "merge-base", "--is-ancestor", commit, of).returncode == 0


def branch_exists(branch: str) -> bool:
    return git(".", "rev-parse", "-q", "--verify", f"refs/heads/{branch}").returncode == 0


def path(number: int) -> str:
    return "." if number == 1 else f".agent/worktrees/slot-{number}"


def create(number: int, prepare: str | None, keep: bool = False) -> str:
    """Make and prepare slot 2..N once, or reuse it off any branch (`keep`: as it is). Returns "" or the error."""
    slot = path(number)
    git(".", "worktree", "prune")  # a slot directory that was removed by hand
    if Path(slot, ".git").exists():
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
    """Slots 2..count, before any agent. The first failure stops the run: [] or [the error]."""
    for number in range(2, count + 1):
        if error := create(number, prepare, number in keep):
            return [error]
    return []


def release(slot: str) -> bool:
    """Take a clean slot off its branch. False: the slot has uncommitted work, which stays where it is."""
    if git(slot, "status", "--porcelain").stdout.strip():
        return False
    git(slot, "checkout", "-q", "--detach")
    return True
