"""Strengthen PR, equivalent proposals and cold mutation runs (ticket 21)."""
import json
import re

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


def strengthen(target, agent_tests, finish_accept, env=None, before=lambda: None):
    """strengthen-start → agent commits → strengthen-verify → strengthen-finish; returns the finish run."""
    before()
    target.ok("strengthen-start", env=env)
    assert target.git("rev-parse", "HEAD").strip() == target.base
    for item, source in agent_tests:
        target.commit_test(item, source)
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
