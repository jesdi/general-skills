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
from test_cli import three, ticket
from test_final import RUNNER, final, fixer, fn
from test_review import FABLE, ONE, OPUS, RISKY, STUCK, finding, fix, review
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


def blocked(rig, nn="01", code=1):
    """The one BLOCKED line of the ticket; `code` 2: a rerun that was rejected too asked `ticket-blocked`."""
    (line,) = rig.led(nn, "BLOCKED")
    assert rig.code == code and rig.state()["tickets"][nn]["phase"] == "blocked"
    return line


# ---------------------------------------------------------------- who reviews

LEDGER = "two runs at once can both write the ledger"
INTERLEAVE = {**finding("concurrent writes interleave ledger lines", "critical"), "basis": "criterion"}


def ledger_tickets():
    """Ticket 01 with a Risk line, 02 after it with none, and 03 independent of both."""
    return three(**{"01-export-rows.md": ticket("01", "Export rows", risk=LEDGER),
                    "02-quote-fields.md": ticket("02", "Quote fields", "01")})


def test_a_risky_ticket_and_a_plain_one_are_each_reviewed_by_the_reviewer_alone(tmp_path):
    rig = Inside(tmp_path, ledger_tickets()).run(plan())
    assert rig.code == 0, rig.ledger()
    for nn in ("01", "02"):
        assert [call["role"] for call in rig.calls(nn) if call["role"] not in ("test-writer", "implementer")] == [
            "reviewer"], nn
        assert not list(rig.folder(nn).glob("correctness*")), nn
    risky = rig.prompt("reviewer-0", "01")
    assert f"Check the Risk line of the ticket as one more criterion: {LEDGER}." in risky
    assert "names no risk" not in risky
    assert "The ticket names no risk." in rig.prompt("reviewer-0", "02")


def test_an_unhandled_risk_is_a_criterion_finding_that_starts_fix_round_1(tmp_path):
    rig = Inside(tmp_path, ledger_tickets())
    rig.run(plan(**{"01/reviewer": [review("01", [INTERLEAVE])], "01/fixer": [fix("01", 1)]}))
    assert rig.code == 0, rig.ledger()
    (found,) = json.loads((rig.folder("01") / "findings-0.json").read_text())
    assert (found["title"], found["basis"], found["source"]) == (INTERLEAVE["title"], "criterion", "reviewer")
    assert len(rig.calls("01", "fixer")) == 1 and ".agent/implement/01/findings-0.json" in rig.prompt("fixer-1", "01")
    assert rig.led("critical finding of kind code from the reviewer", INTERLEAVE["title"])


def test_an_unhandled_risk_at_the_cap_blocks_the_ticket_and_an_independent_one_goes_on(tmp_path):
    rig = Inside(tmp_path, ledger_tickets())
    rig.run(plan(**{"01/reviewer": [review("01", [INTERLEAVE])] * 3, "01/fixer": [fix("01", n) for n in (1, 2)]}))
    assert rig.code == 1 and rig.state()["tickets"]["01"]["round"] == 2, rig.ledger()
    assert rig.led("BLOCKED at the review: after round 2 these findings are open: concurrent writes interleave")
    assert rig.state()["tickets"]["01"]["phase"] == "blocked" and not rig.calls("02")
    assert rig.state()["tickets"]["03"]["phase"] == "merged"


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
                    "final/final-reviewer": [scoped], "final/fixer": [batch]}))

    assert rig.code == 0, rig.ledger()
    assert [item["title"] for item in rig.state()["run"]["final"]["must_fix"]] == ["QUOTING-REGRESSION"]
    assert "QUOTING-REGRESSION" in rig.prompt("fixer-1", "final")


# ---------------------------------------------------------------- the locked files

def test_a_locked_file_check_that_git_cannot_make_is_a_finding(tmp_path):
    rig = Inside(tmp_path)
    assert driver_review.locked_changes(str(rig.repo), None, []) == []
    (found,) = driver_review.locked_changes(str(rig.repo), "0" * 40, ["tests/test_01.py"])
    assert found["severity"] == "critical" and found["title"].startswith("locked file check failed: ")


STALE = finding("STALE", kind="fixture", file="tests/test_01.py")
AMBIGUOUS_1 = "01-spec-ambiguous-1"


