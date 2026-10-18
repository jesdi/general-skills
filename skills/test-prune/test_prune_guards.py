"""Bisect order, parametrize families, branch and ref guards, failure paths and the run status."""
import json
import subprocess
import sys

import pytest

from prune_harness import PRUNE, PRUNE_BRANCH, REFERENCE, STRENGTHEN_BRANCH, R, cand, case, lines_with
from prune_repo import Fail, Repo
from test_prune_strengthen import COMPUTE, COMPUTE_TEST, DRIFT, DRIFT_TEST, IDS_7_9, strengthen_group, strengthen_pr

CULPRIT = R + "test_sell_only_rebalance"  # sole killer of x_rebalance__mutmut_20..22
HOLD = R + "test_hold_when_on_target"
DRIFT_FAMILY = [R + f"test_apply_drift_bounds[{p}]" for p in ("low", "mid", "high")]


def assert_on_base(target):
    assert target.git("symbolic-ref", "--short", "HEAD").strip() == "main"
    assert target.git("rev-parse", "main").strip() == target.base == target.remote_sha("main")
    assert target.git("status", "--porcelain") == ""


# --- bisect -------------------------------------------------------------------------

def test_bisect_restores_a_culprit_in_the_middle_of_the_batch(target):
    deletions = {case(1): REFERENCE, CULPRIT: HOLD, R + "test_zero_weight": R + "test_small_weight"}
    target.prepare([cand(t, into) for t, into in deletions.items()], accept=list(deletions), deletions=deletions)
    assert [d["test_id"] for d in target.read_state("batch.json")["deletions"]][1] == CULPRIT

    out = target.ok("prune").stdout

    head_ids = target.ids_at(target.remote_sha(PRUNE_BRANCH))
    assert CULPRIT in head_ids
    assert case(1) not in head_ids and R + "test_zero_weight" not in head_ids
    assert "23" in lines_with(out, CULPRIT)


def test_bisect_restores_two_culprits_including_an_interaction(target):
    # case_01 and the reference are the only killers of the herfindahl mutants: either may go, not both.
    deletions = {case(1): case(2), REFERENCE: case(3), CULPRIT: HOLD, case(4): case(5)}
    target.prepare([cand(t, into) for t, into in deletions.items()], accept=list(deletions), deletions=deletions)

    out = target.ok("prune").stdout

    head_ids = target.ids_at(target.remote_sha(PRUNE_BRANCH))
    assert REFERENCE in head_ids and CULPRIT in head_ids
    assert case(1) not in head_ids and case(4) not in head_ids
    assert "services/concentration.py had 10 survivors" in lines_with(out, REFERENCE)


# --- parametrize families -------------------------------------------------------------

def test_the_cap_takes_whole_parametrize_families(target):
    singles = [case(i) for i in range(2, 11)]
    deletions = {**{t: REFERENCE for t in singles}, **{t: R + "TestDriftHelpers::test_drift_scales" for t in DRIFT_FAMILY}}
    target.prepare([cand(t, into) for t, into in deletions.items()], accept=list(deletions), deletions=deletions)

    target.ok("prune")

    head = target.remote_sha(PRUNE_BRANCH)
    assert target.ids_at(target.base) - target.ids_at(head) == set(singles)
    body = target.pr_creates()[0]["body"]
    for test_id in DRIFT_FAMILY:
        assert test_id in body.split("## Left for next week")[1]


def test_bisect_restores_a_parametrize_family_whole(target):
    scales = R + "TestDriftHelpers::test_drift_scales"  # with the family, the only killers of apply_drift 1 and 3
    deletions = {scales: HOLD, **{t: HOLD for t in DRIFT_FAMILY}}
    target.prepare([cand(t, into) for t, into in deletions.items()], accept=list(deletions), deletions=deletions)

    out = target.ok("prune").stdout

    head_ids = target.ids_at(target.remote_sha(PRUNE_BRANCH))
    assert set(DRIFT_FAMILY) <= head_ids and scales not in head_ids
    assert "test_apply_drift_bounds` (3 parametrized cases): services/rebalancer.py had 22" in out


# --- ref, branch and date guards -------------------------------------------------------

def test_push_refuses_any_ref_but_a_chore_branch(target):
    with pytest.raises(Fail, match="refusing to push main"):
        Repo(target.root).push("main")
    assert target.remote_sha("main") == target.base


