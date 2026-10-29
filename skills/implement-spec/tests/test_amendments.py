"""Ticket 08 acceptance tests: a run with no `origin` gets its final review.

All through `sh run.sh --runner <script> [--push] [--unattended]` on the fixture repository: the rig of test_final
with and without its bare `origin`.

Interface choices (ticket 08):
- with no `origin`, the tidied commits sit on the commit the PR branch had when the run started (nothing to rebase
  onto), and `refs/implement-spec/feat%2Fcsv/recovery` holds the PR tip from before the tidy, as with an `origin`.
- the one ledger sentence about the missing remote names `origin` and says that the rebase and the push were skipped.
- a refused start (exit 3) prints its reason on stdout or stderr: `origin` for a push that has no remote.
"""
import pytest

from test_cli import git, ticket
from test_final import Final, ref, stop_lines
from test_review import ONE
from test_stops import UNATTENDED
from test_walk import plan

TWO = {"01-export-rows.md": ticket("01", "Export rows"), "02-quote-fields.md": ticket("02", "Quote fields", "01")}


# ---------------------------------------------------------------- no origin

@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    """Two tickets in a chain, no `origin`, no `--push`: both merge as fast-forwards, four commits in all."""
    rig = Final(tmp_path_factory.mktemp("bare"), TWO, remote=False)
    rig.start = rig.branch()
    return rig.run(plan())


def test_with_no_origin_the_branch_is_tidied_gated_and_reviewed_with_no_rebase_and_no_push(bare):
    rig = bare
    assert rig.code == 0, rig.output()
    tip, recovery = rig.branch(), rig.recovery()
    assert recovery and len(rig.commits(rig.start, recovery)) == 4, "the commits of the walk are at the recovery point"
    rows = rig.commits(rig.start)
    assert [r["subject"] for r in rows] == ["feat(01): Export rows", "feat(02): Quote fields"]
    assert ref(rig.repo, f"{rows[0]['sha']}~1") == rig.start, "no rebase: the ticket commits sit on the branch's start"
    assert ref(rig.repo, "feat/csv^{tree}") == ref(rig.repo, "refs/implement-spec/feat%2Fcsv/recovery^{tree}")
    records = sorted(p.name for p in (rig.repo / ".agent/implement/final").glob("gate-*.log"))
    assert records == [f"gate-full-{tip[:7]}.log"], records
    (gate,) = [g for g in rig.gates() if g["commit"] == tip]
    (call,) = rig.final_calls("final-reviewer")
    assert call["head"] == tip and gate["t0"] < call["t0"], "the final review reads the gated, tidied tip"
    assert not rig.final_calls("conflict") and not rig.prs() and git(rig.repo, "remote") == "", "nothing is pushed"
    branch_tip, reviewed, _ = stop_lines(rig.out)
    assert branch_tip and reviewed, rig.out
    assert tip.startswith(branch_tip.group(1)) and tip.startswith(reviewed.group(1))


def test_with_no_origin_one_ledger_sentence_says_why_and_the_summary_does_not_deny_the_final_review(bare):
    (line,) = bare.led("origin")
    low = line.lower()
    assert "rebase" in low and "push" in low and "skipped" in low and "no origin" in low, line
    assert "did not run" not in (bare.repo / ".agent/implement/summary.md").read_text()


@pytest.mark.parametrize("flags", [UNATTENDED, ("--push",)], ids=["unattended", "push"])
def test_a_push_with_no_origin_is_refused_at_the_start_before_any_agent(tmp_path, flags):
    rig = Final(tmp_path, ONE, remote=False).run(plan(), *flags)
    assert rig.code == 3, rig.output()
    assert "origin" in rig.out + rig.err
    assert not rig.calls() and not rig.gates() and not rig.branches()


def test_with_an_origin_the_end_of_run_still_rebases_onto_the_fetched_base_and_pushes(tmp_path):
    rig = Final(tmp_path, ONE)
    rig.move_base({"docs.md": "base docs\n"})
    rig.run(plan(), "--push")
    assert rig.code == 0, rig.output()
    git(rig.repo, "merge-base", "--is-ancestor", ref(rig.origin, "refs/heads/main"), "feat/csv")
    assert rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"] and len(rig.prs()) == 1
    assert len(rig.final_calls("final-reviewer")) == 1 and not [l for l in rig.ledger() if "skipped" in l.lower()]