def corrections(n):
    """`n` test-writer corrections of tests/test_01.py, each with its own content."""
    return [{**tw("01"), "files": {"tests/test_01.py": f"# corrected {i}\n"}, "commit": f"test: 01 correction {i}"}
            for i in range(1, n + 1)]


def asked(rig):
    """The open questions of the run: id -> option ids."""
    return {q["id"]: [o["id"] for o in q["options"]] for q in rig.state()["run"]["questions"] if q["answer"] is None}


def test_a_locked_test_finding_that_comes_back_after_its_correction_asks_spec_ambiguous(tmp_path):
    # Issue #107: the ticket was BLOCKED with no question, so only an edit of state.json could resume it.
    steps = plan(**{"01/test-writer": [tw("01"), *corrections(2)],
                    "01/implementer": [impl("01"), impl("01", text="def fn_01():\n    return 'again'\n")],
                    "01/reviewer": [review("01", [STALE])] * 2 + [review("01")]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    assert rig.led("BLOCKED at the review: the spec reads two ways on a locked test: tests/test_01.py: the first "
                   "reading: STALE: STALE; the second reading: STALE: STALE.")
    assert asked(rig) == {AMBIGUOUS_1: ["keep-test", "correct-test", "edit-spec"]}
    (question,) = rig.state()["run"]["questions"]
    assert question["recommended"] == "" and question["evidence"][0] == ".agent/implement/01/spec-ambiguous-1.json"
    rig.run(steps, "--answer", f"{AMBIGUOUS_1}=correct-test")
    assert rig.code == 0, rig.ledger()
    order = [call["role"] for call in rig.calls("01")]
    assert order == ["test-writer", "implementer", "reviewer", "test-writer", "reviewer", "test-writer", "implementer",
                     "reviewer"]
    assert rig.led("Ruling: 01 locked test is corrected to the second reading (correct-test)")
    assert rig.state()["tickets"]["01"]["phase"] == "merged"


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
    # A weak locked test: the fixer may not edit the locked file, so a fix round could never close it.
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


def test_a_fixture_correction_that_is_rejected_twice_asks_ticket_blocked_and_retry_reviews_again(tmp_path):
    # Issue #107: a correction the driver rejects (for one, a change to a file it does not list) was a dead end too.
    steps = plan(**{"01/test-writer": [tw("01"), {}, {}, *corrections(1)],
                    "01/reviewer": [review("01", [STALE])] * 2 + [review("01")]})
    rig = Inside(tmp_path).run(steps)
    assert rig.code == 2 and list(asked(rig)) == ["01-ticket-blocked-1"], rig.ledger()
    assert rig.led("BLOCKED at the test-writer correction: report .agent/implement/01/test-writer-1.json is not")
    assert rig.state()["run"]["questions"][0]["resume"] == "review"
    rig.run(steps, "--answer", "01-ticket-blocked-1=retry")
    assert rig.code == 0 and len(rig.calls("01", "test-writer")) == 4, rig.ledger()
    assert [call["role"] for call in rig.calls("01")][-3:] == ["reviewer", "test-writer", "reviewer"]


# ---------------------------------------------------------------- one locked-file rule

WEAK = "def test_x():\n    assert True\n"
LOCKED_01 = tw("01")["files"]["tests/test_01.py"]
CHEAT = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": WEAK})
SONNET, OPUS_5_5 = "claude-sonnet-5-5@medium", "claude-opus-5-5@medium"
TWO = {"implementer": [SONNET, OPUS_5_5], "test-writer": [SONNET, OPUS_5_5], "conflict": [OPUS_5_5, SONNET]}
CHANGED_01 = "locked file changed: tests/test_01.py"


def on_pr(rig, path):
    return subprocess.run(["git", "-C", str(rig.repo), "show", f"feat/csv:{path}"], capture_output=True, text=True).stdout


def entries(rig, nn, role):
    return [agent["entry"] for agent in rig.state()["run"]["agents"] if (agent["ticket"], agent["role"]) == (nn, role)]


def attempts(rig, nn, role):
    """The ledger line of each attempt of `role` on ticket `nn`."""
    return rig.led(f"Ticket {nn}: {role} ran on")


def breaks_01(**changes):
    """An implementer of 01 whose commit changes its own locked file."""
    return impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "tests/test_01.py": WEAK}, **changes)


