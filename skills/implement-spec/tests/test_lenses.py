"""Fewer roles, ticket 03: the final review runs three lenses, `final-spec`, `final-correctness` and
`final-structure`, at the same time in slot 1, and merges their findings into one `findings-0.json`.

In process, on the rig of test_tidy. A lens the plan does not script reports no findings."""
import json

import pytest

import driver_final
from test_final import final, fixer, fn
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_review import RISKY, finding, fix, review
from test_tidy import Inside, tools  # noqa: F401  (tools is an autouse fixture)
from test_cli import ticket
from test_walk import plan

SONNET, OPUS, ASTRA = "claude-sonnet-5-5@medium", "claude-opus-5-5@medium", "openai/gpt-6-astra@medium"
MODELS = {"implementer": [SONNET], "final-reviewer": [OPUS, ASTRA]}
TWO = {"01-export-rows.md": ticket("01", "Export rows"), "02-cli-flag.md": ticket("02", "CLI flag")}
LENSES = ("final-spec", "final-correctness", "final-structure")


def rig_of(tmp_path, files=TWO, models=MODELS):
    return Inside(tmp_path, files, models)


def findings_file(rig, k=0):
    return json.loads((rig.repo / f".agent/implement/final/findings-{k}.json").read_text())


def stopped(status="BLOCKED"):
    return {"report": dict(status=status, commits=[], files=[], concerns=["cannot read the spec"], findings=[])}


# ---------------------------------------------------------------- the cast and its input

@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    """Two merged tickets and a clean final review: no lens reports a critical or important finding."""
    rig = rig_of(tmp_path_factory.mktemp("clean"))
    return rig.run(plan(**{"final/final-structure": [final(finding("NAMING", "minor"))]}))


def test_spec_and_structure_run_on_the_first_final_reviewer_entry_and_correctness_on_the_other_provider(clean):
    assert clean.code == 0, clean.out
    assert clean.led("End of run: final-spec ran on", OPUS) and clean.led("End of run: final-structure ran on", OPUS)
    assert clean.led("End of run: final-correctness ran on", ASTRA)
    assert not clean.led("final-correctness ran on", OPUS) and not clean.final_calls("final-reviewer")


def test_each_lens_gets_the_gate_record_of_the_tip_writes_its_own_report_and_names_no_other_report(clean):
    tip, folder = clean.final()["tidied"], clean.repo / ".agent/implement/final"
    for role in LENSES:
        (call,) = clean.final_calls(role)
        assert call["head"] == tip and f"final/gate-full-{tip[:7]}.log" in call["prompt"]
        assert (folder / f"{role}-0.json").is_file()
        assert [other for other in LENSES if other != role and f"{other}-0" in call["prompt"]] == []
    assert "<checks>\n# Correctness reviewer" in clean.final_calls("final-correctness")[0]["prompt"]
    assert "review-diff/spec.md" in clean.final_calls("final-spec")[0]["prompt"]
    assert "deep-quality-review/SKILL.md" in clean.final_calls("final-structure")[0]["prompt"]


def test_a_clean_final_review_runs_no_fix_batch_and_no_scoped_review(clean):
    final_ = clean.final()
    assert not clean.final_calls("fixer") and not clean.final_calls("final-reviewer")
    assert final_["reviewed"] == final_["tidied"] == clean.branch() and final_["open"] == []
    assert [(f["title"], f["source"]) for f in findings_file(clean)] == [("NAMING", "structure")]


def test_the_summary_counts_each_lens_under_its_role_and_has_no_correctness_row(clean):
    summary = (clean.repo / ".agent/implement/summary.md").read_text()
    assert f"| agents, final-spec on {OPUS} | 1 |" in summary
    assert f"| agents, final-correctness on {ASTRA} | 1 |" in summary
    assert f"| agents, final-structure on {OPUS} | 1 |" in summary
    assert "agents, correctness" not in summary


# ---------------------------------------------------------------- input validation

def test_a_final_reviewer_list_with_no_entry_on_another_provider_exits_3_before_any_agent(tmp_path):
    rig = rig_of(tmp_path, models={"implementer": [SONNET], "final-reviewer": [OPUS]}).run(plan())
    assert rig.code == 3 and not rig.calls(), rig.out
    assert rig.out == (f"final-reviewer: no entry of its list ({OPUS}) is on another provider than the implementer's "
                       f"first entry {SONNET}; the correctness lens of the final review never runs on the "
                       "implementer's provider\n")


def test_without_the_deep_quality_review_skill_the_run_exits_3_before_any_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_final, "structure_skill", lambda: None)
    rig = rig_of(tmp_path).run(plan())
    assert rig.code == 3 and not rig.calls(), rig.out
    assert rig.out == ("the deep-quality-review skill is missing: the structure lens of the final review follows it; "
                       "install it with `npx skills add jesdi/general-skills --skill deep-quality-review`\n")


# ---------------------------------------------------------------- the merge

