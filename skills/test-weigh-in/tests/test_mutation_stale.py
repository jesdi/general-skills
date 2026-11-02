"""A refused `--mutation` run still removes the worktree an interrupted run left behind."""
import subprocess

import pytest

from fixture_repo import build_repo, git
from test_mutation_score import commit, mutate, worktrees


def plant_stale(repo, cache):
    """A worktree registered in the repo, under the cache, named after a pid that no longer runs."""
    dead = subprocess.Popen(["true"])
    dead.wait()
    wt = cache / "worktrees" / f"{dead.pid}-stale"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    return wt


@pytest.mark.parametrize("break_it", [
    lambda repo: (repo / "demo" / "board.py").write_text("dirty\n"),
    lambda repo: commit(repo, {"tests/test_claim.py": "def test_claim_flow():\n    assert False\n"}),
], ids=["dirty", "red"])
def test_refused_run_removes_the_stale_worktree(tmp_path, home, cache, break_it):
    repo = build_repo(tmp_path / "demo")
    wt = plant_stale(repo, cache)
    break_it(repo)
    result = mutate(repo, home, cache)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not wt.exists()
    assert len(worktrees(repo)) == 1