IDLE = {"report": {"status": "DONE_WITH_CONCERNS", "commits": [], "files": [], "concerns": ["the start meets the ticket"],
                   "green": []}}


def test_an_implementer_that_changes_its_locked_file_is_rejected_and_runs_again_on_its_second_entry(tmp_path):
    puts_back = impl("01", files={"demo/export.py": "def fn_01():\n    return 'one'\n", "tests/test_01.py": LOCKED_01})
    rig = Inside(tmp_path, models=TWO).run(plan(**{"01/implementer": [breaks_01(), puts_back]}))
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    first, second = attempts(rig, "01", "implementer")
    assert CHANGED_01 in first and second.endswith("report accepted.")
    assert entries(rig, "01", "implementer") == [SONNET, OPUS_5_5]
    assert CHANGED_01 in rig.calls("01", "implementer")[1]["prompt"] and on_pr(rig, "tests/test_01.py") == LOCKED_01


def breached(tmp_path):
    """Ticket 01 blocked after its implementer and the rerun both left tests/test_01.py changed."""
    run = plan(**{"01/implementer": [breaks_01(), IDLE, IDLE, IDLE]})
    return Inside(tmp_path, models=TWO).run(run), run


def test_a_rerun_that_leaves_the_locked_file_changed_blocks_the_ticket_with_one_ticket_blocked_question(tmp_path):
    rig, _ = breached(tmp_path)
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    (question,) = rig.state()["run"]["questions"]
    assert question["kind"] == "ticket-blocked" and CHANGED_01 in question["subject"]
    assert all(CHANGED_01 in line for line in attempts(rig, "01", "implementer"))
    assert entries(rig, "01", "implementer") == [SONNET, OPUS_5_5] and "feat: 01 rows" not in rig.pr_subjects()


def test_a_breach_on_the_tip_survives_a_retry_whose_implementer_commits_nothing(tmp_path):
    rig, run = breached(tmp_path)
    rig.run(run, "--answer", "01-ticket-blocked-1=retry")
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    third = attempts(rig, "01", "implementer")[2]
    assert rig.calls("01", "implementer")[2]["sha"] == "" and CHANGED_01 in third
    assert [q["id"] for q in rig.state()["run"]["questions"] if q["answer"] is None] == ["01-ticket-blocked-2"]


def test_a_fixer_that_changes_the_locked_file_of_a_merged_ticket_is_rejected_and_runs_again(tmp_path):
    weakens = fix("02", 1, files={"demo/export.py": "def fn_02():\n    return 'fix1'\n", "tests/test_01.py": WEAK})
    puts_back = fix("02", 1, files={"demo/export.py": "def fn_02():\n    return 'fix2'\n", "tests/test_01.py": LOCKED_01})
    rig = Inside(tmp_path, three()).run(plan(**{"02/reviewer": [review("02", [STUCK]), review("02")],
                                                "02/fixer": [weakens, puts_back]}))
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.ledger()
    first, second = attempts(rig, "02", "fixer")
    assert CHANGED_01 in first and second.endswith("report accepted.")
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01


def test_an_implementer_that_changes_only_its_code_is_accepted_with_no_locked_file_line(tmp_path):
    rig = Inside(tmp_path).run(plan())
    assert rig.code == 0, rig.ledger()
    (only,) = attempts(rig, "01", "implementer")
    assert only.endswith("report accepted.") and not rig.led("locked file")


def extends(text, listed):
    writer = tw("02")
    writer["files"]["tests/test_01.py"] = text
    writer["report"]["locked"] += ["tests/test_01.py"] * listed
    return writer


MORE_01 = LOCKED_01 + "\n\ndef test_y():\n    assert 02 == 'y'\n"
UNLISTED = "tests/test_01.py is a locked file of another ticket, and `locked` does not list it"


def test_a_listed_change_to_a_merged_ticket_locked_file_is_in_the_reviewer_prompt_and_lands(tmp_path):
    rig = Inside(tmp_path, three()).run(plan(**{"02/test-writer": [extends(MORE_01, True)]}))
    assert rig.code == 0 and not rig.state()["run"]["questions"], rig.ledger()
    prompt = rig.prompt("reviewer-0", "02")
    assert "diff --git a/tests/test_01.py b/tests/test_01.py" in prompt and "+def test_y():" in prompt
    assert on_pr(rig, "tests/test_01.py") == MORE_01 and not rig.led("locked file changed")
    assert rig.state()["tickets"]["02"]["locked"] == ["tests/test_02.py", "tests/test_01.py"]


