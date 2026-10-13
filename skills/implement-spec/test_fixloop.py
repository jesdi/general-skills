"""In-process review, fix-loop and conflict tests: the rig and the scenarios of test_review.py without the launcher,
so coverage sees the driver, plus the cases that only a direct call reaches."""
import json
import os
import shutil
import subprocess

import jsonschema
import pytest

import driver
import driver_agents
import driver_fixloop
import driver_report
import driver_review
import test_review
from test_cli import three
from test_review import FABLE, ONE, OPUS, RISKY, SOL, STUCK, correct, finding, fix, review
from test_walk import impl, plan, tw


class Inside(test_review.Review):
    """The rig of test_review with the driver called in this process."""

    def run(self, steps, *extra, parallel="2", watch=False):
        (self.rig / "plan.json").write_text(json.dumps(steps))
        args = ["--tickets", ".agent/tickets", "--gate", str(self.rig / "gate"), "--spec", "specs/csv/spec.md",
                "--models", str(self.models), "--runner", str(self.rig / "run-agent"), "--parallel", parallel, *extra]
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
    assert str(driver_review.CORRECTNESS) in rig.prompt("correctness-0", "02")
    assert "correctness.md" not in rig.prompt("reviewer-0", "02") and not rig.led("folded")
    assert json.loads((rig.folder("02") / "findings-1.json").read_text()) == []


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
    stale = finding("STALE", "minor", kind="fixture", file="tests/test_01.py")
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
    assert "This call is a fixture correction" in rig.prompt("test-writer-2") and "findings-1.json" in rig.prompt("test-writer-2")


def test_a_locked_file_check_that_git_cannot_make_is_a_finding(tmp_path):
    rig = Inside(tmp_path)
    assert driver_review.locked_changes(str(rig.repo), None, []) == []
    (found,) = driver_review.locked_changes(str(rig.repo), "0" * 40, ["tests/test_01.py"])
    assert found["severity"] == "critical" and found["title"].startswith("locked file check failed: ")


def test_a_fixture_finding_that_comes_back_after_its_correction_blocks_the_ticket(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    correction = {**tw("01"), "files": {"tests/test_01.py": "# corrected\n"}, "commit": "test: 01 fixture fix"}
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), correction], "01/reviewer": [review("01", [stale])] * 2}))
    assert blocked(rig).endswith("BLOCKED at the review: a fixture finding is open after the test-writer correction: STALE.")
    assert len(rig.calls("01", "test-writer")) == 2 and rig.state()["tickets"]["01"]["round"] == 0


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
    assert [gate["commit"] for gate in rig.walk_gates()] == [rig.sha("01", "implementer"), rig.sha("01", "fixer")]


def test_a_fixture_correction_that_is_rejected_blocks_the_ticket(tmp_path):
    stale = finding("STALE", kind="fixture", file="tests/test_01.py")
    rig = Inside(tmp_path).run(plan(**{"01/test-writer": [tw("01"), {}, {}], "01/reviewer": [review("01", [stale])]}))
    assert "BLOCKED at the fixture correction: report .agent/implement/01/test-writer-1.json is not" in blocked(rig)


# ---------------------------------------------------------------- the locked files of the merged tickets

WEAK = "def test_x():\n    assert True\n"
LOCKED_01 = tw("01")["files"]["tests/test_01.py"]
CHANGED_01 = "Ticket 02: critical finding of kind code from the driver at tests/test_01.py:1: locked file changed: tests/test_01.py."


def on_pr(rig, path):
    return subprocess.run(["git", "-C", str(rig.repo), "show", f"feat/csv:{path}"], capture_output=True, text=True).stdout


