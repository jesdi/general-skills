"""Fewer roles, ticket 04: after the fix batch of the final review, one scoped re-review runs, the `final-reviewer`
role on the other-provider entry of the `final-reviewer` list. A finding it leaves open asks `final-ticket-blocked`:
only a `retry` starts another batch and scoped review.

In process, on the rig of test_tidy and test_lenses."""
import json

from test_final import final, fixer, fn
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_cli import git
from test_lenses import ASTRA, LENSES, MODELS, OPUS, findings_file, rig_of
from test_review import RISKY, finding, fix, review
from test_stops import UNATTENDED, decisions
from test_tidy import Inside, tools  # noqa: F401  (tools is an autouse fixture)
from test_walk import plan, tw


def test_the_scoped_review_is_one_agent_on_the_other_provider_over_the_fix_batch_and_no_third_round_starts(tmp_path):
    rig = rig_of(tmp_path)
    rig.run(plan(**{"final/final-spec": [final(finding("Q-ONE"))], "final/final-reviewer": [final(finding("Q-ONE"))],
                    "final/fixer": [fixer(fn("02"), "fix q-one")]}))
    assert rig.code == 2, rig.out
    final_, batch = rig.final(), rig.branch()
    (scoped,) = rig.final_calls("final-reviewer")
    assert rig.led("End of run: final-reviewer ran on", ASTRA) and f"Range: {final_['tidied']}..{batch}\n" in scoped["prompt"]
    assert scoped["head"] == batch and final_["reviewed"] == batch and final_["round"] == 2
    assert [len(rig.final_calls(role)) for role in LENSES] == [1, 1, 1] and len(rig.final_calls("fixer")) == 1
    assert [(f["title"], f["source"]) for f in findings_file(rig, 1)] == [("Q-ONE", "final-reviewer")]


