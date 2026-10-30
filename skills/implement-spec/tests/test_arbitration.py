"""A disputed locked test: the reviewer's arbitration, one correction, and the `spec-ambiguous` question (spec
requirements 3 to 6). In process, on the rig of test_fixloop, so coverage sees the driver."""
import json
import re

import pytest

from test_cli import ticket
from test_fixloop import Inside, inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_review import ONE, STUCK, finding, fix, review
from test_walk import COST, impl, plan, tw

TEST = "tests/test_01.py::test_x"
SINGLE = "the spec quotes with '"
CORRECTED = "def test_x():\n    assert '01' == '01'\n"
AMBIGUOUS = "01-spec-ambiguous-1"
OTHER = "tests/test_01b.py"
SECOND = {"02-quote-fields.md": ticket("02", "Quote fields", touches="`demo/other.py: g`")}


def disputes(*concerns, role="implementer"):
    """A BLOCKED report of `role` that disputes each concern, `(<test>, <reason>)`."""
    report = {"status": "BLOCKED", "commits": [], "files": [], "green": [],
              "concerns": [f"locked test contradicts spec: {test}: {reason}" for test, reason in concerns or
                           [(TEST, SINGLE)]]}
    return {"report": {**report, **({"ticket": "01"} if role == "fixer" else {})}}


def ruling(verdict, reason, test=TEST, requirement="R3"):
    """An arbitration report with one ruling."""
    return {"report": {"status": "DONE", "files": [], "concerns": [],
                       "rulings": [{"test": test, "requirement": requirement, "ruling": verdict, "reason": reason}]}}


def correction(text=CORRECTED, path="tests/test_01.py"):
    step = tw("01")
    step["files"] = {path: text}
    step["report"] = {**step["report"], "files": [path], "locked": [path]}
    return {**step, "commit": "test: 01 corrected"}


def arbitrations(rig):
    return [call for call in rig.calls("01", "reviewer") if call["prompt"].startswith("# Arbitration")]


def roles(rig):
    """The roles of ticket 01 in order, an arbitration as `arbitration`."""
    return ["arbitration" if call["prompt"].startswith("# Arbitration") else call["role"] for call in rig.calls("01")]


def asked(rig):
    return [q for q in rig.state()["run"]["questions"] if q["answer"] is None]


def run(tmp_path, steps, files=ONE, **models):
    return Inside(tmp_path, files, models=models or None).run(plan(**steps))


# ---------------------------------------------------------------- the ruling

def test_a_test_ruled_wrong_gets_one_correction_with_the_ruling_then_the_implementer_runs_again(tmp_path):
    rig = run(tmp_path, {"01/implementer": [disputes(), impl("01")], "01/test-writer": [tw("01"), correction()],
                         "01/reviewer": [ruling("test-wrong", "R3 says single quotes"), review("01")]})
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert roles(rig) == ["test-writer", "implementer", "arbitration", "test-writer", "implementer", "reviewer"]
    assert not rig.state()["run"]["questions"] and rig.state()["tickets"]["01"]["round"] == 0
    (arbitration,) = arbitrations(rig)
    assert f"- {TEST}: {SINGLE}" in arbitration["prompt"]
    assert ".agent/implement/01/implementer-0.json" in arbitration["prompt"]
    (ruled,) = json.loads((rig.folder() / "ruled-1.json").read_text())
    assert (ruled["file"], ruled["kind"], ruled["evidence"]) == ("tests/test_01.py", "ruling", "R3 says single quotes")
    fixed, again = rig.calls("01", "test-writer")[1], rig.calls("01", "implementer")[1]
    assert ".agent/implement/01/ruled-1.json" in fixed["prompt"] and again["head"] == fixed["sha"]
    assert "R3 says single quotes" in again["prompt"]
    entry = rig.state()["tickets"]["01"]
    assert entry["disputes"]["tests/test_01.py"]["ruling"] == "test-wrong" and entry["test_writer_sha"] == fixed["sha"]
    assert entry["corrected"] == {"tests/test_01.py": {"test": TEST, "requirement": "R3",
                                                       "reason": "R3 says single quotes"}}
    assert rig.led("the reviewer ruled test-wrong on the locked test tests/test_01.py::test_x, R3")


def test_a_test_ruled_right_sends_the_implementer_back_with_the_ruling_and_no_correction(tmp_path):
    rig = run(tmp_path, {"01/implementer": [disputes(), impl("01")],
                         "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01")]})
    assert rig.code == 0 and not rig.state()["run"]["questions"], rig.ledger()
    assert roles(rig) == ["test-writer", "implementer", "arbitration", "implementer", "reviewer"]
    assert "R3 says double quotes" in rig.calls("01", "implementer")[1]["prompt"]
    assert rig.state()["tickets"]["01"]["corrected"] == {}


