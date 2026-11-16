"""Slots: the worktrees agents run in. Slot 1 is the PR worktree, slots 2..N live under .agent/worktrees/, and so
does BASE_SLOT, of the end of run's full gate on the base commit (driver_final_gate).

Every path is relative to the PR worktree, which is the driver's working directory for the whole run.
"""
from __future__ import annotations

import fcntl
import subprocess
from pathlib import Path


class PreparationError(RuntimeError):
    """A requested slot could not be prepared; already running tickets may finish."""


def git(slot: str, *args: str) -> subprocess.CompletedProcess[str]:
    # core.quotePath off: git prints a path with a non-ASCII name as it is, so it equals the path that a report names.
    command = ["git", "-C", slot, "-c", "core.quotePath=false", *args]
    if not args or args[0] not in ("checkout", "switch", "branch", "worktree"):
        return subprocess.run(command, capture_output=True, text=True)
    # These commands mutate or scan the shared registry. Git exposes a temporarily empty
    # commondir while adding a checkout; another checkout can otherwise fail reading it.
    common = subprocess.run(["git", "-C", slot, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True)
    if common.returncode:
        return common
    with (Path(common.stdout.strip()) / "implement-spec-worktrees.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        return subprocess.run(command, capture_output=True, text=True)


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


BASE_SLOT = ".agent/worktrees/slot-base"  # the end of run's full gate on the base commit (driver_final_gate)


def _owned_checkout(slot: str) -> bool:
    """An existing slot must be this repository's checkout at exactly the assigned path."""
    common = git(slot, "rev-parse", "--path-format=absolute", "--git-common-dir")
    root = git(slot, "rev-parse", "--show-toplevel")
    expected = git(".", "rev-parse", "--path-format=absolute", "--git-common-dir")
    return (common.returncode == root.returncode == expected.returncode == 0
            and common.stdout == expected.stdout
            and Path(root.stdout.strip()).resolve() == Path(slot).absolute()
            and not Path(slot).is_symlink())


def create(number: int, prepare: str | None, keep: bool = False, *, start: str = "HEAD") -> str:
    """Make and prepare slot 2..N once, or reuse it off any branch (`keep`: as it is). Returns "" or the error."""
    return make(path(number), f"slot {number}", prepare, keep, start=start)


def make(slot: str, name: str, prepare: str | None, keep: bool = False, *, start: str = "HEAD") -> str:
    """Make and prepare the worktree `slot` once, or reuse it off any branch (`keep`: as it is). Returns "" or the
    error, which starts with `name`."""
    if Path(slot, ".git").exists():
        return _reuse(name, slot, keep)
    # One force recovers this missing slot registration only; a locked registration still fails.
    # Never prune: another session may mount the shared metadata without its sibling checkouts.
    added = git(".", "worktree", "add", "-q", "--force", "--detach", slot, start)
    if added.returncode:
        return f"{name}: cannot create {slot}: {added.stderr.strip()}"
    return _prepare(name, slot, prepare) if prepare else ""


def _reuse(name: str, slot: str, keep: bool) -> str:
    """"" when the existing `slot` is this repository's and, unless `keep`, off any branch; else the error."""
    if not _owned_checkout(slot):
        return f"{name}: {slot} is not a healthy checkout of this repository; inspect it before retrying"
    if not (keep or release(slot)):
        return f"{name}: {slot} could not be released: inspect its Git state and uncommitted changes"
    return ""


def _prepare(name: str, slot: str, prepare: str) -> str:
    """Run `prepare` in the new `slot`; on a failure remove the slot and return the error."""
    done = subprocess.run(prepare, shell=True, cwd=slot, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode:
        # An unprepared slot must not look ready to the next run.
        git(".", "worktree", "remove", "--force", slot)
        return (f"{name}: --prepare `{prepare}` exited {done.returncode} in {slot}: "
                f"{(done.stderr or done.stdout).strip()}")
    return ""


def setup(count: int, prepare: str | None, keep: set[int] = frozenset(),
          *, existing_only: bool = False) -> list[str]:
    """Check slots before agents; existing_only defers absent slots until their first assignment."""
    for number in range(2, count + 1):
        if existing_only and not Path(path(number), ".git").exists():
            continue
        if error := create(number, prepare, number in keep):
            return [error]
    return []


def release(slot: str) -> bool:
    """Take a clean slot off its branch. False: Git failed or the slot has uncommitted work, which stays where it is."""
    status = git(slot, "status", "--porcelain")
    if status.returncode or status.stdout.strip():
        return False
    return git(slot, "checkout", "-q", "--detach").returncode == 0


def fresh(patch: Path) -> Path:
    """`patch`, or `<stem>-<n><suffix>` for the first n from 2 that names no file: a snapshot never replaces one that
    an earlier, partly failed set-aside wrote."""
    n, free = 1, patch
    while free.exists():
        n += 1
        free = patch.with_name(f"{patch.stem}-{n}{patch.suffix}")
    return free


def set_aside(slot: str, patch: Path) -> str:
    """Save the uncommitted work of the slot, untracked files too, as a binary patch on its HEAD at `patch`, then take
    the slot off its branch with a clean tree; ignored files stay. "" or the error: until the whole patch is at
    `patch`, the work stays where it is."""
    patch.parent.mkdir(parents=True, exist_ok=True)
    partial = patch.with_name(f"{patch.name}.partial")
    if error := _steps(slot, ("add", "-A"), ("diff", "--cached", "--binary", f"--output={partial.resolve()}", "HEAD")):
        return error
    partial.replace(patch)
    if error := _steps(slot, ("reset", "-q", "--hard"), ("clean", "-q", "-fd")):
        return error
    return "" if release(slot) else f"{slot} could not be taken off its branch"


def _steps(slot: str, *steps: tuple[str, ...]) -> str:
    """Run the git `steps` in `slot` in order. "" or the error of the first that fails, which stops the rest."""
    for step in steps:
        done = git(slot, *step)
        if done.returncode:
            return f"git {step[0]} failed in {slot}: {done.stderr.strip()}"
    return ""