def test_a_must_fix_the_scoped_review_leaves_out_of_resolved_must_fix_asks_final_ticket_blocked(tmp_path):
    rig = Inside(tmp_path, RISKY, MODELS)
    escaping = {**finding("quote escaping", file="csv.py", line=30), "basis": "regression"}
    batch = fixer(fn("02"), "escape quotes", ticket_of="02")
    batch["report"]["addressed_must_fix"] = ["quote escaping (csv.py:30)"]
    rig.run(plan(**{"02/reviewer": [review("02", [escaping])] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-reviewer": [final()], "final/fixer": [batch]}))
    assert rig.code == 2, rig.out
    (question,) = decisions(rig)["questions"]
    assert question["id"] == Q and "quote escaping (csv.py:30)" in question["title"]
    assert len(rig.final_calls("final-reviewer")) == 1


# ---------------------------------------------------------------- a finding open after the scoped review

ATOMIC = finding("ledger write not atomic", file="ledger.py", line=12)
Q = "final-ticket-blocked-1"


def open_after_batch(tmp_path):
    """The scoped review after the batch reports the important finding ATOMIC, not a must-fix one."""
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-spec": [final(ATOMIC)], "final/final-reviewer": [final(ATOMIC), final()],
                    "final/fixer": [fixer(fn("02"), "fix the ledger"), fixer(fn("02"), "write the ledger atomically")]})
    return rig.run(steps, *UNATTENDED), steps


def test_a_finding_open_after_the_scoped_review_asks_one_final_ticket_blocked_and_pushes_nothing(tmp_path):
    rig, _ = open_after_batch(tmp_path)
    assert rig.code == 2 and not rig.pushes() and not rig.prs(), rig.out
    (question,) = decisions(rig)["questions"]
    assert question["id"] == Q and "ledger write not atomic (ledger.py:12)" in question["title"]
    assert [o["id"] for o in question["options"]] == ["retry", "edit"] and question["recommended"] == "retry"
    assert rig.led("End of run: the operator decides on 1 question of kind ticket-blocked")


def test_retry_on_an_open_final_finding_runs_one_more_fix_batch_and_one_scoped_review(tmp_path):
    rig, steps = open_after_batch(tmp_path)
    rig.run(steps, "--answer", f"{Q}=retry", *UNATTENDED)
    assert rig.code == 0 and rig.pushes() == [f"refs/heads/feat/csv {rig.branch()}"], rig.out
    first, second = rig.final_calls("fixer")
    assert "- important: ledger write not atomic (ledger.py:12)" in second["prompt"]
    (_, scoped) = rig.final_calls("final-reviewer")
    assert scoped["t0"] >= second["t1"] and scoped["head"] == rig.branch() == rig.final()["reviewed"]
    assert [len(rig.final_calls(role)) for role in LENSES] == [1, 1, 1]


def test_edit_on_an_open_final_finding_keeps_the_end_of_run_blocked_and_runs_no_agent(tmp_path):
    rig, steps = open_after_batch(tmp_path)
    calls = len(rig.calls())
    rig.run(steps, "--answer", f"{Q}=edit", *UNATTENDED)
    assert rig.code == 1 and len(rig.calls()) == calls and not rig.pushes() and not rig.prs(), rig.out
    assert rig.led("End of run: BLOCKED at the operator keeps the end of run BLOCKED for an edit (edit)")


# ---------------------------------------------------------------- the locked files

def test_a_final_fixer_that_changes_a_locked_file_is_rejected_and_runs_again_on_its_second_entry(tmp_path):
    rig = rig_of(tmp_path, models={**MODELS, "fixer": [OPUS, ASTRA]})
    weakens = fixer(fn("02"), "fix q-one")
    weakens["commits"][0]["files"]["tests/test_01.py"] = "def test_x():\n    assert True\n"
    puts_back = fixer(fn("02"), "put the test back")
    puts_back["commits"][0]["files"]["tests/test_01.py"] = tw("01")["files"]["tests/test_01.py"]
    puts_back["report"]["commits"].insert(0, {"sha": "$HEAD", "ticket": "02"})
    rig.run(plan(**{"final/final-spec": [final(finding("Q-ONE"))], "final/fixer": [weakens, puts_back]}))
    assert rig.code == 0, rig.out
    first, second = rig.led("End of run: fixer ran on")
    assert "locked file changed: tests/test_01.py" in first and second.endswith("report accepted.")
    assert OPUS in first and ASTRA in second
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == tw("01")["files"]["tests/test_01.py"]
    assert {f["source"] for f in findings_file(rig)} == {"spec"} and not rig.led("from driver")


def test_a_run_of_an_older_driver_after_its_first_round_resumes_into_the_fix_batch_and_one_scoped_review(tmp_path):
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-spec": [final(finding("Q-ONE"))],
                    "final/final-reviewer": [final(finding("SCOPED-NOTE", "minor"))],
                    "final/fixer": [{"report": {"status": "DONE"}}] * 2 + [fixer(fn("02"), "fix q-one")]})
    rig.run(steps)
    assert rig.code == 2 and rig.final()["round"] == 1, rig.out
    # As an older driver left it: no lenses record, and findings with no source.
    folder, state = rig.repo / ".agent/implement/final", rig.state()
    del state["run"]["final"]["lenses"]
    state["run"]["final"]["open"] = [{k: v for k, v in f.items() if k != "source"} for f in state["run"]["final"]["open"]]
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    old = [{k: v for k, v in f.items() if k != "source"} for f in findings_file(rig)]
    (folder / "findings-0.json").write_text(json.dumps(old))
    before = len(rig.ledger())

    rig.run(steps, "--answer", f"{Q}=retry")
    assert rig.code == 0, rig.out
    assert [len(rig.final_calls(role)) for role in LENSES] == [1, 1, 1]
    assert len(rig.final_calls("fixer")) == 3 and len(rig.final_calls("final-reviewer")) == 1
    found = [line for line in rig.ledger()[before:] if "finding of kind" in line]
    assert found and all(" from final-reviewer at " in line for line in found)
