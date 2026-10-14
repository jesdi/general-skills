"""Ticket 08 acceptance tests: a run with no `origin` gets its final review; the correctness CLI is mandatory.

All through `sh run.sh --runner <script> [--push] [--unattended]` on the fixture repository: the rig of test_final
with and without its bare `origin`, and the rig of test_review with and without the fake `codex` on PATH.

Interface choices (ticket 08):
- with no `origin`, the tidied commits sit on the commit the PR branch had when the run started (nothing to rebase
  onto), and `refs/implement/recovery` holds the PR tip from before the tidy, as with an `origin`.
- the one ledger sentence about the missing remote names `origin` and says that the rebase and the push were skipped.
- a refused start (exit 3) prints its reason on stdout or stderr: `origin` for a push that has no remote; for the
  correctness role one line that names `correctness` and `codex`, or `correctness` and each entry of its list.
"""
import pytest

from test_cli import git, ticket
from test_final import Final, ref, stop_lines
from test_review import FABLE, ONE, OPUS, RISKY, SOL, Review, correct, review
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
    assert ref(rig.repo, "feat/csv^{tree}") == ref(rig.repo, "refs/implement/recovery^{tree}")
    records = sorted(p.name for p in (rig.repo / ".agent/implement/final").glob("gate-*.log"))
    assert records == [f"gate-{tip[:7]}.log"], records
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


# ---------------------------------------------------------------- the correctness CLI

@pytest.mark.parametrize("files", [RISKY, ONE], ids=["risk-line", "no-risk-line"])
def test_a_correctness_entry_whose_cli_is_not_on_path_stops_the_run_before_any_agent(tmp_path, files):
    rig = Review(tmp_path, files, {"correctness": [SOL, OPUS], "implementer": [OPUS]})
    (tmp_path / "bin/codex").unlink()
    rig.run(plan())
    assert rig.code == 3, rig.output()
    assert [l for l in (rig.out + rig.err).splitlines() if "correctness" in l and "codex" in l], rig.output()
    assert not rig.calls() and not rig.gates()


def test_a_correctness_list_all_on_the_implementers_provider_stops_the_run_before_any_agent(tmp_path):
    rig = Review(tmp_path, RISKY, {"correctness": [OPUS, FABLE], "implementer": [OPUS]})
    rig.run(plan())
    assert rig.code == 3, rig.output()
    assert [l for l in (rig.out + rig.err).splitlines() if "correctness" in l and OPUS in l and FABLE in l], rig.output()
    assert not rig.calls() and not rig.gates()


@pytest.mark.parametrize("entries, codex", [([SOL, OPUS], False), ([OPUS], True)], ids=["no-codex", "one-provider"])
def test_no_run_folds_the_correctness_pass_into_the_reviewer(tmp_path, entries, codex):
    rig = Review(tmp_path, RISKY, {"correctness": entries, "implementer": [OPUS]})
    if not codex:
        (tmp_path / "bin/codex").unlink()
    rig.run(plan())
    assert not [c for c in rig.calls(role="reviewer") if "correctness.md" in c["prompt"]], "a reviewer got the pass"
    assert not rig.led("folded"), rig.ledger()


def test_a_risky_ticket_still_gets_reviewer_and_correctness_together_on_the_entry_of_another_provider(tmp_path):
    rig = Review(tmp_path, RISKY, {"correctness": [OPUS, SOL], "implementer": [OPUS]})
    rig.run(plan(**{"02/reviewer": [review("02", touch=["r"], wait=["c"])],
                    "02/correctness": [correct(touch=["c"], wait=["r"])]}))
    assert rig.code == 0, rig.output()
    (r,), (c,) = rig.calls("02", "reviewer"), rig.calls("02", "correctness")
    assert r["t0"] < c["t1"] and c["t0"] < r["t1"], "each started before the other ended"
    assert rig.led("correctness", "gpt-sol@medium") and not rig.led("correctness", OPUS)
    assert "correctness.md" not in r["prompt"] and not rig.led("folded")