def test_an_unlisted_change_to_a_merged_ticket_locked_file_is_rejected_and_the_test_writer_runs_on_its_second_entry(tmp_path):
    puts_back = {**extends(LOCKED_01, False), "commit": "test: 02 red again"}
    rig = Inside(tmp_path, three(), models=TWO).run(plan(**{"02/test-writer": [extends(MORE_01, False), puts_back]}))
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.ledger()
    first, _ = attempts(rig, "02", "test-writer")
    assert UNLISTED in first and entries(rig, "02", "test-writer") == [SONNET, OPUS_5_5]


def test_an_unlisted_change_to_the_locked_file_of_an_unmerged_ticket_is_rejected(tmp_path):
    """01 and 02 run side by side: 02's test-writer ends after 01's test-writer, and 01 merges after it."""
    files = {name: text for name, text in three().items() if not name.startswith("03")}
    files["02-quote-fields.md"] = test_review.ticket("02", "Quote fields", touches="`demo/other.py: g`")
    rig = Inside(tmp_path, files, models=TWO).run(plan(**{
        "01/implementer": [{**impl("01"), "touch": ["w1"]}], "01/reviewer": [{"wait": ["t2"]}],
        "02/test-writer": [{**extends(MORE_01, False), "wait": ["w1"]},
                           {**tw("02"), "touch": ["t2"], "git": [["rm", "-q", "tests/test_01.py"]], "commit": "test: 02 again"}],
        "02/implementer": [impl("02", "demo/other.py")]}))
    assert rig.code == 0, rig.ledger()
    first, _ = attempts(rig, "02", "test-writer")
    assert UNLISTED in first and rig.calls("02", "test-writer")[0]["t1"] < rig.calls("01", "reviewer")[0]["t1"]


def test_a_correction_runs_with_no_restore_commit_and_no_gate_before_it(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    correction = {**tw("01"), "files": {"tests/test_01.py": "# corrected\n"}, "commit": "test: 01 fixture fix"}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), correction],
                                       "01/reviewer": [review("01", [stale]), review("01")]}))
    assert rig.code == 0, rig.ledger()
    log = rig.calls("01", "reviewer")[1]["log"]  # the subjects of the tip the review after the correction read
    assert "restore" not in log and "test: 01 fixture fix" in log
    corrected = rig.calls("01", "test-writer")[1]
    assert [gate["commit"] for gate in rig.gates("fast")] == [rig.sha("01", "implementer"), corrected["sha"]]


def test_a_refactor_ticket_that_changes_a_locked_test_of_a_merged_ticket_is_rejected(tmp_path):
    files = three(**{"02-quote-fields.md": test_review.ticket("02", "Quote fields", "01", seam="none — refactor", criteria=())})
    rig = Inside(tmp_path, files).run(plan(**{"02/implementer": [CHEAT, CHEAT]}))
    assert rig.code == 2 and not rig.calls("02", "test-writer"), rig.ledger()
    assert all(CHANGED_01 in line for line in attempts(rig, "02", "implementer"))
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01


