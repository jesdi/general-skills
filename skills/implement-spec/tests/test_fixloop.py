"""In-process review, fix-loop and conflict tests: the rig and the scenarios of test_review.py without the launcher,
so coverage sees the driver, plus the cases that only a direct call reaches."""
import json
import os
import shutil
import subprocess
import sys

import jsonschema
import pytest

import driver
import driver_agents
import driver_fixloop
import driver_report
import driver_run
import driver_review
import test_review
from conftest import GIT
from test_cli import three, ticket
from test_final import RUNNER, final, fixer, fn
from test_review import FABLE, ONE, OPUS, RISKY, SOL, STUCK, correct, finding, fix, review
from test_walk import impl, plan, tw


class Inside(test_review.Review):
    """The rig of test_review with the driver called in this process."""

    command = "{gate}"  # the gate command; `{gate}` is the rig's gate script

    def run(self, steps, *extra, parallel="2", watch=False):
        (self.rig / "plan.json").write_text(json.dumps(steps))
        command = self.command.format(gate=self.rig / "gate")
        args = ["--tickets", ".agent/tickets", "--gate", f"export TIER=full; {command}", "--fast-gate",
                f"export TIER=fast; {command}", "--spec", "specs/csv/spec.md", "--models", str(self.models), "--runner",
                str(self.rig / "run-agent"), "--parallel", parallel, *extra]
        here = os.getcwd()
        os.chdir(self.repo)
        try:
            self.code = driver.main(args)
        finally:
            os.chdir(here)
        self.out = self.err = ""
        return self

    def prompt(self, name, nn="01"):
        return (self.folder(nn) / f"{name}.prompt.md").read_text()


@pytest.fixture(autouse=True)
def inside(monkeypatch, tmp_path, capsys):
    """`test_review.scoped` and `.conflicted` build this rig; a CLI is on PATH when the rig's bin has it."""
    which = shutil.which
    monkeypatch.setattr(test_review, "Review", Inside)
    bins = lambda: os.pathsep.join(map(str, [*tmp_path.glob("bin"), *tmp_path.glob("*/bin")]))
    monkeypatch.setattr(driver_agents.shutil, "which", lambda cli: which(cli, path=bins()))


def under(tmp_path, name):
    """A second rig of one test gets its own directory."""
    (tmp_path / name).mkdir()
    return tmp_path / name


def blocked(rig, nn="01"):
    (line,) = rig.led(nn, "BLOCKED")
    assert rig.code == 1 and rig.state()["tickets"][nn]["phase"] == "blocked"
    return line


# ---------------------------------------------------------------- who reviews

