"""History of the PR branch as commits made with git plumbing (requirement 19): the tidy into one commit per ticket,
and the new messages of a rebased history and of a fix batch. Stdlib only.

Nothing here checks out a tree or moves a ref: a new commit takes the tree of an existing commit, so a rewrite
changes how the work is presented and never a file. The caller compares `HEAD^{tree}` before it moves the branch.
"""
from __future__ import annotations

import os
import subprocess

import driver_merge
from driver_slots import git
from driver_tickets import Ticket


def message(ticket: Ticket) -> str:
    return f"feat({ticket.number}): {ticket.title}\n\nTicket: {ticket.file}\n"


def text(commit: str) -> str:
    """The message of `commit`."""
    return git(".", "log", "-1", "--format=%B", commit).stdout


def same_tree(one: str, other: str) -> bool:
    """Both commits exist and have the same tree. The trees are compared, not a diff: a commit that was dropped
    cannot hide behind a diff that looks the same."""
    trees = [git(".", "rev-parse", "-q", "--verify", f"{commit}^{{tree}}").stdout.strip() for commit in (one, other)]
    return bool(trees[0]) and trees[0] == trees[1]


def _commit(source: str, parent: str, words: str) -> str:
    """A commit on `parent` with the tree and the author of `source` and the message `words`; "" when git fails."""
    name, email, date = git(".", "log", "-1", "--format=%an%n%ae%n%aI", source).stdout.splitlines() or [""] * 3
    env = {**os.environ, **driver_merge.identity(source), "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_AUTHOR_DATE": date}
    made = subprocess.run(["git", "commit-tree", f"{source}^{{tree}}", "-p", parent, "-m", words.strip() + "\n"],
                          capture_output=True, text=True, env=env)
    return made.stdout.strip()


def rewrite(parent: str, steps: list[tuple[str, str]]) -> list[str]:
    """One commit per (source commit, message), in a line on `parent`: each has the tree of its source."""
    made = []
    for source, words in steps:
        parent = _commit(source, parent, words)
        made.append(parent)
    return made


def tidy(parent: str, landed: list[list[str]], tickets: dict[str, Ticket]) -> dict[str, str]:
    """ticket number -> its one commit, in merge order, in a line on `parent`. `landed` holds, per ticket, the
    commit the PR branch had when the ticket's merge landed: the ticket's commit takes that tree, so merge commits
    are flattened and a conflict resolution stays with the ticket whose merge made it.
    A ticket whose merge left the tree as it was has no commit of its own: it gets the commit before it."""
    commits = {}
    for number, source in landed:
        if not same_tree(source, parent):
            parent = _commit(source, parent, message(tickets[number]))
        commits[number] = parent
    return commits
