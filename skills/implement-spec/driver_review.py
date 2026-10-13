"""The driver's own part of a review: the locked-file check, the routing of the
findings, and the mutation proofs a fix makes due again. It reads git; only `restore`, `restore_locked` and
`undisturbed` write.
Stdlib only.

A finding is the dict of the reviewer schema plus `source`: the role that reported it, or `driver`.
The locked-file check of a ticket covers two sets of files: the files its own test-writer locked, and `others`, the
locked files of every ticket that is merged. Only a test-writer changes a locked file.
A mutation proof is keyed by (production file, line); the line is the one the file has at the ticket tip.
"""
from __future__ import annotations

import re
from typing import Callable, TypeVar

import driver_agents
from driver_slots import git, head
from driver_tickets import Ticket

# The checks of the correctness pass belong to the review-diff skill, which is installed next to this one.
CORRECTNESS = driver_agents.SKILL.parent / "review-diff" / "correctness.md"
OPEN = ("critical", "important")  # the severities that start a fix round
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)
T = TypeVar("T")


def missing(tickets: list[Ticket]) -> list[str]:
    """[] or the error that stops the run before any agent: a ticket with a Risk line gets the correctness pass,
    and that pass reads CORRECTNESS. A set with no such ticket does not need the file."""
    risky = [ticket.number for ticket in tickets if ticket.risk]
    if CORRECTNESS.exists() or not risky:
        return []
    return [f"{CORRECTNESS} is missing: ticket {', '.join(risky)} has a Risk line and its correctness pass reads "
            "that file; install the review-diff skill next to implement-spec"]


def locked_changes(slot: str, sha: str | None, locked: list[str]) -> list[dict]:
    """Requirement 7: one critical finding per locked file that differs from the test-writer's commit `sha`.
    A diff that git cannot make is a finding too: the check never passes unseen."""
    if not (sha and locked):
        return []  # a refactor ticket has no test-writer
    diff = git(slot, "diff", "--name-only", f"{sha}..HEAD", "--", *locked)
    titles = ({path: f"locked file changed: {path}" for path in diff.stdout.splitlines()} if diff.returncode == 0
              else {locked[0]: f"locked file check failed: {diff.stderr.strip()}"})
    return [{"severity": "critical", "file": path, "line": 1, "title": title, "kind": "code", "source": "driver",
             "evidence": f"git diff {sha}..HEAD -- {path}"} for path, title in titles.items()]


def others(tickets: dict, number: str) -> list[str]:
    """Requirement 7 across tickets: the locked files of every merged ticket of `tickets` of state.json that ticket
    `number` does not lock itself. An implementer, a fixer or a conflict agent of this ticket may not change one.
    Its test-writer may: a file that it lists becomes a locked file of this ticket too."""
    merged = {path for entry in tickets.values() if entry["phase"] == "merged" for path in entry["locked"]}
    return sorted(merged - set(tickets[number]["locked"]))


def started(slot: str, pr: str) -> str:
    """The commit that the ticket branch in `slot` started from on the PR branch `pr`: the reference of a ticket
    that has no test-writer commit (a refactor ticket). When git cannot name it, the reference is the PR branch,
    so the check never passes unseen."""
    return git(slot, "merge-base", "HEAD", f"refs/heads/{pr}").stdout.strip() or f"refs/heads/{pr}"


def _references(entry: dict, others: list[str] | tuple = (), start: str | None = None) -> list[tuple[str | None, list[str]]]:
    """(the commit that is the reference, the files it is the reference of) for the ticket record `entry` and the
    locked files `others` of the merged tickets: that is `test_writer_sha`, or `start` for a ticket that has none,
    or for a file in `held` the older commit named there."""
    held = entry["held"]
    groups = [(entry["test_writer_sha"] or start, [path for path in [*entry["locked"], *others] if path not in held])]
    return groups + [(sha, [path for path, at in held.items() if at == sha]) for sha in dict.fromkeys(held.values())]


def locked_findings(slot: str, entry: dict, others: list[str] | tuple = (), start: str | None = None) -> list[dict]:
    """The locked-file check for the ticket record `entry`: each locked file, and each of `others`, against its
    reference."""
    return [finding for sha, paths in _references(entry, others, start) for finding in locked_changes(slot, sha, paths)]