def test_a_risky_ticket_gets_both_agents_and_one_merged_findings_file(tmp_path):
    rig = Inside(tmp_path, RISKY, {"correctness": [SOL, OPUS], "implementer": [OPUS]})
    rig.run(plan(**{"02/reviewer": [review("02", [finding("R-MINOR", "minor")])],
                    "02/correctness": [correct([finding("C-IMPORTANT")])], "02/fixer": [fix("02", 1)]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ticket 02: minor finding of kind code from the reviewer at demo/export.py:99: R-MINOR.")
    assert rig.led("important finding of kind code from the correctness at", "C-IMPORTANT") and rig.led("correctness ran on", SOL)
    merged = json.loads((rig.folder("02") / "findings-0.json").read_text())
    assert [(f["source"], f["severity"], f["title"]) for f in merged] == [
        ("reviewer", "minor", "R-MINOR"), ("correctness", "important", "C-IMPORTANT")]
    assert ".agent/implement/02/findings-0.json" in rig.prompt("fixer-1", "02")
    # Both agents review the fix again, each scoped to it; only the reviewer's first report must prove the tests.
    for role in ("reviewer", "correctness"):
        again = rig.prompt(f"{role}-1", "02")
        assert "This is the re-review after fix round 1." in again and "findings-0.json" in again
        assert again.rstrip().endswith("one ref store.") and "re-review" not in rig.prompt(f"{role}-0", "02")
    assert driver_review.CORRECTNESS.read_text() in rig.prompt("correctness-0", "02")
    assert "correctness.md" not in rig.prompt("reviewer-0", "02") and not rig.led("folded")
    assert json.loads((rig.folder("02") / "findings-1.json").read_text()) == []


def test_capped_regression_is_carried_through_the_final_fix_batch(tmp_path):
    rig = Inside(tmp_path, RISKY)
    (rig.rig / "run-agent").write_text(RUNNER)
    regression = {**finding("QUOTING-REGRESSION"), "basis": "regression"}
    scoped = final()
    scoped["report"]["resolved_must_fix"] = ["QUOTING-REGRESSION (demo/export.py:99)"]
    batch = fixer(fn("02"), "restore quoted fields", ticket_of="02")
    batch["report"]["addressed_must_fix"] = ["QUOTING-REGRESSION (demo/export.py:99)"]

    rig.run(plan(**{"02/reviewer": [review("02", [regression])] * 3,
                    "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-reviewer": [final(), scoped], "final/fixer": [batch]}))

    assert rig.code == 0, rig.ledger()
    assert [item["title"] for item in rig.state()["run"]["final"]["must_fix"]] == ["QUOTING-REGRESSION"]
    assert "QUOTING-REGRESSION" in rig.prompt("fixer-1", "final")


def test_correctness_entry_is_the_first_of_another_provider_than_the_implementers():
    models = {"implementer": ["opus@medium"], "correctness": ["opus@high", "openai/a@low", "openai/b@low"]}
    assert driver_agents.correctness_entry(models) == "openai/a@low"
    assert driver_agents.correctness_entry({**models, "implementer": ["openai/x@low", "opus@low"]}) == "opus@high"
    assert driver_agents.correctness_entry({**models, "correctness": ["opus@high", "claude-fable-5@low"]}) == ""


def test_a_set_with_a_risk_ticket_needs_review_diff_and_a_set_without_one_does_not(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(driver_review, "CORRECTNESS", tmp_path / "skills/review-diff/correctness.md")
    rig = Inside(tmp_path, three()).run(plan())
    assert rig.code == 3 and not rig.calls() and not (rig.repo / ".agent/implement").exists()
    out = capsys.readouterr().out
    assert f"{tmp_path}/skills/review-diff/correctness.md is missing: ticket 02 has a Risk line" in out
    assert "install the review-diff skill next to implement-spec" in out
    plain = Inside(under(tmp_path, "plain"), ONE).run(plan())
    assert plain.code == 0 and plain.state()["tickets"]["01"]["phase"] == "merged"


# ---------------------------------------------------------------- the locked files

def test_a_changed_locked_file_is_fixed_before_a_fixture_finding_is_corrected(tmp_path):
    rig = Inside(tmp_path)
    locked = tw("01")["files"]
    cheat = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "tests/test_01.py": "# edited\n"})
    stale = finding("STALE", "important", kind="fixture", file="tests/test_01.py")
    correction = {**tw("01"), "files": {"tests/test_01.py": "# corrected\n"}, "commit": "test: 01 fixture fix"}
    rig.run(plan(**{"01/implementer": [cheat], "01/test-writer": [tw("01"), correction],
                    "01/reviewer": [review("01", [stale]), review("01", [stale]), review("01")],
                    "01/fixer": [fix("01", 1, files={**locked, "demo/export.py": "x = 1\n"})]}))
    assert rig.code == 0, rig.ledger()
    (line,) = rig.led("locked file changed: tests/test_01.py")
    assert line == ("Ticket 01: critical finding of kind code from the driver at tests/test_01.py:1: "
                    "locked file changed: tests/test_01.py.")
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "fixer", "reviewer", "test-writer", "reviewer"]
    (first,) = [f for f in json.loads((rig.folder() / "findings-0.json").read_text()) if f["source"] == "driver"]
    assert first["evidence"] == f"git diff {rig.sha('01', 'test-writer')}..HEAD -- tests/test_01.py"
    assert f"its content at {rig.sha('01', 'test-writer')}." in rig.prompt("fixer-1")
    # The correction is the new reference of the locked-file check, and it is outside the rounds.
    entry = rig.state()["tickets"]["01"]
    assert (entry["round"], entry["test_writer_sha"]) == (1, rig.calls("01", "test-writer")[1]["sha"])
    assert entry["locked"] == ["tests/test_01.py"] and (rig.folder() / "test-writer-2.json").exists()
    assert rig.led("fixture correction in tests/test_01.py, outside the fix rounds: STALE: STALE.")
    assert "This call is a correction of the locked tests" in rig.prompt("test-writer-2") and "findings-1.json" in rig.prompt("test-writer-2")


def test_a_locked_file_check_that_git_cannot_make_is_a_finding(tmp_path):
    rig = Inside(tmp_path)
    assert driver_review.locked_changes(str(rig.repo), None, []) == []
    (found,) = driver_review.locked_changes(str(rig.repo), "0" * 40, ["tests/test_01.py"])
    assert found["severity"] == "critical" and found["title"].startswith("locked file check failed: ")


STALE = finding("STALE", kind="fixture", file="tests/test_01.py")
AGAIN_1 = "01-locked-test-again-1"


def corrections(n):
    """`n` test-writer corrections of tests/test_01.py, each with its own content."""
    return [{**tw("01"), "files": {"tests/test_01.py": f"# corrected {i}\n"}, "commit": f"test: 01 correction {i}"}
            for i in range(1, n + 1)]


def asked(rig):
    """The open questions of the run: id -> option ids."""
    return {q["id"]: [o["id"] for o in q["options"]] for q in rig.state()["run"]["questions"] if q["answer"] is None}


def test_a_locked_test_finding_that_comes_back_after_its_correction_asks_the_operator(tmp_path):
    # Issue #107: the ticket was BLOCKED with no question, so only an edit of state.json could resume it.
    steps = plan(**{"01/test-writer": [tw("01"), *corrections(2)],
                    "01/reviewer": [review("01", [STALE])] * 3 + [review("01")]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    assert rig.led("BLOCKED at the review: a locked-test finding is open after the test-writer correction: STALE.")
    assert asked(rig) == {AGAIN_1: ["correct-again", "accept", "redesign"]}
    (question,) = rig.state()["run"]["questions"]
    assert question["recommended"] == "correct-again" and question["evidence"][0] == ".agent/implement/01/findings-0.json"
    rig.run(steps, "--answer", f"{AGAIN_1}=correct-again")
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "test-writer", "reviewer", "reviewer", "test-writer",
                     "reviewer"]
    assert rig.led("Ruling: 01 locked-test finding goes back to the test-writer for one more correction")
    assert rig.state()["tickets"]["01"]["phase"] == "merged"


def test_an_accepted_locked_test_finding_lets_the_ticket_go_on_to_its_fix_rounds(tmp_path):
    steps = plan(**{"01/test-writer": [tw("01"), *corrections(1)], "01/fixer": [fix("01", 1)],
                    "01/reviewer": [review("01", [STALE]), review("01", [STALE]), review("01", [STALE, STUCK]),
                                    review("01", [STALE])]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2 and list(asked(rig)) == [AGAIN_1], rig.ledger()
    rig.run(steps, "--answer", f"{AGAIN_1}=accept")
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "test-writer", "reviewer", "reviewer", "fixer",
                     "reviewer"]
    assert rig.state()["tickets"]["01"]["round"] == 1
    assert rig.led("STALE", "closed as it is by the ruling")


def test_the_extra_corrections_are_bounded_and_redesign_keeps_the_ticket_blocked(tmp_path):
    steps = plan(**{"01/test-writer": [tw("01"), *corrections(3)], "01/reviewer": [review("01", [STALE])] * 6})
    rig = Inside(tmp_path).run(steps)
    for n in (1, 2):
        assert asked(rig) == {f"01-locked-test-again-{n}": ["correct-again", "accept", "redesign"]}, rig.ledger()
        rig.run(steps, "--answer", f"01-locked-test-again-{n}=correct-again")
    # Two extra corrections did not close it: a third one is not offered.
    assert asked(rig) == {"01-locked-test-again-3": ["accept", "redesign"]}, rig.ledger()
    assert len(rig.calls("01", "test-writer")) == 4
    rig.run(steps, "--answer", "01-locked-test-again-3=redesign")
    assert rig.code == 1 and rig.state()["tickets"]["01"]["phase"] == "blocked" and asked(rig) == {}
    assert rig.led("Ruling: 01 stays BLOCKED, the design changes first (redesign): STALE.")


def test_an_open_finding_survives_the_fixture_correction_of_its_review_and_starts_the_fix_round(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    correction = {**tw("01"), "files": {"tests/test_01.py": "# corrected\n"}, "commit": "test: 01 fixture fix"}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), correction], "01/fixer": [fix("01", 1)],
                                       "01/reviewer": [review("01", [stale, STUCK]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "test-writer", "fixer", "reviewer"]
    assert rig.state()["tickets"]["01"]["round"] == 1 and "findings-0.json" in rig.prompt("fixer-1")
    assert rig.calls("01", "fixer")[0]["head"] == rig.calls("01", "test-writer")[1]["sha"]  # on the corrected tip
    # No review saw the correction yet, so the review after the round is a full one.
    assert "re-review" not in rig.prompt("reviewer-1")
    assert [gate["commit"] for gate in rig.gates("fast")] == [rig.sha("01", "implementer"), rig.sha("01", "fixer")]


def test_a_test_finding_on_a_locked_file_gets_the_test_writer_correction_not_a_fix_round(tmp_path):
    # A failed mutation proof: the fixer may not edit the locked file, so a fix round could never close it.
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    correction = {**tw("01"), "files": {"tests/test_01.py": "# stronger\n"}, "commit": "test: 01 stronger test"}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), correction],
                                       "01/reviewer": [review("01", [vacuous]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "test-writer", "reviewer"]
    entry = rig.state()["tickets"]["01"]
    assert (entry["round"], entry["test_writer_sha"]) == (0, rig.calls("01", "test-writer")[1]["sha"])
    assert rig.led("correction in tests/test_01.py, outside the fix rounds: VACUOUS: VACUOUS.")
    # Only a severe finding of kind `test` on a locked file goes to the test-writer, and a severe finding of any kind on
    # a locked file of a merged ticket.
    minor, elsewhere = finding("M", "minor", kind="test", file="tests/test_01.py"), finding("E", kind="test")
    assert driver_review.route([vacuous, minor, elsewhere], ["tests/test_01.py"], []) == ([vacuous], [elsewhere])
    gap, aside = finding("G", file="tests/test_00.py"), finding("A", "minor", file="tests/test_00.py")
    assert driver_review.route([gap, aside, elsewhere], [], ["tests/test_00.py"]) == ([gap], [elsewhere])


NEEDS_CODE = "needs production code: the code has no multi-PR view"


def not_addressing(*titles, **report):
    """A correction of tests/test_01.py that reports `titles` under `not_addressed`."""
    step = {**tw("01"), "files": {"tests/test_01.py": "# corrected\n"}, "commit": "test: 01 fixture fix"}
    reasons = [{"finding": title, "reason": NEEDS_CODE} for title in titles]
    return {**step, "report": {**step["report"], **report, "not_addressed": reasons}}


def test_a_test_finding_the_correction_did_not_address_goes_to_the_fixer_as_a_code_finding(tmp_path):
    # Issue #111: the ledger said "corrected it at <sha>" for every finding, and the finding came back each review.
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), not_addressing("VACUOUS")],
                                       "01/fixer": [fix("01", 1)],
                                       "01/reviewer": [review("01", [stale, vacuous]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "test-writer", "fixer", "reviewer"]
    assert rig.led("STALE: STALE. The test-writer corrected it at")
    assert rig.led("VACUOUS", "corrected it at") == []
    assert rig.led(f"test correction in tests/test_01.py, outside the fix rounds: VACUOUS: VACUOUS. The test-writer "
                   f"did not correct it: {NEEDS_CODE}. It goes to the fixer as a finding of kind code.")
    # The fixer reads the finding as one of kind code, with the reason of the test-writer as its evidence.
    (routed,) = [f for f in json.loads((rig.folder() / "findings-0.json").read_text()) if f["title"] == "VACUOUS"]
    assert (routed["kind"], routed["evidence"]) == ("code", f"the test-writer did not correct it: {NEEDS_CODE}")
    assert rig.state()["tickets"]["01"]["round"] == 1 and "findings-0.json" in rig.prompt("fixer-1")


def test_a_correction_that_addresses_no_finding_needs_no_commit(tmp_path):
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    none = not_addressing("VACUOUS", commits=[], files=[], locked=[], red=[])
    del none["commit"], none["files"]
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), none], "01/fixer": [fix("01", 1)],
                                       "01/reviewer": [review("01", [vacuous]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    assert [call["role"] for call in rig.calls("01")].count("test-writer") == 2  # no rerun
    assert rig.led("VACUOUS. The test-writer did not correct it") and rig.state()["tickets"]["01"]["round"] == 1


def test_a_correction_that_names_a_finding_it_was_not_given_is_rerun(tmp_path):
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), not_addressing("OTHER"), not_addressing()],
                                       "01/reviewer": [review("01", [vacuous]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    assert "not_addressed: OTHER is not the title of a finding of this correction" in rig.prompt("test-writer-1")
    assert rig.led("VACUOUS: VACUOUS. The test-writer corrected it at")


def test_a_test_writer_that_lists_test_node_ids_in_locked_locks_their_files(tmp_path):
    # Issue #71: a node id `<file>::<test>` in `locked` names its file, for the first run and for a correction.
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    ids = ["tests/test_01.py::test_x", "tests/test_01.py::test_y"]
    first = {**tw("01"), "report": {**tw("01")["report"], "locked": ids}}
    correction = {**first, "files": {"tests/test_01.py": "# stronger\n"}, "commit": "test: 01 stronger test"}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [first, correction],
                                       "01/reviewer": [review("01", [vacuous]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    assert [call["role"] for call in rig.calls("01")].count("test-writer") == 2  # no rerun
    assert rig.state()["tickets"]["01"]["locked"] == ["tests/test_01.py"]


CONTRA = "locked test contradicts spec: tests/test_01.py::test_x"
OVERRULED = {**tw("01"), "files": {"tests/test_01.py": "def test_x():\n    assert '01' == '01'\n"},
             "commit": "test: 01 overruled"}
AGAIN = impl("01", text="def fn_01():\n    return 'again'\n")


def fixer_stops():
    return {"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": [CONTRA], "green": [], "ticket": "01"}}


def test_a_fixer_that_reports_a_wrong_locked_test_opens_the_locked_test_question(tmp_path):
    steps = plan(**{"01/reviewer": [review("01", [STUCK]), review("01")], "01/fixer": [fixer_stops()],
                    "01/test-writer": [tw("01"), OVERRULED], "01/implementer": [impl("01"), AGAIN]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2, rig.ledger()
    (asked,) = rig.state()["run"]["questions"]
    assert (asked["id"], asked["evidence"][0]) == ("01-locked-test-1", ".agent/implement/01/fixer-1.json")
    rig.run(steps, "--answer", "01-locked-test-1=overrule-test")
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "fixer", "test-writer", "implementer", "reviewer"]
    # The correction reads the concerns of the fixer that stopped, not those of the implementer.
    assert "01/fixer-1.json" in rig.prompt("test-writer-0") and "implementer-0.json" not in rig.prompt("test-writer-0")


def test_the_fix_rounds_count_from_0_after_an_overruled_test(tmp_path):
    stuck = review("01", [STUCK])
    steps = plan(**{"01/reviewer": [stuck, stuck, review("01")],
                    "01/fixer": [fixer_stops(), fix("01", 1)],
                    "01/test-writer": [tw("01"), OVERRULED], "01/implementer": [impl("01"), AGAIN]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2 and rig.state()["tickets"]["01"]["round"] == 1, rig.ledger()
    rig.run(steps, "--answer", "01-locked-test-1=overrule-test")
    # The corrected tests are a new contract: the first finding on them gets a fresh fix round.
    assert rig.code == 0, rig.ledger()
    assert rig.state()["tickets"]["01"]["round"] == 1 and not rig.led("after round 2")


def test_a_fixture_correction_that_is_rejected_asks_the_operator(tmp_path):
    # Issue #107: a correction the driver rejects (for one, a change to a file it does not list) was a dead end too.
    steps = plan(**{"01/test-writer": [tw("01"), {}, {}, *corrections(1)],
                    "01/reviewer": [review("01", [STALE])] * 2 + [review("01")]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2 and list(asked(rig)) == [AGAIN_1], rig.ledger()
    assert rig.led("BLOCKED at the test-writer correction: report .agent/implement/01/test-writer-1.json is not")
    rig.run(steps, "--answer", f"{AGAIN_1}=correct-again")
    assert rig.code == 0 and len(rig.calls("01", "test-writer")) == 4, rig.ledger()


# ---------------------------------------------------------------- the locked files of the merged tickets

WEAK = "def test_x():\n    assert True\n"
LOCKED_01 = tw("01")["files"]["tests/test_01.py"]
CHEAT = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": WEAK})


def on_pr(rig, path):
    return subprocess.run(["git", "-C", str(rig.repo), "show", f"feat/csv:{path}"], capture_output=True, text=True).stdout


def test_a_later_ticket_that_weakens_a_locked_test_of_a_merged_ticket_lands_only_with_the_test_put_back(tmp_path):
    """Final review, correctness 3: ticket 02 replaced the locked assertion of ticket 01, merged and was pushed.
    Issue #90: the review finds the change; the restore keeps the gate green, so it closes the finding alone."""
    rig = Inside(tmp_path, three()).run(plan(**{"02/implementer": [CHEAT]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ticket 02: critical finding of kind fixture from the driver at tests/test_01.py:1")
    assert rig.led("Ticket 02:", "the restore closes it") and not rig.led("locked file changed")
    assert len(rig.calls("02", "test-writer")) == 1 and not rig.calls("02", "fixer")
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01 and "feat: 02 rows" in rig.pr_subjects()


def extends(text, listed):
    writer = tw("02")
    writer["files"]["tests/test_01.py"] = text
    writer["report"]["locked"] += ["tests/test_01.py"] * listed
    return writer


def test_the_test_writer_of_a_later_ticket_may_extend_the_locked_file_of_a_merged_ticket_when_it_lists_it(tmp_path):
    more = LOCKED_01 + "\n\ndef test_y():\n    assert 02 == 'y'\n"
    rig = Inside(under(tmp_path, "listed"), three()).run(plan(**{"02/test-writer": [extends(more, True)]}))
    assert rig.code == 0, rig.ledger()
    assert not rig.led("locked file changed") and on_pr(rig, "tests/test_01.py") == more
    assert rig.state()["tickets"]["02"]["locked"] == ["tests/test_02.py", "tests/test_01.py"]
    # What the test-writer of 02 wrote is locked for the implementer of 02: the check of its own review finds it.
    again = Inside(under(tmp_path, "cheat"), three()).run(
        plan(**{"02/test-writer": [extends(more, True)], "02/implementer": [CHEAT], "02/fixer": [{}, {}]}))
    assert again.led("Ticket 02:", "locked file changed") == [
        "Ticket 02: critical finding of kind code from the driver at tests/test_01.py:1: locked file changed: tests/test_01.py."]
    assert on_pr(again, "tests/test_01.py") == LOCKED_01
    # An unlisted change is a report violation: one rerun with the error, then BLOCKED before any implementer.
    unlisted = Inside(under(tmp_path, "unlisted"), three()).run(plan(**{"02/test-writer": [extends(more, False)] * 2}))
    error = ("tests/test_01.py is a locked file of a merged ticket, and `locked` does not list it: list it in "
             "`locked`, or leave the file as it was")
    assert blocked(unlisted, "02") == f"Ticket 02: BLOCKED at the test-writer: {error}."
    first, second = unlisted.calls("02", "test-writer")
    assert f"The driver rejected the earlier attempt: {error}" in second["prompt"]
    assert not unlisted.calls("02", "implementer") and on_pr(unlisted, "tests/test_01.py") == LOCKED_01


def test_a_refactor_ticket_may_not_change_a_locked_test_of_a_merged_ticket(tmp_path):
    """It has no test-writer and no locked file of its own: the review finds the change, and the restore closes it."""
    files = three(**{"02-quote-fields.md": test_review.ticket("02", "Quote fields", "01", seam="none — refactor", criteria=())})
    rig = Inside(tmp_path, files).run(plan(**{"02/implementer": [CHEAT]}))
    assert rig.code == 0 and not rig.calls("02", "test-writer"), rig.ledger()
    assert rig.led("Ticket 02:", "the restore closes it") and on_pr(rig, "tests/test_01.py") == LOCKED_01
    clean = Inside(under(tmp_path, "clean"), files).run(plan())
    assert clean.code == 0 and not clean.led("locked file changed"), clean.ledger()


# A correction is a test-writer: a file that it lists is its ticket's own. It must start from the locked version.
# The edit has no context lines, so it applies to the file whatever version the tree holds.
APPEND = "--- a/tests/test_01.py\n+++ b/tests/test_01.py\n@@ -2,0 +3 @@\n+# corrected\n"
CORRECTED_01 = LOCKED_01 + "# corrected\n"
RESTORED_01 = ("Ticket 02: tests/test_01.py was not the locked version at the correction: the driver restored it in a "
               "commit, so the correction starts from the locked version.")


def appending(tmp_path, **changes):
    """A correction of 02 that appends a line to tests/test_01.py as the tree holds it, and lists it."""
    (tmp_path / "append.patch").write_text(APPEND)
    step = {**tw("02"), "git": [["apply", "--unidiff-zero", str(tmp_path / "append.patch")]], "commit": "test: 02 corrected", **changes}
    step["report"] = {**step["report"], "locked": ["tests/test_02.py", "tests/test_01.py"]}
    return step


def proves(nn, path, name="test_x"):
    """A clean review of `nn` with one more mutation proof, for the locked test file `path`."""
    step, red = review(nn), f"FAILED {path}::{name} - AssertionError"
    step["report"]["mutations"] += [dict(test=f"{path}::{name}", file="demo/export.py", line=1, red=red)]
    return {**step, "stdout": step["stdout"] + red + "\n"}


def test_an_overrule_correction_starts_from_the_locked_file_of_the_merged_ticket(tmp_path):
    """Review round 1, I1 (probe b7b): the implementer of 02 weakened the test of 01, the correction listed the file."""
    contra = {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["locked test contradicts spec: tests/test_02.py::test_x"], "green": []}
    correction = appending(tmp_path, files={"tests/test_02.py": "def test_x():\n    assert '02' == '02'\n"})
    steps = plan(**{"02/implementer": [{**CHEAT, "report": contra}, impl("02", text="def fn_02():\n    return 'two'\n")],
                    "02/test-writer": [tw("02"), correction], "02/reviewer": [proves("02", "tests/test_01.py")]})
    rig = Inside(tmp_path, three()).run(steps)
    assert rig.code == 2, rig.ledger()
    rig.run(steps, "--answer", "02-locked-test-1=overrule-test")
    assert on_pr(rig, "tests/test_01.py") == CORRECTED_01, rig.ledger()
    assert rig.led("Ticket 02:", "restored") == [RESTORED_01] and rig.code == 0


def test_a_fixture_correction_starts_from_the_locked_file_of_the_merged_ticket(tmp_path):
    """Review round 1, I1 (probe b6b): the same through the fixture correction of the fix loop."""
    stale = finding("STALE", kind="fixture", file="tests/test_02.py")
    correction = appending(tmp_path, files={"tests/test_02.py": "def test_x():\n    assert '02' == 'x'  # corrected\n"})
    rig = Inside(tmp_path, three()).run(plan(**{"02/implementer": [CHEAT], "02/test-writer": [tw("02"), correction],
                                                "02/reviewer": [review("02", [stale]), proves("02", "tests/test_01.py")]}))
    assert on_pr(rig, "tests/test_01.py") == CORRECTED_01, rig.ledger()
    assert rig.led("Ticket 02:", "restored") == [RESTORED_01] and rig.code == 0


def test_a_finding_in_the_locked_file_of_a_merged_ticket_goes_to_the_correction_not_the_fixer(tmp_path):
    """Issue #116: ticket 02 must change the locked file of merged ticket 01. The implementer changes it, and the
    restore closes the driver's finding. The next review reports the missing change there: a fixer may only make it
    the forbidden way, so the correction makes it and lists the file."""
    missing = finding("MISSING", file="tests/test_01.py", line=2)
    correction = appending(tmp_path, files={})
    correction["report"]["locked"] = ["tests/test_01.py"]
    rig = Inside(tmp_path, three()).run(plan(**{
        "02/implementer": [CHEAT], "02/test-writer": [tw("02"), correction],
        "02/reviewer": [review("02"), review("02", [missing]), proves("02", "tests/test_01.py")]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ticket 02:", "the restore closes it") and not rig.calls("02", "fixer")
    assert rig.led("Ticket 02: code correction in tests/test_01.py, outside the fix rounds: MISSING")
    assert on_pr(rig, "tests/test_01.py") == CORRECTED_01
    assert "tests/test_01.py" in rig.state()["tickets"]["02"]["locked"]
    first, corrected = rig.prompt("test-writer-0", "02"), rig.prompt("test-writer-1", "02")
    assert "When the ticket must change a locked file of a merged ticket" in first
    assert "on a locked file of a merged ticket (tests/test_01.py" in corrected
    assert "only this correction may change that file" in corrected


def test_the_test_writer_checks_fixture_preconditions_and_a_correction_keeps_shared_fixtures_and_runs_the_locked_files(tmp_path):
    """Issue #97: a fixture misses the precondition of its scenario or a rule that a merged ticket locks, and a
    correction adds a requirement that the shared fakes of other locked tests do not satisfy."""
    stale = finding("STALE", kind="fixture", file="tests/test_02.py")
    correction = {**tw("02"), "files": {"tests/test_02.py": "# corrected\n"}, "commit": "test: 02 fixture fix"}
    rig = Inside(tmp_path, three()).run(plan(**{"02/test-writer": [tw("02"), correction],
                                                "02/reviewer": [review("02", [stale]), review("02")]}), parallel="1")
    assert rig.code == 0, rig.ledger()
    first, corrected = rig.prompt("test-writer-0", "02"), rig.prompt("test-writer-1", "02")
    assert "Each fixture sets up exactly the precondition of its scenario" in first
    assert "the locked tests of merged tickets: tests/test_01.py." in first
    assert "update each shared fake or fixture" not in first
    assert "update each shared fake or fixture that other locked tests use" in corrected
    assert "run each whole file of `locked` and each locked test of merged tickets" in corrected


def test_the_restore_before_a_correction_is_committed_so_the_next_fixer_and_its_gate_find_a_clean_tree(tmp_path):
    """Issue #86: the correction and the fixer each commit only their own files. The restore of the merged ticket's
    file must not stay in the tree, where the gate of the fixer's commit is `not clean` and burns a round."""
    weak = finding("WEAK", kind="test", file="tests/test_02.py")
    stronger = "def test_x():\n    assert '02' == 'x'  # stronger\n"
    correction = {**tw("02"), "files": {"tests/test_02.py": stronger}, "commit": "test: 02 stronger",
                  "only": ["tests/test_02.py"]}
    rig = Inside(tmp_path, three()).run(plan(**{
        "02/implementer": [CHEAT], "02/test-writer": [tw("02"), correction],
        "02/reviewer": [review("02", [weak, STUCK]), review("02")], "02/fixer": [fix("02", 1, only=["demo/export.py"])]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ticket 02:", "restored") == [RESTORED_01]
    (fixer,) = rig.calls("02", "fixer")
    assert fixer["status"] == "" and fixer["head"] == rig.calls("02", "test-writer")[1]["sha"]
    assert not [gate for gate in rig.folder("02").glob("gate-*.log") if "not clean" in gate.read_text()]
    assert rig.state()["tickets"]["02"]["round"] == 1 and on_pr(rig, "tests/test_01.py") == LOCKED_01
    # The correction is told which files the driver put back, so it may update one compatibly when it lists it.
    prompt = rig.prompt("test-writer-1", "02")
    assert "The driver put back these files at their locked versions" in prompt and "tests/test_01.py" in prompt


def subject(rig, commit):
    return subprocess.run(["git", "-C", str(rig.repo), "log", "-1", "--format=%s", commit], capture_output=True,
                          text=True).stdout.strip()


def test_the_fast_gate_gets_the_locked_files_of_its_ticket_and_the_full_gate_none(tmp_path):
    """Ticket 02 locks two files and merged ticket 01 locks tests/test_01.py: only 02's files reach its fast gate."""
    writer = tw("02")
    writer["files"]["tests/test_02b.py"] = "def test_x():\n    assert 2 == 'x'\n"
    writer["report"]["locked"] += ["tests/test_02b.py"]
    rig = Inside(tmp_path, three()).run(plan(**{"02/test-writer": [writer]}), parallel="1")
    assert rig.code == 0, rig.ledger()
    candidate = rig.sha("02", "implementer")
    assert [gate["locked"] for gate in rig.gates("fast") if gate["commit"] == candidate] == [
        "tests/test_02.py tests/test_02b.py"]
    assert rig.gates("full") and all(gate["locked"] is None for gate in rig.gates("full"))


def test_the_fixer_is_told_the_fast_gate_with_the_locked_files_of_its_ticket(tmp_path):
    writer = tw("02")
    writer["files"]["tests/test_02b.py"] = "def test_x():\n    assert 2 == 'x'\n"
    writer["report"]["locked"] += ["tests/test_02b.py"]
    proof = dict(test="tests/test_02b.py::test_x", file="demo/export.py", line=1,
                 red="FAILED tests/test_02b.py::test_x - AssertionError at demo/export.py:1")

    def proved(step):  # the full review proves each locked test file
        step["report"]["mutations"].append(proof)
        step["stdout"] += proof["red"] + "\n"
        return step

    rig = Inside(tmp_path, three()).run(plan(**{"02/test-writer": [writer], "02/fixer": [fix("02", 1)],
                                                "02/reviewer": [proved(review("02", [STUCK])), review("02")]}))
    assert rig.code == 0, rig.ledger()
    (fixer,) = rig.calls("02", "fixer")
    test_review.gate_rule(fixer["prompt"], f"export TIER=fast; {rig.rig / 'gate'}",
                           "tests/test_02.py tests/test_02b.py")
    test_review.no_full_gate(rig)


def test_the_restore_commit_gets_the_fast_gate_with_the_locked_files_and_the_restored_ones(tmp_path):
    weak = finding("WEAK", kind="test", file="tests/test_02.py")
    correction = {**tw("02"), "files": {"tests/test_02.py": "def test_x():\n    assert '02' == 'x'  # stronger\n"},
                  "commit": "test: 02 stronger", "only": ["tests/test_02.py"]}
    rig = Inside(tmp_path, three()).run(plan(**{
        "02/implementer": [CHEAT], "02/test-writer": [tw("02"), correction],
        "02/reviewer": [review("02", [weak]), review("02")]}))
    assert rig.code == 0, rig.ledger()
    (restore,) = [gate for gate in rig.gates("fast") if subject(rig, gate["commit"]) == RESTORE]
    assert restore["locked"] == "tests/test_02.py tests/test_01.py"
    assert (rig.folder("02") / f"gate-fast-{restore['commit'][:7]}.log").exists()
    assert not [gate for gate in rig.gates("full") if gate["commit"] == restore["commit"]]


def test_a_locked_path_with_whitespace_is_rejected_once_and_then_blocks(tmp_path):
    spaced = {**tw("01"), "files": {"tests/test 01.py": "def test_x():\n    assert 1 == 'x'\n"}}
    spaced["report"] = {**spaced["report"], "files": ["tests/test 01.py"], "locked": ["tests/test 01.py"]}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [spaced, spaced]}))
    error = ("locked path 'tests/test 01.py' contains whitespace: the fast gate gets the locked paths separated by "
             "spaces, so rename the file")
    assert blocked(rig) == f"Ticket 01: BLOCKED at the test-writer: {error}."
    first, second = rig.calls("01", "test-writer")
    assert f"The driver rejected the earlier attempt: {error}" in second["prompt"]
    assert not rig.calls("01", "implementer")


ADAPTED_01 = LOCKED_01 + "# adapted to the interface of 02\n"
ADAPTS = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": ADAPTED_01})
RESTORE = "implement-spec: restore the locked files before the correction"


def writes(nn, text, subject):
    """A test-writer correction that writes tests/test_<nn>.py and lists it."""
    return {**tw(nn), "files": {f"tests/test_{nn}.py": text}, "commit": subject}


def test_a_restored_locked_file_of_a_merged_ticket_that_fails_with_the_new_code_must_be_updated_by_the_correction(tmp_path):
    """Issue #90: ticket 02 changes an interface that the locked file of merged ticket 01 uses, and its implementer
    adapts that file. The review finds the change, the driver puts the file back in a commit and gates it: red. The
    correction gets that gate record, and a correction that leaves the file as it is is rejected."""
    leaves = writes("02", "def test_x():\n    assert '02' == 'x'  # stronger\n", "test: 02 stronger")
    updates = writes("01", ADAPTED_01, "test: 01 adapted to 02")
    updates["report"] = {**updates["report"], "locked": ["tests/test_01.py", "tests/test_02.py"]}  # both attempts' files
    rig = Inside(tmp_path, three(), gate={"subjects": [RESTORE]}).run(
        plan(**{"02/implementer": [ADAPTS], "02/test-writer": [tw("02"), leaves, updates]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ticket 02: critical finding of kind fixture from the driver at tests/test_01.py:1")
    assert not rig.calls("02", "fixer")
    (restore,) = [gate["commit"] for gate in rig.gates() if gate["exit"]]
    first, second = rig.calls("02", "test-writer")[1:]
    assert f".agent/implement/02/gate-fast-{restore[:7]}.log" in first["prompt"] and first["head"] == restore
    assert "the gate is red on the restore commit" in second["prompt"]  # the rejection of the first attempt
    assert on_pr(rig, "tests/test_01.py") == ADAPTED_01 and "tests/test_01.py" in rig.state()["tickets"]["02"]["locked"]


ACCENT = "tests/tést.py"


def accented(tmp_path, name, **steps):
    """Ticket 01 locks a test file whose name git prints quoted (core.quotePath) when nobody asks otherwise."""
    writer = tw("01")
    writer["files"][ACCENT] = "def test_y():\n    assert 1 == 'y'\n"
    writer["report"]["locked"] += [ACCENT]
    proofs = [proves("01", ACCENT, "test_y")] * 2
    return Inside(under(tmp_path, name), three()).run(plan(**{"01/test-writer": [writer], "01/reviewer": proofs, **steps}))


def test_a_locked_file_with_a_name_that_git_quotes_is_protected_at_review_for_its_ticket_and_a_later_one(tmp_path):
    """Review round 1, I2: the quoted form of the path matched nothing, so no check saw the file."""
    own = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", ACCENT: WEAK})
    rig = accented(tmp_path, "review", **{"01/implementer": [own], "01/fixer": [{}, {}]})
    assert rig.led(f"Ticket 01: critical finding of kind code from the driver at {ACCENT}:1: locked file changed: {ACCENT}."), rig.ledger()
    assert "BLOCKED at fix round 1" in blocked(rig)
    later = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", ACCENT: WEAK})
    rig = accented(tmp_path, "landing", **{"02/implementer": [later]})
    assert rig.state()["tickets"]["01"]["locked"] == ["tests/test_01.py", ACCENT], rig.ledger()
    assert rig.code == 0 and rig.led(f"Ticket 02: critical finding of kind fixture from the driver at {ACCENT}:1")
    assert "assert 1 == 'y'" in on_pr(rig, ACCENT)


# ---------------------------------------------------------------- a ticket that must change a merged ticket's locked file

MERGED_LOCKED = "02-merged-locked-file"
LANDING_RESTORE = "implement-spec: restore the locked files of merged tickets before the landing"


def touches_01(n):
    """Fixer `n` of 02, which also changes tests/test_01.py, the locked file of merged ticket 01."""
    return fix("02", n, files={"demo/export.py": f"def fn_02():\n    return 'fix{n}'\n",
                               "tests/test_01.py": LOCKED_01 + f"# fix {n}\n"})


def changed_at_cap(tmp_path, name, files=None, gate=None):
    """Ticket 02 changes a merged ticket's locked file in its second fix. The finding is carried to the final
    must-fix list, while the landing still requires a ruling about that file."""
    steps = plan(**{"02/reviewer": [review("02", [STUCK])] * 2 + [review("02")],
                    "02/fixer": [fix("02", 1), touches_01(2)]})
    rig = Inside(under(tmp_path, name), files or three(), gate=gate).run(steps, parallel="1")
    assert rig.code == 2 and asked(rig) == {f"{MERGED_LOCKED}-1": ["restore", "adopt", "redesign"]}, rig.ledger()
    return rig, steps


def test_an_accepted_change_to_a_merged_ticket_locked_file_asks_at_the_landing_and_restore_lands_it(tmp_path):
    """Issue #116, comment 1: `accept` sent the ticket to its landing, which the landing check refused with no
    question, so no answer could merge it."""
    rig, steps = changed_at_cap(tmp_path, "restore")
    assert rig.code == 2 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.ledger()
    assert rig.led("Ticket 02: BLOCKED at the merge: locked file changed: tests/test_01.py.")
    assert asked(rig) == {f"{MERGED_LOCKED}-1": ["restore", "adopt", "redesign"]}
    question = [q for q in rig.state()["run"]["questions"] if q["answer"] is None][0]
    assert question["recommended"] == "restore" and question["subject"] == "tests/test_01.py"
    rig.run(steps, "--answer", f"{MERGED_LOCKED}-1=restore", parallel="1")
    assert rig.code == 0, rig.ledger()
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01 and LANDING_RESTORE in rig.pr_subjects()
    assert rig.led("Ticket 02: by the ruling (restore), the driver put back tests/test_01.py")
    assert rig.state()["tickets"]["02"]["phase"] == "merged"


def test_adopt_lands_the_change_to_the_merged_ticket_locked_file_and_the_ticket_locks_it(tmp_path):
    rig, steps = changed_at_cap(tmp_path, "adopt")
    rig.run(steps, "--answer", f"{MERGED_LOCKED}-1=adopt", parallel="1")
    assert rig.code == 0, rig.ledger()
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01 + "# fix 2\n"
    assert "tests/test_01.py" in rig.state()["tickets"]["02"]["locked"] and rig.led("by the ruling (adopt)")


def test_redesign_keeps_the_ticket_blocked_at_its_landing(tmp_path):
    rig, steps = changed_at_cap(tmp_path, "redesign")
    rig.run(steps, "--answer", f"{MERGED_LOCKED}-1=redesign", parallel="1")
    assert rig.code == 1 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.ledger()
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01 and not asked(rig)


def test_a_restore_that_turns_the_merge_gate_red_is_taken_back_and_asks_again_without_restore(tmp_path):
    """The ticket's code needs its change to the file: the restore commit leaves the ticket branch and the question
    comes back with the options left, so the stop still has an answer."""
    two = {name: text for name, text in three().items() if not name.startswith("03")}  # 02 lands as a fast-forward
    rig, steps = changed_at_cap(tmp_path, "red", files=two, gate={"subjects": [LANDING_RESTORE]})
    rig.run(steps, "--answer", f"{MERGED_LOCKED}-1=restore", parallel="1")
    assert rig.code == 2 and rig.led("Ticket 02: BLOCKED at the merge: the full gate is red on"), rig.ledger()
    assert asked(rig) == {f"{MERGED_LOCKED}-2": ["adopt", "redesign"]}
    question = [q for q in rig.state()["run"]["questions"] if q["answer"] is None][0]
    assert question["recommended"] == "adopt" and question["evidence"][0].endswith(".log")
    fixer = rig.calls("02", "fixer")[-1]["sha"]
    assert rig.state()["tickets"]["02"]["tip"] == fixer and on_pr(rig, "tests/test_01.py") == LOCKED_01
    rig.run(steps, "--answer", f"{MERGED_LOCKED}-2=adopt", parallel="1")
    assert rig.code == 0 and on_pr(rig, "tests/test_01.py") == LOCKED_01 + "# fix 2\n", rig.ledger()


# ---------------------------------------------------------------- the rounds

def test_two_rounds_then_blocked_with_the_escalation_at_round_2(tmp_path):
    rig = Inside(tmp_path, three(), {"implementer": [OPUS, FABLE]})
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK])] * 3, "01/fixer": [fix("01", n) for n in (1, 2)]}))
    assert rig.code == 1 and rig.state()["tickets"]["01"]["round"] == 2
    assert rig.led("BLOCKED at the review: after round 2 these findings are open: STUCK (demo/export.py:99).")
    assert rig.ledger().count(f"Ticket 01 round 2: escalated to {FABLE}.") == 1
    assert [len(rig.led("fixer ran on", entry)) for entry in (OPUS, FABLE)] == [1, 1]
    tips = [rig.sha("01", "implementer")] + [call["sha"] for call in rig.calls("01", "fixer")]
    assert [gate["commit"] for gate in rig.gates() if gate["branch"] == "ticket/feat%2Fcsv/01"] == tips
    assert rig.state()["tickets"]["01"]["tip"] == tips[-1] and not rig.calls("02")
    assert rig.state()["tickets"]["03"]["phase"] == "merged"


def test_a_fixer_with_nothing_to_change_is_accepted_and_the_same_tip_is_reviewed_again(tmp_path):
    """Issue 123: a fixer whose finding needs no change reports no commit. That is no rerun: the round counts, the
    gate of the tip stands, and the re-review weighs the fixer's reason."""
    rig = Inside(tmp_path)
    nothing = {"report": {"status": "DONE_WITH_CONCERNS", "commits": [], "files": [], "green": ["1 passed"],
                          "ticket": "01", "concerns": ["STUCK: the code already handles it"]}}
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK]), review("01")], "01/fixer": [nothing]}))
    assert rig.code == 0, rig.ledger()
    assert len(rig.calls("01", "fixer")) == 1 and rig.led("fixer ran on", "report accepted")
    assert rig.state()["tickets"]["01"]["round"] == 1 and rig.state()["tickets"]["01"]["phase"] == "merged"
    tip = rig.sha("01", "implementer")
    assert [gate["commit"] for gate in rig.gates("fast")].count(tip) == 1
    again = rig.prompt("reviewer-1")
    assert f"The fix is {tip}..{tip}" in again and "The fixer of round 1 changed nothing" in again
    assert ".agent/implement/01/fixer-1.json" in again and "no concern names" not in rig.prompt("fixer-1")
    assert "list no commit" in rig.prompt("fixer-1")


def test_a_red_gate_on_a_fixer_commit_starts_the_next_round_with_the_record_as_evidence(tmp_path):
    """Issue 64: the gate of a fix round is red. That is a finding for the next round, not a BLOCKED ticket."""
    rig = Inside(tmp_path, gate={"subjects": ["fix: 01 round 1"]})
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK], lines=(2,)), review("01", lines=(2,))],
                    "01/fixer": [fix("01", 1), fix("01", 2, files={"demo/other.py": "x = 1\n"})]}))
    assert rig.code == 0, rig.ledger()
    assert [call["role"] for call in rig.calls("01")] == ["test-writer", "implementer", "reviewer", "fixer", "fixer",
                                                         "reviewer"]
    first, second = (call["sha"] for call in rig.calls("01", "fixer"))
    record = f".agent/implement/01/gate-fast-{first[:7]}.log"
    (gate,) = json.loads((rig.folder() / "findings-1.json").read_text())
    assert (gate["source"], gate["severity"], gate["kind"]) == ("gate", "critical", "code")
    assert gate["title"] == f"the fast gate is red on {first[:7]}" and record in gate["evidence"]
    assert rig.led(f"Ticket 01: critical finding of kind code from the gate at {record}:0: the fast gate is red on {first[:7]}.")
    assert "findings-1.json" in rig.prompt("fixer-2") and rig.state()["tickets"]["01"]["round"] == 2
    # No review saw the red tip: the re-review covers both fixes, and the proof that round 1 made due stays due.
    again = rig.prompt("reviewer-2")
    assert f"The fix is {rig.sha('01', 'implementer')}..{second}" in again and "demo/export.py:2" in again


