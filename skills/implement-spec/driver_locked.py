"""The one locked-file rule: only the test-writer changes a locked file. Which files are locked, the baseline each
agent is checked against, and the git diff that finds a change. A check returns errors.

    a ticket test-writer       each locked file of another ticket that it changes is in its `locked`
    the implementer, a fixer   the ticket's own locked files as its last test-writer commit left them, or their clean
    and the conflict agent     three-way merge with the PR tip; every other ticket's as the merge base of the branch
    of a ticket                and the PR tip has them, or as the PR tip has them
    the final fixer            every ticket's locked files as the tidied tip has them
    the rebase conflict agent  every ticket's locked files as the recovery point has them
    the landing                every other ticket's locked files, but those the ticket lists, as the PR tip has them
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

from driver_slots import git, names

_CHECKED = ("implementer", "fixer", "conflict")  # the ticket agents whose commits may not change a locked file


def every(tickets: dict) -> list[str]:
    """The locked files of each ticket whose test-writer has run, merged or not. `tickets` is that of state.json."""
    return sorted({path for entry in tickets.values() if entry["test_writer_sha"] for path in entry["locked"]})


def others(tickets: dict, number: str) -> list[str]:
    """`every` locked file but those of ticket `number`."""
    return every({key: entry for key, entry in tickets.items() if key != number})


def written(entry: dict, listed: list[str], tip: str) -> dict:
    """The locked-file fields of the ticket record after a test-writer commit at `tip` that lists `listed`: `tip` is
    the new baseline of the ticket's own locked files."""
    return {"test_writer_sha": tip, "locked": list(dict.fromkeys(entry["locked"] + listed))}


def changed_elsewhere(slot: str, pr: str, paths: list[str]) -> str:
    """The diff of each of `paths` that the ticket branch in `slot` changed since its merge base with `pr`."""
    if not paths:
        return ""
    base = git(slot, "merge-base", "HEAD", pr).stdout.strip() or pr
    return git(slot, "diff", base, "HEAD", "--", *paths).stdout


class Locked(NamedTuple):
    """The locked files that the commits of an agent are checked on, read when the agent ends."""
    others: list[str]  # locked files of other tickets: as the merge base with `pr` has them, or as `pr` has them
    pr: str  # the PR tip, which holds what landed
    own: list[str] = []  # the files the agent may not change from `since`
    since: str | None = None


def for_ticket(tickets: dict, role: str, number: str, pr: str) -> Locked | None:
    """The check of a ticket agent with the PR tip `pr`: the locked files of the other tickets for a test-writer, and
    the ticket's own too for a role that may not change one; None for a role with no check."""
    if role == "test-writer":
        return Locked(others(tickets, number), pr)
    if role not in _CHECKED:
        return None
    return Locked(others(tickets, number), pr, tickets[number]["locked"], tickets[number]["test_writer_sha"])


def for_end_of_run(tickets: dict, baseline: str) -> Locked:
    """Every ticket's locked files stay as `baseline` has them: the tidied tip, or for the rebase the recovery point.
    With `baseline` as the PR tip too, the clean merge of `own_errors` is the file at `baseline`."""
    return Locked([], baseline, every(tickets), baseline)


def present(repo: str, commit: str, paths: list[str]) -> list[str]:
    """The `paths` that are files at `commit`."""
    return names(git(repo, "ls-tree", "-r", "--name-only", "-z", commit, "--", *paths).stdout) if paths else []


def errors(role: str, report: dict, context: dict) -> list[str]:
    """The locked-file errors of the report of `role`. `context` is that of driver_report.validate; its `locked` is
    the check (a `Locked`), None for an agent with no check."""
    locked = context.get("locked")
    if locked is None:
        return []
    if role == "test-writer":
        return _unlisted_errors(report, context, locked)
    return _own_errors(context, locked) + _others_errors(context, locked)


def _blob(repo: str, commit: str, path: str) -> str:
    return git(repo, "rev-parse", "-q", "--verify", f"{commit}:{path}").stdout.strip()