def test_a_later_ticket_that_weakens_a_locked_test_of_a_merged_ticket_does_not_merge(tmp_path):
    """Final review, correctness 3: ticket 02 replaced the locked assertion of ticket 01, merged and was pushed."""
    cheat = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": WEAK})
    rig = Inside(tmp_path, three()).run(plan(**{"02/implementer": [cheat], "02/fixer": [{}, {}]}))
    assert rig.led("Ticket 02:", "locked file changed") == [CHANGED_01], rig.ledger()
    (found,) = [f for f in json.loads((rig.folder("02") / "findings-0.json").read_text()) if f["source"] == "driver"]
    assert found["evidence"] == f"git diff {rig.sha('02', 'test-writer')}..HEAD -- tests/test_01.py"
    assert "BLOCKED at fix round 1: report" in blocked(rig, "02")
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01 and "feat: 02 rows" not in rig.pr_subjects()
    assert rig.state()["tickets"]["01"]["phase"] == "merged" and not rig.led("Ticket 01:", "locked file changed")


def test_the_fix_round_that_puts_the_locked_test_of_the_merged_ticket_back_lets_the_ticket_merge(tmp_path):
    cheat = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": WEAK})
    back = fix("02", 1, files={"tests/test_01.py": LOCKED_01, "demo/export.py": "x = 1\n"})
    rig = Inside(tmp_path, three()).run(plan(**{"02/implementer": [cheat], "02/fixer": [back]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ticket 02:", "locked file changed") == [CHANGED_01] and rig.state()["tickets"]["02"]["round"] == 1
    assert on_pr(rig, "tests/test_01.py") == LOCKED_01 and "fix: 02 round 1" in rig.pr_subjects()


@pytest.mark.parametrize("listed", [True, False])
def test_the_test_writer_of_a_later_ticket_may_extend_the_locked_file_of_a_merged_ticket(tmp_path, listed):
    more = LOCKED_01 + "\n\ndef test_y():\n    assert 02 == 'y'\n"
    writer = tw("02")
    writer["files"]["tests/test_01.py"] = more
    writer["report"]["locked"] += ["tests/test_01.py"] * listed
    rig = Inside(tmp_path, three()).run(plan(**{"02/test-writer": [writer]}))
    assert rig.code == 0, rig.ledger()
    assert not rig.led("locked file changed") and on_pr(rig, "tests/test_01.py") == more
    assert rig.state()["tickets"]["02"]["locked"] == ["tests/test_02.py"] + ["tests/test_01.py"] * listed
    # What the test-writer of 02 wrote is locked for the implementer of 02, in either case.
    cheat = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": WEAK})
    again = Inside(under(tmp_path, "cheat"), three()).run(
        plan(**{"02/test-writer": [writer], "02/implementer": [cheat], "02/fixer": [{}, {}]}))
    assert again.led("Ticket 02:", "locked file changed") == [CHANGED_01] and on_pr(again, "tests/test_01.py") == LOCKED_01


def test_a_refactor_ticket_may_not_change_a_locked_test_of_a_merged_ticket(tmp_path):
    """It has no test-writer commit: the reference is the commit its branch started from."""
    files = three(**{"02-quote-fields.md": test_review.ticket("02", "Quote fields", "01", seam="none — refactor", criteria=())})
    cheat = impl("02", files={"demo/export.py": "def fn_02():\n    return '02'\n", "tests/test_01.py": WEAK})
    rig = Inside(tmp_path, files).run(plan(**{"02/implementer": [cheat], "02/fixer": [{}, {}]}))
    assert rig.led("Ticket 02:", "locked file changed") == [CHANGED_01], rig.ledger()
    start = rig.calls("02", "implementer")[0]["head"]
    (found,) = [f for f in json.loads((rig.folder("02") / "findings-0.json").read_text()) if f["source"] == "driver"]
    assert found["evidence"] == f"git diff {start}..HEAD -- tests/test_01.py" and not rig.calls("02", "test-writer")
    assert "BLOCKED at fix round 1: report" in blocked(rig, "02") and on_pr(rig, "tests/test_01.py") == LOCKED_01
    clean = Inside(under(tmp_path, "clean"), files).run(plan())
    assert clean.code == 0 and not clean.led("locked file changed"), clean.ledger()


# ---------------------------------------------------------------- the rounds

def test_four_rounds_then_blocked_with_the_escalation_at_round_3(tmp_path):
    rig = Inside(tmp_path, three(), {"implementer": [OPUS, FABLE]})
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK])] * 5, "01/fixer": [fix("01", n) for n in (1, 2, 3, 4)]}))
    assert rig.code == 1 and rig.state()["tickets"]["01"]["round"] == 4
    assert rig.led("BLOCKED at the review: after round 4 these findings are open: STUCK (demo/export.py:99).")
    assert rig.ledger().count(f"Ticket 01 round 3: escalated to {FABLE}.") == 1
    assert [len(rig.led("fixer ran on", entry)) for entry in (OPUS, FABLE)] == [2, 2]
    tips = [rig.sha("01", "implementer")] + [call["sha"] for call in rig.calls("01", "fixer")]
    assert [gate["commit"] for gate in rig.gates() if gate["branch"] == "ticket/01"] == tips
    assert rig.state()["tickets"]["01"]["tip"] == tips[-1] and not rig.calls("02")
    assert rig.state()["tickets"]["03"]["phase"] == "merged"