def test_a_red_gate_on_the_implementer_commit_starts_fix_round_1_with_the_record_as_evidence(tmp_path):
    """Issue 80: the gate of the implementer commit is red. That is a finding for round 1, not a BLOCKED ticket."""
    rig = Inside(tmp_path, gate={"subjects": ["feat: 01 rows"]})
    rig.run(plan(**{"01/reviewer": [review("01")], "01/fixer": [fix("01", 1)]}))
    assert rig.code == 0, rig.ledger()
    assert [call["role"] for call in rig.calls("01")] == ["test-writer", "implementer", "fixer", "reviewer"]
    tip = rig.sha("01", "implementer")
    record = f".agent/implement/01/gate-fast-{tip[:7]}.log"
    (gate,) = json.loads((rig.folder() / "findings-0.json").read_text())
    assert (gate["source"], gate["severity"], gate["kind"]) == ("gate", "critical", "code")
    assert gate["title"] == f"the fast gate is red on {tip[:7]}" and record in gate["evidence"]
    assert "findings-0.json" in rig.prompt("fixer-1") and rig.state()["tickets"]["01"]["phase"] == "merged"
    assert "The fix is" not in rig.prompt("reviewer-1")  # no review saw the implementer commit: a full review
    assert [gate["commit"] for gate in rig.gates()].count(tip) == 1 and not rig.led("killed by signal")


