"""Merges into the PR branch through git plumbing: no checkout, so no slot's tree changes. Only a conflicting merge
gets a tree: `begin` opens it in the ticket's own slot for the conflict agent.

`refused` is the locked-file check of a landing. The caller holds the merge lock.
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
    """(the commit the PR branch gets, "") or ("", the error): `pr_tip` when the ticket is merged already, `tip`
    for a fast-forward, else a new merge commit."""
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
    """Open the merge of `pr_tip` into the branch of `slot`. Returns the files in conflict; with none, the slot is
    as it was."""
    # git merge needs a committer even with --no-commit.
    env = {**os.environ, **identity("HEAD", slot)}
    subprocess.run(["git", "-C", slot, "merge", "--no-commit", "--no-ff", pr_tip], capture_output=True, env=env)
    files = git(slot, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    if not files:
        abort(slot)
    return files


def abort(slot: str) -> None:
    """Close the merge that `begin` opened."""
    git(slot, "merge", "--abort")


def protected(tickets: dict, number: str) -> list[str]:
    """The locked files of the merged tickets that ticket `number` does not lock itself. `tickets` is that of
    state.json; a record with no `locked` locked nothing."""
    merged = {path for entry in tickets.values() if entry.get("phase") == "merged" for path in entry.get("locked") or []}
    return sorted(merged - set(tickets.get(number, {}).get("locked") or []))


def _entries(commit: str, paths: list[str]) -> tuple[dict[str, str], str]:
    """({path: "mode type object-id"} for each of `paths` that `commit` has, "") or ({}, why git cannot tell)."""
    tree = git(".", "ls-tree", "-z", f"{commit}^{{commit}}", "--", *paths)
    if tree.returncode:
        return {}, f"cannot read the locked files at {commit}: {tree.stderr.strip() or f'git ls-tree exit {tree.returncode}'}"
    return {path: entry for entry, _, path in (line.partition("\t") for line in names(tree.stdout))}, ""


def refused(tickets: dict, number: str, old: str, new: str) -> str:
    """"" or why ticket `number` may not land `new` on the PR tip `old`: each locked file of a merged ticket must
    be the same blob with the same mode in both."""
    paths = protected(tickets, number)
    if not paths:
        return ""  # `git ls-tree` with no path lists the whole tree
    (before, error), (after, failed) = _entries(old, paths), _entries(new, paths)
    return error or failed or "; ".join(f"locked file changed: {path}" for path in paths
                                        if before.get(path) != after.get(path))


def restore(slot: str, pr_tip: str, paths: list[str]) -> list[str]:
    """Put each of `paths` that the ticket changed back to the version of the PR tip, in the tree of `slot` only; a
    file the PR tip does not have is removed. Returns those paths."""
    # Against the merge base: a file that only the PR branch changed since then is not the ticket's change.
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
    """Delete a ticket branch whose commits are all in the PR branch. git refuses one a worktree has checked out."""
    return ancestor(branch, pr_branch) and git(".", "branch", "-q", "-D", branch).returncode == 0
