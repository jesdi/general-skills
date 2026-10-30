"""Ticket tw/s3-skeleton/06: rules.toml scoring, the Fast feedback pillar, stdout and --no-open.

The anchor tests pin the logistic curve on total runtime and on branch coverage (branch arcs only,
not statements plus branches).
Interface: see rules_fixture.py. TEST_WEIGH_IN_RULES=<path> replaces the skill's rules.toml.
"""
import os
import stat
from pathlib import Path

import pytest

from fixture_repo import FAKECLOCK, build_repo, load, snapshots, weigh as _weigh
from rules_fixture import sha256, write_rules

KEY = "github.com/jesdi/demo"


def weigh(repo, home, *flags, cache, rules=None, now=None):
    if rules:
        os.environ["TEST_WEIGH_IN_RULES"] = str(rules)
    try:
        return _weigh(repo, home, *flags, cache=cache, now=now)
    finally:
        os.environ.pop("TEST_WEIGH_IN_RULES", None)


def set_test_duration(repo, seconds):
    """The 40 tests of the default repo take `seconds` each: total runtime is 40 * seconds."""
    site = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    (site / "fakeclock.py").write_text(FAKECLOCK.replace("1.125", repr(seconds)))


def last_snapshot(home):
    return load(snapshots(home, KEY)[-1])


def test_anchors_score_90_and_10(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")  # 40 tests x 1.125 s = 45 s
    at_target = write_rules(tmp_path / "target.toml", total_runtime=dict(target=45, bad=450))
    at_bad = write_rules(tmp_path / "bad.toml", total_runtime=dict(target=450, bad=45))
    r1 = weigh(repo, home, cache=cache, rules=at_target, now="2026-01-01T00:00:00Z")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2 = weigh(repo, home, cache=cache, rules=at_bad, now="2026-01-01T00:00:01Z")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    first, second = (load(p)["metrics"]["total_runtime"] for p in snapshots(home, KEY))
    assert first["value"] == pytest.approx(45)
    assert first["score"] == pytest.approx(90, abs=0.01)
    assert second["score"] == pytest.approx(10, abs=0.01)


def add_half_covered_branch(repo):
    """A module with one `if` (2 branch arcs, 4 statements) and a test taking only the true arm: 50% branch coverage."""
    (repo / "demo" / "pick.py").write_text("def pick(x):\n    if x:\n        return 1\n    return 2\n")
    (repo / "tests" / "test_pick.py").write_text("from demo import pick\n\n\ndef test_pick_true():\n    assert pick.pick(True) == 1\n")


def test_branch_coverage_counts_branch_arcs_only_and_hits_the_anchors(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    add_half_covered_branch(repo)
    at_target = write_rules(tmp_path / "target.toml", branch_coverage=dict(target=50, bad=10))
    at_bad = write_rules(tmp_path / "bad.toml", branch_coverage=dict(target=90, bad=50))
    for rules, now in ((at_target, "2026-01-01T00:00:00Z"), (at_bad, "2026-01-01T00:00:01Z")):
        result = weigh(repo, home, cache=cache, rules=rules, now=now)
        assert result.returncode == 0, result.stdout + result.stderr
    first, second = (load(p)["metrics"]["branch_coverage"] for p in snapshots(home, KEY))
    assert first["value"] == pytest.approx(50)
    assert first["score"] == pytest.approx(90, abs=0.01)
    assert second["score"] == pytest.approx(10, abs=0.01)


def test_faster_runtime_scores_higher_even_beyond_the_bad_anchor(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    set_test_duration(repo, 30)  # 1,200 s, past the 600 s bad anchor
    r1 = weigh(repo, home, cache=cache, now="2026-01-01T00:00:00Z")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    set_test_duration(repo, 22.5)  # 900 s
    r2 = weigh(repo, home, cache=cache, now="2026-01-01T00:00:01Z")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    first, second = (load(p) for p in snapshots(home, KEY))
    assert first["inventory"]["runtime_seconds"] == pytest.approx(1200)
    assert second["inventory"]["runtime_seconds"] == pytest.approx(900)
    assert second["pillars"]["feedback"]["score"] > first["pillars"]["feedback"]["score"]


def test_fast_feedback_weights_not_summing_to_one_stop_the_run(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    rules = write_rules(tmp_path / "rules.toml", tests_over_10s=dict(weight=.10))  # .40 .30 .15 .10
    result = weigh(repo, home, cache=cache, rules=rules)
    assert result.returncode == 2
    assert "Fast feedback" in result.stdout + result.stderr
    assert snapshots(home, KEY) == []


def test_equal_anchors_stop_the_run_naming_the_metric(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    rules = write_rules(tmp_path / "rules.toml", branch_coverage=dict(target=80, bad=80))
    result = weigh(repo, home, cache=cache, rules=rules)
    assert result.returncode == 2
    assert "branch coverage" in (result.stdout + result.stderr).lower()
    assert snapshots(home, KEY) == []


def test_edited_rules_keep_the_version_and_change_the_hash(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    before = write_rules(tmp_path / "before.toml", branch_coverage=dict(bad=50))
    after = write_rules(tmp_path / "after.toml", branch_coverage=dict(bad=60))
    for rules, now in ((before, "2026-01-01T00:00:00Z"), (after, "2026-01-01T00:00:01Z")):
        result = weigh(repo, home, cache=cache, rules=rules, now=now)
        assert result.returncode == 0, result.stdout + result.stderr
    first, second = (load(p)["rules"] for p in snapshots(home, KEY))
    assert (first["version"], second["version"]) == (1, 1)
    assert first["sha256"] == sha256(before)
    assert second["sha256"] == sha256(after)
    assert first["sha256"] != second["sha256"]


def test_shipped_rules_are_version_1_and_hashed(run, home):
    _, result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    rules = last_snapshot(home)["rules"]
    shipped = Path(__file__).resolve().parent.parent / "rules.toml"
    assert rules["version"] == 1
    assert rules["sha256"] == sha256(shipped)


@pytest.fixture
def browser_log(tmp_path):
    """Fake browser launchers first on PATH: each one appends its name to the log when run."""
    bin_dir, log = tmp_path / "fakebin", tmp_path / "opened.log"
    bin_dir.mkdir()
    for name in ("open", "xdg-open", "osascript", "sensible-browser", "x-www-browser", "gio", "wslview"):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho {name} "$@" >> "{log}"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    os.environ["BROWSER"] = str(bin_dir / "open")
    yield log
    os.environ.pop("BROWSER")
    os.environ["PATH"] = os.environ["PATH"].split(os.pathsep, 1)[1]


def test_no_open_opens_no_browser_and_prints_the_dashboard_path(run, home, browser_log):
    _, result = run("--no-open")
    assert result.returncode == 0, result.stdout + result.stderr
    dashboard = home / ".test-weigh-in" / KEY / "dashboard.html"
    assert dashboard.is_file()
    assert str(dashboard) in result.stdout
    assert not browser_log.exists()


def test_without_no_open_the_dashboard_is_opened(run, home, browser_log):
    _, result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(home / ".test-weigh-in" / KEY / "dashboard.html") in result.stdout
    assert browser_log.exists()