# ---------------------------------------------------------------- a red full gate at landing

def full_red(rig, commit):
    """The exit of each full gate that the rig's gate logged on `commit`."""
    return [gate["exit"] for gate in rig.gates("full") if gate["commit"] == commit]


def test_a_red_full_gate_at_landing_starts_round_1_and_the_fix_gets_a_fast_gate_a_re_review_and_a_full_gate(tmp_path):
    rig = Inside(tmp_path, gate={"full": {"subjects": ["feat: 01 rows"]}})
    rig.run(plan(**{"01/fixer": [fix("01", 1)]}))
    assert rig.code == 0, rig.ledger()
    assert [call["role"] for call in rig.calls("01")] == ["test-writer", "implementer", "reviewer", "fixer", "reviewer"]
    tip, fixed = rig.sha("01", "implementer"), rig.sha("01", "fixer")
    record = f".agent/implement/01/gate-full-{tip[:7]}.log"
    assert full_red(rig, tip) == [1] and full_red(rig, fixed) == [0]
    assert [gate["commit"] for gate in rig.gates("fast")] == [tip, fixed]
    # The PR branch did not move to the red tip: the fixer started with the PR branch at the spec commit.
    fixer_call = rig.calls("01", "fixer")[0]
    assert "feat: 01 rows" not in fixer_call["pr_log"] and fixer_call["branch"] == "ticket/feat%2Fcsv/01"
    *review_findings, gate = json.loads((rig.folder() / "findings-0.json").read_text())
    assert (gate["source"], gate["severity"], gate["kind"], gate["file"]) == ("gate", "critical", "code", record)
    assert gate["title"] == f"the full gate is red on {tip[:7]}"
    assert rig.led(f"Ticket 01: the full gate is red on {tip[:7]} at the landing, so feat/csv does not move and fix "
                   "round 1 starts.")
    prompt = rig.prompt("fixer-1")
    assert "findings-0.json" in prompt and record in prompt and "not on ticket/feat%2Fcsv/01" not in prompt
    assert f"The fix is {tip}..{fixed}" in rig.prompt("reviewer-1")
    entry = rig.state()["tickets"]["01"]
    assert (entry["round"], entry["phase"], entry["landing"]) == (1, "merged", None)


