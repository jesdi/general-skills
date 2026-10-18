"""Unattended prune: branch, PR, cap, bisect, nothing-to-do exits and deletion shape (ticket 21)."""
import re

from prune_harness import PRUNE_BRANCH, REFERENCE, R, arg, cand, case, lines_with

CULPRIT = R + "test_sell_only_rebalance"  # sole killer of x_rebalance__mutmut_20..22


def deleted_on(target, ref):
    return target.ids_at(target.base) - target.ids_at(ref)


def assert_base_untouched(target):
    assert target.git("rev-parse", "main").strip() == target.base
    assert target.remote_sha("main") == target.base
    assert target.git("symbolic-ref", "--short", "HEAD").strip() == "main"


def assert_delete_only(target, ref):
    numstat = target.git("diff", "--numstat", target.base, ref).splitlines()
    assert numstat, "the branch changes nothing"
    for line in numstat:
        added, _, path = line.split("\t")
        assert added == "0", line
        assert path.startswith("backend/tests/") and not path.endswith("conftest.py"), line


def assert_no_pr(target):
    assert target.remote_branches() == {"main"}
    assert target.git("branch", "--list", "chore/*").strip() == ""
    assert target.log("gh") == []


def test_unattended_six_deletions_open_one_pr_and_leave_the_base_branch_at_c1(target):
    ids = [case(i) for i in range(1, 7)]
    reasons = {i: f"reviewer: {i.split('::')[1]} only repeats the reference equal-weight check" for i in ids}
    target.prepare([cand(i) for i in ids], accept=ids, reasons=reasons, deletions={i: REFERENCE for i in ids})

    target.ok("prune")

    assert_base_untouched(target)
    assert PRUNE_BRANCH in target.remote_branches()
    head = target.remote_sha(PRUNE_BRANCH)
    assert deleted_on(target, head) == set(ids)
    assert target.ids_at(head) <= target.ids_at(target.base)
    assert_delete_only(target, head)

    (create,) = target.pr_creates()
    assert arg(create["argv"], "--head") == PRUNE_BRANCH
    assert arg(create["argv"], "--base") == "main"
    body = create["body"]
    for test_id in ids:
        assert REFERENCE in lines_with(body, test_id), f"{test_id} listed without its containing test"
        assert reasons[test_id] in body
    assert "services/rebalancer.py: 20 survivors, baseline 20" in body  # ratchet result per module
    assert "services/concentration.py: 2 survivors, baseline 2" in body
    assert "Prune gate PASSED" in body
    assert not [c for c in target.log("gh") if c["argv"][:2] == ["pr", "merge"]]
    assert [g["base"] for g in target.log("prune-gate")] == [target.base]


def test_unattended_fourteen_accepted_deletes_ten_and_leaves_four_for_next_week(target):
    ids = [case(i) for i in range(1, 15)]
    target.prepare([cand(i) for i in ids], accept=ids, deletions={i: REFERENCE for i in ids})

    target.ok("prune")

    head = target.remote_sha(PRUNE_BRANCH)
    assert deleted_on(target, head) == set(ids[:10])
    assert set(ids[10:]) <= target.ids_at(head)
    (create,) = target.pr_creates()
    body = create["body"]
    assert "left for next week" in body.lower()
    for test_id in ids[10:]:
        assert test_id in body
    assert_base_untouched(target)


def test_bisect_restores_the_sole_killer_and_reports_it_with_23_against_20(target):
    ids = [case(i) for i in range(1, 10)] + [CULPRIT]
    deletions = {i: REFERENCE for i in ids[:9]}
    deletions[CULPRIT] = R + "test_hold_when_on_target"
    target.prepare([cand(i, into) for i, into in deletions.items()], accept=ids, deletions=deletions)

    out = target.ok("prune").stdout

    head = target.remote_sha(PRUNE_BRANCH)
    assert deleted_on(target, head) == set(ids[:9])
    assert CULPRIT in target.ids_at(head)
    (create,) = target.pr_creates()
    for text in (out, create["body"]):
        report = lines_with(text, CULPRIT)
        assert re.search(r"\b23\b", report) and re.search(r"\b20\b", report), report
        assert "services/rebalancer.py" in text
    assert_base_untouched(target)


