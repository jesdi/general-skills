"""Merges into the PR branch through git plumbing: no checkout, so no slot's tree changes. Stdlib only.
The one merge with a tree is the conflicting one: it is opened in the ticket's own slot for the conflict agent.

The PR branch is checked out in no slot while tickets run, so moving its ref is safe. The caller serializes merges.
"""
from __future__ import annotations

import os
import subprocess

from driver_slots import git


def ancestor(commit: str, of: str) -> bool:
    return git(".", "merge-base", "--is-ancestor", commit, of).returncode == 0


def identity(tip: str) -> dict[str, str]:
    """git's own identity when it has one; else the identity of the ticket's last commit, so the merge can be made."""
    if git(".", "var", "GIT_COMMITTER_IDENT").returncode == 0:
        return {}
    name, email = git(".", "log", "-1", "--format=%an%n%ae", tip).stdout.splitlines()
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def commit(pr_tip: str, tip: str, message: str) -> tuple[str, str]:
    """(the commit the PR branch gets, "") or ("", the conflict).

    `pr_tip` itself when the ticket is merged already, `tip` for a fast-forward, else a new merge commit.
    """
    if ancestor(tip, pr_tip):
        return pr_tip, ""
    if ancestor(pr_tip, tip):
        return tip, ""
    tree = git(".", "merge-tree", "--write-tree", "--name-only", "--no-messages", pr_tip, tip)
    if tree.returncode:
        files = ", ".join(tree.stdout.splitlines()[1:]) or tree.stderr.strip()
        return "", f"the merge into the PR branch conflicts in {files}"
    made = subprocess.run(["git", "commit-tree", tree.stdout.strip(), "-p", pr_tip, "-p", tip, "-m", message],
                          capture_output=True, text=True, env={**os.environ, **identity(tip)})
    return (made.stdout.strip(), "") if made.returncode == 0 else ("", f"git commit-tree failed: {made.stderr.strip()}")


def begin(slot: str, pr_tip: str) -> list[str]:
    """Open the merge of `pr_tip` into the branch that `slot` has checked out, for the conflict agent to commit.
    Returns the files in conflict; with none there is nothing to resolve and the slot is as it was."""
    git(slot, "merge", "--no-commit", "--no-ff", pr_tip)
    files = git(slot, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    if not files:
        abort(slot)
    return files


def abort(slot: str) -> None:
    """Close the merge that `begin` opened: the slot is on its branch as before, with a clean tree."""
    git(slot, "merge", "--abort")


def land(pr_branch: str, new: str, old: str) -> str:
    """Move the PR branch from `old` to `new`; "" or the error. git refuses when the branch is no longer at `old`."""
    moved = git(".", "update-ref", "-m", "implement-spec: merge", f"refs/heads/{pr_branch}", new, old)
    return moved.stderr.strip() if moved.returncode else ""


def delete(branch: str, pr_branch: str) -> bool:
    """Delete a merged ticket branch. It stays while its commits are not all in the PR branch, and `git branch -D`
    itself refuses a branch that a worktree has checked out, so no slot is left detached."""
    return ancestor(branch, pr_branch) and git(".", "branch", "-q", "-D", branch).returncode == 0
