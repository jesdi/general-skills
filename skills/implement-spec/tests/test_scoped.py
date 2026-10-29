"""Fewer roles, ticket 04: after the fix batch of the final review, one scoped re-review runs, the `final-reviewer`
role on the other-provider entry of the `final-reviewer` list. No third round starts.

In process, on the rig of test_tidy and test_lenses."""
import json

from test_final import final, fixer, fn
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_lenses import ASTRA, LENSES, MODELS, findings_file, rig_of
from test_review import RISKY, finding, fix, review
from test_tidy import Inside, tools  # noqa: F401  (tools is an autouse fixture)
from test_walk import plan


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


def test_a_must_fix_the_scoped_review_leaves_out_of_resolved_must_fix_blocks_the_run(tmp_path):
    rig = Inside(tmp_path, RISKY, MODELS)
    escaping = {**finding("quote escaping", file="csv.py", line=30), "basis": "regression"}
    batch = fixer(fn("02"), "escape quotes", ticket_of="02")
    batch["report"]["addressed_must_fix"] = ["quote escaping (csv.py:30)"]
    rig.run(plan(**{"02/reviewer": [review("02", [escaping])] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-reviewer": [final()], "final/fixer": [batch]}))
    assert rig.code == 1, rig.out
    assert rig.led("End of run: BLOCKED at the final review still has unresolved must-fix findings: quote escaping")
    assert len(rig.final_calls("final-reviewer")) == 1


def test_a_run_of_an_older_driver_after_its_first_round_resumes_into_the_fix_batch_and_one_scoped_review(tmp_path):
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-spec": [final(finding("Q-ONE"))],
                    "final/final-reviewer": [final(finding("SCOPED-NOTE", "minor"))],
                    "final/fixer": [{"report": {"status": "DONE"}}] * 2 + [fixer(fn("02"), "fix q-one")]})
    rig.run(steps)
    assert rig.code == 1 and rig.final()["round"] == 1, rig.out
    # As an older driver left it: no lenses record, and findings with no source.
    folder, state = rig.repo / ".agent/implement/final", rig.state()
    del state["run"]["final"]["lenses"]
    state["run"]["final"]["open"] = [{k: v for k, v in f.items() if k != "source"} for f in state["run"]["final"]["open"]]
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    old = [{k: v for k, v in f.items() if k != "source"} for f in findings_file(rig)]
    (folder / "findings-0.json").write_text(json.dumps(old))
    before = len(rig.ledger())

    rig.run(steps)
    assert rig.code == 0, rig.out
    assert [len(rig.final_calls(role)) for role in LENSES] == [1, 1, 1]
    assert len(rig.final_calls("fixer")) == 3 and len(rig.final_calls("final-reviewer")) == 1
    found = [line for line in rig.ledger()[before:] if "finding of kind" in line]
    assert found and all(" from final-reviewer at " in line for line in found)