def test_a_red_full_gate_on_a_merge_commit_sends_the_fixer_to_the_ticket_branch_with_the_merge_commit(tmp_path):
    files = {"01-export-rows.md": ticket("01", "Export rows"),
             "02-quote-fields.md": ticket("02", "Quote fields", touches="`demo/other.py: g`")}
    rig = Inside(tmp_path, files, gate={"full": {"subjects": ["feat: 02 rows"]}})
    rig.run(plan(**{"02/implementer": [impl("02", "demo/other.py", wait_pr_moves=True)],
                    "02/fixer": [fix("02", 1, files={"demo/other.py": "x = 1\n"})]}))
    assert rig.code == 0, "\n".join(rig.ledger())
    tip = rig.sha("02", "implementer")
    (merge,) = [gate["commit"] for gate in rig.gates("full") if gate["exit"]]
    assert merge != tip and tip not in [gate["commit"] for gate in rig.gates("full")]
    fixer_call = rig.calls("02", "fixer")[0]
    assert (fixer_call["branch"], fixer_call["head"]) == ("ticket/feat%2Fcsv/02", tip)
    prompt = rig.prompt("fixer-1", "02")
    assert f"{merge[:7]} is not on ticket/feat%2Fcsv/02: it is the merge commit" in prompt
    assert f"implement/02/gate-full-{merge[:7]}.log" in prompt and "git merge --no-commit --no-ff feat/csv" in prompt
    assert rig.state()["tickets"]["02"]["phase"] == "merged"


def test_a_red_full_gate_at_landing_after_round_2_blocks_the_ticket_and_its_dependent(tmp_path):
    rig = Inside(tmp_path, three(), gate={"full": {"subjects": ["fix: 01 round 2"]}})
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK]), review("01", [STUCK]), review("01")],
                    "01/fixer": [fix("01", n) for n in (1, 2)]}))
    (red,) = [gate["commit"] for gate in rig.gates("full") if gate["exit"]]  # 03 may land first: then a merge commit
    assert f"BLOCKED at the merge: the full gate is red on {red[:7]}" in blocked(rig), rig.ledger()
    assert len(rig.calls("01", "fixer")) == 2 and rig.state()["tickets"]["01"]["round"] == 2
    assert "fix: 01 round 2" not in rig.pr_subjects() and "Ticket 01: merged" not in "".join(rig.ledger())
    assert not rig.calls("02") and rig.state()["tickets"]["02"]["phase"] == "pending"
    assert rig.state()["tickets"]["03"]["phase"] == "merged"


def test_a_run_stopped_after_a_red_full_gate_at_landing_resumes_with_the_fixer(tmp_path, monkeypatch):
    rig = Inside(tmp_path, gate={"full": {"subjects": ["feat: 01 rows"]}})
    real = driver_fixloop.red_landing

    def stop(*args):
        try:
            real(*args)
        except driver_fixloop.Unlanded:
            raise driver_run.Halted("stopped right after the red landing") from None

    monkeypatch.setattr(driver_fixloop, "red_landing", stop)
    steps = plan(**{"01/fixer": [fix("01", 1)]})
    rig.run(steps)
    assert rig.code == 4 and not rig.calls("01", "fixer"), rig.ledger()
    entry, tip = rig.state()["tickets"]["01"], rig.sha("01", "implementer")
    assert (entry["phase"], entry["round"], entry["landing"]) == ("fix", 1, tip)
    monkeypatch.setattr(driver_fixloop, "red_landing", real)
    rig.run(steps)
    assert rig.code == 0, rig.ledger()
    roles = [call["role"] for call in rig.calls("01")]
    assert roles == ["test-writer", "implementer", "reviewer", "fixer", "reviewer"]
    assert full_red(rig, tip) == [1] and rig.state()["tickets"]["01"]["round"] == 1


def killed(rig, subject, times, end="os.kill(os.getpid(), signal.SIGTERM)"):
    """The rig's gate, ended by `end` (SIGTERM by default) on its first `times` runs on the commit with `subject`, as a
    `pkill -f` of another slot ends it (issue 101). A killed run logs nothing into `<rig>/gates/`."""
    gate, real = rig.rig / "gate", rig.rig / "real-gate"
    gate.rename(real)
    gate.write_text(f"""#!{sys.executable}
import os, signal, subprocess, sys
from pathlib import Path
kills = Path(__file__).with_name("kills")
subject = subprocess.run(["git", "log", "-1", "--format=%s"], capture_output=True, text=True).stdout.strip()
if subject == {subject!r} and len(kills.read_text() if kills.exists() else "") < {times}:
    with kills.open("a") as file:
        file.write("x")
    {end}
os.execv({str(real)!r}, [{str(real)!r}])
""")
    gate.chmod(0o755)
    return rig