def test_round_3_stays_on_its_entry_when_the_implementer_has_no_next_entry(tmp_path):
    rig = Inside(tmp_path, ONE, {"implementer": [OPUS]})
    rig.run(plan(**{"01/reviewer": [review("01", [STUCK])] * 3 + [review("01")], "01/fixer": [fix("01", n) for n in (1, 2, 3)]}))
    assert rig.code == 0, rig.ledger()
    assert rig.led(f"Ticket 01 round 3: no next implementer entry has its CLI on PATH, so the round stays on {OPUS}.")
    assert len(rig.led("fixer ran on", OPUS)) == 3


def test_the_escalation_takes_the_next_entry_whose_cli_is_on_path(monkeypatch):
    models = {"implementer": ["a@low", "openai/b@low", "c@low"]}
    monkeypatch.setattr(driver_agents.shutil, "which", lambda cli: cli)
    assert [driver_fixloop.entry(models, n) for n in (1, 2, 3, 4)] == ["a@low", "a@low", "openai/b@low", "openai/b@low"]
    monkeypatch.setattr(driver_agents.shutil, "which", lambda cli: None if cli == "codex" else cli)
    assert driver_fixloop.entry(models, 3) == "c@low"
    # No later entry has its CLI: the round stays on the first. A run cannot show this: it needs both CLIs to start,
    # the implementer's for its first entry and the other for the correctness pass.
    assert driver_fixloop.entry({"implementer": ["a@low", "openai/b@low"]}, 3) == "a@low"


@pytest.mark.parametrize("step, error", [
    ({}, "BLOCKED at fix round 1: report .agent/implement/01/fixer-1.json is not"),
    ({"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": ["cannot"], "green": [], "ticket": "01"}},
     "BLOCKED at fix round 1: the fixer reported BLOCKED: cannot.")])
def test_a_fixer_that_does_not_finish_blocks_the_ticket_in_its_round(tmp_path, step, error):
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [review("01", [STUCK])], "01/fixer": [step, step]}))
    entry = rig.state()["tickets"]["01"]
    assert error in blocked(rig) and entry["round"] == 1 and entry["tip"] == rig.sha("01", "implementer")


# ---------------------------------------------------------------- the reviewers' reports and the slot

def test_a_first_review_proves_each_locked_file_and_a_second_bad_report_blocks(tmp_path):
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [review("01", lines=())] * 2}))
    assert "BLOCKED at the review: the reviewer: mutations: no mutation proof for the locked test file tests/test_01.py." in blocked(rig)
    assert "feat: 01 rows" not in rig.pr_subjects()


def test_the_locked_file_rule_is_for_the_first_review_only(tmp_path):
    report = dict(status="DONE", commits=[], files=[], concerns=[], findings=[], mutations=[])
    path = tmp_path / "reviewer-0.json"
    path.write_text(json.dumps(report))
    context = {"repo": ".", "branch": "main", "start": "HEAD", "log": str(path), "locked": ["tests/a.py", "tests/b.py"]}
    assert driver_report.validate("reviewer", str(path), {**context, "round": 0}) == []
    assert driver_report.validate("reviewer", str(path), {**context, "round": 1}) == [
        "mutations: no mutation proof for the locked test file tests/a.py",
        "mutations: no mutation proof for the locked test file tests/b.py"]
    assert driver_report.validate("correctness", str(path), {**context, "round": 0}) == []