def test_the_lens_findings_merge_into_one_file_and_the_critical_and_important_ones_go_to_the_fix_batch(tmp_path):
    rig = rig_of(tmp_path)
    flag = finding("missing --dry-run flag", file="cli.py", line=40)
    atomic = finding("ledger write not atomic", "critical", file="ledger.py", line=12)
    rig.run(plan(**{"final/final-spec": [final(flag)], "final/final-correctness": [final(atomic)],
                    "final/final-structure": [final({**atomic, "severity": "important"}, finding("LONG", "minor"))],
                    "final/fixer": [fixer(fn("02"), "fix the flag and the ledger")]}))
    assert rig.code == 0, rig.out
    found = findings_file(rig)
    assert [(f["title"], f["severity"], f["source"]) for f in found] == [
        ("missing --dry-run flag", "important", "spec"), ("ledger write not atomic", "critical", "correctness"),
        ("LONG", "minor", "structure")]
    batch = rig.final_calls("fixer")[0]["prompt"]
    assert "important: missing --dry-run flag (cli.py:40)" in batch and "critical: ledger write not atomic" in batch
    assert not [line for line in batch.splitlines() if "LONG" in line]
    assert rig.led("important finding of kind code from spec at cli.py:40: missing --dry-run flag")
    assert rig.led("critical finding of kind code from correctness at ledger.py:12: ledger write not atomic")
    assert rig.led("minor finding of kind code from structure at demo/export.py:99: LONG")
    assert len(rig.final_calls("final-reviewer")) == 1, "one scoped review"


def test_a_tie_keeps_the_earlier_lens_and_the_driver_findings_merge_too():
    one = finding("same", file="a.py", line=1)
    merged = driver_final._merged([{**one, "source": "spec"}, {**one, "source": "structure"},
                                   {**one, "line": 2, "source": "structure"}], [])
    assert [(f["line"], f["source"]) for f in merged] == [(1, "spec"), (2, "structure")]
    # The driver comes last: a lens finding wins a tie with a locked-file finding.
    locked = {**finding("locked test changed", "critical", file="t.py", line=1), "source": "driver"}
    lens = {**locked, "source": "correctness"}
    assert [f["source"] for f in driver_final._merged([lens], [locked])] == ["correctness"]


def test_a_must_fix_finding_reaches_every_lens_and_the_fix_batch(tmp_path):
    rig = Inside(tmp_path, RISKY, MODELS)
    escaping = {**finding("quote escaping", file="csv.py", line=30), "basis": "regression"}
    title = "quote escaping (csv.py:30)"
    scoped = final()
    scoped["report"]["resolved_must_fix"] = [title]
    batch = fixer(fn("02"), "escape quotes", ticket_of="02")
    batch["report"]["addressed_must_fix"] = [title]
    rig.run(plan(**{"02/reviewer": [review("02", [escaping])] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-correctness": [final({**escaping, "severity": "critical"})],
                    "final/final-reviewer": [scoped], "final/fixer": [batch]}))
    assert rig.code == 0, rig.out
    assert all(title in rig.final_calls(role)[0]["prompt"] for role in LENSES)
    assert title in rig.final_calls("fixer")[0]["prompt"]
    (found,) = findings_file(rig)
    assert (found["must_fix"], found["ticket"], found["source"]) == (True, "02", "correctness")


# ---------------------------------------------------------------- a lens that stops

@pytest.mark.parametrize("bad", ["BLOCKED", "rejected"])
def test_a_lens_that_stops_halts_the_round_after_the_others_and_a_rerun_of_the_tip_runs_only_that_lens(tmp_path, bad):
    rig = rig_of(tmp_path)
    flag, atomic = finding("missing --dry-run flag", file="cli.py", line=40), finding("not atomic", file="ledger.py")
    broken = stopped() if bad == "BLOCKED" else {"report": {"status": "DONE"}}
    steps = plan(**{"final/final-spec": [final(flag)], "final/final-correctness": [final(atomic)],
                    "final/final-structure": [broken] * (1 if bad == "BLOCKED" else 2) + [final(finding("LONG"))],
                    "final/fixer": [fixer(fn("02"), "fix all three")]})
    rig.run(steps)
    assert rig.code == 1 and not rig.final_calls("fixer"), rig.out
    (line,) = rig.led("End of run: BLOCKED at the final review")
    assert ("the final-structure reported BLOCKED: cannot read the spec" if bad == "BLOCKED" else
            "the final-structure: report: ") in line
    tip = rig.final()["tidied"]
    assert rig.final()["reviewed"] is None and set(rig.final()["lenses"]) == {"final-spec", "final-correctness"}
    assert all(rig.final()["lenses"][role]["tip"] == tip for role in ("final-spec", "final-correctness"))

    rig.run(steps)
    assert rig.code == 0, rig.out
    assert [len(rig.final_calls(role)) for role in LENSES] == [1, 1, 3 if bad == "rejected" else 2]
    assert [(f["title"], f["source"]) for f in findings_file(rig)] == [
        ("missing --dry-run flag", "spec"), ("not atomic", "correctness"), ("LONG", "structure")]
    assert len(rig.final_calls("fixer")) == 1


def test_a_kept_lens_report_is_not_reused_for_another_tip(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-structure": [stopped()]}))
    assert rig.code == 1 and rig.final()["lenses"]
    tip = rig.final()["tidied"]
    state = rig.state()
    for kept in state["run"]["final"]["lenses"].values():
        kept["tip"] = "0" * 40  # as if the lens had reviewed another tip
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    rig.run(plan())
    assert rig.code == 0 and [len(rig.final_calls(role)) for role in LENSES] == [2, 2, 2], rig.out
    assert rig.final()["reviewed"] == tip


def test_a_lens_that_changes_the_slot_on_both_attempts_leaves_no_kept_lens_report(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-spec": [final(files={"left.txt": "x"}), final(files={"more.txt": "x"}, commit="by it")]}))
    assert rig.code == 1 and rig.final()["reviewed"] is None, rig.out
    assert [len(rig.final_calls(role)) for role in LENSES] == [2, 2, 2] and rig.final()["lenses"] == {}
