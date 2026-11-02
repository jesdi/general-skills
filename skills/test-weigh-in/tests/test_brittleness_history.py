"""Ticket tw/s6-brittleness/12: the history brittleness reads (window boundary, renames, merges), a red suite, and file discovery.
Interface: see brittle_repo.py."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from brittle_repo import CLAIMS, TEST, claims_repo, commit, history
from fixture_repo import git, load, snapshots, weigh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from weigh_inventory import python_files  # noqa: E402

KEY = "github.com/jesdi/demo"


def measure(repo, home, cache):
    result = weigh(repo, home, "--no-open", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    return load(snapshots(home, KEY)[-1])


def test_the_window_holds_exactly_the_last_200_source_changing_commits(tmp_path, home, cache):
    # oldest to newest: the suite (both), 298 claims-only, a co-change at 201st newest, a claims-only at 200th newest,
    # then 199 holding 100 co-changes: 100 of 200 is not brittle, while 100 of 199 or 101 of 201 would be
    repo = history(claims_repo(tmp_path / "demo"), both=0, claims_only=298)
    commit(repo, "201st", [CLAIMS, TEST])
    commit(repo, "200th", [CLAIMS])
    for i in range(199):
        commit(repo, f"recent {i}", [CLAIMS, TEST] if i % 2 == 0 else [CLAIMS])
    data = measure(repo, home, cache)
    assert data["test_files"][TEST]["brittle"] is False


def test_a_renamed_source_keeps_the_history_before_its_rename(tmp_path, home, cache):
    # the suite, the rename to claims_old.py (which edits the test's import), 8 co-changes as claims_old.py, the rename
    # back, then 4 changing claims.py alone: 11 of 15 is brittle; reading claims.py's path alone, 3 of 7 is not
    repo = claims_repo(tmp_path / "demo")
    old = "demo/claims_old.py"
    rename(repo, CLAIMS, old, "import claims,", "import claims_old as claims,")
    for i in range(8):
        commit(repo, f"both {i}", [old, TEST])
    rename(repo, old, CLAIMS, "import claims_old as claims,", "import claims,")
    history(repo, both=0, claims_only=4)
    data = measure(repo, home, cache)
    assert data["test_files"][TEST] == {"sources": [CLAIMS], "brittle": True}


def rename(repo, src, dst, old_import, new_import):
    git(repo, "mv", src, dst)
    (repo / TEST).write_text((repo / TEST).read_text().replace(old_import, new_import))
    commit(repo, f"rename to {dst}", [TEST])


def test_a_side_branch_change_to_a_name_main_renamed_counts_under_the_new_name(tmp_path, home, cache, monkeypatch):
    # the suite and a rename to claims_old.py; then main renames it back while a side branch makes 8 co-changes to
    # claims_old.py, all dated after main's rename back, and the merge (both files differ from each parent) brings them
    # in; then 4 claims.py-only commits: 12 of 16 is brittle, while leaving the side commits under claims_old.py is 4 of 8
    clock = iter(range(1_000_000))
    monkeypatch.setattr(subprocess, "run", dated(subprocess.run, clock))
    repo = claims_repo(tmp_path / "demo")
    old = "demo/claims_old.py"
    rename(repo, CLAIMS, old, "import claims,", "import claims_old as claims,")
    git(repo, "branch", "side")
    git(repo, "mv", old, CLAIMS)  # unlike rename(), the test's last line stays, so the merge has no conflict
    (repo / TEST).write_text((repo / TEST).read_text().replace("import claims_old as claims,", "import claims,"))
    git(repo, "commit", "-q", "-am", "rename back")
    git(repo, "checkout", "-q", "side")
    for i in range(8):
        commit(repo, f"side {i}", [old, TEST])
    git(repo, "checkout", "-q", "-")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    history(repo, both=0, claims_only=4)
    monkeypatch.undo()
    data = measure(repo, home, cache)
    assert data["test_files"][TEST] == {"sources": [CLAIMS], "brittle": True}


def test_a_new_file_reusing_a_renamed_away_name_leaves_the_history_with_the_renamed_file(tmp_path, home, cache):
    # the suite, 8 co-changes, the rename to claims_new.py (which edits the test), a new claims.py, then 4 changing
    # claims_new.py alone: 10 of 14 is brittle, while handing the history before the rename to the new claims.py is 1 of 5
    repo = history(claims_repo(tmp_path / "demo"), both=8, claims_only=0)
    new = "demo/claims_new.py"
    rename(repo, CLAIMS, new, "import claims,", "import claims_new as claims,")
    (repo / CLAIMS).write_text("VALUE = 1\n")
    git(repo, "add", CLAIMS)
    git(repo, "commit", "-q", "-m", "a new claims.py")
    for i in range(4):
        commit(repo, f"new {i}", [new])
    data = measure(repo, home, cache)
    assert data["test_files"][TEST] == {"sources": [new], "brittle": True}


def test_the_window_holds_the_newest_commits_by_date_across_merged_branches(tmp_path, home, cache, monkeypatch):
    # a side branch makes 200 co-changes, then main makes 200 claims.py-only changes dated after them, and the merge:
    # by date the window is the merge and 199 of main's, 0 co-changes; the side branch's history first is 199 of 200
    clock = iter(range(1_000_000))
    monkeypatch.setattr(subprocess, "run", dated(subprocess.run, clock))
    repo = claims_repo(tmp_path / "demo")
    git(repo, "checkout", "-q", "-b", "side")
    for i in range(200):
        commit(repo, f"side {i}", [CLAIMS, TEST])
    git(repo, "checkout", "-q", "-")
    for i in range(200):  # at the top of claims.py, where the side branch's appended lines don't conflict
        (repo / CLAIMS).write_text(f"# main {i}\n" + (repo / CLAIMS).read_text())
        git(repo, "commit", "-q", "-am", f"main {i}")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    monkeypatch.undo()
    data = measure(repo, home, cache)
    assert data["test_files"][TEST]["brittle"] is False


def dated(run, clock):
    """subprocess.run that dates each git commit or merge a minute after the last, so log order is commit order."""
    def run_dated(args, **kw):
        if "commit" in args or "merge" in args:
            stamp = f"{int(time.time()) - 86_400 + 60 * next(clock)} +0000"
            kw["env"] = {**(kw.get("env") or os.environ), "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        return run(args, **kw)
    return run_dated


def test_a_merge_counts_the_changes_its_resolution_made(tmp_path, home, cache):
    # the suite (both) and 1 claims-only make 1 of 2; a merge whose resolution changes both files makes it 2 of 3
    repo = history(claims_repo(tmp_path / "demo"), both=0, claims_only=1)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "side", extra=["docs/side.txt"])
    git(repo, "checkout", "-q", "-")
    commit(repo, "main", extra=["docs/main.txt"])
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    commit(repo, "merge", [CLAIMS, TEST])
    data = measure(repo, home, cache)
    assert data["test_files"][TEST]["brittle"] is True


def test_a_red_suite_leaves_refactoring_partial_with_brittleness_measured(tmp_path, home, cache):
    repo = claims_repo(tmp_path / "demo")
    (repo / "tests" / "test_fails.py").write_text("def test_fails():\n    assert False\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a failing test")
    history(repo, both=8, claims_only=1)
    data = measure(repo, home, cache)
    assert data["test_files"][TEST]["brittle"] is True
    assert "brittle_test_files" in data["metrics"]
    assert data["pillars"]["refactoring"]["partial"] is True


def test_a_repository_whose_index_git_cannot_read_raises_and_a_plain_folder_is_walked(tmp_path):
    repo = claims_repo(tmp_path / "demo")
    (repo / "untracked.py").write_text("")
    (repo / ".git" / "index").write_text("garbage")
    with pytest.raises(subprocess.CalledProcessError):
        python_files(repo)
    (tmp_path / "plain" / "pkg").mkdir(parents=True)
    (tmp_path / "plain" / "pkg" / "a.py").write_text("")
    assert python_files(tmp_path / "plain") == ["pkg/a.py"]


def test_a_git_file_pointing_at_a_missing_gitdir_raises(tmp_path):
    repo = tmp_path / "broken"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("")
    (repo / ".git").write_text(f"gitdir: {tmp_path / 'missing'}\n")
    with pytest.raises(subprocess.CalledProcessError):
        python_files(repo)