def restore_locked(slot: str, entry: dict, others: list[str] | tuple = ()) -> list[str]:
    """Before a correction: each locked file, and each of `others`, that the tree has in another version than its
    reference, after a commit or an edit of the implementer, goes back to the reference version in the tree.
    Returns those paths. A diff that git cannot make restores nothing: the check of the next review then fails in
    the open."""
    restored = []
    for sha, paths in _references(entry, others):
        changed = git(slot, "diff", "--name-only", sha, "--", *paths).stdout.splitlines() if sha and paths else []
        for path in changed:
            git(slot, "checkout", "-q", sha, "--", path)
        restored += changed
    return restored


def _differ(slot: str, old: str, new: str, paths: list[str]) -> list[str]:
    """The files of `paths` that differ between two commits. A diff that git cannot make gives each file: the
    check never passes unseen."""
    diff = git(slot, "diff", "--name-only", old, new, "--", *paths)
    return diff.stdout.splitlines() if diff.returncode == 0 else paths


def written(slot: str, entry: dict, listed: list[str], tip: str, others: list[str] | tuple = (),
            start: str | None = None) -> dict:
    """The locked-file fields of the ticket record after a test-writer commit at `tip` that lists `listed` in
    `locked`. `tip` is the new reference. When it is a correction on a branch that holds other work, a locked file,
    or one of `others`, that the correction does not list and that differs from the old reference keeps the old
    one in `held`: a change that the implementer made before the correction stays a finding of the next review.
    The old reference is `test_writer_sha`, or `start` for a ticket that had none."""
    reference = entry["test_writer_sha"] or start
    held = {path: sha for path, sha in entry["held"].items() if path not in listed}
    unlisted = [path for path in [*entry["locked"], *others] if path not in listed and path not in held]
    if reference and unlisted:
        held.update(dict.fromkeys(_differ(slot, reference, tip, unlisted), reference))
    return {"test_writer_sha": tip, "locked": list(dict.fromkeys(entry["locked"] + listed)), "held": held}


def route(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """(the fixture findings, the findings that start a fix round). A fixture finding goes to the test-writer
    whatever its severity (requirement 10); a minor finding of another kind stays in the ledger."""
    fixture = [finding for finding in findings if finding["kind"] == "fixture"]
    return fixture, [finding for finding in findings if finding["kind"] != "fixture" and finding["severity"] in OPEN]


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
    """The proofs of commit `old` as they are at `new`: (file, new line) -> True when the fix changed that line,
    so its proof is due again. A line that only moved keeps its proof.
    ponytail: a renamed file reads as deleted, so each of its proofs is due again; follow renames if that costs."""
    hunks = {file: _hunks(slot, old, new, file) for file in {file for file, _ in proofs}}
    found: dict[tuple[str, int], bool] = {}
    for file, line in sorted(proofs):
        at, changed = _follow(line, hunks[file])
        found[file, at] = changed or found.get((file, at), False)
    return found


def restore(slot: str, branch: str, tip: str) -> None:
    """Put the slot back on `branch` at `tip` with a clean tree. Only for a read-only role: it has no work to keep,
    so its edits, its commits and its untracked files go. Ignored files stay (`.agent/` is one)."""
    git(slot, "checkout", "-q", "-f", "-B", branch, tip)
    git(slot, "clean", "-q", "-fd")


def disturbed(slot: str, branch: str, tip: str) -> str:
    """Reviewers are read-only: "" when the slot is still on `branch` at `tip` with a clean tree, else what they
    left. The caller checks this only when no agent runs in the slot."""
    if git(slot, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip() != branch or head(slot) != tip:
        return f"the slot is no longer on {branch} at {tip[:7]}"
    left = [line[3:] for line in git(slot, "status", "--porcelain").stdout.splitlines()]
    return f"the tree is not as the review found it: {', '.join(left)}" if left else ""


def undisturbed(attempt: Callable[[], tuple[str, T]], slot: str, branch: str, tip: str, ledger: Callable[[str], object],
                who: str, where: str, what: str) -> tuple[str, T]:
    """One review by read-only roles, until it leaves the slot as it was (requirements 5 and 6): `attempt()` runs
    the agents and returns (its error, its result). A review that changed the slot is restored and runs one more
    time. Returns ("", the result) or (why the owner is blocked, the result of the last attempt). The ledger names
    the owner `who`, the slot `where` and the review `what`."""
    for last in (False, True):
        error, result = attempt()
        left = disturbed(slot, branch, tip)
        if left:
            restore(slot, branch, tip)
            ledger(f"{who}: a read-only role broke its rule: {left}; the driver restored {where} to {tip[:7]}"
                   f"{'' if error or last else f' and runs {what} again'}.")
        if error or not left:
            break
    return error or left, result
