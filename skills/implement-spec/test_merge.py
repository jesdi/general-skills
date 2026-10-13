"""Plumbing merges: fast-forward, merge commit, conflict, the guarded ref move and the guarded branch delete."""
import os
import subprocess

import driver_merge
from conftest import GIT


def sh(*command):
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def commit_on(branch, file, text, change=None):
    """A commit on `branch`, made in a scratch worktree so the main one never moves. `change(path)` replaces the write."""
    sh("git", "worktree", "add", "-q", "-f", "../scratch", branch)
    if change:
        change(f"../scratch/{file}")
    else:
        with open(f"../scratch/{file}", "w") as out:
            out.write(text)
    sh(*GIT, "-C", "../scratch", "add", "-A")
    sh(*GIT, "-C", "../scratch", "commit", "-q", "-m", f"{branch} {file}")
    sh("git", "worktree", "remove", "--force", "../scratch")
    return sh("git", "rev-parse", branch)


def test_commit_is_the_tip_for_a_fast_forward_and_the_pr_tip_when_already_merged(repo):
    repo()
    base = sh("git", "rev-parse", "HEAD")
    sh("git", "branch", "ticket/01")
    tip = commit_on("ticket/01", "a.txt", "a\n")
    assert driver_merge.commit(base, tip, "m") == (tip, "")
    assert driver_merge.commit(tip, base, "m") == (tip, "")


def test_a_merge_commit_has_both_parents_and_touches_no_worktree(repo, monkeypatch):
    repo()
    sh("git", "branch", "ticket/01")
    tip = commit_on("ticket/01", "a.txt", "a\n")
    sh("git", "branch", "pr")
    pr = commit_on("pr", "b.txt", "b\n")
    merge, error = driver_merge.commit(pr, tip, "Merge ticket/01 into pr")
    assert error == "" and sh("git", "rev-list", "--parents", "-n1", merge).split() == [merge, pr, tip]
    assert sh("git", "ls-tree", "--name-only", merge).split() == [".gitignore", "a.txt", "b.txt"]
    assert sh("git", "status", "--porcelain") == "" and sh("git", "symbolic-ref", "--short", "HEAD") == "feat/x"
    # With no identity of its own, git gets the identity of the ticket's last commit.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    merge, error = driver_merge.commit(pr, tip, "m")
    assert error == "" and sh("git", "log", "-1", "--format=%an <%ae> %cn", merge) == "t <t@example.com> t"


def test_a_conflict_names_its_files_and_makes_no_commit(repo):
    repo()
    sh("git", "branch", "ticket/01")
    tip = commit_on("ticket/01", "a.txt", "one\n")
    sh("git", "branch", "pr")
    pr = commit_on("pr", "a.txt", "two\n")
    assert driver_merge.commit(pr, tip, "m") == ("", "the merge into the PR branch conflicts in a.txt")
    assert driver_merge.commit(pr, "1" * 40, "m")[0] == ""  # no such commit


def test_land_moves_the_branch_only_from_the_tip_the_merge_was_made_on(repo):
    repo()
    base = sh("git", "rev-parse", "HEAD")
    sh("git", "branch", "pr")
    sh("git", "branch", "ticket/01")
    tip = commit_on("ticket/01", "a.txt", "a\n")
    moved = commit_on("pr", "b.txt", "b\n")
    assert driver_merge.land("pr", tip, base) != "" and sh("git", "rev-parse", "pr") == moved
    assert driver_merge.land("pr", tip, moved) == "" and sh("git", "rev-parse", "pr") == tip


def test_delete_keeps_a_branch_a_slot_has_checked_out_and_a_branch_that_is_not_merged(repo, tmp_path):
    repo()
    sh("git", "branch", "ticket/01")
    slot = tmp_path / ".agent/worktrees/slot-2"
    sh("git", "worktree", "add", "-q", str(slot), "ticket/01")
    assert driver_merge.delete("ticket/01", "feat/x") is False
    assert sh("git", "-C", str(slot), "symbolic-ref", "--short", "HEAD") == "ticket/01"  # the slot is not detached
    sh("git", "-C", str(slot), "checkout", "-q", "--detach")
    sh("git", "branch", "ticket/02")
    commit_on("ticket/02", "a.txt", "a\n")
    assert driver_merge.delete("ticket/02", "feat/x") is False and driver_merge.delete("ticket/01", "feat/x") is True
    assert sh("git", "branch", "--format=%(refname:short)").split() == ["feat/x", "ticket/02"]


def test_the_landing_check_refuses_what_it_cannot_read(repo, monkeypatch):
    """Ticket 09: the check fails closed. Only a path that a commit which exists does not have is "absent"."""
    repo()
    base = sh("git", "rev-parse", "HEAD")
    sh("git", "branch", "pr")
    sh("git", "branch", "ticket/02")
    same = commit_on("ticket/02", "a.txt", "a\n")
    tickets = {"01": {"phase": "merged", "locked": ["t.py", "gone.py"]}, "02": {"phase": "merge", "locked": []}}
    assert driver_merge.refused(tickets, "02", base, same) == ""  # absent on both sides of two commits that exist
    there = commit_on("ticket/02", "t.py", "t\n")
    assert driver_merge.refused(tickets, "02", base, there) == "locked file changed: t.py"
    assert driver_merge.refused(tickets, "02", there, base) == "locked file changed: t.py"
    # A candidate that is no commit: an id that does not resolve, and a tree.
    for bad in ("1" * 40, sh("git", "rev-parse", f"{base}^{{tree}}")):
        assert driver_merge.refused(tickets, "02", base, bad).startswith(f"cannot read the locked files at {bad}: ")
        assert driver_merge.refused(tickets, "02", bad, base).startswith(f"cannot read the locked files at {bad}: ")
    # A record with no `locked` key is a ticket that locked nothing: no error in the middle of a landing.
    assert driver_merge.protected({"01": {"phase": "merged"}, "02": {"phase": "merge"}}, "02") == []
    assert driver_merge.protected({"01": {"phase": "merged", "locked": ["a"]}}, "02") == ["a"]
    # git fails on both sides: the two answers are equal, and the landing is refused all the same.
    monkeypatch.setenv("GIT_DIR", "/nowhere")
    assert driver_merge.refused(tickets, "02", base, there).startswith(f"cannot read the locked files at {base}: ")
    monkeypatch.delenv("GIT_DIR")
    assert driver_merge.land("pr", there, "1" * 40) != "" and sh("git", "rev-parse", "pr") == base


def test_the_landing_check_compares_the_mode_and_the_type_of_a_locked_file_too(repo):
    """Review round 1, Codex 4: a symlink whose target is the old text has the blob id of the file it replaced."""
    repo()
    sh("git", "branch", "ticket/01")
    file = commit_on("ticket/01", "t.py", "t\n")
    tickets = {"01": {"phase": "merged", "locked": ["t.py"]}, "02": {"phase": "merge", "locked": []}}
    sh("git", "branch", "ticket/02", file)
    runs = commit_on("ticket/02", "t.py", "", lambda path: os.chmod(path, 0o755))
    link = commit_on("ticket/02", "t.py", "", lambda path: (os.remove(path), os.symlink("t\n", path)))
    blob = lambda commit: sh("git", "rev-parse", f"{commit}:t.py")
    assert blob(file) == blob(runs) == blob(link) and sh("git", "ls-tree", link, "t.py").startswith("120000 ")
    assert driver_merge.refused(tickets, "02", file, file) == ""
    assert driver_merge.refused(tickets, "02", file, runs) == "locked file changed: t.py"
    assert driver_merge.refused(tickets, "02", file, link) == "locked file changed: t.py"