# How the gate's shell reports a SIGTERM of the command: `exec` replaces the shell, so the exit is -15; a shell that
# outlives its child, as Ubuntu's dash does even for one simple command, exits 128+15.
SHAPES = {"exec": ("exec {gate}", -15), "compound": ("{gate}; exit $?", 143)}


def shaped(tmp_path, command):
    rig = Inside(tmp_path)
    rig.command = command
    return rig


@pytest.mark.parametrize("command, code", SHAPES.values(), ids=SHAPES)
def test_a_gate_killed_by_a_signal_runs_once_more_and_its_green_second_run_counts(tmp_path, command, code):
    rig = killed(shaped(tmp_path, command), "feat: 01 rows", 1).run(plan(**{"01/reviewer": [review("01")]}))
    assert rig.code == 0, rig.ledger()
    tip = rig.sha("01", "implementer")
    assert [call["role"] for call in rig.calls("01")] == ["test-writer", "implementer", "reviewer"]
    assert rig.led("killed by signal") == [f"Ticket 01: the fast gate of {tip[:7]} was killed by signal 15 and runs again."]
    assert rig.led(f"Ticket 01: the fast gate is green on {tip[:7]}") and not rig.led("finding of kind")
    assert json.loads((rig.folder() / "findings-0.json").read_text()) == []  # the review's, with no gate finding
    record = (rig.folder() / f"gate-fast-{tip[:7]}.log").read_text()
    assert record.count("exit: ") == 1 and "\nexit: 0\nafter: clean\n" in record  # the second run overwrote it


@pytest.mark.parametrize("command, code", SHAPES.values(), ids=SHAPES)
def test_a_gate_killed_by_a_signal_twice_is_red(tmp_path, command, code):
    rig = killed(shaped(tmp_path, command), "feat: 01 rows", 2)
    rig.run(plan(**{"01/reviewer": [review("01")], "01/fixer": [fix("01", 1)]}))
    assert rig.code == 0, rig.ledger()
    tip = rig.sha("01", "implementer")
    assert [call["role"] for call in rig.calls("01")] == ["test-writer", "implementer", "fixer", "reviewer"]
    assert len(rig.led("killed by signal")) == 1 and tip not in [gate["commit"] for gate in rig.gates()]
    (gate,) = json.loads((rig.folder() / "findings-0.json").read_text())
    assert gate["title"] == f"the fast gate is red on {tip[:7]}"
    assert f"\nexit: {code}\nafter: clean\n" in (rig.folder() / f"gate-fast-{tip[:7]}.log").read_text()


@pytest.mark.parametrize("exit", [1, 142])  # 142 is 128+SIGALRM: a signal, but not one that kills a gate from outside
def test_a_gate_that_exits_with_any_other_code_is_red_without_running_again(tmp_path, exit):
    rig = killed(shaped(tmp_path, "{gate}; exit $?"), "feat: 01 rows", 1, end=f"sys.exit({exit})")
    rig.run(plan(**{"01/reviewer": [review("01")], "01/fixer": [fix("01", 1)]}))
    assert rig.code == 0, rig.ledger()
    tip = rig.sha("01", "implementer")
    assert [call["role"] for call in rig.calls("01")] == ["test-writer", "implementer", "fixer", "reviewer"]
    assert not rig.led("killed by signal") and tip not in [gate["commit"] for gate in rig.gates()]
    record = (rig.folder() / f"gate-fast-{tip[:7]}.log").read_text()
    assert record.count("exit: ") == 1 and f"\nexit: {exit}\nafter: clean\n" in record


def test_a_red_gate_on_the_fixer_commit_of_round_2_blocks_the_ticket(tmp_path):
    rig = Inside(tmp_path, gate={"subjects": ["fix: 01 round 2"]})
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK])] * 2, "01/fixer": [fix("01", n) for n in (1, 2)]}))
    last = rig.calls("01", "fixer")[-1]["sha"]
    assert f"BLOCKED at the gate: the fast gate is red on {last[:7]}" in blocked(rig)
    assert len(rig.calls("01", "reviewer")) == 2 and not (rig.folder() / "findings-2.json").exists()


def test_one_implementer_entry_is_invalid_before_agents_start(tmp_path):
    rig = Inside(tmp_path, ONE, {"implementer": [OPUS]})
    rig.models.write_text(json.dumps({**json.loads(rig.models.read_text()), "implementer": [OPUS]}))
    rig.run(plan())
    assert rig.code == 3 and not rig.calls()


def test_the_escalation_takes_the_next_entry_of_the_list():
    models = {"implementer": ["a@low", "openai/b@low", "c@low"]}
    assert [driver_fixloop.entry(models, n) for n in (1, 2)] == ["a@low", "openai/b@low"]


def test_a_fixer_that_does_not_finish_blocks_the_ticket_in_its_round(tmp_path):
    """A fixer that reports BLOCKED asks a `fixer-stopped` question: test_decisions covers it."""
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [review("01", [STUCK])], "01/fixer": [{}, {}]}))
    entry = rig.state()["tickets"]["01"]
    error = "BLOCKED at fix round 1: report .agent/implement/01/fixer-1.json is not"
    assert error in blocked(rig) and entry["round"] == 1 and entry["tip"] == rig.sha("01", "implementer")


# ---------------------------------------------------------------- the reviewers' reports and the slot

def test_a_first_review_proves_each_locked_file_and_a_second_bad_report_blocks(tmp_path):
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [review("01", lines=())] * 2}))
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    assert rig.led("BLOCKED at the review: the reviewer: mutations: no mutation proof for the locked test file "
                   "tests/test_01.py.")
    assert [q["id"] for q in rig.state()["run"]["questions"]] == ["01-review-rejected-1"]
    assert "feat: 01 rows" not in rig.pr_subjects()


def test_a_full_review_after_a_correction_names_each_locked_file_that_needs_a_proof(tmp_path):
    """Issue #92: the full review after a correction in a later round must prove each locked file, not only round 1."""
    two = ("tests/test_01.py", "tests/test_01b.py")
    first = {**tw("01"), "files": {path: "def test_x():\n    assert 0\n" for path in two}}
    first["report"] = {**first["report"], "files": list(two), "locked": list(two)}
    proofs = [dict(test=f"{path}::test_x", file="demo/export.py", line=1, red=f"FAILED {path}::test_x") for path in two]
    both = lambda found=(): {"stdout": "".join(p["red"] + "\n" for p in proofs),
                             "report": {**review("01", found)["report"], "mutations": proofs}}
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    correction = {**first, "files": {"tests/test_01.py": "# stronger\n"}, "commit": "test: 01 stronger test"}
    correction["report"] = {**first["report"], "files": ["tests/test_01.py"], "locked": ["tests/test_01.py"]}
    regression = review("01", lines=())
    regression["report"]["regressions"] = [vacuous]
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [first, correction], "01/fixer": [fix("01", 1)],
                                       "01/reviewer": [both([STUCK]), regression, both()]}))
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "fixer", "reviewer", "test-writer", "reviewer"]
    due = "give one mutation proof or more for each of these locked test files: tests/test_01.py tests/test_01b.py\n"
    initial, again, full = (call["prompt"] for call in rig.calls("01", "reviewer"))
    assert due in initial and due in full, full
    assert "In round 1" not in full and due not in again


def test_a_full_review_needs_no_proof_for_a_locked_fixture_that_holds_no_test(tmp_path):
    """Issue #118: a fake `gh` the test-writer locks holds no test, so no `<locked file>::<test name>` can name it."""
    first = {**tw("01"), "files": {"tests/test_01.py": "def test_x():\n    assert 0\n", "tests/fakes/gh": "exit 0\n"}}
    first["report"] = {**first["report"], "files": ["tests/test_01.py", "tests/fakes/gh"],
                       "locked": ["tests/test_01.py", "tests/fakes/gh"], "fixtures": ["tests/fakes/gh"]}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [first], "01/reviewer": [review("01")]}))
    assert rig.code == 0 and len(rig.calls("01", "reviewer")) == 1, rig.ledger()
    assert rig.state()["tickets"]["01"]["fixtures"] == ["tests/fakes/gh"]
    assert "for each of these locked test files: tests/test_01.py\n" in rig.calls("01", "reviewer")[0]["prompt"]


def test_a_fixture_must_be_a_locked_file_and_a_correction_that_relists_it_decides_again(tmp_path):
    report = dict(status="NEEDS_CONTEXT", commits=[], files=[], concerns=[], locked=["a"], red=[], fixtures=["gh"])
    path = tmp_path / "test-writer-0.json"
    path.write_text(json.dumps(report))
    context = {"repo": ".", "branch": "HEAD", "start": "HEAD", "log": str(path)}
    assert "fixtures: gh is not in `locked`" in driver_report.validate("test-writer", str(path), context)
    entry = {"test_writer_sha": None, "locked": ["tests/a.py", "gh", "fake"], "held": {}, "fixtures": ["gh", "fake"]}
    assert driver_review.written(".", entry, ["gh"], [], "tip")["fixtures"] == ["fake"]


def test_the_locked_file_rule_is_for_the_first_review_only(tmp_path):
    report = dict(status="DONE", commits=[], files=[], concerns=[], findings=[], mutations=[])
    path = tmp_path / "reviewer-0.json"
    path.write_text(json.dumps(report))
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    for name in ("a", "b"):
        (repo / f"tests/{name}.py").write_text("def test_x():\n    pass\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "tests"], check=True)
    context = {"repo": str(repo), "branch": "main", "start": "HEAD", "log": str(path),
               "locked": ["tests/a.py", "tests/b.py"]}
    assert driver_report.validate("reviewer", str(path), context) == []
    assert driver_report.validate("reviewer", str(path), {**context, "full": True}) == [
        "mutations: no mutation proof for the locked test file tests/a.py",
        "mutations: no mutation proof for the locked test file tests/b.py"]
    assert driver_report.validate("correctness", str(path), context) == []


def test_a_timed_out_read_only_role_is_restored_only_when_no_other_agent_is_in_the_slot(tmp_path):
    rig = Inside(tmp_path)
    tip = subprocess.run(["git", "-C", str(rig.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (rig.repo / "junk.txt").write_text("x\n")
    context, shared = {"repo": str(rig.repo), "branch": "feat/csv", "start": tip}, lambda: False
    assert "first revert each edit of the earlier attempt" in driver_run._timeout_lines("correctness", 30, context, shared)
    assert (rig.repo / "junk.txt").exists()
    assert "in the tree: continue from it" in driver_run._timeout_lines("fixer", 60, context, shared) and (rig.repo / "junk.txt").exists()
    # `--only reviewer`: one agent
    assert f"restored the worktree to {tip}" in driver_run._timeout_lines("reviewer", 45, context, lambda: True)
    assert not (rig.repo / "junk.txt").exists()


DIRTY = [({"files": {"junk.txt": "x\n", "demo/export.py": "mutated\n"}},
          "the tree is not as the review found it: demo/export.py, junk.txt"),
         ({"git": [["checkout", "-q", "--detach"]]}, "the slot is no longer on ticket/feat%2Fcsv/01 at "),
         ({"commit": "sneak", "files": {"demo/export.py": "x = 2\n"}}, "the slot is no longer on ticket/feat%2Fcsv/01 at ")]


def slot_state(rig):
    git = lambda *args: subprocess.run(["git", "-C", str(rig.repo), *args], capture_output=True, text=True).stdout.strip()
    return git("status", "--porcelain"), git("rev-parse", "ticket/feat%2Fcsv/01")


@pytest.mark.parametrize("step, left", DIRTY)
def test_a_reviewer_that_changes_the_slot_is_run_once_more_on_the_restored_tip(tmp_path, step, left):
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [{**review("01"), **step}, review("01")]}))
    assert rig.code == 0, rig.ledger()
    tip = rig.sha("01", "implementer")
    (line,) = rig.led("Ticket 01: a read-only role broke its rule:")
    assert left in line and line.endswith(f"the driver restored the slot to {tip[:7]} and runs the review again.")
    first, second = rig.calls("01", "reviewer")
    assert (second["branch"], second["head"], second["status"]) == ("ticket/feat%2Fcsv/01", tip, "")
    assert rig.tip() == tip and not (rig.repo / "junk.txt").exists()


def test_a_second_review_that_changes_the_slot_blocks_the_ticket_with_the_slot_clean(tmp_path):
    step, left = DIRTY[0]
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [{**review("01"), **step}] * 3}))
    assert f"BLOCKED at the review: {left}." in blocked(rig)
    assert len(rig.calls("01", "reviewer")) == 2 and len(rig.led("a read-only role broke its rule:")) == 2
    assert slot_state(rig) == ("", rig.sha("01", "implementer")) and "feat: 01 rows" not in rig.pr_subjects()