def test_a_fixer_of_round_2_that_disputes_runs_again_in_round_2_after_the_correction(tmp_path):
    stuck = review("01", [STUCK])
    rig = run(tmp_path, {"01/reviewer": [stuck, stuck, ruling("test-wrong", "R3 says single quotes"), review("01")],
                         "01/fixer": [fix("01", 1), disputes(role="fixer"), fix("01", 2)],
                         "01/test-writer": [tw("01"), correction()]})
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert roles(rig) == ["test-writer", "implementer", "reviewer", "fixer", "reviewer", "fixer", "arbitration",
                          "test-writer", "fixer", "reviewer"]
    assert rig.state()["tickets"]["01"]["round"] == 2 and not rig.led("round 3")
    assert rig.calls("01", "fixer")[2]["head"] == rig.calls("01", "test-writer")[1]["sha"]
    assert "fixer-2.json" in rig.calls("01", "fixer")[2]["prompt"]
    assert [agent["entry"] for agent in rig.state()["run"]["agents"] if agent["role"] == "fixer"][1:] == [
        "openai/gpt-sol@medium"] * 2
    # No review saw the correction, so the review after the fix is a full one.
    assert "re-review" not in rig.prompt("reviewer-2")


def test_a_dispute_on_another_locked_file_is_arbitrated_too(tmp_path):
    two = tw("01")
    two["files"][OTHER] = "def test_y():\n    assert 1 == 'y'\n"
    two["report"] = {**two["report"], "locked": ["tests/test_01.py", OTHER], "files": ["tests/test_01.py", OTHER]}
    rig = run(tmp_path, {"01/test-writer": [two],
                         "01/implementer": [disputes(), disputes((f"{OTHER}::test_y", "y is not a column")),
                                            impl("01")],
                         "01/reviewer": [ruling("code-wrong", "R3 says double quotes"),
                                         ruling("code-wrong", "R4 says y is a column", f"{OTHER}::test_y", "R4"),
                                         review("01")]})
    assert rig.code == 0 and not rig.state()["run"]["questions"], rig.ledger()
    assert len(arbitrations(rig)) == 2 and set(rig.state()["tickets"]["01"]["disputes"]) == {"tests/test_01.py", OTHER}
    assert (rig.folder() / "arbitration-2.json").exists()


def test_a_dispute_that_names_no_locked_file_is_rejected_and_runs_again_on_the_second_entry(tmp_path):
    rig = run(tmp_path, {"01/implementer": [disputes(("tests/test_99.py::test_z", "z")), impl("01")]})
    assert rig.code == 0 and not arbitrations(rig), rig.ledger()
    first, second = rig.led("Ticket 01: implementer ran on")
    assert "the dispute of tests/test_99.py::test_z names no locked file of the ticket" in first
    assert second.endswith("report accepted.") and "names no locked file" in rig.calls("01", "implementer")[1]["prompt"]
    assert [agent["entry"] for agent in rig.state()["run"]["agents"] if agent["role"] == "implementer"] == [
        "anthropic/claude-opus-5@medium", "openai/gpt-sol@medium"]


def test_a_dispute_with_uncommitted_work_is_rejected(tmp_path):
    dirty = {**disputes(), "files": {"demo/export.py": "half = 1\n"}}
    rig = run(tmp_path, {"01/implementer": [dirty, impl("01")]})
    assert rig.code == 0 and not arbitrations(rig), rig.ledger()
    assert "a dispute leaves no uncommitted change" in rig.led("Ticket 01: implementer ran on")[0]


def test_the_summary_keeps_its_table_and_counts_an_arbitration_as_a_reviewer_agent(tmp_path):
    """Ticket 01: a dispute, its arbitration, a fix round, then the landing; ticket 02 stops twice, so the end of
    run, and its full gate, wait."""
    stuck = {"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["no column order"], "green": []}}
    rig = run(tmp_path, {"01/implementer": [disputes(), impl("01")], "02/implementer": [stuck, stuck],
                         "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01", [STUCK]),
                                         review("01")],
                         "01/fixer": [fix("01", 1)]}, files={**ONE, **SECOND})
    assert rig.code == 2, rig.ledger()
    rows = (rig.repo / ".agent/implement/summary.md").read_text().split("| Measure | Value |\n|---|---|\n")[1]
    names = re.findall(r"^\| (.+?) \|", rows, re.M)
    assert {f"wall time, {role}" for role in ("test-writer", "implementer", "reviewer", "fixer")} < set(names), rows
    assert names[-3:] == ["fix rounds per ticket", "human stops", "cost"] and rows.endswith(" USD |\n"), rows
    for row in ("| fast gates run | 2 |", "| full gates run | 1 |", "| agents, reviewer on anthropic/claude-opus-5@medium | 3 |",
                "| fix rounds per ticket | 01: 1, 02: 0 |", "| human stops | 1 |"):
        assert row in rows, rows


