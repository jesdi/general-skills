"""Merges into the PR branch through git plumbing: no checkout, so no slot's tree changes. Stdlib only.
The one merge with a tree is the conflicting one: it is opened in the ticket's own slot for the conflict agent.

The PR branch is checked out in no slot while tickets run, so moving its ref is safe. The caller serializes merges.
`refused` is the locked-file check of the landing (requirement 7): the locked files of the merged tickets.
"""
from __future__ import annotations

import os
import subprocess

from driver_slots import git, names


def ancestor(commit: str, of: str) -> bool:
    return git(".", "merge-base", "--is-ancestor", commit, of).returncode == 0


def identity(tip: str, slot: str = ".") -> dict[str, str]:
    """git's own identity when it has one; else the identity of the ticket's last commit, so the merge can be made."""
    if git(slot, "var", "GIT_COMMITTER_IDENT").returncode == 0:
        return {}
    name, email = git(slot, "log", "-1", "--format=%an%n%ae", tip).stdout.splitlines()
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
    # git merge needs a committer even with --no-commit; a machine with no git identity (CI) gets the ticket's own.
    env = {**os.environ, **identity("HEAD", slot)}
    subprocess.run(["git", "-C", slot, "merge", "--no-commit", "--no-ff", pr_tip], capture_output=True, env=env)
    files = git(slot, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    if not files:
        abort(slot)
    return files


def abort(slot: str) -> None:
    """Close the merge that `begin` opened: the slot is on its branch as before, with a clean tree."""
    git(slot, "merge", "--abort")


def protected(tickets: dict, number: str) -> list[str]:
    """The locked files of every merged ticket of `tickets` of state.json that ticket `number` does not lock itself.
    Its test-writer may change one: a file that it lists in `locked` is a locked file of this ticket too.
    A record with no `locked` locked nothing: this runs in the middle of a landing and does not raise."""
    merged = {path for entry in tickets.values() if entry.get("phase") == "merged" for path in entry.get("locked") or []}
    return sorted(merged - set(tickets.get(number, {}).get("locked") or []))


def _entries(commit: str, paths: list[str]) -> tuple[dict[str, str], str]:
    """(path -> its tree entry: mode, type and object id, for each of `paths` that the commit `commit` has, "") or
    ({}, why git cannot tell). A file that became a symlink or got another mode has another entry.
    Only a path that a commit which exists does not have is absent: an id that is no commit, or any other git
    failure, is an error, so the check never passes unseen."""
    tree = git(".", "ls-tree", "-z", f"{commit}^{{commit}}", "--", *paths)
    if tree.returncode:
        return {}, f"cannot read the locked files at {commit}: {tree.stderr.strip() or f'git ls-tree exit {tree.returncode}'}"
    return {path: entry for entry, _, path in (line.partition("\t") for line in names(tree.stdout))}, ""


def refused(tickets: dict, number: str, old: str, new: str) -> str:
    """Requirement 7 at the landing of ticket `number`: "" or why the commit `new` may not land on the PR tip
    `old`. Each locked file of a merged ticket must be the same blob, with the same mode, in both; a file that only
    one has differs.
    The caller lands `new` only from `old` (`land`), so the check and the ref move are one step."""
    paths = protected(tickets, number)
    if not paths:
        return ""  # `git ls-tree` with no path lists the whole tree
    (before, error), (after, failed) = _entries(old, paths), _entries(new, paths)
    return error or failed or "; ".join(f"locked file changed: {path}" for path in paths
                                        if before.get(path) != after.get(path))


def restore(slot: str, pr_tip: str, paths: list[str]) -> list[str]:
    """Before a correction: each of `paths` (`protected`) that the ticket changed, in a commit or in the tree of
    `slot`, goes back to the version of the PR tip in the tree; a file that the PR tip does not have is removed.
    Returns those paths. A file that the correction lists becomes a locked file of its ticket, so it must start from
    the locked version. "Changed" is against the commit that the branch has in common with the PR tip: a file that
    only the PR branch changed since then is not the ticket's change. With no such commit, it is against the PR tip."""
    base = git(slot, "merge-base", "HEAD", pr_tip).stdout.strip() or pr_tip
    changed = names(git(slot, "diff", "--name-only", "-z", base, "--", *paths).stdout) if paths else []
    for path in changed:
        if git(slot, "checkout", "-q", pr_tip, "--", path).returncode:
            git(slot, "rm", "-q", "-f", "--", path)
    return changed


def land(pr_branch: str, new: str, old: str) -> str:
    """Move the PR branch from `old` to `new`; "" or the error. git refuses when the branch is no longer at `old`."""
    moved = git(".", "update-ref", "-m", "implement-spec: merge", f"refs/heads/{pr_branch}", new, old)
    return moved.stderr.strip() if moved.returncode else ""


def delete(branch: str, pr_branch: str) -> bool:
    """Delete a merged ticket branch. It stays while its commits are not all in the PR branch, and `git branch -D`
    itself refuses a branch that a worktree has checked out, so no slot is left detached."""
    return ancestor(branch, pr_branch) and git(".", "branch", "-q", "-D", branch).returncode == 0
