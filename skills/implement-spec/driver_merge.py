"""Merges into the PR branch through git plumbing: no checkout, so no slot's tree changes. Only a conflicting merge
gets a tree: `begin` opens it in the ticket's own slot for the conflict agent. The caller holds the merge lock.
"""
from __future__ import annotations

import os
import subprocess

from driver_slots import ancestor, git


def identity(tip: str, slot: str = ".") -> dict[str, str]:
    """git's own identity when it has one; else the identity of the ticket's last commit, so the merge can be made."""
    if git(slot, "var", "GIT_COMMITTER_IDENT").returncode == 0:
        return {}
    name, email = git(slot, "log", "-1", "--format=%an%n%ae", tip).stdout.splitlines()
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def signs() -> bool:
    """commit.gpgsign is on: every commit the driver makes must be signed."""
    return git(".", "config", "--type=bool", "commit.gpgsign").stdout.strip() == "true"


def commit_tree(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """`git commit-tree` with `env` on top of os.environ, signed when commit.gpgsign is on: commit-tree ignores it."""
    sign = ["-S"] if signs() else []
    return subprocess.run(["git", "commit-tree", *sign, *args], capture_output=True, text=True, env={**os.environ, **env})


def unsigned(tip: str) -> list[str]:
    """The commits of `tip` that origin does not have and that have no good signature, when commit.gpgsign is on."""
    if not signs():
        return []
    rows = git(".", "log", "--format=%h %G?", tip, "--not", "--remotes=origin").stdout.splitlines()
    return [row.split()[0] for row in rows if not row.endswith(" G")]


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
    made = commit_tree([tree.stdout.strip(), "-p", pr_tip, "-p", tip, "-m", message], identity(tip))
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


def land(pr_branch: str, new: str, old: str) -> str:
    """Move the PR branch from `old` to `new`; "" or the error. git refuses when the branch is no longer at `old`."""
    moved = git(".", "update-ref", "-m", "implement-spec: merge", f"refs/heads/{pr_branch}", new, old)
    return moved.stderr.strip() if moved.returncode else ""


def delete(branch: str, pr_branch: str) -> bool:
    """Delete a ticket branch whose commits are all in the PR branch. git refuses one a worktree has checked out."""
    return ancestor(branch, pr_branch) and git(".", "branch", "-q", "-D", branch).returncode == 0
