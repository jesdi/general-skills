"""The gate: the repo's one check command, run by the driver on the commit it is about to trust.

A gate record is `<folder>/gate-<first 7 hex of the commit>.log`. It is green only for exit 0 on a tree that is
clean before the command and after it, with HEAD still on the commit. A command that a signal ended, on a clean tree
with HEAD unmoved, did not judge the commit: it runs once more, and that run overwrites the record. A signal ended the
command when its exit is negative, or when it is 128+N for N in SIGHUP, SIGINT, SIGKILL or SIGTERM: a shell that does
not exec its last command, as dash does not, outlives the killed child and exits with 128+N.
"""
from __future__ import annotations

import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from driver_slots import git, head


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after(slot: str, commit: str) -> str:
    """What the command left behind: "clean", or why the record cannot be green."""
    if head(slot) != commit:
        return f"HEAD moved to {head(slot)}"
    status = git(slot, "status", "--short").stdout
    return "not clean\n" + "".join(f"    {line}\n" for line in status.splitlines()).rstrip("\n") if status else "clean"


_KILLS = {signal.SIGHUP, signal.SIGINT, signal.SIGKILL, signal.SIGTERM}


def _signal(code: int | str) -> int | None:
    """The signal that ended the command: from a negative exit, or from 128+N as a shell reports its killed child."""
    if not isinstance(code, int):
        return None
    if code < 0:
        return -code
    return code - 128 if code - 128 in _KILLS else None


def record_path(folder: Path, commit: str) -> Path:
    return folder / f"gate-{commit[:7]}.log"


def _attempt(command: str, slot: str, commit: str, record: Path, env: dict) -> tuple[int | str, str]:
    """Run the command once into `record`. Returns (the exit code or "not run", the state after it)."""
    status = git(slot, "status", "--short").stdout
    with record.open("w") as out:
        out.write(f"start: {_now()}\ncommit: {commit}\nstatus: {'not clean' if status else 'clean'}\n")
        out.writelines(f"    {line}\n" for line in status.splitlines())
        out.write(f"command: {command}\n\n")
        out.flush()
        # shell=True: the command is the operator's own shell line, as `make check` is.
        code = "not run" if status else subprocess.run(
            command, shell=True, cwd=slot, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT
        ).returncode
        after = "not run" if status else _after(slot, commit)
        out.write(f"\nexit: {code}\nafter: {after}\nend: {_now()}\n")
    return code, after


def run(command: str, slot: str, folder: Path, pr_branch: str) -> tuple[bool, Path, list[str]]:
    """Gate the HEAD of `slot`. Returns (green, the record, a ledger sentence per gate that a signal ended)."""
    commit = head(slot)
    folder.mkdir(parents=True, exist_ok=True)
    record = record_path(folder, commit)
    # The cache of compiled Python stays out of the slot, so the gate leaves the tree clean.
    env = dict(os.environ, IMPLEMENT_SPEC_BASE=pr_branch, PYTHONPYCACHEPREFIX=str(folder.parent.resolve() / "pycache"))
    code, after = _attempt(command, slot, commit, record, env)
    killed = []
    # A process outside the gate can kill it, as a `pkill -f` of another slot does: that run judged nothing.
    number = _signal(code)
    if number and after == "clean":
        killed.append(f"the gate of {commit[:7]} was killed by signal {number} and runs again")
        code, after = _attempt(command, slot, commit, record, env)
    return code == 0 and after == "clean", record, killed
