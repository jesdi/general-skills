"""The gate: the repo's one check command, run by the driver on the commit it is about to trust. Stdlib only.

A gate record is `<folder>/gate-<first 7 hex of the commit>.log`: start time, commit, status, the full log,
exit code, the state after the command, end time. It is green only for exit 0 on a tree that is clean before the
command and after it, with HEAD still on the commit (design.md: a record on a dirty tree is not one).
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from driver_slots import git, head


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after(slot: str, commit: str) -> str:
    """What the command left behind: "clean", or why the record cannot be green. Ignored files do not count."""
    if head(slot) != commit:
        return f"HEAD moved to {head(slot)}"
    status = git(slot, "status", "--short").stdout
    return "not clean\n" + "".join(f"    {line}\n" for line in status.splitlines()).rstrip("\n") if status else "clean"


def run(command: str, slot: str, folder: Path, pr_branch: str) -> tuple[bool, Path]:
    """Gate the HEAD of `slot`. Returns (green, the record)."""
    commit = head(slot)
    status = git(slot, "status", "--short").stdout
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / f"gate-{commit[:7]}.log"
    # The cache of compiled Python stays out of the slot, so the gate leaves the tree clean.
    env = dict(os.environ, IMPLEMENT_SPEC_BASE=pr_branch, PYTHONPYCACHEPREFIX=str(folder.parent.resolve() / "pycache"))
    with record.open("w") as out:
        out.write(f"start: {_now()}\ncommit: {commit}\nstatus: {'not clean' if status else 'clean'}\n")
        out.writelines(f"    {line}\n" for line in status.splitlines())
        out.write(f"command: {command}\n\n")
        out.flush()
        # The command is the operator's own shell line, as `make check` is. A dirty tree is red without a run.
        code = "not run" if status else subprocess.run(
            command, shell=True, cwd=slot, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT
        ).returncode
        # The command tested the tree it left: a changed tree or a moved HEAD is not the commit the driver trusts.
        after = "not run" if status else _after(slot, commit)
        out.write(f"\nexit: {code}\nafter: {after}\nend: {_now()}\n")
    return code == 0 and after == "clean", record
