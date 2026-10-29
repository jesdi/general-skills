import pytest

from fixture_repo import build_repo, git, load, short_sha, site_files, snapshots, weigh


def ok(result):
    assert result.returncode == 0, result.stdout + result.stderr


def one_snapshot(home, key):
    (snap,) = snapshots(home, key)
    return load(snap)


def test_uv_repo_measured_without_touching_it(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    before = site_files(repo)
    result = weigh(repo, home, cache=cache)
    ok(result)
    assert len(snapshots(home, "github.com/jesdi/demo")) == 1
    assert git(repo, "status", "--porcelain") == ""
    assert site_files(repo) == before


def test_inventory_counts_lines_and_runtime(run, home):
    _, result = run(test_lines=6000, source_lines=4000)
    ok(result)
    inv = one_snapshot(home, "github.com/jesdi/demo")["inventory"]
    assert (inv["tests"], inv["test_files"], inv["test_lines"], inv["source_lines"]) == (40, 8, 6000, 4000)
    assert inv["runtime_seconds"] == pytest.approx(45)


def test_pytest_args_select_tests_and_are_recorded(run, home):
    _, result = run("--pytest-args", "-m 'not e2e'")
    ok(result)
    snap = one_snapshot(home, "github.com/jesdi/demo")
    assert snap["inventory"]["tests"] == 30
    assert snap["pytest_args"] == "-m 'not e2e'"


def test_repo_pytest_version_is_used_not_the_overlays(run, home):
    _, result = run(pytest_version="7.4.4")
    ok(result)
    assert one_snapshot(home, "github.com/jesdi/demo")["pytest_version"].startswith("7.4")


@pytest.mark.parametrize(
    "origin, name, key",
    [
        ("git@github.com:jesdi/api.git", "api", "github.com/jesdi/api"),
        ("https://github.com/acme/api.git", "api", "github.com/acme/api"),
        ("git@github.com:a-b/c.git", "x", "github.com/a-b/c"),
        ("git@github.com:a/b-c.git", "y", "github.com/a/b-c"),
        ("git@gitlab.com:jesdi/api.git", "y", "gitlab.com/jesdi/api"),
        (None, "scratch", "local/scratch"),
    ],
)
def test_repo_key_comes_from_origin(run, home, origin, name, key):
    _, result = run(origin=origin, name=name)
    ok(result)
    assert len(snapshots(home, key)) == 1


def test_second_run_adds_a_snapshot_and_keeps_the_first(run, home, cache):
    repo, result = run()
    ok(result)
    (first,) = snapshots(home, "github.com/jesdi/demo")
    content = first.read_bytes()
    ok(weigh(repo, home, cache=cache))
    assert len(snapshots(home, "github.com/jesdi/demo")) == 2
    assert first.read_bytes() == content


def test_dirty_tree_is_recorded(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    (repo / "demo" / "board.py").write_text("def render(n):\n    return str(n) + ''\n")
    ok(weigh(repo, home, cache=cache))
    snap = one_snapshot(home, "github.com/jesdi/demo")
    assert git(repo, "rev-parse", "HEAD").startswith(snap["commit"])
    assert snap["commit"] == short_sha(repo)
    assert snap["dirty"] is True


def test_same_second_runs_get_suffixed_names(run, home, cache):
    now = "2026-01-02T03:04:05Z"
    repo, result = run(now=now)
    ok(result)
    ok(weigh(repo, home, cache=cache, now=now))
    names = sorted(p.name for p in snapshots(home, "github.com/jesdi/demo"))
    stem = f"20260102T030405Z_{short_sha(repo)}"
    assert names == sorted([stem + ".json", stem + "-2.json"])