def test_a_finding_in_the_locked_file_of_a_merged_ticket_goes_to_the_correction_not_the_fixer(tmp_path):
    """Issue #116: ticket 02 must change the locked file of merged ticket 01. The review reports the missing change
    there: a fixer may not make it, so the correction makes it and lists the file."""
    missing = finding("MISSING", file="tests/test_01.py", line=2)
    correction = {**tw("02"), "files": {"tests/test_01.py": MORE_01}, "commit": "test: 02 corrected"}
    correction["report"] = {**correction["report"], "locked": ["tests/test_01.py"]}
    rig = Inside(tmp_path, three()).run(plan(**{
        "02/test-writer": [tw("02"), correction], "02/reviewer": [review("02", [missing]), review("02")]}))
    assert rig.code == 0, rig.ledger()
    assert not rig.calls("02", "fixer")
    assert rig.led("Ticket 02: code correction in tests/test_01.py, outside the fix rounds: MISSING")
    assert on_pr(rig, "tests/test_01.py") == MORE_01
    assert "tests/test_01.py" in rig.state()["tickets"]["02"]["locked"]
    first, corrected = rig.prompt("test-writer-0", "02"), rig.prompt("test-writer-1", "02")
    assert "When the ticket must change a locked file of another ticket" in first
    assert "on a locked file of another ticket (tests/test_01.py" in corrected
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
    assert "the locked tests of other tickets: tests/test_01.py." in first
    assert "update each shared fake or fixture" not in first
    assert "update each shared fake or fixture that other locked tests use" in corrected
    assert "run each whole file of `locked` and each locked test of merged tickets" in corrected


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
    rig = Inside(tmp_path, three()).run(plan(**{"02/test-writer": [writer], "02/fixer": [fix("02", 1)],
                                                "02/reviewer": [review("02", [STUCK]), review("02")]}))
    assert rig.code == 0, rig.ledger()
    (fixer,) = rig.calls("02", "fixer")
    test_review.gate_rule(fixer["prompt"], f"export TIER=fast; {rig.rig / 'gate'}",
                           "tests/test_02.py tests/test_02b.py")
    test_review.no_full_gate(rig)


def test_a_locked_path_with_whitespace_is_rejected_once_and_then_blocks(tmp_path):
    spaced = {**tw("01"), "files": {"tests/test 01.py": "def test_x():\n    assert 1 == 'x'\n"}}
    spaced["report"] = {**spaced["report"], "files": ["tests/test 01.py"], "locked": ["tests/test 01.py"]}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [spaced, spaced]}))
    error = ("locked path 'tests/test 01.py' contains whitespace: the fast gate gets the locked paths separated by "
             "spaces, so rename the file")
    assert blocked(rig, code=2) == f"Ticket 01: BLOCKED at the test-writer: {error}."
    first, second = rig.calls("01", "test-writer")
    assert f"The earlier attempt did not finish the role: {error}" in second["prompt"]
    assert not rig.calls("01", "implementer")


ACCENT = "tests/tést.py"


def accented(tmp_path, name, **steps):
    """Ticket 01 locks a test file whose name git prints quoted (core.quotePath) when nobody asks otherwise."""
    writer = tw("01")
    writer["files"][ACCENT] = "def test_y():\n    assert 1 == 'y'\n"
    writer["report"]["locked"] += [ACCENT]
    return Inside(under(tmp_path, name), three()).run(plan(**{"01/test-writer": [writer], **steps}))


def test_a_locked_file_with_a_name_that_git_quotes_is_protected_for_its_ticket_and_a_later_one(tmp_path):
    """Review round 1, I2: the quoted form of the path matched nothing, so no check saw the file."""
    own = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", ACCENT: WEAK})
    rig = accented(tmp_path, "own", **{"01/implementer": [own, own]})
    assert rig.code == 2 and all(f"locked file changed: {ACCENT}" in line for line in attempts(rig, "01", "implementer"))
    later = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", ACCENT: WEAK})
    rig = accented(tmp_path, "later", **{"02/implementer": [later, later]})
    assert rig.state()["tickets"]["01"]["locked"] == ["tests/test_01.py", ACCENT], rig.ledger()
    assert rig.code == 2 and all(f"locked file changed: {ACCENT}" in line for line in attempts(rig, "02", "implementer"))
    assert "assert 1 == 'y'" in on_pr(rig, ACCENT)


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
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK]), review("01")],
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
    # No review saw the red tip: the re-review covers both fixes.
    assert f"The fix is {rig.sha('01', 'implementer')}..{second}" in rig.prompt("reviewer-2")


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


def test_a_fixer_that_does_not_finish_blocks_the_ticket_with_its_round_to_run_again(tmp_path):
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [review("01", [STUCK])], "01/fixer": [{}, {}]}))
    entry = rig.state()["tickets"]["01"]
    assert rig.code == 2 and entry["phase"] == "blocked", rig.ledger()
    assert rig.led("BLOCKED at fix round 1: report .agent/implement/01/fixer-1.json is not")
    assert [q["id"] for q in rig.state()["run"]["questions"]] == ["01-ticket-blocked-1"]
    assert entry["round"] == 0 and entry["tip"] == rig.sha("01", "implementer")


