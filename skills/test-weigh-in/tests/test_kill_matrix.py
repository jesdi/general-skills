"""The sampled kill matrix of `--mutation`, read from the snapshot.

Snapshot interface the tests pin (the spec leaves the JSON keys open):
  mutation.sampled   list of the sampled mutant ids (mutmut names, e.g. demo.parser.x_parse__mutmut_3)
  mutation.mutants   {id: {"line": source line of the mutation}} for the sampled mutants
  mutation.matrix    {id: [ids of the tests that kill it]}, a row for every sampled mutant
  tests[node]        booleans "subsumed", "hollow" (killed none of >= 2 sampled mutants on its lines)
                     and "insufficient_evidence" (fewer than 2 sampled mutants on its lines)
  metrics            hollow_tests and subsumed_in_kill_matrix are percentages of all tests
"""
import re

import pytest

from fixture_repo import load, snapshots, weigh
from kill_repo import matrix_repo, sample_repo, shift_repo

KEY = "github.com/jesdi/demo"


def function_of(mutant):
    return re.search(r"\.x_(\w+?)__mutmut_\d+$", mutant)[1]


def mutate(factory, name, tmp_path_factory, cache, *flags, nows=("2026-01-01T00:00:00Z",)):
    home = tmp_path_factory.mktemp(name + "-home")
    repo = factory(tmp_path_factory.mktemp(name) / "demo")
    snaps = []
    for now in nows:
        result = weigh(repo, home, "--mutation", "--no-open", *flags, cache=cache, now=now)
        assert result.returncode == 0, result.stdout + result.stderr
        snaps.append(load(snapshots(home, KEY)[-1]))
    return snaps


@pytest.fixture(scope="module")
def twice(tmp_path_factory, cache):
    return mutate(sample_repo, "twice", tmp_path_factory, cache, nows=("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"))


@pytest.fixture(scope="module")
def everything(tmp_path_factory, cache):
    return mutate(sample_repo, "all", tmp_path_factory, cache, "--matrix-per-function", "all")[0]


@pytest.fixture(scope="module")
def ops(tmp_path_factory, cache):
    return mutate(matrix_repo, "ops", tmp_path_factory, cache)[0]


@pytest.fixture(scope="module")
def shift(tmp_path_factory, cache):
    return mutate(shift_repo, "shift", tmp_path_factory, cache)[0]


def by_function(snap, name):
    return [m for m in snap["mutation"]["sampled"] if function_of(m) == name]


def test_two_runs_list_the_same_sampled_mutants_and_kill_matrix(twice):
    first, second = (s["mutation"] for s in twice)
    assert first["sampled"] and first["matrix"]
    assert first["sampled"] == second["sampled"]
    assert first["matrix"] == second["matrix"]


def test_at_most_three_mutants_per_function_on_different_lines(twice):
    snap = twice[0]
    parse = by_function(snap, "parse")
    assert len(parse) == 3
    assert len({snap["mutation"]["mutants"][m]["line"] for m in parse}) == 3
    assert len(by_function(snap, "noop")) == 1


def test_all_takes_every_mutant_of_the_function(everything):
    matrix = everything["mutation"]["matrix"]
    parse = {m for m in matrix if function_of(m) == "parse"}
    assert len(parse) == 12
    assert len({everything["mutation"]["mutants"][m]["line"] for m in parse}) == 6


def test_matrix_row_records_every_killing_test(twice):
    snap = twice[0]
    tests = snap["tests"]
    ok, edge = (next(n for n in tests if n.endswith("::" + t)) for t in ("test_parse_ok", "test_parse_edge"))
    rows = [snap["mutation"]["matrix"][m] for m in by_function(snap, "parse")]
    assert rows
    for row in rows:
        assert {ok, edge} <= set(row)


def test_test_killing_what_another_kills_is_subsumed(ops):
    tests = ops["tests"]
    a = next(t for n, t in tests.items() if n.endswith("::test_a"))
    assert a["subsumed"] is True
    assert a["insufficient_evidence"] is False
    assert ops["metrics"]["subsumed_in_kill_matrix"]["value"] == pytest.approx(100 * sum(t["subsumed"] for t in tests.values()) / len(tests))
    assert ops["metrics"]["subsumed_in_kill_matrix"]["value"] > 0


def test_single_sampled_mutant_is_insufficient_evidence_and_counts_nowhere(shift):
    tests = shift["tests"]
    invalid = next(t for n, t in tests.items() if n.endswith("::test_parse_invalid"))
    assert invalid["insufficient_evidence"] is True
    assert invalid["subsumed"] is False
    assert invalid["hollow"] is False
    assert shift["metrics"]["subsumed_in_kill_matrix"]["value"] == 0
    assert shift["metrics"]["hollow_tests"]["value"] == 0
    assert shift["metrics"]["no_unique_coverage"]["value"] == 0
    assert shift["metrics"]["duplicate_clusters"]["value"] == 0


def test_test_that_kills_none_of_four_sampled_mutants_is_hollow(ops):
    tests = ops["tests"]
    roundtrip = next(t for n, t in tests.items() if n.endswith("::test_session_roundtrip"))
    assert roundtrip["hollow"] is True
    assert roundtrip["insufficient_evidence"] is False
    assert ops["metrics"]["hollow_tests"]["value"] == pytest.approx(100 * sum(t["hollow"] for t in tests.values()) / len(tests))
    assert ops["metrics"]["hollow_tests"]["value"] > 0


def test_regression_and_economy_get_their_full_weights(twice):
    snap = twice[0]
    assert {"hollow_tests", "pseudo_tested_functions", "subsumed_in_kill_matrix"} <= snap["metrics"].keys()
    assert snap["pillars"]["regression"]["partial"] is False
    assert snap["pillars"]["economy"]["partial"] is False