def test_unattended_no_protected_candidates_pushes_nothing_and_exits_0(target, tmp_path):
    summary = tmp_path / "summary.md"
    env = {"GITHUB_STEP_SUMMARY": str(summary)}
    target.prepare([cand(case(1), unprotected=True)], env=env)

    proc = target.run("prune", env=env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no protected candidates" in proc.stdout
    assert "no protected candidates" in summary.read_text()
    assert [e["args"] for e in target.log("test-candidates")] == [["--protected-only"]]
    assert_no_pr(target)
    assert_base_untouched(target)


def test_unattended_every_deletion_restored_pushes_nothing_and_exits_0(target, tmp_path):
    summary = tmp_path / "summary.md"
    env = {"GITHUB_STEP_SUMMARY": str(summary)}
    target.prepare([cand(CULPRIT, R + "test_hold_when_on_target")], accept=[CULPRIT],
                   deletions={CULPRIT: R + "test_hold_when_on_target"}, env=env)

    proc = target.run("prune", env=env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = lines_with(proc.stdout, CULPRIT)
    assert re.search(r"\b23\b", report) and re.search(r"\b20\b", report), proc.stdout
    assert "services/rebalancer.py" in proc.stdout
    assert CULPRIT in summary.read_text()
    assert_no_pr(target)
    assert_base_untouched(target)
    assert target.git("status", "--porcelain") == ""


def test_unattended_failed_gate_pushes_nothing_and_exits_1(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})

    proc = target.run("prune", env={"FAKE_GATE_FAIL": "1"})

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Prune gate FAILED at point 1" in proc.stdout
    assert target.remote_branches() == {"main"}  # a local branch may stay; nothing leaves the machine
    assert target.log("gh") == []
    assert_base_untouched(target)


SHAPED = '''"""Rebalancer tests."""
from decimal import ROUND_HALF_UP, Decimal

import pytest

from services.rebalancer import apply_drift, compute_trades, rebalance

TOLERANCE = 1e-9


def _weights(**kw):
    return dict(kw)


@pytest.fixture
def targets():
    return _weights(AAA=0.6, BBB=0.4)


def test_zero_weight(targets):
    trades = compute_trades({"AAA": 0.0}, targets)
    assert trades["AAA"] == pytest.approx(0.6)


def test_small_weight(targets):
    trades = compute_trades({"AAA": 0.01}, targets)
    assert trades["AAA"] > 0
    assert set(trades) == {"AAA", "BBB"}


def test_hold_when_within_band(targets):
    assert rebalance({"AAA": 0.6000001, "BBB": 0.3999999}, targets) == {}


def test_sell_only_rebalance(targets):
    trades = rebalance({"AAA": 0.9, "BBB": 0.1}, targets)
    assert trades["AAA"] < 0


def test_trade_size_is_decimal():
    assert isinstance(Decimal("1.5"), Decimal)


@pytest.mark.parametrize("drift", [0.1, 0.2, 0.3], ids=["low", "mid", "high"])
def test_apply_drift_bounds(drift):
    assert 0 <= apply_drift(drift) <= 1


class TestDriftHelpers:
    @pytest.fixture
    def base(self):
        return 1.0
'''


def test_deletion_shape_removes_whole_tests_classes_and_dead_imports_only(target):
    applied = {
        R + "TestDriftHelpers::test_drift_scales": R + "test_apply_drift_bounds[high]",  # class keeps its fixture
        R + "TestRounding::test_round_down": R + "test_trade_size_is_decimal",  # with its decorator
        R + "TestRounding::test_round_half_up": R + "test_trade_size_is_decimal",  # class left with a docstring
        R + "test_compute_trades_sums_to_zero": R + "test_zero_weight",  # with its comment; `math` dies with it
        R + "test_hold_when_on_target": R + "test_small_weight",
    }
    skipped = {
        R + "test_apply_drift_bounds[low]": R + "test_apply_drift_bounds[mid]",  # siblings not accepted
        R + "test_hold_when_within_band": R + "test_hold_when_on_target",  # its `into` is deleted earlier
    }
    deletions = {**applied, **skipped}
    target.prepare([cand(i, into) for i, into in deletions.items()], accept=list(deletions), deletions=deletions)

    batch = target.read_state("batch.json")
    assert sorted(d["test_id"] for d in batch["deletions"]) == sorted(applied)
    assert sorted(s["test_id"] for s in batch["skipped"]) == sorted(skipped)
    assert all(s["reason"].strip() for s in batch["skipped"])

    target.ok("prune")

    head = target.remote_sha(PRUNE_BRANCH)
    assert target.show(head, "backend/tests/test_rebalancer.py") == SHAPED
    assert deleted_on(target, head) == set(applied)
    assert_delete_only(target, head)
    assert target.git("diff", "--name-only", target.base, head).split() == ["backend/tests/test_rebalancer.py"]
    assert_base_untouched(target)
