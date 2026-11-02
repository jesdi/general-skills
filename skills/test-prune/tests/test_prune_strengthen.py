"""Strengthen PR, equivalent proposals and cold mutation runs (ticket 21)."""
import json
import re

import pytest

from prune_harness import PRUNE_BRANCH, REFERENCE, STRENGTHEN_BRANCH, R, arg, cand, case

COMPUTE = "rebalancer.compute_trades"
DRIFT = "rebalancer.apply_drift"
HERFINDAHL = "concentration.herfindahl"
IDS_7_9 = ["x_compute_trades__mutmut_7", "x_compute_trades__mutmut_9"]
EQUIVALENT_ID = "x_herfindahl__mutmut_9"
EQUIVALENT_REASON = "the <= mutant only differs on an empty weight list, which herfindahl never receives"

COMPUTE_TEST = '''
def test_compute_trades_rounding(targets):
    trades = compute_trades({"AAA": 0.333, "BBB": 0.667}, targets)
    assert round(trades["AAA"], 3) == 0.267
'''
DRIFT_TEST = '''
def test_apply_drift_new():
    assert apply_drift(-0.5) == 0
'''  # kills x_apply_drift__mutmut_2 only, not _4


def strengthen_group(ids, reason="fixer: no test pins this"):
    return {"verdict": "strengthen", "mutant_ids": ids, "test": "tests/test_rebalancer.py", "reason": reason}