# ---------------------------------------------------------------- the reviewers' reports and the slot

MANUAL = "scripts/test_e2e_manual.py"


def manual(tmp_path, *writers, reviewer=None):
    """Ticket 14, whose only locked file is a manual end-to-end script that the gate does not run."""
    rig = Inside(tmp_path, {"14-manual-e2e.md": ticket("14", "Manual e2e")})
    return rig.run(plan(**{"14/test-writer": list(writers), "14/implementer": [impl("14")],
                           "14/reviewer": [reviewer or review("14")]}))


def writes_manual(red=f"FAILED {MANUAL}::test_x - AttributeError", **report):
    step = tw("14")
    step["files"] = {MANUAL: "def test_x():\n    assert 14 == 'x'\n"}
    step["stdout"] = f"FAILED {MANUAL}::test_x - AttributeError\n"
    step["report"] = {**step["report"], "files": [MANUAL], "locked": [MANUAL],
                      "red": [{"test": f"{MANUAL}::test_x", "line": red}], **report}
    return step


def test_a_full_review_with_no_mutation_proof_is_accepted_and_the_ticket_lands(tmp_path):
    rig = manual(tmp_path, writes_manual())
    assert rig.code == 0 and rig.state()["tickets"]["14"]["phase"] == "merged", rig.ledger()
    assert len(rig.calls("14", "reviewer")) == 1 and rig.led("Ticket 14", "reviewer", "report accepted")
    assert not rig.led("no mutation proof") and "feat: 14 rows" in rig.pr_subjects()


def test_a_leftover_mutations_field_whose_red_line_is_not_in_the_log_is_ignored(tmp_path):
    leftover = review("14")
    leftover["report"]["mutations"] = [{"test": "tests/test_csv.py::test_quote", "file": "csv.py", "line": 12,
                                        "red": "AssertionError"}]
    rig = manual(tmp_path, writes_manual(), reviewer=leftover)
    assert rig.code == 0 and len(rig.calls("14", "reviewer")) == 1, rig.ledger()
    assert not rig.led("red line not found")


def test_a_fixtures_field_is_ignored_and_a_red_line_not_in_the_log_is_still_rejected(tmp_path):
    rig = manual(tmp_path, writes_manual(red="FAILED never printed", fixtures=["gh"]), writes_manual(fixtures=["gh"]))
    assert rig.code == 0, rig.ledger()
    first, second = rig.calls("14", "test-writer")
    assert f"red line not found in log: {MANUAL}::test_x: FAILED never printed" in second["prompt"]
    assert "red line not found" not in first["prompt"] and len(rig.calls("14", "test-writer")) == 2
    assert "fixtures" not in rig.state()["tickets"]["14"]


def test_no_prompt_of_a_ticket_review_asks_for_a_mutation_proof(tmp_path):
    rig = Inside(tmp_path, three())
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK]), review("01")], "01/fixer": [fix("01", 1)]}))
    assert rig.code == 0 and len(rig.calls("01", "reviewer")) == 2, rig.ledger()
    prompts = [call["prompt"] for nn in ("01", "02") for call in rig.calls(nn, "reviewer")]
    assert len(prompts) == 3 and not [prompt for prompt in prompts if "mutation" in prompt.lower()]
    again = rig.prompt("reviewer-1")
    assert "This is the re-review after fix round 1" in again and "proof" not in again
    assert not [line for line in again.splitlines() if "demo/export.py:" in line]


def test_the_test_writer_rewrites_a_test_that_is_green_before_any_implementation(tmp_path):
    rig = Inside(tmp_path, ONE).run(plan())
    assert rig.code == 0, rig.ledger()
    writer, reviewer = rig.prompt("test-writer-0"), rig.prompt("reviewer-0")
    assert "A test that is green before any implementation is not a proof. Rewrite it until it fails.\n" in writer
    assert "mutation" not in writer.lower() and "mutation" not in reviewer.lower()
    assert "Run again" not in reviewer


