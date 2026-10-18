"""Bisect order, parametrize families, branch and ref guards, failure paths and the run status."""
import json
import shutil
import subprocess
import sys

import pytest

from prune_harness import PRUNE, PRUNE_BRANCH, arg, REFERENCE, STRENGTHEN_BRANCH, R, cand, case, lines_with
from prune_repo import Fail, Repo
from prune_review import cap
from test_prune_strengthen import (COMPUTE, COMPUTE_TEST, DRIFT, DRIFT_TEST, EQUIVALENT_ID, HERFINDAHL, IDS_7_9,
                                   strengthen, strengthen_group, strengthen_pr)

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
    assert "✓ services/rebalancer.py: 18 survivors, baseline 20" in body  # make mutation-check ran
    assert target.log("crap"), "strengthen-finish runs make crap"


def test_finish_refuses_a_head_moved_since_the_proof_and_stays_for_the_rerun(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.ok("strengthen-verify")
    target.commit_test(COMPUTE, COMPUTE_TEST.replace("test_compute_trades_rounding", "test_unproven"))

    proc = target.run("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))

    assert proc.returncode == 2 and "strengthen-verify" in proc.stderr
    assert STRENGTHEN_BRANCH not in target.remote_branches() and target.pr_creates() == []
    assert "test_unproven" in (target.root / "backend/tests/test_rebalancer.py").read_text()  # HEAD stayed
    assert target.git("rev-parse", "main").strip() == target.base == target.remote_sha("main")


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
    status = target.read_state("status.json")
    assert status["exit"] == 1 and status["reason"] == "test-prune: run incomplete: the strengthen loop has not finished"
    target.ok("strengthen-start")
    status = target.read_state("status.json")
    assert status["exit"] == 0 and status["reason"].startswith("nothing to strengthen")
    assert [c["command"] for c in status["commands"]] == ["collect", "run", "review-input", "batch", "prune",
                                                         "strengthen-start", "run"]

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

def split_proposals(target, strengthen_ids, equivalent_ids, second="equivalent"):
    proposals = target.proposals(groups={COMPUTE: strengthen_group(strengthen_ids)})
    proposals["weak_spots"].append({"group": COMPUTE, "verdict": second, "mutant_ids": equivalent_ids,
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


@pytest.mark.parametrize("strengthen_ids, equivalent_ids, second, problem", [
    (IDS_7_9, IDS_7_9[1:], "equivalent", "in both entries"),
    (None, IDS_7_9[1:], "equivalent", "lists mutant_ids in both entries"),
    (IDS_7_9[:1], IDS_7_9[1:], "strengthen", "at most one strengthen and one equivalent entry"),
])
def test_a_bad_split_exits_2_naming_the_group(target, strengthen_ids, equivalent_ids, second, problem):
    target.set_candidates([cand(case(1))])
    target.ok("collect")
    proposals = split_proposals(target, strengthen_ids or [], equivalent_ids, second)
    if strengthen_ids is None:
        del proposals["weak_spots"][[w["group"] for w in proposals["weak_spots"]].index(COMPUTE)]["mutant_ids"]

    proc = target.review_input(proposals)

    assert proc.returncode == 2 and f"weak spot {COMPUTE}: " in proc.stderr and problem in proc.stderr


# --- round 2 --------------------------------------------------------------------------

def test_leaving_never_discards_a_change_the_run_did_not_make(target):
    mine, theirs = target.root / "backend/tests/test_rebalancer.py", target.root / "backend/services/rebalancer.py"
    target.git("checkout", "-q", "--detach")
    repo = Repo(target.root)
    repo.write({"backend/tests/test_rebalancer.py": mine.read_text() + "# the run's own edit\n"}, target.base)

    repo.leave_on("main", target.base)
    assert target.git("symbolic-ref", "--short", "HEAD").strip() == "main" and target.git("status", "--porcelain") == ""

    target.git("checkout", "-q", "--detach")
    theirs.write_text(theirs.read_text() + "# someone else's edit\n")
    repo.leave_on("main", target.base)
    assert "someone else's edit" in theirs.read_text() and "someone else" not in repo.stranded
    assert "backend/services/rebalancer.py" in repo.stranded
    assert target.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"


def test_a_failed_finish_precondition_keeps_uncommitted_edits(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.ok("strengthen-verify")
    path = target.root / "backend/tests/test_concentration.py"
    path.write_text(path.read_text() + "# work in progress\n")

    proc = target.run("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))

    assert proc.returncode == 2 and "test_concentration.py" in proc.stderr
    assert "# work in progress" in path.read_text()


def test_finish_reruns_after_bad_reviews(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.ok("strengthen-verify")
    proven = target.read_state("strengthen-proof.json")["head"]
    bad = target.tmp / "bad-reviews.json"
    bad.write_text(json.dumps({"reviews": [{"id": COMPUTE, "verdict": "maybe", "reason": "unsure"}]}))

    assert target.run("strengthen-finish", "--reviews", str(bad)).returncode == 2
    assert target.git("rev-parse", "HEAD").strip() == proven

    target.ok("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))
    assert STRENGTHEN_BRANCH in target.remote_branches()
    assert_on_base(target)


def test_the_status_keeps_the_worst_exit_while_strengthen_still_runs(target):
    target.prepare([cand(case(1))], accept=[case(1), COMPUTE], deletions={case(1): REFERENCE},
                   groups={COMPUTE: strengthen_group(IDS_7_9)})
    assert target.run("prune", env={"FAKE_GATE_FAIL": "1"}).returncode == 1

    target.ok("strengthen-start")

    status = target.read_state("status.json")
    assert status["exit"] == 1 and "prune-gate failed" in status["reason"]
    assert [(c["command"], c["exit"]) for c in status["commands"][-2:]] == [("prune", 1), ("strengthen-start", 0)]


def test_a_malformed_date_is_recorded_in_the_status(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})

    proc = subprocess.run([sys.executable, str(PRUNE), "prune", "--state", str(target.state), "--date", "nope"],
                          cwd=target.root, env=target.env(), capture_output=True, text=True)

    status = target.read_state("status.json")
    assert proc.returncode == 2 and status["exit"] == 2 and "--date must be YYYY-MM-DD" in status["reason"]


def test_the_cap_skips_a_family_that_does_not_fit_and_takes_later_ones():
    singles = [{"test_id": f"tests/t.py::test_{i}", "into": "x"} for i in range(9)]
    family = [{"test_id": f"tests/t.py::test_f[{i}]", "into": "x"} for i in range(3)]
    last = [{"test_id": "tests/t.py::test_last", "into": "x"}]

    big = [{"test_id": f"tests/t.py::test_big[{i}]", "into": "x"} for i in range(11)]

    take, left, over = cap(big + singles + family + last)

    assert take == singles + last and left == family
    assert [o["test_id"] for o in over] == [d["test_id"] for d in big]


@pytest.mark.parametrize("addition, deleted", [
    ("class TestRoundingMore(TestRounding):\n    pass\n", ["TestRounding::test_round_down"]),
    ('def test_names_the_class():\n    assert "TestRounding"\n',
     ["TestRounding::test_round_down", "TestRounding::test_round_half_up"]),
])
def test_a_test_in_a_class_named_elsewhere_is_skipped(target, addition, deleted):
    path = target.root / "backend/tests/test_rebalancer.py"
    path.write_text(path.read_text() + "\n\n" + addition)
    target.git("commit", "-q", "-am", "name TestRounding elsewhere")
    deletions = {R + d: R + "test_trade_size_is_decimal" for d in deleted}

    target.prepare([cand(t, into) for t, into in deletions.items()], accept=list(deletions), deletions=deletions)

    batch = target.read_state("batch.json")
    assert batch["deletions"] == []
    assert all("class TestRounding is subclassed or named" in s["reason"] for s in batch["skipped"])
    assert sorted(s["test_id"] for s in batch["skipped"]) == sorted(deletions)


def test_make_runs_without_pytest_addopts(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "Makefile").write_text("show:\n\t@echo \"[$$PYTEST_ADDOPTS][$$KEEP_CACHE]\"\n")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("KEEP_CACHE", "1")

    assert Repo(tmp_path).make("show").stdout.strip() == "[][]"


def test_batch_prints_long_mutant_lists_shortened():
    from prune_review import mutant_summary
    ids = [f"x_f__mutmut_{i}" for i in range(1, 66)]

    assert mutant_summary(ids) == "(65 mutants: " + ", ".join(ids[:5]) + " +60 more)"
    assert mutant_summary(ids[:1]) == "(1 mutant: x_f__mutmut_1)"


def test_a_nested_class_that_would_empty_its_outer_class_is_skipped(target):
    path = target.root / "backend/tests/test_rebalancer.py"
    path.write_text(path.read_text() + "\n\nclass TestOuter:\n    class TestInner:\n        def test_inner(self):\n"
                    "            assert apply_drift(0.5) == 0.5\n")
    target.git("commit", "-q", "-am", "nest a test class")
    inner = R + "TestOuter::TestInner::test_inner"

    target.prepare([cand(inner, HOLD)], accept=[inner], deletions={inner: HOLD})

    (skipped,) = target.read_state("batch.json")["skipped"]
    assert skipped["test_id"] == inner and "leaves class TestOuter empty" in skipped["reason"]


def test_an_unattended_batch_skips_a_family_larger_than_the_cap(target):
    path = target.root / "backend/tests/test_rebalancer.py"
    path.write_text(path.read_text() + "\n\n@pytest.mark.parametrize(\"d\", " + repr(list(range(11))) + ")\n"
                    "def test_drift_grid(d):\n    assert apply_drift(d / 20) == d / 20\n")
    target.git("commit", "-q", "-am", "an eleven-case family")
    grid = [f"{R}test_drift_grid[{i}]" for i in range(11)]
    deletions = {**{t: HOLD for t in grid}, case(1): REFERENCE}

    target.prepare([cand(t, into) for t, into in deletions.items()], accept=list(deletions), deletions=deletions)

    batch = target.read_state("batch.json")
    assert [d["test_id"] for d in batch["deletions"]] == [case(1)]
    assert sorted(s["test_id"] for s in batch["skipped"]) == sorted(grid)
    assert {s["reason"] for s in batch["skipped"]} == {"11 cases exceed the cap of 10"}


@pytest.mark.parametrize("old", ['{"command": "collect", "exit": 0, "reason": "1 candidates"}', "{not json", "[]"])
def test_an_old_or_malformed_status_never_crashes_a_command(target, old):
    target.prepare([cand(case(1), unprotected=True)])
    (target.state / "status.json").write_text(old)

    proc = target.run("prune")

    assert proc.returncode == 0 and "Traceback" not in proc.stderr
    status = target.read_state("status.json")
    assert status["exit"] == 0 and [c["command"] for c in status["commands"]] == ["prune"]  # treated as missing


# --- round 3 --------------------------------------------------------------------------

def fake_make(target, script, goal="test"):
    """A `make` first on PATH that runs `script` (sh, $MAKE is the real make) for `make <goal>`."""
    path = target.bin / "make"
    path.write_text(f"#!/bin/sh\nMAKE={shutil.which('make')}\ncase \" $* \" in *\" {goal} \"*)\n{script}\n;; esac\n"
                    "exec $MAKE \"$@\"\n")
    path.chmod(0o755)


def test_an_exit_2_clears_once_the_same_command_passes(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    bad = target.tmp / "bad.json"
    bad.write_text("{}")
    assert target.run("batch", "--reviews", str(bad)).returncode == 2
    target.ok("batch", "--reviews", str(target.reviews([case(1)])))

    target.ok("prune")
    target.ok("strengthen-start")

    status = target.read_state("status.json")
    assert status["exit"] == 0
    assert [(c["command"], c["exit"]) for c in status["commands"][-5:]] == [
        ("batch", 2), ("batch", 0), ("prune", 0), ("strengthen-start", 0), ("run", 0)]


def test_a_failed_make_test_reports_one_line_and_the_end_of_its_output(target, tmp_path):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    fake_make(target, "for i in $(seq 1 100); do echo \"DeprecationWarning noise $i\"; done\n"
                      "echo 'FAILED app-chained-targets.test.ts: timed out'; exit 2")
    summary = tmp_path / "summary.md"

    proc = target.run("prune", env={"GITHUB_STEP_SUMMARY": str(summary)})

    status = target.read_state("status.json")
    assert proc.returncode == 1 and status["reason"] == "test-prune: make test failed (exit 2)"
    for text in (proc.stdout, summary.read_text()):
        assert "FAILED app-chained-targets.test.ts: timed out" in text and "noise 72" in text
        assert "noise 71\n" not in text
        assert "## Mutation ratchet (cold run on this branch)" in text and "services/concentration.py" in text
    assert_on_base(target)


def test_leaving_keeps_a_user_edit_to_a_file_the_run_wrote(target, tmp_path):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    edited = target.root / "backend/tests/test_concentration.py"
    fake_make(target, f"echo '# my edit' >> '{edited}'; exit 2")
    summary = tmp_path / "summary.md"

    proc = target.run("prune", env={"GITHUB_STEP_SUMMARY": str(summary)})

    assert proc.returncode == 1 and "# my edit" in edited.read_text()
    assert target.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
    assert target.git("rev-parse", "main").strip() == target.base
    for text in (proc.stdout, summary.read_text()):
        assert "staying at" in text and "backend/tests/test_concentration.py" in text


def test_staying_off_the_base_branch_fails_a_passing_command(target, tmp_path):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    foreign = target.root / "backend/services/concentration.py"
    gh = target.bin / "gh"
    gh.rename(target.bin / "real-gh")
    gh.write_text(f"#!/bin/sh\necho '# foreign' >> '{foreign}'\nexec '{target.bin / 'real-gh'}' \"$@\"\n")
    gh.chmod(0o755)
    summary = tmp_path / "summary.md"

    proc = target.run("prune", env={"GITHUB_STEP_SUMMARY": str(summary)})

    status = target.read_state("status.json")
    assert proc.returncode == 1 and len(target.pr_creates()) == 1 and "# foreign" in foreign.read_text()
    assert status["exit"] == 1 and status["reason"].startswith("test-prune: staying at ")
    assert "backend/services/concentration.py" in status["reason"]
    assert "staying at" in proc.stdout and "staying at" in summary.read_text()


def test_leaving_restores_a_file_whose_name_git_quotes(target):
    name = 'backend/tests/test_a "quoted" name.py'
    (target.root / name).write_text("def test_x():\n    assert 1\n")
    target.git("add", "--", name)
    target.git("commit", "-q", "-m", "a quoted name")
    target.git("checkout", "-q", "--detach")
    repo, head = Repo(target.root), target.git("rev-parse", "main").strip()
    repo.write({name: ""}, head)

    repo.leave_on("main", head)

    assert repo.stranded == "" and target.git("symbolic-ref", "--short", "HEAD").strip() == "main"
    assert "def test_x" in (target.root / name).read_text()


def proven_strengthen(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})
    target.ok("strengthen-start")
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.ok("strengthen-verify")
    return target.root / "backend/mutation_baseline.json"


def test_a_change_to_the_baseline_during_a_failed_update_is_kept(target):
    baseline = proven_strengthen(target)
    fake_make(target, f"echo ' ' >> '{baseline}'; exit 2", goal="mutation-baseline")
    edited = baseline.read_text() + " \n"

    proc = target.run("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))

    assert proc.returncode == 1 and "make mutation-baseline failed (exit 2)" in proc.stdout
    assert baseline.read_text() == edited and "staying at" in proc.stdout
    assert target.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
    assert target.remote_branches() == {"main"}


def test_a_baseline_deleted_by_a_failed_update_fails_the_status_without_a_traceback(target):
    baseline = proven_strengthen(target)
    fake_make(target, f"rm -f '{baseline}'; exit 2", goal="mutation-baseline")

    proc = target.run("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))

    status = target.read_state("status.json")
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert status["exit"] == 1 and status["commands"][-1]["command"] == "strengthen-finish"
    assert status["commands"][-1]["exit"] == 1
    assert not baseline.exists() and "staying at" in proc.stdout  # the run did not delete it: not restored


def test_an_unexpected_error_is_reported_and_recorded(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    (target.state / "batch.json").write_text("{")

    proc = target.run("prune")

    status = target.read_state("status.json")
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert "test-prune: unexpected JSONDecodeError" in proc.stdout
    assert status["exit"] == 1 and status["reason"].startswith("test-prune: unexpected JSONDecodeError")


def test_a_user_edit_during_a_passing_mutation_run_is_never_overwritten(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    edited = target.root / "backend/tests/test_concentration.py"
    fake_make(target, f"$MAKE \"$@\"; rc=$?; echo '# my edit' >> '{edited}'; exit $rc", goal="mutation")

    proc = target.run("prune")

    assert proc.returncode == 1 and "# my edit" in edited.read_text()
    assert "backend/tests/test_concentration.py changed during the run, not by test-prune" in proc.stdout
    assert target.log("gh") == [] and target.remote_branches() == {"main"}
    assert target.read_state("status.json")["exit"] == 1


def test_a_failure_report_says_where_the_deletions_are(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    proc = target.run("prune", env={"FAKE_TEST_FAIL": "1"})
    assert "## Deletions that passed the safety check, not committed (1)" in proc.stdout
    assert "## Deleted tests" not in proc.stdout

    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    proc = target.run("prune", env={"FAKE_GATE_FAIL": "1"})
    assert f"## Deletions on the unpushed branch `{PRUNE_BRANCH}` (1)" in proc.stdout
    assert "## Deleted tests" not in proc.stdout


def test_an_exit_1_stays_open_after_the_command_passes():
    from prune import run_status
    status = None
    for command, code in [("collect", 0), ("prune", 1), ("prune", 0), ("batch", 2), ("strengthen-start", 0)]:
        status = run_status(status, command, code, f"{command} said {code}")

    assert status["exit"] == 2 and status["reason"] == "batch said 2"
    status = run_status(status, "batch", 0, "batch said 0")
    assert status["exit"] == 1 and status["reason"] == "prune said 1"
    status = run_status(status, "strengthen-finish", 1, "strengthen-finish said 1")
    assert status["exit"] == 1 and status["reason"] == "strengthen-finish said 1"  # the latest on a tie


# --- PR body size ----------------------------------------------------------------------

def prune_pr(target):
    (create,) = [c for c in target.pr_creates() if arg(c["argv"], "--head") == PRUNE_BRANCH]
    return create["body"]


def test_the_pr_carries_the_gate_report_not_its_whole_mutmut_log(target, tmp_path):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    fake_make(target, "for i in $(seq 1 20000); do echo \"    x_herfindahl__mutmut_$i: killed\"; done\n"
                      "echo 'Mutation results from commit abc123'\n"
                      "echo '  ✓ services/concentration.py: 2 survivors, baseline 2, score 80.00'\n"
                      "echo 'Mutation ratchet PASSED.'\n"
                      'exec $MAKE "$@"', goal="prune-gate")
    summary = tmp_path / "summary.md"

    proc = target.run("prune", env={"GITHUB_STEP_SUMMARY": str(summary)})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    body, log = prune_pr(target), target.state / "prune-gate.log"
    gate = body.split("## Prune gate", 1)[1]
    assert "Mutation results from commit abc123" in gate and "Mutation ratchet PASSED." in gate
    assert "  ✓ services/concentration.py: 2 survivors" in gate and "Prune gate PASSED" in gate
    assert "mutmut_" not in gate and str(log) in gate and "truncated" not in body
    assert "x_herfindahl__mutmut_20000: killed" in log.read_text()
    assert len(summary.read_text()) < 60_000


def test_pr_bodies_and_summaries_over_the_limit_are_cut_visibly(target, tmp_path):
    huge = "the fixer's argument, " * 3500  # 77,000 characters
    summary = tmp_path / "summary.md"
    env = {"GITHUB_STEP_SUMMARY": str(summary)}
    target.prepare([cand(case(1))], accept=[case(1), COMPUTE, HERFINDAHL], deletions={case(1): REFERENCE}, env=env,
                   reasons={case(1): huge}, groups={COMPUTE: strengthen_group(IDS_7_9),
                                                   HERFINDAHL: {"verdict": "equivalent", "mutant_ids": [EQUIVALENT_ID],
                                                                "test": "tests/test_concentration.py", "reason": huge}})

    target.ok("prune", env=env)
    assert strengthen(target, [(COMPUTE, COMPUTE_TEST)], [COMPUTE], env=env).returncode == 0

    for body, name in ((prune_pr(target), "prune-pr-full.md"), (strengthen_pr(target), "strengthen-pr-full.md")):
        full = target.state / name
        assert len(body) < 60_000 and f"… truncated, full text in {full}" in body
        assert huge in full.read_text() and body == (target.state / name.replace("-full", "")).read_text()
    batch_full = target.state / "summary-1.md"  # batch prints both arguments
    assert f"… truncated, full text in {batch_full}" in summary.read_text() and huge in batch_full.read_text()


def test_the_gate_report_keeps_the_ratchet_block_and_the_verdict():
    from prune_runs import gate_report
    noise = [f"    x_hhi__mutmut_{i}: killed" for i in range(500)]
    check = ["Mutation results from commit abc", "  ✗ services/a.py: 3 survivors > baseline 2 (score 50.00)",
             "      x_a__mutmut_1", "  ✓ services/b.py: 1 survivors, baseline 1, score 90.00",
             "    ↑ fewer survivors than the baseline, run 'make mutation-baseline' to lock it in",
             "Mutation ratchet FAILED."]
    verdict = ["Prune gate FAILED at point 4:", "  ✗ the mutation ratchet check failed (output above)"]

    assert gate_report("\n".join(noise + check + noise[:3] + verdict)) == "\n".join(check + verdict)
    assert gate_report("\n".join(noise + verdict)) == "\n".join(verdict)
    assert gate_report("\n".join(noise)) == "\n".join(noise[-30:])


def test_a_cut_inside_a_fence_closes_it(tmp_path):
    from prune_repo import capped
    text = "## Prune gate\n\n```\n" + "x\n" * 40_000 + "```\n"

    cut = capped(text, tmp_path / "full.md", 1000)

    assert len(cut) <= 1000 and cut.count("```") == 2 and cut.endswith(f"… truncated, full text in {tmp_path / 'full.md'}\n")
    assert (tmp_path / "full.md").read_text() == text and capped("short", tmp_path / "other.md") == "short"


def test_a_pr_that_failed_after_the_push_says_the_branch_is_pushed(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    (target.bin / "gh").write_text("#!/bin/sh\necho 'Body is too long' >&2\nexit 1\n")

    proc = target.run("prune")

    assert proc.returncode == 1 and "gh pr create failed: Body is too long" in proc.stdout
    assert f"## Deletions on `{PRUNE_BRANCH}`, pushed without a PR (1)" in proc.stdout
    assert PRUNE_BRANCH in target.remote_branches()


def test_apply_keeps_a_user_edit_when_it_puts_the_test_files_back(target):
    target.prepare([cand(case(1))], unattended=False, accept=[case(1)], deletions={case(1): REFERENCE})
    edited = target.root / "backend/tests/test_concentration.py"
    fake_make(target, f"echo '# my edit' >> '{edited}'; exit 2")

    proc = target.run("apply")

    assert proc.returncode == 1 and "# my edit" in edited.read_text()
    assert "backend/tests/test_concentration.py changed during the run, not by test-prune" in proc.stdout
    assert "make test failed (exit 2)" in target.read_state("status.json")["reason"]


# --- final review -----------------------------------------------------------------------

def test_collect_refuses_a_state_dir_that_is_not_a_run_s(target):
    notes = target.tmp / "notes"
    notes.mkdir()
    (notes / "mine.txt").write_text("keep me\n")
    target.set_candidates([cand(case(1))])

    proc = subprocess.run([sys.executable, str(PRUNE), "collect", "--state", str(notes), "--date", "2026-10-17"],
                          cwd=target.root, env=target.env(), capture_output=True, text=True)

    assert proc.returncode == 2 and "refusing to use" in proc.stderr
    assert sorted(p.name for p in notes.iterdir()) == ["mine.txt"] and target.log("mutation") == []


def test_collect_resets_only_the_run_s_own_files(target):
    target.prepare([cand(case(1), unprotected=True)])
    (target.state / "notes.txt").write_text("mine\n")

    target.ok("collect", "--unattended")

    assert (target.state / "notes.txt").read_text() == "mine\n"
    assert not (target.state / "batch.json").exists()


def test_a_run_that_stops_after_collect_stays_red(target):
    target.set_candidates([cand(case(1))])

    target.ok("collect", "--unattended")

    status = target.read_state("status.json")
    assert status["exit"] == 1 and "run incomplete" in status["reason"]


def test_an_argument_error_is_recorded_as_exit_2(target):
    target.set_candidates([cand(case(1))])
    target.ok("collect", "--unattended")

    proc = target.run("batch")

    status = target.read_state("status.json")
    assert proc.returncode == 2 and "--reviews" in proc.stderr
    assert status["exit"] == 2 and status["commands"][-1]["command"] == "batch" and "--reviews" in status["reason"]


def test_a_finish_without_rejections_runs_no_extra_cold_proof(target):
    proven_strengthen(target)
    runs = len(target.log("mutation"))

    target.ok("strengthen-finish", "--reviews", str(target.reviews([COMPUTE], ids=[COMPUTE])))

    assert len(target.log("mutation")) == runs
    assert target.read_state("status.json")["exit"] == 0


def test_a_failed_gate_lands_in_the_job_summary(target, tmp_path):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})
    summary = tmp_path / "summary.md"

    assert target.run("prune", env={"FAKE_GATE_FAIL": "1", "GITHUB_STEP_SUMMARY": str(summary)}).returncode == 1

    assert "Prune gate FAILED at point 1" in summary.read_text()
