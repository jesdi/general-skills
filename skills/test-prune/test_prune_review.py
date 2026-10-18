"""Fixer/reviewer hand-off, interactive mode and input guards (ticket 21)."""
from prune_harness import REFERENCE, R, cand, case


def test_reviewer_rejects_zero_weight_into_small_weight_with_the_boundary_reason(target):
    zero, small = R + "test_zero_weight", R + "test_small_weight"
    reason = "test_zero_weight asserts the zero-weight boundary (a full buy at 0.0); test_small_weight never checks 0"

    out = target.prepare([cand(zero, small)], unattended=False, deletions={zero: small},
                         accept=(), reasons={zero: reason}).stdout

    # The fixer saw the candidate's and the container's source and assertions.
    (fixer_cand,) = target.read_state("fixer-input.json")["candidates"]
    assert fixer_cand["test_id"] == zero and fixer_cand["contained_by"] == [small]
    assert "def test_zero_weight(targets):" in fixer_cand["source"]
    assert [c["test_id"] for c in fixer_cand["containers"]] == [small]

    # The reviewer sees both tests' assertions, not only coverage.
    (proposal,) = [p for p in target.read_state("review-input.json")["proposals"] if p["id"] == zero]
    assert proposal["kind"] == "delete"
    assert proposal["candidate"]["test_id"] == zero and proposal["into"]["test_id"] == small
    assert any('trades["AAA"] == pytest.approx(0.6)' in a for a in proposal["candidate"]["assertions"])
    assert any('assert trades["AAA"] > 0' in a for a in proposal["into"]["assertions"])
    assert any('assert set(trades) == {"AAA", "BBB"}' in a for a in proposal["into"]["assertions"])
    assert "def test_small_weight(targets):" in proposal["into"]["source"]

    batch = target.read_state("batch.json")
    assert zero not in [d["test_id"] for d in batch["deletions"]]
    assert {"id": zero, "kind": "delete", "reason": reason} in batch["rejected"]
    assert zero in out and reason in out


def test_interactive_twelve_deletions_and_three_strengthen_edit_nothing_before_approval(target):
    tests_dir = target.root / "backend" / "tests"
    before = {p.name: p.read_bytes() for p in tests_dir.iterdir() if p.is_file()}
    deletions = {case(i): REFERENCE for i in range(1, 13)}
    strengthen = {case(i): "assert herfindahl([0.25] * 4) == pytest.approx(0.25)" for i in range(13, 16)}

    target.prepare([cand(case(i)) for i in range(1, 16)], unattended=False,
                   accept=[*deletions, *strengthen], deletions=deletions, strengthen=strengthen)

    batch = target.read_state("batch.json")
    assert sorted(d["test_id"] for d in batch["deletions"]) == sorted(deletions)
    assert sorted(s["id"] for s in batch["strengthen"] if s["kind"] == "candidate") == sorted(strengthen)
    assert target.git("status", "--porcelain") == ""
    assert {p.name: p.read_bytes() for p in tests_dir.iterdir() if p.is_file()} == before
    assert target.git("rev-parse", "HEAD").strip() == target.base


def test_interactive_apply_commits_approved_deletions_and_restores_the_culprit(target):
    culprit = R + "test_sell_only_rebalance"
    deletions = {case(1): REFERENCE, case(2): REFERENCE, culprit: R + "test_hold_when_on_target"}
    target.prepare([cand(t, into) for t, into in deletions.items()], unattended=False,
                   accept=list(deletions), deletions=deletions)

    out = target.ok("apply").stdout

    assert target.git("symbolic-ref", "--short", "HEAD").strip() == "main"
    assert target.git("log", "-1", "--format=%s").strip() == "test: delete 2 redundant tests"
    assert target.git("rev-parse", "HEAD~1").strip() == target.base
    head_ids = target.ids_at("HEAD")
    assert case(1) not in head_ids and case(2) not in head_ids and culprit in head_ids
    assert target.git("status", "--porcelain") == ""
    assert culprit in out


def test_apply_in_an_unattended_state_exits_2(target):
    target.prepare([cand(case(1))], accept=[case(1)], deletions={case(1): REFERENCE})

    proc = target.run("apply")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert target.git("rev-parse", "HEAD").strip() == target.base
    assert target.git("status", "--porcelain") == ""


def test_prune_in_an_interactive_state_exits_2(target):
    target.prepare([cand(case(1))], unattended=False, accept=[case(1)], deletions={case(1): REFERENCE})

    proc = target.run("prune")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert target.remote_branches() == {"main"}
    assert target.log("gh") == []
    assert target.git("status", "--porcelain") == ""


def test_collect_on_a_dirty_tracked_tree_exits_2(target):
    path = target.root / "backend" / "tests" / "test_rebalancer.py"
    path.write_text(path.read_text() + "\n# local edit\n")
    target.set_candidates([cand(case(1))])

    proc = target.run("collect")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "test_rebalancer.py" in proc.stdout + proc.stderr
    assert target.log("mutation") == []


def test_invalid_proposals_exit_2_naming_the_ids(target):
    target.set_candidates([cand(case(1)), cand(case(2))])
    target.ok("collect")

    missing = target.proposals(deletions={case(1): REFERENCE})
    missing["candidates"] = [c for c in missing["candidates"] if c["test_id"] != case(2)]
    proc = target.review_input(missing)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert case(2) in proc.stdout + proc.stderr

    nope = "tests/test_concentration.py::test_nope"
    proc = target.review_input(target.proposals(deletions={case(1): nope}))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert nope in proc.stdout + proc.stderr