def _unlisted_errors(report: dict, context: dict, locked: Locked) -> list[str]:
    """A test-writer changes a locked file of another ticket only when it lists the file in `locked`."""
    repo, branch, start = context["repo"], context["branch"], context["start"]
    return [f"{path} is a locked file of another ticket, and `locked` does not list it: list it in `locked`, or "
            "leave the file as it was" for path in locked.others if path not in report.get("locked", [])
            and _blob(repo, branch, path) not in (_blob(repo, start, path), _blob(repo, locked.pr, path))]


def _differ(repo: str, old: str, new: str, paths: list[str]) -> set[str] | None:
    """The `paths` whose entry (mode, type, content) differs between the two commits; None when git cannot tell."""
    diff = git(repo, "diff", "--name-only", "-z", old, new, "--", *paths)
    return None if diff.returncode else set(names(diff.stdout))


def _merge_base(repo: str, commit: str, pr: str) -> str:
    """The merge base of `commit` and the PR tip; the PR tip when git finds none."""
    return git(repo, "merge-base", commit, pr).stdout.strip() or pr


def _breach(path: str, baseline: str) -> str:
    return (f"locked file changed: {path}: only the test-writer may change a locked file, so make it as it is at "
            f"{baseline[:7]}")


def _others_errors(context: dict, locked: Locked) -> list[str]:
    """The locked files of other tickets, but the agent's own, that the branch changed since its merge base with the
    PR tip. A file as the PR tip has it is no change: it holds what landed."""
    repo, branch, pr = context["repo"], context["branch"], locked.pr
    paths = [path for path in locked.others if path not in locked.own]
    if not paths:
        return []
    baseline = _merge_base(repo, branch, pr)
    changed, off_pr = _differ(repo, baseline, branch, paths), _differ(repo, pr, branch, paths)
    if changed is None or off_pr is None:
        return [f"the locked-file check failed: git cannot compare {baseline[:7]}, {pr} and {branch}"]
    as_landed = set(present(repo, branch, paths)) - off_pr
    return [_breach(path, baseline) for path in sorted(changed - as_landed)]


def _show(repo: str, commit: str, path: str) -> str | None:
    """The content of `path` at `commit`; None when it is no file there."""
    shown = git(repo, "show", f"{commit}:{path}")
    return None if shown.returncode else shown.stdout


def _clean_merge(repo: str, base: str, ours: str, theirs: str, path: str) -> str | None:
    """The three-way merge of `path` (`git merge-file`) from `base` with `ours` and `theirs`, a missing file empty;
    None when it conflicts or git fails."""
    with tempfile.TemporaryDirectory() as folder:
        files = []
        for name, commit in (("ours", ours), ("base", base), ("theirs", theirs)):
            (Path(folder) / name).write_text(_show(repo, commit, path) or "")
            files.append(str(Path(folder) / name))
        merged = subprocess.run(["git", "merge-file", "-p", *files], capture_output=True, text=True)
    return None if merged.returncode else merged.stdout


def _own_errors(context: dict, locked: Locked) -> list[str]:
    """The agent's own locked files that the branch changed since `locked.since`. A file is no change when it is the
    clean three-way merge of its version there and the PR tip's, from their merge base: a conflict merge takes in what
    landed and keeps the test-writer's tests."""
    repo, branch, pr, since = context["repo"], context["branch"], locked.pr, locked.since
    if not (since and locked.own):
        return []
    changed = _differ(repo, since, branch, locked.own)
    if changed is None:
        return [f"the locked-file check failed: git cannot compare {since[:7]} and {branch}"]
    base = _merge_base(repo, since, pr)
    return [_breach(path, since) for path in sorted(changed)
            if (merged := _clean_merge(repo, base, since, pr, path)) is None or _show(repo, branch, path) != merged]


def landing_errors(tickets: dict, number: str, pr_tip: str, commit: str) -> list[str]:
    """The locked files of every other ticket, but those ticket `number` lists, that `commit`, about to land, changes
    from the PR tip: the PR tip holds what their test-writers locked."""
    paths = [path for path in others(tickets, number) if path not in tickets[number]["locked"]]
    changed = _differ(".", pr_tip, commit, paths) if paths else set()
    if changed is None:
        return [f"the locked-file check failed: git cannot compare {pr_tip[:7]} and {commit[:7]}"]
    return [f"locked file changed: {path}: the commit that would land changes a locked file of another ticket, so make "
            "it as the PR tip has it" for path in sorted(changed)]