def test_a_timed_out_read_only_role_is_restored_only_when_no_other_agent_is_in_the_slot(tmp_path):
    rig = Inside(tmp_path)
    tip = subprocess.run(["git", "-C", str(rig.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (rig.repo / "junk.txt").write_text("x\n")
    context, shared = {"repo": str(rig.repo), "branch": "feat/csv", "start": tip}, lambda: False
    assert "first revert each edit of the earlier attempt" in driver._timed_out("correctness", 30, context, shared)
    assert (rig.repo / "junk.txt").exists()
    assert "in the tree: continue from it" in driver._timed_out("fixer", 60, context, shared) and (rig.repo / "junk.txt").exists()
    # `--only reviewer`: one agent
    assert f"restored the worktree to {tip}" in driver._timed_out("reviewer", 45, context, lambda: True)
    assert not (rig.repo / "junk.txt").exists()


DIRTY = [({"files": {"junk.txt": "x\n", "demo/export.py": "mutated\n"}},
          "the tree is not as the review found it: demo/export.py, junk.txt"),
         ({"git": [["checkout", "-q", "--detach"]]}, "the slot is no longer on ticket/01 at "),
         ({"commit": "sneak", "files": {"demo/export.py": "x = 2\n"}}, "the slot is no longer on ticket/01 at ")]


def slot_state(rig):
    git = lambda *args: subprocess.run(["git", "-C", str(rig.repo), *args], capture_output=True, text=True).stdout.strip()
    return git("status", "--porcelain"), git("rev-parse", "ticket/01")


@pytest.mark.parametrize("step, left", DIRTY)
def test_a_reviewer_that_changes_the_slot_is_run_once_more_on_the_restored_tip(tmp_path, step, left):
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [{**review("01"), **step}, review("01")]}))
    assert rig.code == 0, rig.ledger()
    tip = rig.sha("01", "implementer")
    (line,) = rig.led("Ticket 01: a read-only role broke its rule:")
    assert left in line and line.endswith(f"the driver restored the slot to {tip[:7]} and runs the review again.")
    first, second = rig.calls("01", "reviewer")
    assert (second["branch"], second["head"], second["status"]) == ("ticket/01", tip, "")
    assert rig.tip() == tip and not (rig.repo / "junk.txt").exists()


def test_a_second_review_that_changes_the_slot_blocks_the_ticket_with_the_slot_clean(tmp_path):
    step, left = DIRTY[0]
    rig = Inside(tmp_path).run(plan(**{"01/reviewer": [{**review("01"), **step}] * 3}))
    assert f"BLOCKED at the review: {left}." in blocked(rig)
    assert len(rig.calls("01", "reviewer")) == 2 and len(rig.led("a read-only role broke its rule:")) == 2
    assert slot_state(rig) == ("", rig.sha("01", "implementer")) and "feat: 01 rows" not in rig.pr_subjects()


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
    assert f"The fix is {old}..{new}: review only the lines it changed" in rig.prompt("reviewer-1")


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
    return rig.run(plan(**{"01/test-writer": [{**tw("01"), "touch": ["s1"]}], "01/conflict": [resolve, resolve],
                           "03/implementer": [{**impl("03", "demo/cli.py", test_review.cli("three")), "wait": ["s1"]}],
                           "01/implementer": [{**one, "wait_pr_moves": True}]}))


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
    assert "BLOCKED at the merge: the conflict agent: locked file changed: tests/test_03.py." in blocked(rig)
    assert rig.tip() == rig.sha("03", "implementer") and on_pr(rig, "tests/test_03.py") == tw("03")["files"]["tests/test_03.py"]
    assert rig.state()["tickets"]["01"]["tip"] == rig.sha("01", "implementer")


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


def test_git_identity_is_not_needed_to_open_the_merge_for_the_conflict_agent(tmp_path):
    import driver_merge
    rig = Inside(tmp_path)
    git = lambda *args: subprocess.run(["git", "-C", str(rig.repo), *args], capture_output=True, text=True).stdout
    assert driver_merge.begin(str(rig.repo), "main") == [] and git("status", "--porcelain") == ""
