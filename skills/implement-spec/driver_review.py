"""The driver's own part of a review: the locked-file check of the ticket's own tests, the routing of the findings,
and the mutation proofs that a fix makes due again.

A finding is the dict of the reviewer schema plus `source`: the role that reported it, or `driver`.
A mutation proof is keyed by (production file, line); the line is the one the file has at the ticket tip.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import driver_agents
from driver_slots import git, head, names, on_branch
from driver_tickets import Ticket

# The checks of the correctness pass: this file of the review-diff skill, which is installed next to this one.
CORRECTNESS = driver_agents.SKILL.parent / "review-diff" / "correctness.md"
OPEN = ("critical", "important")  # the severities that start a fix round
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def precondition_errors(tickets: list[Ticket]) -> list[str]:
    """[] or the error that stops the run before any agent: a ticket with a Risk line needs CORRECTNESS."""
    risky = [ticket.number for ticket in tickets if ticket.risk]
    if CORRECTNESS.exists() or not risky:
        return []
    return [f"{CORRECTNESS} is missing: ticket {', '.join(risky)} has a Risk line and its correctness prompt carries "
            "that file; install the review-diff skill next to implement-spec"]


def checks() -> str:
    """CORRECTNESS, inlined into the correctness prompt: the pass reads files only from git at the tip and from
    `.agent/`, and a project-local install of review-diff sits in an ignored directory (issue #120). "" when it is
    missing: only a ticket with a Risk line renders it, and `precondition_errors` checks the file for those."""
    return CORRECTNESS.read_text() if CORRECTNESS.exists() else ""


def locked_changes(slot: str, sha: str | None, locked: list[str]) -> list[dict]:
    """One critical finding per locked file that differs from the test-writer's commit `sha`, or for a diff that
    git cannot make."""
    if not (sha and locked):
        return []  # a refactor ticket has no test-writer
    diff = git(slot, "diff", "--name-only", "-z", f"{sha}..HEAD", "--", *locked)
    titles = ({path: f"locked file changed: {path}" for path in names(diff.stdout)} if diff.returncode == 0
              else {locked[0]: f"locked file check failed: {diff.stderr.strip()}"})
    return [{"severity": "critical", "file": path, "line": 1, "title": title, "kind": "code", "source": "driver",
             "evidence": f"git diff {sha}..HEAD -- {path}"} for path, title in titles.items()]


def merged_changes(paths: list[str]) -> list[dict]:
    """One critical `fixture` finding per locked file of a merged ticket that the ticket changed. A fixture finding
    goes to a test-writer correction: the driver puts the file back before it and gates that commit."""
    return [{"severity": "critical", "file": path, "line": 1, "kind": "fixture", "source": "driver",
             "title": f"the ticket changed the locked file of a merged ticket: {path}",
             "evidence": f"git diff <merge base with the PR branch> -- {path}"} for path in paths]


def _references(entry: dict) -> list[tuple[str | None, list[str]]]:
    """(reference commit, the locked files it is the reference of): `test_writer_sha`, or the older commit that
    `held` names for a file."""
    held = entry["held"]
    groups = [(entry["test_writer_sha"], [path for path in entry["locked"] if path not in held])]
    return groups + [(sha, [path for path, at in held.items() if at == sha]) for sha in dict.fromkeys(held.values())]


def locked_findings(slot: str, entry: dict) -> list[dict]:
    """The locked-file check for the ticket record `entry`."""
    return [finding for sha, paths in _references(entry) for finding in locked_changes(slot, sha, paths)]


def restore_locked(slot: str, entry: dict) -> list[str]:
    """Put each locked file that differs from its reference back to the reference version, in the tree only.
    Returns those paths."""
    restored = []
    for sha, paths in _references(entry):
        if sha and paths:
            restored += names(git(slot, "diff", "--name-only", "-z", sha, "--", *paths).stdout)
    for path in restored:
        git(slot, "checkout", "-q", entry["held"].get(path, entry["test_writer_sha"]), "--", path)
    return restored


def written(slot: str, entry: dict, listed: list[str], fixtures: list[str], tip: str) -> dict:
    """The locked-file fields of the ticket record after a test-writer commit at `tip` that lists `listed`, of which
    `fixtures` hold no test. `tip` is the new reference. A locked file that a correction does not list and that differs
    from the old reference keeps the old one in `held`, so a change the implementer made before stays a finding."""
    held = {path: sha for path, sha in entry["held"].items() if path not in listed}
    others = [path for path in entry["locked"] if path not in listed and path not in held]
    if entry["test_writer_sha"] and others:
        diff = git(slot, "diff", "--name-only", "-z", entry["test_writer_sha"], tip, "--", *others)
        # A diff that git cannot make holds each file.
        held.update(dict.fromkeys(names(diff.stdout) if diff.returncode == 0 else others, entry["test_writer_sha"]))
    return {"test_writer_sha": tip, "locked": list(dict.fromkeys(entry["locked"] + listed)), "held": held,
            "fixtures": [path for path in entry["fixtures"] if path not in listed] + fixtures}


def route(findings: list[dict], locked: list[str], protected: list[str]) -> tuple[list[dict], list[dict]]:
    """(the findings for a test-writer correction; the other findings that are severe enough for a fix round).
    A correction gets each fixture finding, whatever its severity, each severe test finding on a `locked` file, and
    each severe finding on a `protected` file (a locked file of a merged ticket): the fixer may not edit those files,
    so a fix round cannot close it. Only a correction can take a protected file into the ticket's `locked` (#116)."""
    correct = [finding for finding in findings if finding["kind"] == "fixture" or finding["severity"] in OPEN and (
               finding["file"] in protected or finding["kind"] == "test" and finding["file"] in locked)]
    return correct, [finding for finding in findings if finding not in correct and finding["severity"] in OPEN]


def _hunks(slot: str, old: str, new: str, file: str) -> list[tuple[int, int, int, int]]:
    """(first old line, old count, first new line, new count) per hunk of `file`, with no context lines."""
    diff = git(slot, "diff", "-U0", old, new, "--", file).stdout
    return [(int(a), int(b or 1), int(c), int(d or 1)) for a, b, c, d in _HUNK.findall(diff)]


def _follow(line: int, hunks: list[tuple[int, int, int, int]]) -> tuple[int, bool]:
    """(the number old `line` has after the hunks, whether a hunk changed the line)."""
    shift = 0
    for a, b, c, d in hunks:
        if a <= line < a + b:
            return c + min(line - a, max(d - 1, 0)), True
        if line >= a + max(b, 1):  # a hunk with no old line is an insert after line `a`
            shift += d - b
    return line + shift, False


def follow(slot: str, old: str, new: str, proofs: set[tuple[str, int]]) -> dict[tuple[str, int], bool]:
    """The proofs of commit `old` as they are at `new`: (file, new line) -> whether the fix changed that line, so
    its proof is due again."""
    # ponytail: a renamed file reads as deleted, so each of its proofs is due again; follow renames if that costs.
    hunks = {file: _hunks(slot, old, new, file) for file in {file for file, _ in proofs}}
    found: dict[tuple[str, int], bool] = {}
    for file, line in sorted(proofs):
        at, changed = _follow(line, hunks[file])
        found[file, at] = changed or found.get((file, at), False)
    return found


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