def test_a_timed_out_read_only_role_is_restored_only_when_no_other_agent_is_in_the_slot(tmp_path):
    rig = Inside(tmp_path)
    tip = subprocess.run(["git", "-C", str(rig.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (rig.repo / "junk.txt").write_text("x\n")
    context, shared = {"repo": str(rig.repo), "branch": "feat/csv", "start": tip}, lambda: False
    assert "first revert each edit of the earlier attempt" in driver_run._timeout_lines("reviewer", 45, context, shared)
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


def conflict(tmp_path, resolve, then=None, models=None):
    """01 and 03 both add demo/cli.py and 03 merges first (as test_review.conflicted); `resolve` is 01's conflict step,
    `then` its rerun (`resolve` again by default)."""
    rig = Inside(tmp_path, {k: v for k, v in three().items() if not k.startswith("02")}, models=models)
    one = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "demo/cli.py": test_review.cli("one")})
    rig.steps = plan(**{"01/test-writer": [{**tw("01"), "touch": ["s1"]}], "01/conflict": [resolve, then or resolve],
                        "03/implementer": [{**impl("03", "demo/cli.py", test_review.cli("three")), "wait": ["s1"]}],
                        "01/implementer": [{**one, "wait_pr_moves": True}]})
    return rig.run(rig.steps)


RESOLVED = {"status": "DONE", "commits": ["$SHA"], "files": ["demo/cli.py"], "concerns": []}


def test_a_conflict_resolution_that_changes_its_own_locked_file_is_rejected_twice_and_blocks(tmp_path):
    rig = conflict(tmp_path, {"files": {"demo/cli.py": test_review.cli("resolved"), "tests/test_01.py": "# weaker\n"},
                              "commit": "merge: resolve cli", "report": RESOLVED})
    assert rig.code == 2 and all(CHANGED_01 in line for line in attempts(rig, "01", "conflict")), rig.ledger()
    (question,) = rig.state()["run"]["questions"]
    assert question["kind"] == "ticket-blocked" and CHANGED_01 in question["subject"]
    assert len(rig.calls("01", "conflict")) == 2 and rig.tip() == rig.sha("03", "implementer")


def test_a_conflict_resolution_that_changes_a_locked_file_of_a_merged_ticket_is_rejected_and_runs_on_its_second_entry(tmp_path):
    locked_03 = tw("03")["files"]["tests/test_03.py"]
    weakens = {"files": {"demo/cli.py": test_review.cli("resolved"), "tests/test_03.py": WEAK},
               "commit": "merge: resolve cli", "report": RESOLVED}
    puts_back = {"files": {"tests/test_03.py": locked_03}, "commit": "merge: put back tests/test_03.py", "report": RESOLVED}
    rig = conflict(tmp_path, weakens, puts_back, models=TWO)
    assert rig.code == 0, rig.ledger()
    first, second = attempts(rig, "01", "conflict")
    assert "locked file changed: tests/test_03.py" in first and second.endswith("report accepted.")
    assert entries(rig, "01", "conflict") == [OPUS_5_5, SONNET] and not rig.state()["run"]["questions"]
    assert on_pr(rig, "tests/test_03.py") == locked_03 and on_pr(rig, "demo/cli.py") == test_review.cli("resolved")


def test_a_conflict_agent_that_gives_up_leaves_no_open_merge_in_the_slot(tmp_path):
    stuck = {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["both sides are needed"]}
    rig = conflict(tmp_path, {"files": {"demo/cli.py": "half\n"}, "report": stuck})
    assert rig.code == 2 and rig.led("Ticket 01: BLOCKED at the merge: the conflict agent: it reported BLOCKED: both sides are needed.")
    assert len(rig.calls("01", "conflict")) == 2
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
    # The final review gets the ledger as a pointer (requirement 19); no other prompt names it.
    final = ("final-reviewer.md", "final-lens.md", "lens-correctness.md")
    for path in driver_report.PROMPTS.glob("*.md"):
        assert ("ledger" in path.read_text().lower()) == (path.name in final), path.name
    reviewer = (driver_report.PROMPTS / "reviewer.md").read_text()
    assert "Test each suspicion that the ticket names in its Suspicions line." in reviewer


def test_git_identity_is_not_needed_to_open_the_merge_for_the_conflict_agent(tmp_path):
    import driver_merge
    rig = Inside(tmp_path)
    git = lambda *args: subprocess.run(["git", "-C", str(rig.repo), *args], capture_output=True, text=True).stdout
    assert driver_merge.begin(str(rig.repo), "main") == [] and git("status", "--porcelain") == ""
