"""`--mutation` through weigh.py: the score over all mutants, and recovery from a run SIGKILLed while mutmut's own
processes keep running.

Snapshot interface: metrics.mutation_score {value: percent of all mutants killed, score}.
"""
import os
import signal
import sys

import pytest

from fixture_repo import HERE, load, snapshots, weigh
from kill_repo import replaced
from test_mutation_score import SLOW, commit, finish, start, wait_for_mutmut, worktrees

sys.path.insert(0, str(HERE.parent))

KEY = "github.com/jesdi/demo"
# add's mutant is killed, mul's survives (x / y with y == 1), orphan's has no covering test: 1 of 3 killed
SCORED = "def add(x, y):\n    return x + y\n\n\ndef mul(x, y):\n    return x * y\n\n\ndef orphan(x, y):\n    return x - y\n"
SCORED_TESTS = "from demo import scored\n\n\ndef test_ops():\n    assert scored.add(1, 2) == 3\n    assert scored.mul(3, 1) == 3\n"


def test_score_is_over_all_mutants_killed_surviving_and_uncovered(tmp_path, home, cache):
    from weigh_rules import load as load_rules
    from weigh_score import metric_score

    repo = replaced(tmp_path / "demo", ("scored.py", SCORED), ("test_scored.py", SCORED_TESTS))
    result = weigh(repo, home, "--mutation", "--no-open", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    metric = load(snapshots(home, KEY)[-1])["metrics"]["mutation_score"]
    assert metric["value"] == pytest.approx(100 / 3)
    rule = load_rules()["pillars"]["regression"]["metrics"]["mutation_score"]
    assert metric["score"] == pytest.approx(metric_score(100 / 3, rule["target"], rule["bad"], rule.get("log", False)))


def kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_run_sigkilled_while_mutmut_runs_is_cleaned_up_by_the_next_run(tmp_path, home, cache):
    from fixture_repo import build_repo

    repo = build_repo(tmp_path / "demo")
    commit(repo, {"tests/test_slow.py": SLOW})
    first = start(repo, home, cache, tmp_path / "first.err")
    second = None
    try:
        wait_for_mutmut(repo, first)
        first.kill()  # weigh.py alone: mutmut and its workers live on, as after a real SIGKILL
        first.wait()
        stale = [l.removeprefix("worktree ") for l in worktrees(repo)[1:]]
        assert len(stale) == 1
        second = start(repo, home, cache, tmp_path / "second.err")
        fresh = wait_for_mutmut(repo, second, ignore=stale)
        assert [l.removeprefix("worktree ") for l in worktrees(repo)[1:]] == [fresh]
        assert not os.path.exists(stale[0])
        assert finish(second, tmp_path / "second.err", 900) == 0
    finally:
        kill_group(first.pid)
        if second:
            kill_group(second.pid)
            second.wait()
    assert len(worktrees(repo)) == 1