def codes_for(proof, mutant_id):
    """(base code, head code) of mutant_id anywhere in strengthen-proof.json."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if mutant_id in node and isinstance(node[mutant_id], dict):
                found.append(node[mutant_id])
            if mutant_id in (node.get("mutant_id"), node.get("id")):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(proof)
    assert found, f"{mutant_id} not in strengthen-proof.json: {proof}"
    entry = found[0]
    base = next(v for k, v in entry.items() if k.startswith("base"))
    head = next(v for k, v in entry.items() if k.startswith("head"))
    return base, head


def baseline_at(target, ref):
    return json.loads(target.show(ref, "backend/mutation_baseline.json"))


def strengthen(target, agent_tests, finish_accept, env=None, before=lambda: None, footer=""):
    """strengthen-start → agent commits → strengthen-verify → strengthen-finish; returns the finish run."""
    before()
    target.ok("strengthen-start", env=env)
    assert target.git("rev-parse", "HEAD").strip() == target.base
    for item, source in agent_tests:
        target.commit_test(item, source, footer=footer)
    before()
    target.ok("strengthen-verify", env=env)
    before()
    return target.run("strengthen-finish", "--reviews",
                      str(target.reviews(finish_accept, ids=list(finish_accept))), env=env)


def strengthen_pr(target):
    (create,) = [c for c in target.pr_creates() if arg(c["argv"], "--head") == STRENGTHEN_BRANCH]
    assert arg(create["argv"], "--base") == "main"
    return create["body"]


def test_strengthen_pr_names_ids_7_and_9_killed_proves_them_surviving_before_and_lowers_the_baseline_by_2(target):
    target.prepare([cand(case(1))], accept=[case(1), COMPUTE], deletions={case(1): REFERENCE},
                   groups={COMPUTE: strengthen_group(IDS_7_9)})
    target.ok("prune")
    prune_head = target.remote_sha(PRUNE_BRANCH)

    finish = strengthen(target, [(COMPUTE, COMPUTE_TEST)], [COMPUTE])

    assert finish.returncode == 0, finish.stdout + finish.stderr
    assert all(i in (target.state / "strengthen-input.json").read_text() for i in IDS_7_9)
    proof = target.read_state("strengthen-proof.json")
    for mutant_id in IDS_7_9:
        base_code, head_code = codes_for(proof, mutant_id)
        assert base_code in (0, 5, 33) and head_code in (1, 3), (mutant_id, base_code, head_code)

    head = target.remote_sha(STRENGTHEN_BRANCH)
    assert baseline_at(target, target.base)["services/rebalancer.py"]["survivors"] == 20
    assert baseline_at(target, head)["services/rebalancer.py"]["survivors"] == 18
    assert "def test_compute_trades_rounding(targets):" in target.show(head, "backend/tests/test_rebalancer.py")
    # Branched from the base commit, independent of the prune branch.
    assert target.git("merge-base", head, prune_head).strip() == target.base
    for path in target.git("diff", "--name-only", target.base, head).split():
        assert path.startswith("backend/tests/") or path == "backend/mutation_baseline.json", path

    body = strengthen_pr(target)
    assert "test_compute_trades_rounding" in body
    for mutant_id in IDS_7_9:
        assert mutant_id in body
    assert re.search(r"surviv", body, re.I)
    assert re.search(r"services/rebalancer\.py\D*\b20\b\D+\b18\b", body), body
    assert not [c for c in target.log("gh") if c["argv"][:2] == ["pr", "merge"]]
    assert target.git("rev-parse", "main").strip() == target.base == target.remote_sha("main")
    assert target.git("symbolic-ref", "--short", "HEAD").strip() == "main"


def test_a_co_author_paragraph_after_the_item_trailer_does_not_hide_it(target):
    # Headless Claude Code appends its own last paragraph; git reads trailers from the last one only.
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})

    finish = strengthen(target, [(COMPUTE, COMPUTE_TEST)], [COMPUTE],
                        footer="\n\nCo-Authored-By: Claude <noreply@anthropic.com>")

    assert finish.returncode == 0, finish.stdout + finish.stderr
    assert all(i in strengthen_pr(target) for i in IDS_7_9)


def test_a_commit_naming_two_items_is_rejected_with_a_recovery_the_agent_can_run(target):
    # The unattended agent may not reset: the recovery must need only checkout, edits and commits.
    target.prepare([cand(case(1))], accept=[COMPUTE, DRIFT],
                   groups={COMPUTE: strengthen_group(IDS_7_9), DRIFT: strengthen_group(["x_apply_drift__mutmut_2"])})
    target.ok("strengthen-start")
    start = target.read_state("strengthen.json")["start"]
    path = target.root / "backend/tests/test_rebalancer.py"
    path.write_text(path.read_text() + COMPUTE_TEST + DRIFT_TEST)
    target.git("commit", "-qam", f"test: both\n\nTest-Prune-Item: {COMPUTE}\nTest-Prune-Item: {DRIFT}")

    verify = target.run("strengthen-verify")

    assert verify.returncode == 2
    out = verify.stdout + verify.stderr
    assert "one item per commit" in out
    assert f"git checkout {start}" in out
    target.git("checkout", "-q", start)
    target.commit_test(COMPUTE, COMPUTE_TEST)
    target.commit_test(DRIFT, DRIFT_TEST)
    target.ok("strengthen-verify")


def test_strengthen_item_whose_ids_are_not_all_killed_is_reverted_and_left_out_of_the_pr(target):
    drift_ids = ["x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"]
    target.prepare([cand(case(1))], accept=[COMPUTE, DRIFT],
                   groups={COMPUTE: strengthen_group(IDS_7_9), DRIFT: strengthen_group(drift_ids)})

    finish = strengthen(target, [(COMPUTE, COMPUTE_TEST), (DRIFT, DRIFT_TEST)], [COMPUTE])

    assert finish.returncode == 0, finish.stdout + finish.stderr
    head = target.remote_sha(STRENGTHEN_BRANCH)
    source = target.show(head, "backend/tests/test_rebalancer.py")
    assert "def test_compute_trades_rounding(targets):" in source
    assert "test_apply_drift_new" not in source
    assert baseline_at(target, head)["services/rebalancer.py"]["survivors"] == 18
    body = strengthen_pr(target)
    assert "test_apply_drift_new" not in body
    assert all(i not in body for i in drift_ids)
    assert all(i in body for i in IDS_7_9)


def test_unattended_equivalent_proposals_are_listed_in_the_pr_bodies_and_never_written(target):
    target.prepare([cand(case(1))], accept=[case(1), COMPUTE, HERFINDAHL], deletions={case(1): REFERENCE},
                   groups={COMPUTE: strengthen_group(IDS_7_9),
                           HERFINDAHL: {"verdict": "equivalent", "mutant_ids": [EQUIVALENT_ID],
                                        "test": "tests/test_concentration.py", "reason": EQUIVALENT_REASON}})
    equivalents = target.read_state("batch.json")["equivalents"]
    assert [(e["group"], e["mutant_ids"]) for e in equivalents] == [(HERFINDAHL, [EQUIVALENT_ID])]

    target.ok("prune")
    finish = strengthen(target, [(COMPUTE, COMPUTE_TEST)], [COMPUTE])

    assert finish.returncode == 0, finish.stdout + finish.stderr
    (prune_pr,) = [c for c in target.pr_creates() if arg(c["argv"], "--head") == PRUNE_BRANCH]
    for body in (prune_pr["body"], strengthen_pr(target)):
        assert EQUIVALENT_ID in body and EQUIVALENT_REASON in body
    head = target.remote_sha(STRENGTHEN_BRANCH)
    for module, entry in baseline_at(target, head).items():
        assert entry["equivalent"] == {}, module
    assert (target.root / "backend" / "mutation_baseline.json").read_text() == \
        target.show(target.base, "backend/mutation_baseline.json")


def test_equivalent_guard_trips_when_the_baseline_update_gains_an_equivalent_id(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})

    finish = strengthen(target, [(COMPUTE, COMPUTE_TEST)], [COMPUTE],
                        env={"FAKE_SNEAK_EQUIVALENT": f"services/concentration.py:{EQUIVALENT_ID}"})

    assert finish.returncode == 1, finish.stdout + finish.stderr
    assert EQUIVALENT_ID in finish.stdout + finish.stderr
    assert target.log("mutation-baseline"), "the guard must run after make mutation-baseline"
    assert STRENGTHEN_BRANCH not in target.remote_branches()
    assert target.pr_creates() == []
    assert target.git("rev-parse", "main").strip() == target.base == target.remote_sha("main")


def test_every_post_batch_mutation_run_starts_from_an_empty_mutants_directory(target):
    culprit = R + "test_sell_only_rebalance"
    keep_cache = {"KEEP_CACHE": "1"}
    deletions = {case(1): REFERENCE, culprit: R + "test_hold_when_on_target"}
    target.prepare([cand(i, into) for i, into in deletions.items()], accept=[*deletions, COMPUTE],
                   deletions=deletions, groups={COMPUTE: strengthen_group(IDS_7_9)},
                   env=keep_cache, before=target.junk_mutants)
    target.junk_mutants()
    target.ok("prune", env=keep_cache)
    finish = strengthen(target, [(COMPUTE, COMPUTE_TEST)], [COMPUTE], env=keep_cache, before=target.junk_mutants)
    assert finish.returncode == 0, finish.stdout + finish.stderr

    runs = target.log("mutation")
    assert len(runs) >= 4, runs  # collect, prune (batch + after the restore), strengthen-verify
    assert all(r["mutants"] in ("absent", "empty") for r in runs), runs
    assert all(r["KEEP_CACHE"] is None for r in runs), runs
    assert culprit in target.ids_at(target.remote_sha(PRUNE_BRANCH))


# --- strengthen-commit: prune.py makes each item's commit, one item per call -------------------

TESTS_FILE = "backend/tests/test_rebalancer.py"


def write_test(target, source, file=TESTS_FILE):
    path = target.root / file
    path.write_text(path.read_text() + "\n\n" + source.strip("\n") + "\n")


def two_items(target, drift_ids=("x_apply_drift__mutmut_2",)):
    target.prepare([cand(case(1))], accept=[COMPUTE, DRIFT],
                   groups={COMPUTE: strengthen_group(IDS_7_9), DRIFT: strengthen_group(list(drift_ids))})
    target.ok("strengthen-start")


def refused(target, item, needle):
    head = target.git("rev-parse", "HEAD").strip()
    proc = target.run("strengthen-commit", "--item", item)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert needle in proc.stderr, proc.stderr
    assert target.git("rev-parse", "HEAD").strip() == head
    assert target.read_state("status.json")["commands"][-1] == {
        "command": "strengthen-commit", "exit": 2, "reason": proc.stderr.strip().splitlines()[0]}


def test_strengthen_commit_makes_one_commit_per_item_that_strengthen_verify_accepts(target):
    two_items(target)
    shas = []
    for item, source in [(COMPUTE, COMPUTE_TEST), (DRIFT, DRIFT_TEST)]:
        write_test(target, source)
        proc = target.ok("strengthen-commit", "--item", item)
        sha = target.git("rev-parse", "HEAD").strip()
        assert proc.stdout.strip() == sha
        assert target.git("log", "-1", "--format=%s", sha).strip() == f"test: strengthen {item}"
        trailers = target.git("log", "-1", "--format=%(trailers:key=Test-Prune-Item,valueonly)", sha).split()
        assert trailers == [item]
        assert target.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split() == [TESTS_FILE]
        shas.append(sha)
    assert target.git("rev-list", "--reverse", f"{target.base}..HEAD").split() == shas
    assert target.git("status", "--porcelain").strip() == ""

    target.ok("strengthen-verify")

    assert target.read_state("strengthen.json")["status"] == {
        COMPUTE: "proven", DRIFT: "proven"}
    write_test(target, COMPUTE_TEST.replace("_rounding", "_rounding_again"))
    target.ok("strengthen-commit", "--item", COMPUTE)  # a proven item is still open for more commits


def test_strengthen_commit_commits_a_new_test_file_under_backend_tests(target):
    two_items(target)
    (target.root / "backend/tests/test_new.py").write_text(DRIFT_TEST)

    target.ok("strengthen-commit", "--item", DRIFT)

    assert target.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == [
        "backend/tests/test_new.py"]


def test_strengthen_commit_refuses_an_unknown_item(target):
    two_items(target)
    write_test(target, COMPUTE_TEST)
    refused(target, "rebalancer.no_such_group", "rebalancer.no_such_group")


def test_strengthen_commit_refuses_an_item_strengthen_verify_closed(target):
    two_items(target, drift_ids=("x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"))
    write_test(target, DRIFT_TEST)
    target.ok("strengthen-commit", "--item", DRIFT)
    target.ok("strengthen-verify")  # DRIFT_TEST leaves _4 alive: the item is reverted and closed
    assert target.read_state("strengthen.json")["status"][DRIFT] == "reverted"
    write_test(target, DRIFT_TEST.replace("_new", "_again"))
    refused(target, DRIFT, "reverted")


def test_strengthen_commit_refuses_before_strengthen_start(target):
    target.prepare([cand(case(1))], accept=[COMPUTE], groups={COMPUTE: strengthen_group(IDS_7_9)})
    write_test(target, COMPUTE_TEST)
    refused(target, COMPUTE, "strengthen.json")


@pytest.mark.parametrize("outside", ["backend/services/rebalancer.py", "backend/notes.txt", "README-x.md"])
def test_strengthen_commit_refuses_any_change_outside_backend_tests(target, outside):
    two_items(target)
    write_test(target, COMPUTE_TEST)
    path = target.root / outside
    path.write_text((path.read_text() if path.exists() else "") + "\n# edited\n")
    refused(target, COMPUTE, outside)


def test_strengthen_commit_refuses_a_staged_change_outside_backend_tests(target):
    two_items(target)
    write_test(target, COMPUTE_TEST)
    path = target.root / "backend/services/rebalancer.py"
    path.write_text(path.read_text() + "\n# edited\n")
    target.git("add", "backend/services/rebalancer.py")
    target.git("restore", "--worktree", "--source=HEAD", "backend/services/rebalancer.py")
    refused(target, COMPUTE, "backend/services/rebalancer.py")


def test_strengthen_commit_refuses_a_file_moved_into_backend_tests_from_outside(target):
    two_items(target)
    target.git("mv", "backend/services/rebalancer.py", "backend/tests/moved.py")
    refused(target, COMPUTE, "\nbackend/services/rebalancer.py")


def test_strengthen_commit_refuses_when_nothing_changed(target):
    two_items(target)
    refused(target, COMPUTE, "nothing to commit")


def test_strengthen_commit_refuses_a_head_off_the_strengthen_start(target):
    two_items(target)
    target.git("checkout", "-q", "--orphan", "elsewhere")
    target.git("commit", "-q", "-m", "unrelated root")
    write_test(target, COMPUTE_TEST)
    refused(target, COMPUTE, "does not descend")


# --- closing an item removes its tests, even under a later item's commits ---------------------

def test_an_unproven_item_committed_before_a_proven_one_is_removed_without_a_conflict(target):
    # Both items append to the end of one file: `git revert` of the earlier one conflicts.
    two_items(target, drift_ids=("x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"))
    for item, source in [(DRIFT, DRIFT_TEST), (COMPUTE, COMPUTE_TEST)]:
        write_test(target, source)
        target.ok("strengthen-commit", "--item", item)

    target.ok("strengthen-verify")

    assert target.read_state("strengthen.json")["status"] == {COMPUTE: "proven", DRIFT: "reverted"}
    source = (target.root / TESTS_FILE).read_text()
    assert "def test_compute_trades_rounding(targets):" in source
    assert "test_apply_drift_new" not in source
    assert source == target.show(target.base, TESTS_FILE) + "\n\n" + COMPUTE_TEST.strip("\n") + "\n"
    trailers = target.git("log", "-1", "--format=%(trailers:key=Test-Prune-Item,valueonly)").split()
    assert trailers == [DRIFT]
    assert target.git("status", "--porcelain").strip() == ""


def test_a_new_file_left_without_tests_by_a_closed_item_is_deleted(target):
    two_items(target, drift_ids=("x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"))
    new_file = "backend/tests/test_drift_new.py"
    (target.root / new_file).write_text("from services.rebalancer import apply_drift\n" + DRIFT_TEST)
    write_test(target, DRIFT_TEST.replace("_new", "_also"))  # the append COMPUTE's append conflicts with
    target.ok("strengthen-commit", "--item", DRIFT)
    (target.root / new_file).write_text((target.root / new_file).read_text() + COMPUTE_TEST)
    refused(target, COMPUTE, new_file)  # another item's file
    target.git("checkout", "-q", "--", new_file)
    write_test(target, COMPUTE_TEST)
    target.ok("strengthen-commit", "--item", COMPUTE)

    target.ok("strengthen-verify")

    assert not (target.root / new_file).exists()
    assert (target.root / TESTS_FILE).read_text() == \
        target.show(target.base, TESTS_FILE) + "\n\n" + COMPUTE_TEST.strip("\n") + "\n"


def test_a_test_a_closed_item_changed_is_free_for_another_item(target):
    two_items(target, drift_ids=("x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"))
    path = target.root / TESTS_FILE
    path.write_text(path.read_text().rstrip("\n") + "\n        assert apply_drift(-0.5) == 0\n")
    target.ok("strengthen-commit", "--item", DRIFT)
    write_test(target, COMPUTE_TEST)
    target.ok("strengthen-commit", "--item", COMPUTE)
    target.ok("strengthen-verify")  # DRIFT leaves _4 alive: reverted, test_drift_scales is back to the base
    scales = "        assert apply_drift(base) <= base\n"
    path.write_text(path.read_text().replace(scales, scales + "        assert apply_drift(base) >= 0\n"))

    target.ok("strengthen-commit", "--item", COMPUTE)


def test_strengthen_commit_refuses_a_test_another_item_already_changed(target):
    two_items(target)
    write_test(target, COMPUTE_TEST)
    target.ok("strengthen-commit", "--item", COMPUTE)
    path = target.root / TESTS_FILE
    path.write_text(path.read_text().replace("== 0.267", "== 0.267  # pinned"))
    write_test(target, DRIFT_TEST)

    refused(target, DRIFT, f"test_compute_trades_rounding is {COMPUTE}'s")


def test_a_test_the_closed_item_changed_is_restored_under_a_later_items_test(target):
    two_items(target, drift_ids=("x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"))
    path = target.root / TESTS_FILE
    path.write_text(path.read_text().rstrip("\n") + "\n        assert apply_drift(-0.5) == 0\n")
    target.ok("strengthen-commit", "--item", DRIFT)
    write_test(target, COMPUTE_TEST)
    target.ok("strengthen-commit", "--item", COMPUTE)

    target.ok("strengthen-verify")

    assert target.read_state("strengthen.json")["status"] == {COMPUTE: "proven", DRIFT: "reverted"}
    assert path.read_text() == target.show(target.base, TESTS_FILE) + "\n\n" + COMPUTE_TEST.strip("\n") + "\n"


def test_a_closed_item_that_changed_more_than_functions_still_stops_the_run_on_a_conflict(target):
    two_items(target, drift_ids=("x_apply_drift__mutmut_2", "x_apply_drift__mutmut_4"))
    write_test(target, "LIMIT = -0.5\n" + DRIFT_TEST)
    target.ok("strengthen-commit", "--item", DRIFT)
    write_test(target, COMPUTE_TEST)
    target.ok("strengthen-commit", "--item", COMPUTE)

    verify = target.run("strengthen-verify")

    assert verify.returncode == 1, verify.stdout + verify.stderr
    assert f"changes {TESTS_FILE} beyond adding or editing functions" in verify.stdout + verify.stderr
    assert target.git("status", "--porcelain").strip() == ""
