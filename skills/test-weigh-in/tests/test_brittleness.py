"""Ticket tw/s6-brittleness/12: brittle test files from git history, and the deterministic default tier.
Interface: see brittle_repo.py."""
from brittle_repo import CLAIMS, TEST, board_repo, claims_repo, commit, history, without_git
from fixture_repo import build_repo, load, snapshots, weigh

KEY = "github.com/jesdi/demo"


def measure(repo, home, cache, key=KEY):
    result = weigh(repo, home, "--no-open", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    return load(snapshots(home, key)[-1])


def test_a_test_file_changed_in_9_of_the_10_commits_that_changed_its_source_is_brittle(tmp_path, home, cache):
    repo = history(claims_repo(tmp_path / "demo"), both=8, claims_only=1)
    data = measure(repo, home, cache)
    assert data["test_files"][TEST] == {"sources": [CLAIMS], "brittle": True}
    assert data["metrics"]["brittle_test_files"]["value"] == 100
    assert data["pillars"]["refactoring"]["partial"] is False


def test_a_test_file_changed_in_5_of_the_10_commits_is_not_brittle(tmp_path, home, cache):
    repo = history(claims_repo(tmp_path / "demo"), both=4, claims_only=5)
    data = measure(repo, home, cache)
    assert data["test_files"][TEST] == {"sources": [CLAIMS], "brittle": False}
    assert data["metrics"]["brittle_test_files"]["value"] == 0


def test_a_helper_with_40_of_300_covered_lines_is_not_a_mapped_source(tmp_path, home, cache):
    data = measure(board_repo(tmp_path / "demo"), home, cache)
    assert data["test_files"]["tests/test_board.py"]["sources"] == ["demo/board.py"]


def test_a_commit_that_changed_140_files_is_not_counted(tmp_path, home, cache):
    repo = history(claims_repo(tmp_path / "demo"), both=0, claims_only=1)  # suite (both) + 1 claims-only: 1 of 2
    commit(repo, "reformat", [CLAIMS, TEST], [f"docs/f{i}.txt" for i in range(140)])  # counted, it would make 2 of 3
    data = measure(repo, home, cache)
    assert data["test_files"][TEST]["brittle"] is False


def test_only_the_last_200_of_500_source_changing_commits_are_counted(tmp_path, home, cache):
    # the suite commit and 299 more change both files, the last 200 change claims.py alone: 300 of 500 would be brittle
    repo = history(claims_repo(tmp_path / "demo"), both=299, claims_only=200)
    data = measure(repo, home, cache)
    assert data["test_files"][TEST]["brittle"] is False


def test_a_target_that_is_not_a_git_repository_leaves_brittleness_unmeasured(tmp_path, home, cache):
    repo = without_git(history(claims_repo(tmp_path / "demo"), both=8, claims_only=1))
    data = measure(repo, home, cache, key="local/demo")
    assert not data["commit"]
    assert data["test_files"][TEST]["brittle"] is None
    assert "brittle_test_files" not in data["metrics"]
    assert data["pillars"]["refactoring"]["partial"] is True


def test_two_runs_of_the_default_repo_are_equal_apart_from_durations(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")

    def run():
        data = measure(repo, home, cache)
        assert "brittle_test_files" in data["metrics"]
        for test in data["tests"].values():
            del test["duration"]
        del data["inventory"]["runtime_seconds"]
        for metric in ("total_runtime", "p95_duration", "slowest_decile_share", "tests_over_10s"):
            del data["metrics"][metric]
        del data["pillars"]["feedback"]["score"]
        return data

    first = run()
    second = run()
    assert first == second
    assert [list(f["sources"]) for f in first["test_files"].values()] == [sorted(f["sources"]) for f in first["test_files"].values()]
