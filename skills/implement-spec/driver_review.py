"""The driver's own part of a review: the locked files of the tickets and the routing of the findings.

A finding is the dict of the reviewer schema plus `source`: the role that reported it, or `gate` for a red full gate.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from driver_slots import git, head, on_branch

OPEN = ("critical", "important")  # the severities that start a fix round


def others(tickets: dict, number: str) -> list[str]:
    """The locked files of every ticket but `number` whose test-writer has run, merged or not. `tickets` is that of
    state.json."""
    return sorted({path for key, entry in tickets.items() if key != number and entry["test_writer_sha"]
                   for path in entry["locked"]})


def changed_elsewhere(slot: str, pr: str, paths: list[str]) -> str:
    """The diff of each of `paths` that the ticket branch in `slot` changed since its merge base with `pr`."""
    if not paths:
        return ""
    base = git(slot, "merge-base", "HEAD", pr).stdout.strip() or pr
    return git(slot, "diff", base, "HEAD", "--", *paths).stdout


def written(entry: dict, listed: list[str], tip: str) -> dict:
    """The locked-file fields of the ticket record after a test-writer commit at `tip` that lists `listed`: `tip` is
    the new baseline of the ticket's own locked files."""
    return {"test_writer_sha": tip, "locked": list(dict.fromkeys(entry["locked"] + listed))}


def _corrected(finding: dict, locked: list[str], protected: list[str]) -> bool:
    """Whether a correction, not a fixer, takes the finding: see `route`."""
    return finding["kind"] == "fixture" or finding["severity"] in OPEN and (
        finding["file"] in protected or finding["kind"] == "test" and finding["file"] in locked)


def route(findings: list[dict], locked: list[str], protected: list[str]) -> tuple[list[dict], list[dict]]:
    """(the findings for a test-writer correction; the other findings that are severe enough for a fix round).
    A correction gets each fixture finding, whatever its severity, each severe test finding on a `locked` file, and
    each severe finding on a `protected` file (a locked file of another ticket): the fixer may not edit those files,
    so a fix round cannot close it. Only a correction can take a protected file into the ticket's `locked` (#116)."""
    correct = [finding for finding in findings if _corrected(finding, locked, protected)]
    return correct, [finding for finding in findings if finding not in correct and finding["severity"] in OPEN]


def restore(slot: str, branch: str, tip: str) -> None:
    """Put the slot back on `branch` at `tip` with a clean tree. Only for a read-only role: its edits, commits and
    untracked files go. Ignored files stay."""
    git(slot, "checkout", "-q", "-f", "-B", branch, tip)
    git(slot, "clean", "-q", "-fd")


def disturbed(slot: str, branch: str, tip: str) -> str:
    """"" when the slot is still on `branch` at `tip` with a clean tree, else what the read-only agents left."""
    if on_branch(slot) != branch or head(slot) != tip:
        return f"the slot is no longer on {branch} at {tip[:7]}"
    left = [line[3:] for line in git(slot, "status", "--porcelain").stdout.splitlines()]
    return f"the tree is not as the review found it: {', '.join(left)}" if left else ""


Keys = dict[str, list[str | None]]  # each key and its values in order; None is a valueless key, such as `[core] bare`
Config = tuple[Path, bytes, Keys | None]  # (file, its bytes, its keys)


class Unrestored(Exception):
    """The driver could not put the shared git config back."""


def _keys(config: Path) -> Keys | None:
    """The keys of the file; {} when it does not exist, None when git cannot read it."""
    if not config.exists():
        return {}
    listed = subprocess.run(["git", "config", "--file", str(config), "--list", "-z"], capture_output=True, text=True)
    if listed.returncode:
        return None
    keys: Keys = {}
    for entry in filter(None, listed.stdout.split("\0")):
        key, newline, value = entry.partition("\n")
        keys.setdefault(key, []).append(value if newline else None)
    return keys


def shared_config(slot: str) -> Config:
    """(the git config file that every worktree of the repository shares, its bytes, its keys)."""
    config = Path(git(slot, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(), "config")
    return config, config.read_bytes() if config.exists() else b"", _keys(config)


# What `git worktree add -b x origin/main` writes into a new branch section, per key: the check of its value.
_TRACKING = {"remote": lambda value, before: f"remote.{value}.url" in before,
             "merge": lambda value, before: value.startswith("refs/heads/")}


def _tracking(key: str, before: Keys, now: Keys) -> bool:
    """Whether `key` is what `git worktree add -b x origin/main` of another session writes while a review runs: a new
    branch section with `remote` on a configured remote and `merge` on a branch. Any other branch key counts."""
    section, _, field = key.rpartition(".")
    check = _TRACKING.get(field) if section.startswith("branch.") else None
    values = now.get(key, [])
    if check is None or len(values) != 1 or values[0] is None:
        return False
    return not any(old.startswith(section + ".") for old in before) and check(values[0], before)


def _rewrite(config: Path, key: str, values: list[str]) -> bool:
    """Set `key` to `values` with git; False when a git call fails, as with a held config.lock."""
    git_config = ["git", "config", "--file", str(config)]
    if subprocess.run([*git_config, "--unset-all", key], capture_output=True).returncode not in (0, 5):  # 5: no key
        return False
    return all(not subprocess.run([*git_config, "--add", key, value], capture_output=True).returncode
               for value in values)


def _put_back(config: Path, keys: Keys, changed: list[str]) -> bool:
    """Write the `keys` values of each changed key with git and check them; False when git cannot."""
    if any(None in keys.get(key, []) for key in changed):
        return False  # `git config` cannot write a valueless key
    if not all(_rewrite(config, key, keys.get(key, [])) for key in changed):
        return False
    now = _keys(config)
    return now is not None and all(now.get(key) == keys.get(key) for key in changed)


def _write_whole(config: Path, data: bytes) -> bool:
    """Replace the file by `data` in one rename, past any lock; whether the file now holds `data`."""
    try:
        temporary = config.with_name(f"{config.name}.driver-{os.getpid()}")
        temporary.write_bytes(data)
        if config.exists():
            os.chmod(temporary, config.stat().st_mode)
        os.replace(temporary, config)
        return config.read_bytes() == data
    except OSError:
        return False


def restore_config(before: Config) -> str:
    """"" when no key of the shared git config but another session's tracking entry changed since `before` (from
    `shared_config`), else put back the changed keys and say which. One worktree that writes the config breaks git in
    all of them, as a core.worktree on a gone directory does. Only those keys go back: other sessions write the file at
    the same time. When git cannot put them back, the whole file goes back; raises Unrestored when that fails too."""
    config, data, keys = before
    now = _keys(config)
    if now is None or keys is None:
        changed = ["(git cannot read the file)"]
    else:
        changed = sorted(key for key in keys.keys() | now.keys()
                         if keys.get(key) != now.get(key) and not _tracking(key, keys, now))
        if not changed or _put_back(config, keys, changed):
            return f"the shared git config {config} changed: {', '.join(changed)}" if changed else ""
    if not _write_whole(config, data):
        raise Unrestored(f"the driver cannot restore the shared git config {config}: {', '.join(changed)}")
    return f"the shared git config {config} changed: {', '.join(changed)}"