def test_a_reviewer_that_writes_the_shared_git_config_gets_it_restored_and_runs_once_more(tmp_path):
    # Issue #94: a reviewer ran git with GIT_DIR on the real git dir and left core.worktree on a deleted temp dir.
    rig = Inside(tmp_path)
    shared = rig.repo / ".git" / "config"
    config = shared.read_bytes()
    gone = {"git": [["config", "core.worktree", str(tmp_path / "gone")]]}
    rig.run(plan(**{"01/reviewer": [{**review("01"), **gone}, review("01")]}))
    assert rig.code == 0, rig.ledger()
    assert shared.read_bytes() == config
    (line,) = rig.led("Ticket 01: a read-only role broke its rule:")
    assert f"the shared git config {shared.resolve()} changed" in line
    assert line.endswith("the driver restored the shared git config and runs the review again.")
    tip, prompt = rig.sha("01", "implementer"), rig.prompt("reviewer-0")
    assert len(rig.calls("01", "reviewer")) == 2 and rig.tip() == tip
    assert f".agent/implement/01/gate-fast-{tip[:7]}.log" in prompt and "never write with `git config`" in prompt


def test_a_branch_entry_that_another_worktree_writes_during_the_review_is_no_rule_break_and_stays(tmp_path):
    # Issue #130: a `git worktree add -b x origin/main` of another session writes [branch "x"] into the shared config.
    rig = Inside(tmp_path)
    shared = rig.repo / ".git" / "config"
    # The rig has no remote, so only the merge key: the unit tests below cover a remote key.
    track = {"git": [["config", "branch.x.merge", "refs/heads/main"]]}
    rig.run(plan(**{"01/reviewer": [{**review("01"), **track}]}))
    assert rig.code == 0, rig.ledger()
    assert not rig.led("a read-only role broke its rule:") and len(rig.calls("01", "reviewer")) == 1
    assert '[branch "x"]' in shared.read_text()


def config_values(config, key):
    return subprocess.run(["git", "config", "--file", str(config), "--get-all", key], capture_output=True, text=True).stdout


@pytest.mark.parametrize("key, value", [("core.hooksPath", "/tmp/hooks"), ("alias.st", "!rm -rf ."),
                                        ("include.path", "/tmp/evil"), ("remote.origin.url", "/tmp/evil")])
def test_a_dangerous_key_is_restored_and_the_other_sessions_branch_entry_stays(tmp_path, key, value):
    rig = Inside(tmp_path)
    shared = rig.repo / ".git" / "config"
    before = config_values(shared, key)
    both = {"git": [["config", key, value], ["config", "branch.x.merge", "refs/heads/main"]]}
    rig.run(plan(**{"01/reviewer": [{**review("01"), **both}, review("01")]}))
    assert rig.code == 0, rig.ledger()
    (line,) = rig.led("Ticket 01: a read-only role broke its rule:")
    assert f"the shared git config {shared.resolve()} changed: {key.lower()}" in line
    assert config_values(shared, key) == before and config_values(shared, "branch.x.merge") == "refs/heads/main\n"


def tracked_repo(tmp_path):
    """A repository with an origin remote and a main branch that tracks it, and its shared config."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("remote.origin.url", "/tmp/origin"), ("branch.main.remote", "origin"),
                       ("branch.main.merge", "refs/heads/main")):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    return repo, repo / ".git" / "config"


def set_key(repo, *args):
    subprocess.run(["git", "-C", str(repo), "config", *args], check=True)


def test_a_new_branch_that_tracks_a_configured_remote_is_no_change(tmp_path):
    repo, config = tracked_repo(tmp_path)
    before = driver_review.shared_config(str(repo))
    set_key(repo, "branch.y.remote", "origin")
    set_key(repo, "branch.y.merge", "refs/heads/main")
    assert driver_review.restore_config(before) == ""
    assert config_values(config, "branch.y.merge") == "refs/heads/main\n"


@pytest.mark.parametrize("key, value", [("branch.x.remote", "/tmp/evil"), ("branch.x.remote", "nowhere"),
                                        ("branch.x.pushremote", "origin"), ("branch.x.merge", "HEAD"),
                                        ("branch.main.merge", "refs/heads/evil"), ("branch.main.rebase", "true")])
def test_a_branch_key_that_no_worktree_add_writes_is_flagged_and_restored(tmp_path, key, value):
    repo, config = tracked_repo(tmp_path)
    before = driver_review.shared_config(str(repo))
    set_key(repo, key, value)
    assert driver_review.restore_config(before) == f"the shared git config {config} changed: {key}"
    assert config.read_bytes() == before[1]


def test_a_branch_key_next_to_an_existing_one_is_flagged(tmp_path):
    repo, config = tracked_repo(tmp_path)
    set_key(repo, "branch.x.merge", "refs/heads/main")
    before = driver_review.shared_config(str(repo))
    set_key(repo, "branch.x.remote", "origin")
    assert driver_review.restore_config(before).endswith("changed: branch.x.remote")
    assert config_values(config, "branch.x.remote") == ""


def test_a_held_config_lock_falls_back_to_the_whole_file_and_is_verified(tmp_path):
    repo, config = tracked_repo(tmp_path)
    before = driver_review.shared_config(str(repo))
    mode = config.stat().st_mode
    set_key(repo, "core.hooksPath", "/tmp/hooks")
    config.with_name("config.lock").write_text("")
    assert driver_review.restore_config(before).endswith("changed: core.hookspath")
    assert config.read_bytes() == before[1] and config.stat().st_mode == mode


def test_a_config_the_driver_cannot_write_back_raises(tmp_path):
    repo, config = tracked_repo(tmp_path)
    before = driver_review.shared_config(str(repo))
    set_key(repo, "core.hooksPath", "/tmp/hooks")
    config.with_name("config.lock").write_text("")
    os.chmod(config.parent, 0o555)
    try:
        with pytest.raises(driver_review.Unrestored, match="core.hookspath"):
            driver_review.restore_config(before)
    finally:
        os.chmod(config.parent, 0o755)


@pytest.mark.parametrize("change", [["--unset", "x.flag"], ["x.flag", "false"], ["--add", "x.flag", "true"]])
def test_a_valueless_key_goes_back_valueless(tmp_path, change):
    repo, config = tracked_repo(tmp_path)
    with config.open("a") as text:
        text.write("[x]\n\tflag\n")
    before = driver_review.shared_config(str(repo))
    assert before[2]["x.flag"] == [None]
    set_key(repo, *change)
    assert driver_review.restore_config(before).endswith("changed: x.flag")
    assert config.read_bytes() == before[1]


def test_a_reviewer_that_times_out_starts_again_from_the_clean_tip(tmp_path):
    hang = {"files": {"demo/export.py": "mutated\n", "junk.txt": "x\n"}, "sleep": 60}
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [hang, review("01")]}), "--timeout", "reviewer=0.03")
    assert rig.code == 0, rig.ledger()
    first, second = rig.calls("01", "reviewer")
    assert second["status"] == "" and second["head"] == rig.sha("01", "implementer")
    line = f"The driver restored the worktree to {second['head']}: start again from there."
    assert "timed out after 0.03 minutes" in second["prompt"] and line in second["prompt"]
    assert "in the tree" not in second["prompt"] and second["prompt"].rstrip().endswith("one ref store.")


# ---------------------------------------------------------------- mutation proofs

def test_a_re_review_names_only_the_proofs_the_fix_made_due(tmp_path):
    first = test_review.edit(test_review.LONG, l12="v12 = 'a'", l30="v30 = 'a'")
    rig = test_review.scoped(tmp_path, (12, 30), [first, test_review.edit(first, l30="v30 = 'b'")], (30,))
    assert rig.code == 0, rig.ledger()
    assert "no proof is due): demo/export.py:12 demo/export.py:30\n" in rig.prompt("reviewer-1")
    assert "no proof is due): demo/export.py:30\n" in rig.prompt("reviewer-2")
    old, new = rig.sha("01", "implementer"), rig.calls("01", "fixer")[0]["sha"]
    assert f"The fix is {old}..{new}: check only whether your own critical or important findings" in rig.prompt("reviewer-1")


def test_a_proof_follows_its_line_through_inserts_and_deletes(tmp_path):
    lines = test_review.LONG.splitlines(keepends=True)
    moved = "".join(lines[:19] + ["i1 = 1\n", "i2 = 2\n"] + lines[19:29] + ["v30 = 'changed'\n"] + lines[30:])
    rig = test_review.scoped(tmp_path, (12, 30, 35), [moved], (32,))
    assert rig.code == 0, rig.ledger()
    assert "no proof is due): demo/export.py:32\n" in rig.prompt("reviewer-1")
    # (first old line, old count, first new line, new count): lines 3-4 deleted, 10-11 replaced by one line.
    hunks = [(3, 2, 2, 0), (10, 2, 8, 1), (20, 0, 18, 3)]
    assert [driver_review._follow(line, hunks) for line in (2, 3, 4, 5, 10, 11, 12, 20, 21)] == [
        (2, False), (2, True), (2, True), (3, False), (8, True), (8, True), (9, False), (17, False), (21, False)]
    repo, old, new = str(rig.repo), rig.sha("01", "implementer"), rig.calls("01", "fixer")[0]["sha"]
    assert driver_review.follow(repo, old, new, {("demo/export.py", 30), ("demo/export.py", 35), ("gone.py", 4)}) == {
        ("demo/export.py", 32): True, ("demo/export.py", 37): False, ("gone.py", 4): False}


# ---------------------------------------------------------------- conflict agent

def test_a_conflict_is_resolved_once_in_the_ticket_slot_and_a_second_one_blocks(tmp_path):
    rig = test_review.conflicted(under(tmp_path, "once"))
    assert rig.code == 0, rig.ledger()
    assert rig.tip() in [gate["commit"] for gate in rig.gates()] and dict(rig.state()["run"]["landed"])["01"] == rig.tip()
    assert rig.led("Ticket 01: the conflict agent resolved the merge conflict in demo/cli.py at ")
    prompt = rig.prompt("conflict-0")
    assert "Files in conflict: demo/cli.py\n" in prompt and f"at commit {rig.sha('03', 'implementer')}\n" in prompt
    again = test_review.conflicted(under(tmp_path, "twice"), moves_again=True)
    line = blocked(again)
    assert "BLOCKED at the merge: after the conflict agent, the merge into the PR branch conflicts in demo/cli.py." in line
    assert len(again.calls("01", "conflict")) == 1


def conflict(tmp_path, resolve):
    """01 and 03 both add demo/cli.py and 03 merges first (as test_review.conflicted); `resolve` is 01's conflict step."""
    rig = Inside(tmp_path, {k: v for k, v in three().items() if not k.startswith("02")})
    one = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "demo/cli.py": test_review.cli("one")})
    rig.steps = plan(**{"01/test-writer": [{**tw("01"), "touch": ["s1"]}], "01/conflict": [resolve, resolve],
                        "03/implementer": [{**impl("03", "demo/cli.py", test_review.cli("three")), "wait": ["s1"]}],
                        "01/implementer": [{**one, "wait_pr_moves": True}]})
    return rig.run(rig.steps)


