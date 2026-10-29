"""History of the PR branch as commits made with git plumbing: the tidy into one commit per ticket, and the new
messages of a rebased history and of a fix batch.

Nothing here checks out a tree or moves a ref: a new commit takes the tree of an existing commit.
"""
from __future__ import annotations

import driver_merge
from driver_slots import git
from driver_tickets import Ticket


def message(ticket: Ticket) -> str:
    return f"feat({ticket.number}): {ticket.title}\n\nTicket: {ticket.file}\n"


def text(commit: str) -> str:
    """The message of `commit`."""
    return git(".", "log", "-1", "--format=%B", commit).stdout


def same_tree(one: str, other: str) -> bool:
    """Both commits exist and have the same tree."""
    trees = [git(".", "rev-parse", "-q", "--verify", f"{commit}^{{tree}}").stdout.strip() for commit in (one, other)]
    return bool(trees[0]) and trees[0] == trees[1]


def _commit(source: str, parent: str, words: str) -> str:
    """A commit on `parent` with the tree and the author of `source` and the message `words`; "" when git fails."""
    name, email, date = git(".", "log", "-1", "--format=%an%n%ae%n%aI", source).stdout.splitlines() or [""] * 3
    env = {**driver_merge.identity(source), "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
    return driver_merge.commit_tree([f"{source}^{{tree}}", "-p", parent, "-m", words.strip() + "\n"], env).stdout.strip()


def rewrite(parent: str, steps: list[tuple[str, str]]) -> list[str]:
    """One commit per (source commit, message), in a line on `parent`: each has the tree of its source."""
    made = []
    for source, words in steps:
        parent = _commit(source, parent, words)
        made.append(parent)
    return made


def tidy(parent: str, landed: list[list[str]], tickets: dict[str, Ticket]) -> dict[str, str]:
    """ticket number -> its one commit, in a line on `parent`; a merge that changed no file gets the one before it."""
    commits = {}
    for number, source in landed:
        if not same_tree(source, parent):
            parent = _commit(source, parent, message(tickets[number]))
        commits[number] = parent
    return commits
