"""Sampling candidates, the mutation settings in the snapshot, and kills after a hung matrix run.

Snapshot interface: mutation_settings {mutation: bool, matrix_per_function: N or "all"}; mutation.matrix as in
test_kill_matrix.py, a row (empty when no test covers the function) for every sampled mutant.
"""
import sys

import pytest

from fixture_repo import HERE, load, snapshots, weigh
from kill_repo import replaced

sys.path.insert(0, str(HERE.parent))

KEY = "github.com/jesdi/demo"
SOURCE = "def add(x, y):\n    return x + y\n\n\ndef orphan(x, y):\n    return x - y\n"
TESTS = "from demo import calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n"


@pytest.fixture(scope="module")
def runs(tmp_path_factory, cache):
    home = tmp_path_factory.mktemp("sampling-home")
    repo = replaced(tmp_path_factory.mktemp("sampling") / "demo", ("calc.py", SOURCE), ("test_calc.py", TESTS))
    snaps = {}
    for now, flags in (("2026-01-01T00:00:00Z", ()), ("2026-01-02T00:00:00Z", ("--matrix-per-function", "all")),
                       ("2026-01-03T00:00:00Z", None)):
        result = weigh(repo, home, "--no-open", *(("--mutation", *flags) if flags is not None else ()), cache=cache, now=now)
        assert result.returncode == 0, result.stdout + result.stderr
        snaps[flags] = load(snapshots(home, KEY)[-1])
    return snaps


@pytest.mark.parametrize("flags", [(), ("--matrix-per-function", "all")])
def test_a_function_no_test_covers_is_sampled_with_an_empty_row(runs, flags):
    matrix = runs[flags]["mutation"]["matrix"]
    orphan = [m for m in matrix if ".x_orphan__mutmut_" in m]
    assert orphan and all(matrix[m] == [] for m in orphan)
    assert any(".x_add__mutmut_" in m and matrix[m] for m in matrix)


def test_the_snapshot_holds_the_mutation_settings(runs):
    assert runs[()]["mutation_settings"] == {"mutation": True, "matrix_per_function": 3}
    assert runs[("--matrix-per-function", "all")]["mutation_settings"] == {"mutation": True, "matrix_per_function": "all"}
    assert runs[None]["mutation_settings"] == {"mutation": False, "matrix_per_function": 3}


def test_a_hung_run_credits_only_the_tests_seen_to_fail_or_hang(monkeypatch):
    import weigh_mutation

    alone = {"t_fail": {"t_fail"}, "t_hang": None, "t_pass": set()}
    monkeypatch.setattr(weigh_mutation, "_failing",
                        lambda python, wt, env, mutant, tests, timeout: alone[tests[0]] if len(tests) == 1 else None)
    assert weigh_mutation._kills(None, None, {}, "m", ["t_hang", "t_fail", "t_pass"], 1) == ["t_fail", "t_hang"]