def test_a_conflict_resolution_that_changes_a_locked_file_does_not_land(tmp_path):
    done = {"status": "DONE", "commits": ["$SHA"], "files": ["demo/cli.py"], "concerns": []}
    rig = conflict(tmp_path, {"files": {"demo/cli.py": test_review.cli("resolved"), "tests/test_01.py": "# weaker\n"},
                              "commit": "merge: resolve cli", "report": done})
    assert "BLOCKED at the merge: the conflict agent: locked file changed: tests/test_01.py." in blocked(rig)
    assert len(rig.calls("01", "conflict")) == 1 and rig.tip() == rig.sha("03", "implementer")
    assert rig.state()["tickets"]["01"]["tip"] == rig.sha("01", "implementer")


def test_a_conflict_resolution_that_changes_a_locked_file_of_a_merged_ticket_does_not_land(tmp_path):
    done = {"status": "DONE", "commits": ["$SHA"], "files": ["demo/cli.py"], "concerns": []}
    rig = conflict(tmp_path, {"files": {"demo/cli.py": test_review.cli("resolved"), "tests/test_03.py": WEAK},
                              "commit": "merge: resolve cli", "report": done})
    # The resolution is a commit of the ticket; the landing check refuses the tree that it would put on the PR branch.
    assert rig.code == 2 and rig.led("Ticket 01: BLOCKED at the merge: locked file changed: tests/test_03.py."), rig.ledger()
    assert asked(rig) == {"01-merged-locked-file-1": ["restore", "adopt", "redesign"]}
    assert rig.tip() == rig.sha("03", "implementer") and on_pr(rig, "tests/test_03.py") == tw("03")["files"]["tests/test_03.py"]
    assert rig.state()["tickets"]["01"]["tip"] == rig.calls("01", "conflict")[0]["sha"]
    rig.run(rig.steps, "--answer", "01-merged-locked-file-1=restore")
    assert rig.code == 0 and on_pr(rig, "tests/test_03.py") == tw("03")["files"]["tests/test_03.py"], rig.ledger()
    assert on_pr(rig, "demo/cli.py") == test_review.cli("resolved") and len(rig.calls("01", "conflict")) == 1


def test_a_conflict_agent_that_gives_up_leaves_no_open_merge_in_the_slot(tmp_path):
    stuck = {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["both sides are needed"]}
    rig = conflict(tmp_path, {"files": {"demo/cli.py": "half\n"}, "report": stuck})
    assert "BLOCKED at the merge: the conflict agent: the conflict reported BLOCKED: both sides are needed." in blocked(rig)
    git = lambda *args: subprocess.run(["git", "-C", str(rig.repo), *args], capture_output=True, text=True)
    assert git("status", "--porcelain").stdout == "" and git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode
    assert git("symbolic-ref", "--short", "HEAD").stdout.strip() == "feat/csv"  # slot 1 is free again


# ---------------------------------------------------------------- templates

@pytest.mark.parametrize("role", ["fixer", "conflict"])
def test_the_templates_of_the_new_roles_hold_a_valid_schema_and_end_with_the_rule(role):
    jsonschema.Draft202012Validator.check_schema(driver_report.schema(role))
    text = (driver_report.PROMPTS / f"{role}.md").read_text()
    assert text.rstrip().endswith("one ref store.") and "ledger" not in text.lower()
    assert text.rfind("{report}") < text.rfind("DONE_WITH_CONCERNS")


def test_no_template_names_the_ledger_and_the_method_of_step_6_is_in_the_reviewer_prompt():
    # The final reviewer gets the ledger as a pointer (requirement 19); no other prompt names it.
    for path in driver_report.PROMPTS.glob("*.md"):
        assert ("ledger" in path.read_text().lower()) == (path.name == "final-reviewer.md"), path.name
    reviewer = (driver_report.PROMPTS / "reviewer.md").read_text()
    for sentence in ("Test each suspicion that the ticket names in its Suspicions line.",
                     "a later round repeats it only when that line changed.",
                     "Run again each mutation that the test-writer report names in `concerns`."):
        assert sentence in reviewer, sentence


def test_the_correctness_prompt_keeps_the_agent_off_the_tree_the_reviewer_edits():
    text = (driver_report.PROMPTS / "correctness.md").read_text()
    assert "Read file content from git at {start_sha}" in text and "never from the working tree" in text
    assert "run no tests" in text


def test_the_correctness_pass_reads_the_untracked_inputs_of_the_driver_from_the_working_tree(tmp_path):
    """Issue 66: the ticket and the findings files are in the ignored .agent/, so git at the tip has none of them."""
    rig = Inside(tmp_path, RISKY, {"correctness": [SOL, OPUS], "implementer": [OPUS]})
    rig.run(plan(**{"02/correctness": [correct([finding("C-IMPORTANT")])], "02/fixer": [fix("02", 1)]}))
    assert rig.code == 0, rig.ledger()
    again = rig.prompt("correctness-1", "02")
    inputs = (".agent/tickets/02-quote-fields.md", ".agent/implement/02/findings-0.json")
    assert all(path in again for path in inputs), again
    tracked = subprocess.run(["git", "-C", str(rig.repo), "ls-files", *inputs], capture_output=True, text=True).stdout
    assert tracked == ""
    assert "read the spec, the ticket and the findings file from the working tree" in again


def test_the_correctness_prompt_carries_the_checks_of_a_review_diff_in_a_gitignored_directory(tmp_path, monkeypatch):
    """Issue #120: a project-local install is ignored, and the pass may read neither the tree nor git for it."""
    rig = Inside(tmp_path, RISKY, {"correctness": [SOL, OPUS], "implementer": [OPUS]}, ignore=".my-skills/")
    checks = rig.repo / ".my-skills/review-diff/correctness.md"
    checks.parent.mkdir(parents=True)
    checks.write_text("# Correctness reviewer\n\n| Check | Flag |\n|---|---|\n| Ledger | A row booked twice. |\n")
    monkeypatch.setattr(driver_review, "CORRECTNESS", checks)
    rig.run(plan())
    assert rig.code == 0, rig.ledger()
    ignored = subprocess.run(["git", "-C", str(rig.repo), "check-ignore", "-q", str(checks)])
    assert ignored.returncode == 0
    assert "| Ledger | A row booked twice. |" in rig.prompt("correctness-0", "02")


STOPPED = "02-review-stopped-1"


def stuck(role, status="NEEDS_CONTEXT"):
    """A review step whose report stops: BLOCKED or NEEDS_CONTEXT, with no finding."""
    step = review("02") if role == "reviewer" else correct()
    step["report"] = {**step["report"], "status": status, "concerns": ["the checks file is absent"]}
    return step


def risky(tmp_path):
    return Inside(tmp_path, RISKY, {"correctness": [SOL, OPUS], "implementer": [OPUS]})


@pytest.mark.parametrize("role", ["reviewer", "correctness"])
def test_a_review_that_reports_needs_context_asks_the_operator_and_retry_resumes_it(tmp_path, role):
    """Issue #120: a stopped reviewer or correctness pass was a dead end with no question."""
    steps = plan(**{f"02/{role}": [stuck(role)]})
    rig = risky(tmp_path)
    rig.run(steps)
    assert rig.code == 2 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.ledger()
    assert rig.led(f"BLOCKED at the review: the {role}: the {role} reported NEEDS_CONTEXT: the checks file is absent.")
    (question,) = rig.state()["run"]["questions"]
    assert (question["id"], question["recommended"]) == (STOPPED, "retry")
    assert [o["id"] for o in question["options"]] == ["retry", "accept", "redesign"]
    assert question["evidence"][0] == f".agent/implement/02/{role}-0.json"
    rig.run(steps, "--answer", f"{STOPPED}=retry")
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.ledger()
    assert len(rig.calls("02", role)) == 2
    assert rig.led(f"Ruling: 02 review runs again (retry): the {role} reported NEEDS_CONTEXT")


def test_an_accepted_review_stop_closes_the_review_with_the_other_reports(tmp_path):
    steps = plan(**{"02/correctness": [stuck("correctness", "BLOCKED")] * 2,
                    "02/reviewer": [review("02"), review("02", [finding("R-IMPORTANT")]), review("02")],
                    "02/fixer": [fix("02", 1)]})
    rig = risky(tmp_path)
    rig.run(steps)
    assert rig.code == 2, rig.ledger()
    rig.run(steps, "--answer", f"{STOPPED}=accept")
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.ledger()
    # The reviewer's finding still gets its fix round; the stopped pass does not stop the ticket again.
    assert rig.state()["tickets"]["02"]["round"] == 1 and len(rig.calls("02", "correctness")) == 3
    assert rig.led("the correctness reported BLOCKED: the checks file is absent", "goes on with the other reports")


def test_a_review_stop_answered_redesign_keeps_the_ticket_blocked(tmp_path):
    steps = plan(**{"02/correctness": [stuck("correctness")]})
    rig = risky(tmp_path)
    rig.run(steps)
    assert rig.code == 2, rig.ledger()
    rig.run(steps, "--answer", f"{STOPPED}=redesign")
    assert rig.code == 1 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.ledger()
    assert len(rig.calls("02", "correctness")) == 1


def test_the_reviewer_schema_asks_for_one_proof_for_each_locked_test_file_in_the_first_review():
    """Issue 66: a first review with no proof for a locked test file costs a rerun."""
    description = driver_report.schema("reviewer")["properties"]["mutations"]["description"]
    assert "each locked test file" in description and "`<locked file>::<test name>`" in description


def test_git_identity_is_not_needed_to_open_the_merge_for_the_conflict_agent(tmp_path):
    import driver_merge
    rig = Inside(tmp_path)
    git = lambda *args: subprocess.run(["git", "-C", str(rig.repo), *args], capture_output=True, text=True).stdout
    assert driver_merge.begin(str(rig.repo), "main") == [] and git("status", "--porcelain") == ""