# ---------------------------------------------------------------- a second dispute or correction: spec-ambiguous

def ambiguous(tmp_path, second=TEST, then=()):
    """Ticket 01 after a `code-wrong` ruling and a second dispute on `second`."""
    rig = run(tmp_path, {"01/implementer": [disputes(), disputes((second, "the example in R3 uses '")), *then],
                         "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01")]})
    return rig


@pytest.mark.parametrize("second", [TEST, "test_01.py::test_x[single]"])
def test_a_second_dispute_on_the_same_test_asks_spec_ambiguous_with_both_readings(tmp_path, second):
    rig = ambiguous(tmp_path, second)
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    assert len(arbitrations(rig)) == 1, "no second arbitration"
    (question,) = asked(rig)
    assert question["id"] == AMBIGUOUS and question["recommended"] == ""
    assert [option["id"] for option in question["options"]] == ["keep-test", "correct-test", "edit-spec"]
    for part in (second, "R3", "R3 says double quotes", "the example in R3 uses '"):
        assert part in question["subject"], part
    page = (rig.repo / ".agent/implement-review.html").read_text()
    (block,) = re.findall(rf'data-q="{AMBIGUOUS}">(.*?)</textarea>', page, re.S)
    assert "chip rec" not in block, "no option is recommended"


def test_after_a_correction_a_review_finding_on_the_test_asks_spec_ambiguous(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    rig = run(tmp_path, {"01/implementer": [disputes(), impl("01")], "01/test-writer": [tw("01"), correction()],
                         "01/reviewer": [ruling("test-wrong", "R3 says single quotes"), review("01", [stale])]})
    assert rig.code == 2 and len(rig.calls("01", "test-writer")) == 2, rig.ledger()
    (question,) = asked(rig)
    assert question["id"] == AMBIGUOUS and "R3 says single quotes" in question["subject"]
    assert "STALE" in question["subject"] and TEST in question["subject"]


def test_a_test_ruled_wrong_that_a_review_corrected_before_asks_spec_ambiguous(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    rig = run(tmp_path, {"01/test-writer": [tw("01"), correction()],
                         "01/reviewer": [review("01", [stale, STUCK]), ruling("test-wrong", "R3 says single quotes")],
                         "01/fixer": [disputes(role="fixer")]})
    assert rig.code == 2 and len(rig.calls("01", "test-writer")) == 2, rig.ledger()
    (question,) = asked(rig)
    assert "STALE" in question["subject"] and "R3 says single quotes" in question["subject"]
    assert question["resume"] == "review" and rig.state()["tickets"]["01"]["round"] == 0


def test_a_ruling_that_asks_spec_ambiguous_is_kept_so_a_dispute_after_keep_test_is_not_arbitrated_again(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    steps = plan(**{"01/test-writer": [tw("01"), correction()],
                    "01/reviewer": [review("01", [stale, STUCK]), ruling("test-wrong", "R3 says single quotes"),
                                    review("01", [STUCK]), ruling("test-wrong", "R3 says single quotes")],
                    "01/fixer": [disputes(role="fixer"), disputes(role="fixer")]})
    rig = Inside(tmp_path, ONE).run(steps)
    assert rig.state()["tickets"]["01"]["disputes"]["tests/test_01.py"]["ruling"] == "test-wrong", rig.ledger()
    rig.run(steps, "--answer", f"{AMBIGUOUS}=keep-test")
    assert rig.code == 2 and len(arbitrations(rig)) == 1, rig.ledger()
    assert [q["id"] for q in asked(rig)] == ["01-spec-ambiguous-2"]


# ---------------------------------------------------------------- the answers

def test_keep_test_runs_the_implementer_again_and_keeps_the_record(tmp_path):
    rig = ambiguous(tmp_path, then=(disputes(), impl("01")))
    before = rig.state()["tickets"]["01"]["disputes"]
    rig.run(plan(**{"01/implementer": [disputes(), disputes(), disputes(), impl("01")],
                    "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01")]}),
            "--answer", f"{AMBIGUOUS}=keep-test")
    assert rig.code == 2 and rig.led(f"Ruling: 01 locked test stands (keep-test): {TEST}"), rig.ledger()
    assert len(rig.calls("01", "implementer")) == 3 and "R3 says double quotes" in rig.calls("01", "implementer")[2]["prompt"]
    assert [q["id"] for q in asked(rig)] == ["01-spec-ambiguous-2"] and len(arbitrations(rig)) == 1
    assert rig.state()["tickets"]["01"]["disputes"] == before


def test_correct_test_corrects_to_the_second_reading_then_the_implementer_runs_and_the_record_stays(tmp_path):
    second = disputes((TEST, "the example in R3 uses '"))
    steps = {"01/implementer": [disputes(), second, disputes()], "01/test-writer": [tw("01"), correction()],
             "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01")]}
    rig = run(tmp_path, steps)
    rig.run(plan(**steps), "--answer", f"{AMBIGUOUS}=correct-test")
    assert rig.code == 2, rig.ledger()
    assert roles(rig)[-2:] == ["test-writer", "implementer"] and len(arbitrations(rig)) == 1
    fixed = rig.calls("01", "test-writer")[1]
    assert ".agent/implement/01/spec-ambiguous-1.json" in fixed["prompt"]
    (wanted,) = json.loads((rig.folder() / "spec-ambiguous-1.json").read_text())
    assert wanted["evidence"] == "correct the test to the second reading: the example in R3 uses '"
    assert rig.calls("01", "implementer")[2]["head"] == fixed["sha"]
    assert [q["id"] for q in asked(rig)] == ["01-spec-ambiguous-2"]
    assert set(rig.state()["tickets"]["01"]["disputes"]) == {"tests/test_01.py"}


