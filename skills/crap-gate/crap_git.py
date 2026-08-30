"""Thin git wrapper: what changed against the base, and base file contents."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from crap_model import ToolError

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def toplevel(cwd: Path) -> Path:
    return Path(_git(cwd, "rev-parse", "--show-toplevel").stdout.strip())


def merge_base(root: Path, ref: str) -> str:
    proc = _git(root, "merge-base", "HEAD", ref, check=False)
    if proc.returncode != 0:
        raise ToolError(f"cannot resolve base {ref!r}: {proc.stderr.strip()} (fetch it or pass --base)")
    return proc.stdout.strip()


def changed_files(root: Path, base: str) -> list[tuple[str, str | None]]:
    """(path, old_path) for every file that differs between the working tree
    and `base`, plus untracked files. Deleted files are omitted."""
    out: dict[str, str | None] = {}
    for line in _git(root, "diff", "--name-status", "-M", base).stdout.splitlines():
        parts = line.split("\t")
        status = parts[0][0]
        if status == "D":
            continue
        if status == "R":
            out[parts[2]] = parts[1]
        elif status == "A":
            out[parts[1]] = None
        else:
            out[parts[1]] = parts[1]
    for path in _git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines():
        out[path] = None
    return sorted(out.items())


def touched_lines(root: Path, base: str, path: str) -> list[tuple[int, int]]:
    """Inclusive line ranges of the working-tree file touched since `base`.
    A pure deletion at +c touches c and c+1 (the seam)."""
    ranges: list[tuple[int, int]] = []
    for line in _git(root, "diff", "-U0", base, "--", path).stdout.splitlines():
        m = _HUNK.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        ranges.append((start, start + count - 1) if count else (start, start + 1))
    return ranges


def show(root: Path, rev: str, path: str) -> str | None:
    proc = _git(root, "show", f"{rev}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def tracked_files(root: Path) -> list[str]:
    """Tracked + untracked files that exist in the working tree."""
    listed = _git(root, "ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
    return sorted(p for p in set(listed) if (root / p).is_file())