def test_a_malformed_date_exits_2(target):
    proc = subprocess.run([sys.executable, str(PRUNE), "collect", "--state", str(target.state), "--date", "2026-1-1"],
                          cwd=target.root, env=target.env(), capture_output=True, text=True)
    assert proc.returncode == 2 and "2026-1-1" in proc.stderr
    assert target.log("mutation") == []


def test_leaving_the_run_trips_when_the_base_branch_moved(target):
    target.git("checkout", "-q", "--detach")
    target.git("commit", "-q", "--allow-empty", "-m", "elsewhere")
    target.git("update-ref", "refs/heads/main", target.git("rev-parse", "HEAD").strip())

    with pytest.raises(Fail, match="main moved"):
        Repo(target.root).leave_on("main", target.base)


def test_prune_exits_2_when_the_branch_already_exists_on_origin(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    target.git("push", "-q", "origin", f"main:refs/heads/{PRUNE_BRANCH}")
    runs = len(target.log("mutation"))

    proc = target.run("prune")

    assert proc.returncode == 2 and PRUNE_BRANCH in proc.stderr
    assert len(target.log("mutation")) == runs and target.log("gh") == []
    assert_on_base(target)


def test_prune_exits_2_off_the_collected_branch(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    target.git("checkout", "-q", "-b", "elsewhere")

    proc = target.run("prune")

    assert proc.returncode == 2 and "prune runs on main" in proc.stderr
    assert target.remote_branches() == {"main"}


# --- failure paths --------------------------------------------------------------------

def test_unattended_collect_stops_when_make_test_fails_on_the_base(target):
    target.set_candidates([cand(case(1))])

    proc = target.run("collect", "--unattended", env={"FAKE_TEST_FAIL": "1"})

    assert proc.returncode == 1 and "make test failed" in proc.stdout
    assert target.log("mutation") == []


def test_collect_stops_when_the_base_fails_the_ratchet(target):
    path = target.root / "backend" / "mutation_baseline.json"
    baseline = json.loads(path.read_text())
    baseline["services/rebalancer.py"]["survivors"] = 19
    path.write_text(json.dumps(baseline, indent=2) + "\n")
    target.git("commit", "-q", "-am", "tighten the baseline")
    target.set_candidates([cand(case(1))])

    proc = target.run("collect")

    assert proc.returncode == 1 and "services/rebalancer.py: 20 survivors > baseline 19" in proc.stdout
    assert not (target.state / "fixer-input.json").exists()


def test_prune_stops_before_committing_when_make_test_fails(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})

    proc = target.run("prune", env={"FAKE_TEST_FAIL": "1"})

    assert proc.returncode == 1 and "make test failed" in proc.stdout
    assert target.git("branch", "--list", "chore/*").strip() == ""
    assert target.remote_branches() == {"main"}
    assert_on_base(target)


def test_apply_puts_the_test_files_back_when_make_test_fails(target):
    target.prepare([cand(case(1))], unattended=False, accept=[case(1)], deletions={case(1): REFERENCE})

    proc = target.run("apply", env={"FAKE_TEST_FAIL": "1"})

    assert proc.returncode == 1
    assert_on_base(target)


# --- strengthen -----------------------------------------------------------------------

def test_candidate_strengthen_verdicts_are_listed_for_a_human_not_applied(target):
    assertion = "assert herfindahl([1.0]) == 1.0"
    target.prepare([cand(case(1)), cand(case(2))], accept=[case(1), case(2)], deletions={case(2): REFERENCE},
                   strengthen={case(1): assertion})

    target.ok("prune")
    out = target.ok("strengthen-start").stdout

    assert "nothing to strengthen" in out and assertion in out
    assert_on_base(target)
    body = target.pr_creates()[0]["body"]
    assert assertion in lines_with(body, case(1))


def test_a_rejected_proven_item_is_reverted_and_left_out_of_the_pr(target):
    drift_id = "x_apply_drift__mutmut_2"
    target.prepare([cand(case(1))], accept=[COMPUTE, DRIFT],
                   groups={COMPUTE: strengthen_group(IDS_7_9), DRIFT: strengthen_group([drift_id])})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.commit_test(DRIFT, DRIFT_TEST)
    target.ok("strengthen-verify")
    reviews = target.tmp / "reviews-reject.json"
    reviews.write_text(json.dumps({"reviews": [{"id": COMPUTE, "verdict": "accept", "reason": "pins the rounding"},
                                               {"id": DRIFT, "verdict": "reject", "reason": "restates the clamp"}]}))

    target.ok("strengthen-finish", "--reviews", str(reviews))

    source = target.show(target.remote_sha(STRENGTHEN_BRANCH), "backend/tests/test_rebalancer.py")
    assert "test_compute_trades_rounding" in source and "test_apply_drift_new" not in source
    body = strengthen_pr(target)
    assert drift_id not in body and "test_apply_drift_new" not in body


def test_finish_refuses_a_head_moved_since_the_proof_and_returns_to_the_base(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.ok("strengthen-verify")
    target.commit_test(COMPUTE, COMPUTE_TEST.replace("test_compute_trades_rounding", "test_unproven"))

    proc = target.run("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))

    assert proc.returncode == 2 and "strengthen-verify" in proc.stderr
    assert STRENGTHEN_BRANCH not in target.remote_branches() and target.pr_creates() == []
    assert_on_base(target)


def test_new_commits_for_a_reverted_item_are_refused(target):
    drift_ids = ["x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"]
    target.prepare([cand(case(1))], accept=[COMPUTE, DRIFT],
                   groups={COMPUTE: strengthen_group(IDS_7_9), DRIFT: strengthen_group(drift_ids)})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.commit_test(DRIFT, DRIFT_TEST)
    target.ok("strengthen-verify")
    target.commit_test(DRIFT, DRIFT_TEST.replace("test_apply_drift_new", "test_apply_drift_again"))

    proc = target.run("strengthen-verify")

    assert proc.returncode == 2 and f"{DRIFT} was closed" in proc.stderr


# --- run status -----------------------------------------------------------------------

def test_every_command_records_its_status_for_the_headless_job(target):
    target.prepare([cand(case(1), unprotected=True)])
    target.ok("prune")
    assert target.read_state("status.json") == {
        "command": "prune", "exit": 0, "reason": "test-prune: no protected candidates; no branch, no PR"}

    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    proc = target.run("prune", env={"FAKE_GATE_FAIL": "1"})
    status = target.read_state("status.json")
    assert proc.returncode == 1 and status["exit"] == 1 and "prune-gate failed" in status["reason"]


def test_status_names_the_bad_input(target):
    target.prepare([cand(case(1))], unattended=False, accept=[case(1)], deletions={case(1): REFERENCE})

    target.run("prune")

    status = target.read_state("status.json")
    assert status["exit"] == 2 and "runs only in unattended mode" in status["reason"]


# --- split weak-spot groups -------------------------------------------------------------

def split_proposals(target, strengthen_ids, equivalent_ids):
    proposals = target.proposals(groups={COMPUTE: strengthen_group(strengthen_ids)})
    proposals["weak_spots"].append({"group": COMPUTE, "verdict": "equivalent", "mutant_ids": equivalent_ids,
                                    "test": "tests/test_rebalancer.py", "reason": "the rounding mutant is unobservable"})
    return proposals


def test_a_group_splits_into_a_strengthen_and_an_equivalent_proposal(target):
    target.set_candidates([cand(case(1))])
    target.ok("collect", "--unattended")
    assert target.review_input(split_proposals(target, IDS_7_9[:1], IDS_7_9[1:])).returncode == 0
    ids = [p["id"] for p in target.read_state("review-input.json")["proposals"]]
    assert f"{COMPUTE}:strengthen" in ids and f"{COMPUTE}:equivalent" in ids

    target.ok("batch", "--reviews", str(target.reviews(ids)))

    batch = target.read_state("batch.json")
    assert [(e["group"], e["mutant_ids"]) for e in batch["equivalents"]] == [(COMPUTE, IDS_7_9[1:])]
    (item,) = [s for s in batch["strengthen"] if s["id"] == f"{COMPUTE}:strengthen"]
    assert item["mutant_ids"] == IDS_7_9[:1] and item["kind"] == "weak_spot"


@pytest.mark.parametrize("strengthen_ids, equivalent_ids, problem", [
    (IDS_7_9, IDS_7_9[1:], "in both entries"),
    (None, IDS_7_9[1:], "lists mutant_ids in both entries"),
])
def test_a_bad_split_exits_2_naming_the_group(target, strengthen_ids, equivalent_ids, problem):
    target.set_candidates([cand(case(1))])
    target.ok("collect")
    proposals = split_proposals(target, strengthen_ids or [], equivalent_ids)
    if strengthen_ids is None:
        del proposals["weak_spots"][[w["group"] for w in proposals["weak_spots"]].index(COMPUTE)]["mutant_ids"]

    proc = target.review_input(proposals)

    assert proc.returncode == 2 and f"weak spot {COMPUTE}: " in proc.stderr and problem in proc.stderr