def test_edit_spec_keeps_the_ticket_blocked_with_the_ruling_and_runs_no_agent(tmp_path):
    rig = ambiguous(tmp_path)
    seen, before = len(rig.calls("01")), rig.state()["tickets"]["01"]
    rig.run(plan(), "--answer", f"{AMBIGUOUS}=edit-spec")
    assert rig.code == 1 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    assert rig.led("Ruling: 01 stays BLOCKED for an edit of the spec (edit-spec)") and len(rig.calls("01")) == seen
    after = rig.state()["tickets"]["01"]
    assert (after["disputes"], after["corrected"]) == (before["disputes"], before["corrected"])


def test_keep_test_on_a_review_finding_closes_it_and_the_ticket_goes_on(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    steps = plan(**{"01/test-writer": [tw("01"), correction()], "01/fixer": [fix("01", 1)],
                    "01/reviewer": [review("01", [stale]), review("01", [stale]), review("01", [stale, STUCK]),
                                    review("01")]})
    rig = Inside(tmp_path, ONE).run(steps)
    assert rig.code == 2 and [q["id"] for q in asked(rig)] == [AMBIGUOUS], rig.ledger()
    rig.run(steps, "--answer", f"{AMBIGUOUS}=keep-test")
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert roles(rig) == ["test-writer", "implementer", "reviewer", "test-writer", "reviewer", "reviewer", "fixer",
                          "reviewer"]
    assert rig.led("STALE", "closed as it is by the ruling (keep-test)")


# ---------------------------------------------------------------- a correction that fails

def test_an_arbitration_correction_rejected_twice_asks_ticket_blocked_and_retry_starts_from_before_it(tmp_path):
    spill = {**correction(), "files": {"tests/test_01.py": CORRECTED, "demo/export.py": "spill = 1\n"}}
    steps = plan(**{"01/implementer": [disputes(), disputes(), impl("01")],
                    "01/test-writer": [tw("01"), spill, spill, correction()],
                    "01/reviewer": [ruling("test-wrong", "R3 says single quotes"),
                                    ruling("test-wrong", "R3 says single quotes"), review("01")]})
    rig = Inside(tmp_path, ONE).run(steps)
    assert rig.code == 2, rig.ledger()
    (question,) = asked(rig)
    assert question["kind"] == "ticket-blocked" and question["resume"] == "implement"
    assert "its commits also change demo/export.py" in question["subject"]
    start = rig.calls("01", "test-writer")[1]["head"]
    assert rig.state()["tickets"]["01"]["tip"] == start and rig.state()["tickets"]["01"]["disputes"] == {}
    rig.run(steps, "--answer", f"{question['id']}=retry")
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert rig.calls("01", "implementer")[1]["head"] == start and len(arbitrations(rig)) == 2


def test_an_implementer_with_nothing_to_change_after_the_correction_is_accepted(tmp_path):
    """Issue 123: the correction made the locked test green, so the implementer has nothing to change."""
    idle = {"report": {"status": "DONE_WITH_CONCERNS", "commits": [], "files": [], "green": ["1 passed"],
                       "concerns": ["the correction made the locked tests green: the start needs no change"]}}
    green = {**correction(), "stdout": "FAILED tests/test_01.py::test_x - AttributeError\n" + COST}
    rig = run(tmp_path, {"01/implementer": [disputes(), idle], "01/test-writer": [tw("01"), green],
                         "01/reviewer": [ruling("test-wrong", "R3 says single quotes"), review("01")]})
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    fixed = rig.calls("01", "test-writer")[1]["sha"]
    assert fixed in [gate["commit"] for gate in rig.gates()] and rig.calls("01", "reviewer")[1]["head"] == fixed
