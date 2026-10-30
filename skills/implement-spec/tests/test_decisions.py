"""In-process tests of the human stops: the scenarios of test_stops.py without the launcher, so coverage sees the
driver, plus the cases that only a direct call reaches (the answers file as input from outside, the question ids,
a template that is not there)."""
import json
import shutil

import pytest

import driver_answers
import driver_decisions
import driver_review
import driver_review_page
from test_cli import git, ticket
from test_fixloop import Inside, inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_tidy import Inside as Pushing, tools  # noqa: F401  (an unattended run pushes: the rig with an origin and a fake gh)
from test_review import ONE, STUCK, finding, fix, review
from test_stops import CONTRA, NEW_TEST, Q1, UNATTENDED, blocked, correction, decisions, stage, steps, write_answers
from test_walk import COST, impl, plan, tw

PAGE = ".agent/implement-review.html"


def stopped(tmp_path, *extra, then=(impl("01"),)):
    return Inside(tmp_path, ONE).run(steps(blocked(), *then), *extra)


def doc(*questions, run="run-1"):
    """decisions.json of a run whose state.json holds `questions`."""
    return driver_decisions.document({"revision": run, "questions": list(questions)})


def asked(kind="locked-test", ticket="01", subject="tests/test_a.py::test_x", before=(), stop=1):
    return driver_decisions.question(kind, ticket, subject, ["report.json", "ledger.md:3"], list(before), stop)


# ---------------------------------------------------------------- the stop and its answer

def test_a_stop_prints_the_question_and_an_answer_is_applied_once(tmp_path, capsys):
    rig = stopped(tmp_path)
    out = capsys.readouterr().out
    assert rig.code == 2 and f"Question {Q1}: Ticket 01: the implementer reports" in out
    assert "  overrule-implementer (recommended): " in out and "--answer <question id>=<option id>" in out
    (q,) = rig.state()["run"]["questions"]
    assert (q["ticket"], q["kind"], q["subject"], q["stop"], q["answer"]) == (
        "01", "locked-test", "tests/test_01.py::test_x", 1, None)
    assert q["evidence"][0] == ".agent/implement/01/implementer-0.json" and rig.state()["run"]["stops"] == 1
    assert rig.led("Ticket 01: the operator decides on 1 question of kind locked-test in .agent/implement/decisions.json.")

    rig.run(steps(blocked(), impl("01")), "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    assert len(rig.led("Ruling: 01 locked test overruled: the test-writer corrects tests/test_01.py::test_x")) == 1
    prompt = rig.prompt("test-writer-0")
    assert "This call is a correction after a ruling" in prompt and "read `concerns` in .agent/implement/01/implementer-0.json" in prompt
    assert "ruling" not in rig.prompt("implementer-0") and prompt.rstrip().endswith("one ref store.")
    entry = rig.state()["tickets"]["01"]
    assert (entry["phase"], entry["note"], entry["locked"]) == ("merged", "", ["tests/test_01.py"])
    assert rig.state()["run"]["questions"][0]["answer"] == "overrule-test"
    assert decisions(rig)["questions"] == [] and not (rig.repo / PAGE).exists()
    assert "Question" not in capsys.readouterr().out

    # The question is closed: its answer cannot be given again, and the old answers file is history.
    rig.run({}, "--answer", f"{Q1}=overrule-implementer")
    assert rig.code == 3 and f"--answer {Q1}=overrule-implementer: no open question {Q1}" in capsys.readouterr().out
    rig.run({})
    assert rig.code == 0 and not rig.led("ignored") and len(rig.led("Ruling")) == 1


def test_overrule_implementer_runs_the_implementer_again_with_the_ruling(tmp_path, capsys):
    rig = stopped(tmp_path)
    rig.run(steps(blocked(), impl("01")), "--answer", f"{Q1}=nope")
    assert rig.code == 3 and f"--answer {Q1}=nope: {Q1} offers overrule-test, overrule-implementer" in capsys.readouterr().out
    assert not (rig.repo / driver_answers.FILE).exists()
    rig.run(steps(blocked(), impl("01")), "--answer", f"{Q1}=overrule-implementer")
    assert rig.code == 0, rig.ledger()
    assert rig.led("Ruling: 01 locked test stands: the implementer is overruled on tests/test_01.py::test_x")
    assert len(rig.calls("01", "test-writer")) == 1 and len(rig.calls("01", "implementer")) == 2
    assert "the operator ruled that the locked tests stand as they are" in rig.prompt("implementer-0")


def test_an_answer_needs_a_question_and_an_option(tmp_path, capsys):
    with pytest.raises(SystemExit) as usage:
        stopped(tmp_path, "--answer", "no-equals-sign")
    assert usage.value.code == 3 and "--answer" in capsys.readouterr().out


def test_a_second_stop_counts_on_and_its_page_holds_only_the_open_question(tmp_path, capsys):
    rig = stopped(tmp_path, then=(blocked(),))
    rig.run(steps(blocked(), blocked()), "--answer", f"{Q1}=overrule-test")
    assert rig.code == 2 and [q["id"] for q in decisions(rig)["questions"]] == ["01-locked-test-2"]
    html = (rig.repo / PAGE).read_text()
    assert 'data-q="01-locked-test-2"' in html and Q1 not in html and rig.state()["run"]["stops"] == 2


# ---------------------------------------------------------------- what a stop keeps for the rerun

def test_an_answers_file_of_an_earlier_run_is_not_applied_to_the_same_question_of_a_new_run(tmp_path, capsys):
    rig = stopped(tmp_path)
    old = decisions(rig)
    write_answers(rig, {Q1: "overrule-test"})
    # The operator starts again: the run directory goes, the answers file is outside it and stays.
    shutil.rmtree(rig.repo / ".agent/implement")
    git(rig.repo, "branch", "-D", "ticket/feat%2Fcsv/01")
    again = steps(blocked(), blocked(), impl("01"))
    rig.run(again)
    new = decisions(rig)
    assert rig.code == 2 and new["questions"] == old["questions"], "the same question, word for word"
    assert new["run"] == rig.state()["run"]["revision"] != old["run"] and new["revision"] != old["revision"]
    rig.run(again)
    assert rig.code == 2 and rig.led("is ignored: its revision") and not rig.led("Ruling")
    assert len(rig.calls("01", "implementer")) == 2, "no agent ran on the old answer"
    assert doc(asked(), run="a")["revision"] != doc(asked(), run="b")["revision"]


def partial():
    """An implementer that commits a part of its work, then reports the contradicted locked test."""
    step = {**blocked(), "files": {"demo/export.py": "def partial():\n    return 1\n"}, "commit": "feat: 01 partial work"}
    step["report"] = {**step["report"], "commits": ["$SHA"], "files": ["demo/export.py"]}
    return step


@pytest.mark.parametrize("option, role", [("overrule-implementer", "implementer"), ("overrule-test", "test-writer")])
def test_after_a_ruling_the_ticket_goes_on_from_the_commits_of_the_blocked_implementer(tmp_path, capsys, option, role):
    rig = Inside(tmp_path, ONE).run(steps(partial(), impl("01")))
    stop = rig.calls("01", "implementer")[0]["sha"]
    assert rig.code == 2 and rig.state()["tickets"]["01"]["tip"] == stop
    rig.run(steps(partial(), impl("01")), "--answer", f"{Q1}={option}")
    assert rig.code == 0, rig.ledger()
    after = rig.calls("01", role)[1]
    assert after["head"] == stop and "feat: 01 partial work" in after["log"].splitlines()
    # After overrule-test the implementer starts from the correction, which is on top of its own commit.
    assert "feat: 01 partial work" in rig.calls("01", "implementer")[1]["log"].splitlines()
    assert rig.state()["tickets"]["01"]["locked"] == ["tests/test_01.py"]
    assert "feat: 01 partial work" in rig.pr_subjects() and "feat: 01 rows" in rig.pr_subjects()


def test_a_stop_with_uncommitted_work_keeps_its_slot_and_takes_the_answer(tmp_path, capsys):
    dirty = {**blocked(), "files": {"demo/export.py": "half = 1\n"}}
    rig = Pushing(tmp_path, ONE).run(steps(dirty, impl("01")), *UNATTENDED)
    assert rig.code == 2 and git(rig.repo, "status", "--porcelain") == " M demo/export.py\n"
    rig.run(steps(dirty, impl("01")), *UNATTENDED)
    assert rig.code == 2 and len(rig.calls()) == 2, "the stop repeats: the work in the slot is no error"
    assert (rig.repo / "demo/export.py").read_text() == "half = 1\n"
    write_answers(rig, {Q1: "overrule-implementer"})
    rig.run(steps(dirty, impl("01")), *UNATTENDED)
    assert rig.code == 0, rig.ledger()
    second = rig.calls("01", "implementer")[1]
    assert second["status"] == "M demo/export.py" and second["branch"] == "ticket/feat%2Fcsv/01", "the work is in the tree of the rerun"
    prompt = (rig.folder() / "implementer-0.prompt.md").read_text()
    assert "the locked tests stand as they are" in prompt and "uncommitted work" in prompt
    assert "feat: 01 rows" in rig.pr_subjects() and not git(rig.repo, "status", "--porcelain")


# ---------------------------------------------------------------- the correction after overrule-test

OTHER = "tests/test_01b.py"
OTHER_TEST = "def test_y():\n    assert 1 == 'y'\n"


def two_locked():
    """A test-writer that locks two files; the contradiction is about the first one."""
    step = tw("01")
    step["files"][OTHER] = OTHER_TEST
    step["report"] = {**step["report"], "locked": ["tests/test_01.py", OTHER], "files": ["tests/test_01.py", OTHER]}
    return step


def restored(path):
    return f"Ticket 01: {path} was not the locked version at the correction: the driver restored it in a commit"


TWO_TESTS = "def test_x():\n    assert 01 == 'x'\n\n\ndef test_y():\n    assert 1 == 'y'\n"
ONLY_X = TWO_TESTS.split("\n\n\n")[0] + "\n"
# The edit of an agent: the one line of test_x, in place, in the file as the tree has it.
EDIT = "--- a/tests/test_01.py\n+++ b/tests/test_01.py\n@@ -2 +2 @@\n-    assert 01 == 'x'\n+    assert '01' == '01'\n"


@pytest.mark.parametrize("committed", [True, False])
def test_the_correction_starts_from_the_locked_version_of_a_file_the_implementer_changed(tmp_path, capsys, committed):
    writer = {**tw("01"), "files": {"tests/test_01.py": TWO_TESTS}}
    deletes = {**(partial() if committed else blocked()), "files": {"tests/test_01.py": ONLY_X}}
    rig = Inside(tmp_path, ONE)
    (rig.rig / "edit.patch").write_text(EDIT)
    edits = {**correction(), "files": {}, "git": [["apply", "--unidiff-zero", str(rig.rig / "edit.patch")]]}
    scenario = plan(**{"01/test-writer": [writer, edits], "01/implementer": [deletes, impl("01")]})
    rig.run(scenario)
    assert rig.code == 2, rig.ledger()
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == TWO_TESTS.replace("01 == 'x'", "'01' == '01'")
    assert len(rig.led(restored("tests/test_01.py"))) == 1 and not rig.led("locked file changed")
    assert rig.state()["tickets"]["01"]["held"] == {}


def test_a_locked_file_the_correction_changes_without_listing_it_is_still_a_finding(tmp_path, capsys):
    gutted = partial()
    gutted["files"] = {**gutted["files"], OTHER: "# gutted\n"}
    # The driver restored the other file before the correction; the correction guts it again and commits it unlisted.
    guts = {**correction(), "files": {"tests/test_01.py": NEW_TEST, OTHER: "# gutted again\n"}}
    restore = fix("01", 1, files={"demo/export.py": "x = 1\n", OTHER: OTHER_TEST})
    scenario = plan(**{"01/test-writer": [two_locked(), guts], "01/implementer": [gutted, impl("01")],
                       "01/fixer": [restore]})
    rig = Inside(tmp_path, ONE).run(scenario)
    first = rig.calls("01", "test-writer")[0]["sha"]
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    assert rig.led(restored(OTHER)) and not rig.led(restored("tests/test_01.py"))
    (line,) = rig.led(f"locked file changed: {OTHER}")
    assert "critical" in line and len(rig.calls("01", "fixer")) == 1
    entry = rig.state()["tickets"]["01"]
    assert entry["held"] == {OTHER: first} and entry["test_writer_sha"] == rig.calls("01", "test-writer")[1]["sha"]
    assert git(rig.repo, "show", f"feat/csv:{OTHER}") == OTHER_TEST and git(rig.repo, "show", "feat/csv:tests/test_01.py") == NEW_TEST


def test_a_correction_that_commits_more_than_the_files_it_lists_is_rejected(tmp_path, capsys):
    dirty = {**blocked(), "files": {"demo/export.py": "half = 1\n", OTHER: "# gutted\n"}}
    # The correction commits with `git add -A`, so the implementer's work in the tree goes into its commit. The
    # locked file it gutted is back at the locked version before the correction, so only the other file is a fault.
    scenario = plan(**{"01/test-writer": [two_locked(), correction(), correction()], "01/implementer": [dirty, impl("01")]})
    rig = Inside(tmp_path, ONE).run(scenario)
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 2, rig.ledger()
    assert rig.led("Ticket 01: BLOCKED at the test-writer: a correction may change only the files it lists in "
                   "`locked`: its commits also change demo/export.py.")
    # Issue #134: the refused correction asks the locked-test question again, from the same blocked report.
    (again,) = decisions(rig)["questions"]
    asked_again = rig.state()["run"]["questions"][-1]
    assert again["id"] == "01-locked-test-2" and asked_again["subject"] == "tests/test_01.py::test_x"
    assert asked_again["evidence"][0] == ".agent/implement/01/implementer-0.json"
    assert rig.led(restored(OTHER)) and git(rig.repo, "show", f"ticket/feat%2Fcsv/01:{OTHER}") == OTHER_TEST
    assert len(rig.calls("01", "test-writer")) == 3 and len(rig.calls("01", "implementer")) == 1, "one rerun, no implementer"
    assert "its commits also change demo/export.py" in rig.prompt("test-writer-0")
    assert "feat: 01 rows" not in rig.pr_subjects() and rig.state()["tickets"]["01"]["held"] == {}


MUTATION = "FAILED tests/test_01.py::test_x - AssertionError: fn_01 deleted"


def test_an_overrule_correction_whose_test_is_green_against_the_tip_reaches_the_review(tmp_path, capsys):
    # Issue #134: the corrected test is green on the implementer's code, so its red line comes from a reverted mutation.
    green = {**correction(), "stdout": MUTATION + "\n" + COST}
    green["report"] = {**green["report"], "red": [{"test": "tests/test_01.py::test_x", "line": MUTATION}]}
    scenario = plan(**{"01/test-writer": [tw("01"), green, green], "01/implementer": [partial(), impl("01")]})
    rig = Inside(tmp_path, ONE).run(scenario)
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    assert len(rig.calls("01", "test-writer")) == 2, "the green correction is accepted with no rerun"
    assert "break the production line that the test protects" in rig.prompt("test-writer-0")
    assert rig.state()["tickets"]["01"]["phase"] == "merged"


def test_an_overrule_correction_may_leave_the_overruled_concern_not_addressed(tmp_path, capsys):
    keeps = {**correction(), "files": {}, "commit": None, "stdout": COST}
    del keeps["files"], keeps["commit"]
    keeps["report"] = {**keeps["report"], "commits": [], "files": [], "locked": [], "red": [],
                       "not_addressed": [{"finding": CONTRA, "reason": "the test is right"}]}
    scenario = plan(**{"01/test-writer": [tw("01"), keeps, keeps], "01/implementer": [blocked(), impl("01")]})
    rig = Inside(tmp_path, ONE).run(scenario)
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    assert len(rig.calls("01", "test-writer")) == 2, "the concern is a target the correction can name"
    assert f"- {CONTRA}" in rig.prompt("test-writer-0")
    assert rig.led(f"Ticket 01: the test-writer correction did not correct {CONTRA}: the test is right.")

def test_a_correction_of_its_own_file_leaves_the_work_in_the_tree_to_the_implementer(tmp_path, capsys):
    dirty = {**blocked(), "files": {"demo/export.py": "half = 1\n"}}
    # This correction commits only its test file: the implementer's file is out of the reach of `git add -A`.
    only = {**correction(), "git_after": [["update-index", "--assume-unchanged", "demo/export.py"]]}
    goes_on = {**impl("01"), "files": {}, "git": [["update-index", "--no-assume-unchanged", "demo/export.py"]]}
    scenario = plan(**{"01/test-writer": [two_locked(), only], "01/implementer": [dirty, goes_on]})
    rig = Inside(tmp_path, ONE).run(scenario)
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    fixed = rig.calls("01", "test-writer")[1]["sha"]
    assert git(rig.repo, "show", "--name-only", "--format=", fixed).split() == ["tests/test_01.py"]
    assert git(rig.repo, "show", "feat/csv:demo/export.py") == "half = 1\n", "the implementer committed its own work"
    entry = rig.state()["tickets"]["01"]
    assert entry["held"] == {} and entry["locked"] == ["tests/test_01.py", OTHER] and not rig.led("locked file changed")
    assert git(rig.repo, "show", f"feat/csv:{OTHER}") == OTHER_TEST


STRONGER = "def test_x():\n    assert '01' == '01'  # stronger\n"


@pytest.mark.parametrize("cheat", [False, True])
def test_a_second_correction_of_a_locked_file_is_its_new_reference(tmp_path, capsys, cheat):
    # Issue #75: the review correction (#70) changes tests/test_01.py and first lists only the other file. Before
    # the fix it was accepted, `held` kept the overrule commit as the reference, and each review after a fix that
    # did not touch the file found "locked file changed". A correction now lists each file it changes.
    vacuous = finding("VACUOUS", "critical", kind="test", file="tests/test_01.py")
    files = {"tests/test_01.py": STRONGER.replace("stronger", "draft"), OTHER: OTHER_TEST + "# stronger\n"}
    unlisted = {**correction(), "files": files, "commit": "test: 01 stronger"}
    unlisted["report"] = {**unlisted["report"], "locked": [OTHER], "files": [OTHER]}
    # The rerun lists both files: the commit of the rejected attempt is in its commits too.
    listed = {**unlisted, "files": {"tests/test_01.py": STRONGER}}
    listed["report"] = {**unlisted["report"], "locked": ["tests/test_01.py", OTHER]}
    gutted = fix("01", 1, files={"demo/export.py": "x = 1\n", "tests/test_01.py": "# gutted\n"})
    fixes = [gutted, fix("01", 2, files={"demo/export.py": "x = 2\n", "tests/test_01.py": STRONGER})] if cheat else [
        fix("01", 1)]
    scenario = plan(**{"01/test-writer": [two_locked(), correction(), unlisted, listed],
                       "01/implementer": [blocked(), impl("01")], "01/fixer": fixes,
                       "01/reviewer": [review("01", [vacuous]), review("01", [finding("STUCK")])] + [review("01")] * 3})
    rig = Inside(tmp_path, ONE).run(scenario)
    rig.run(scenario, "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.ledger()
    assert rig.led("BLOCKED", "correction") == [] and rig.led("its commits also change tests/test_01.py")
    entry = rig.state()["tickets"]["01"]
    assert entry["held"] == {} and entry["test_writer_sha"] == rig.calls("01", "test-writer")[3]["sha"]
    assert len(rig.led("locked file changed: tests/test_01.py")) == cheat, "only a fixer's change is a finding"
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == STRONGER


def test_a_locked_file_keeps_its_old_reference_until_a_test_writer_lists_it(repo, tmp_path):
    def commit(name, text):
        (tmp_path / name).write_text(text)
        return git(tmp_path, "add", "-A") + git(tmp_path, "commit", "-q", "-m", name) + git(tmp_path, "rev-parse", "HEAD").strip()

    repo()
    commit("a.py", "1\n"), commit("c.py", "1\n")
    old = commit("b.py", "1\n")
    commit("b.py", "the implementer changed it\n")
    tip = commit("a.py", "the correction\n")
    first = driver_review.written(".", {"test_writer_sha": None, "locked": [], "held": {}}, ["a.py", "b.py", "c.py"], old)
    assert first == {"test_writer_sha": old, "locked": ["a.py", "b.py", "c.py"], "held": {}}
    after = driver_review.written(".", first, ["a.py"], tip)
    assert after == {"test_writer_sha": tip, "locked": ["a.py", "b.py", "c.py"], "held": {"b.py": old}}
    (finding,) = driver_review.locked_findings(".", after)
    assert (finding["title"], finding["evidence"]) == ("locked file changed: b.py", f"git diff {old}..HEAD -- b.py")
    assert driver_review.written(".", after, ["b.py"], tip)["held"] == {}, "the test-writer corrected it: a new reference"
    # A diff that git cannot make holds each other file, and then the check of the review fails in the open.
    lost = driver_review.written(".", {**first, "test_writer_sha": "0" * 40}, ["a.py"], tip)
    assert lost["held"] == {"b.py": "0" * 40, "c.py": "0" * 40}
    assert [f["title"].split(":")[0] for f in driver_review.locked_findings(".", lost)] == ["locked file check failed"]


# ---------------------------------------------------------------- legacy finding-question decisions

def test_one_answer_that_keeps_the_ticket_blocked_wins_over_an_accept():
    one = asked("finding", subject="A")
    two = asked("finding", subject="B", before=[one])
    other = asked("finding", ticket="02", subject="C", before=[one, two], stop=2)
    rulings, moved = driver_decisions.apply([one, two, other], {one["id"]: "accept", two["id"]: "redesign",
                                                                other["id"]: "accept", "09-finding-1": "accept"})
    assert moved == {"02": {"phase": "merge"}} and len(rulings) == 3
    assert rulings[1] == "Ruling: 01 stays BLOCKED, the design changes first (redesign): B."
    assert driver_decisions.apply([one, two, other], {one["id"]: "split"}) == ([], {}), "an answer is applied once"


# ---------------------------------------------------------------- unattended

def test_unattended_writes_the_stage_file_and_prints_no_question(tmp_path, capsys):
    rig = Pushing(tmp_path, ONE).run(steps(blocked(), impl("01")), *UNATTENDED)
    assert rig.code == 2 and "Question" not in rig.out
    assert stage(rig) == {"stage": "implement", "status": "awaiting-review", "artifact": PAGE, "open_questions": 1}
    write_answers(rig, {Q1: "overrule-test"})
    rig.run(steps(blocked(), impl("01")), *UNATTENDED)
    assert rig.code == 0 and stage(rig) == {"stage": "implement", "status": "done"}, "no stage file of the earlier run stays"
    assert (rig.repo / "tests/test_01.py").read_text() == NEW_TEST


def test_unattended_names_the_blocked_tickets_and_needs_a_stage_path_to_write(tmp_path):
    rig = Pushing(tmp_path, ONE, gate={"red": ["ticket/feat%2Fcsv/01"]})
    rig.run(plan(**{"01/fixer": [fix("01", n) for n in (1, 2)]}), "--unattended")
    assert rig.code == 1 and not (rig.repo / ".agent/stage.json").exists()
    rig.run(plan(**{"01/fixer": [fix("01", n) for n in (1, 2)]}), "--unattended", "--stage-json", ".agent/out/stage.json")
    assert json.loads((rig.repo / ".agent/out/stage.json").read_text()) == {
        "stage": "implement", "status": "blocked", "blocked": ["01"]}


# ---------------------------------------------------------------- the page

def test_a_stop_without_the_review_page_skill_says_so_and_the_next_run_writes_the_page(tmp_path, monkeypatch, capsys):
    template = driver_review_page.TEMPLATE
    monkeypatch.setattr(driver_review_page, "TEMPLATE", tmp_path / "skills/review-page/template.html")
    rig = stopped(tmp_path)
    out = capsys.readouterr().out
    assert rig.code == 3 and f"cannot write {PAGE}" in out and "skills/review-page/template.html" in out
    assert "install the review-page skill next to implement-spec" in out and decisions(rig)["questions"]
    rig.run({})
    assert rig.code == 3 and not rig.calls("01", "reviewer"), "the same error, before any agent"
    monkeypatch.setattr(driver_review_page, "TEMPLATE", template)
    rig.run({})
    assert rig.code == 2 and f'data-q="{Q1}"' in (rig.repo / PAGE).read_text()


def test_a_template_of_another_shape_is_an_error_and_every_text_of_a_question_is_escaped(tmp_path, monkeypatch):
    q = asked(subject='<img onerror="x">')
    q["evidence"], q["options"][0]["description"] = ["<i>.json"], "a & b"
    html = driver_review_page.render(doc(q))
    assert "<img" not in html and "&lt;img onerror=&quot;x&quot;&gt;" in html and "<code>&lt;i&gt;.json</code>" in html
    assert "a &amp; b" in html and "<h1>1 open question of the implementation</h1>" in html
    monkeypatch.setattr(driver_review_page, "TEMPLATE", tmp_path / "template.html")
    (tmp_path / "template.html").write_text(html.replace("<!-- slot: track -->", ""))
    with pytest.raises(ValueError, match="has 0 matches"):
        driver_review_page.render(doc(q))


# ---------------------------------------------------------------- questions

def test_only_an_implementer_or_a_fixer_blocked_on_the_contradiction_concern_is_a_stop():
    report = {"status": "BLOCKED", "concerns": [CONTRA, "other", "locked test contradicts spec: b::c", driver_decisions.CONTRADICTS]}
    assert driver_decisions.contradicted("implementer", report) == "tests/test_01.py::test_x; b::c"
    assert driver_decisions.contradicted("fixer", report) == "tests/test_01.py::test_x; b::c"
    assert driver_decisions.contradicted("reviewer", report) == ""
    assert driver_decisions.contradicted("implementer", {**report, "status": "DONE"}) == ""
    assert driver_decisions.contradicted("implementer", {}) == "", "a rejected report is no report"
    assert driver_decisions.contradicted("implementer", {"status": "BLOCKED", "concerns": [driver_decisions.CONTRADICTS]}) == ""


def test_question_ids_count_per_ticket_and_kind_over_the_run_and_only_open_questions_are_published():
    one = asked()
    two, other, found = asked(before=[one]), asked(ticket="02", before=[one]), asked("finding", before=[one])
    assert [q["id"] for q in (one, two, other, found)] == ["01-locked-test-1", "01-locked-test-2", "02-locked-test-1", "01-finding-1"]
    both = doc(one, two)
    assert set(both["questions"][0]) == set(driver_decisions.PUBLIC) and len(both["questions"]) == 2
    one["answer"] = "overrule-test"
    left = doc(one, two)
    assert [q["id"] for q in left["questions"]] == ["01-locked-test-2"] and left["revision"] != both["revision"]
    assert driver_decisions.parked([one, two]) and not driver_decisions.parked([one, found])
    assert driver_decisions.text([], "answers.json", "page.html") == ""
    for kind in driver_decisions.KINDS.values():
        assert 2 <= len(kind.options) <= 4 and kind.recommended in kind.options


# ---------------------------------------------------------------- the answers file

@pytest.fixture
def one(tmp_path, monkeypatch):
    """The decisions file of one open question, in a directory where the answers file goes."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent").mkdir()
    return doc(asked())


def answers(document, **fields):
    body = {"v": 1, "stage": "implement", "submitted": "approve", "revision": document["revision"],
            "answers": {Q1: "overrule-test"}, **fields}
    driver_answers.FILE.write_text(json.dumps(body))
    return driver_answers.read(document)


@pytest.mark.parametrize("fields, why", [
    ({"revision": "0" * 64}, "its revision 000000000000 is not the revision"),
    ({"revision": None}, "its revision None is not the revision"),
    ({"submitted": None}, "it is a draft: `submitted` is not set"),
    ({"stage": "plan"}, "it is not a version 1 file of the implement stage"),
    ({"v": 2}, "it is not a version 1 file of the implement stage"),
    ({"answers": ["overrule-test"]}, "it is not an object with an `answers` object")])
def test_a_file_that_is_not_a_submission_for_this_revision_gives_no_answer(one, fields, why):
    found, (ignored,) = answers(one, **fields)
    assert found == {} and ignored.startswith(f"The answers file .agent/implement-answers.json is ignored: {why}")


@pytest.mark.parametrize("content", ["{not json", "[]", '"text"'])
def test_a_file_that_is_not_a_json_object_gives_no_answer(one, content):
    driver_answers.FILE.write_text(content)
    found, (ignored,) = driver_answers.read(one)
    assert found == {} and "is ignored: it is not" in ignored


def test_each_answer_is_checked_on_its_own_and_a_note_goes_with_its_question(one):
    found, ignored = answers(one, answers={Q1: "overrule-test", f"{Q1}.note": "see ADR 3", "track": "standard",
                                           "02-finding-1.note": "x"})
    assert found == {Q1: "overrule-test", f"{Q1}.note": "see ADR 3"}
    assert ignored == ["The answer for track in .agent/implement-answers.json is ignored: there is no open question track.",
                       "The answer for 02-finding-1.note in .agent/implement-answers.json is ignored: there is no open "
                       "question 02-finding-1."]
    found, (ignored,) = answers(one, answers={Q1: ["overrule-test"], f"{Q1}.note": 5})
    assert found == {} and ignored.endswith("the question offers overrule-test, overrule-implementer.")


def test_no_open_question_or_no_file_reads_nothing_and_given_writes_only_valid_answers(one):
    assert driver_answers.read(one) == ({}, [])
    assert driver_answers.given([], one) == [] and not driver_answers.FILE.exists()
    assert driver_answers.given([(Q1, "overrule-test"), ("zz", "accept")], one) == ["--answer zz=accept: no open question zz"]
    assert not driver_answers.FILE.exists()
    assert driver_answers.given([(Q1, "overrule-test")], one) == []
    assert driver_answers.read(one) == ({Q1: "overrule-test"}, [])
    assert driver_answers.read(doc()) == ({}, []), "with no open question the file is history"


# ---------------------------------------------------------------- an implementer with nothing to change

NO_CHANGE = "the correction made the locked tests green: the start needs no change"


def unchanged():
    """An implementer step with no commit: the start commit already meets the ticket."""
    return {"report": {"status": "DONE_WITH_CONCERNS", "commits": [], "files": [], "concerns": [NO_CHANGE],
                       "green": ["1 passed"]}}


def test_an_implementer_with_nothing_to_change_after_a_correction_is_accepted_and_its_start_is_gated(tmp_path):
    """Issue 123: after `overrule-test` the correction made the locked tests green, so the implementer had nothing to
    change, and no report of it was valid: NEEDS_CONTEXT blocked the ticket with no question."""
    rig = stopped(tmp_path)
    rig.run(steps(blocked(), unchanged()), "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert len(rig.calls("01", "implementer")) == 2 and rig.led("implementer ran on", "report accepted")
    corrected = rig.calls("01", "test-writer")[1]["sha"]
    assert corrected in [gate["commit"] for gate in rig.gates()]
    assert rig.calls("01", "reviewer")[0]["head"] == corrected
    assert "list no commit" in rig.prompt("implementer-0")


# ---------------------------------------------------------------- a stop or a rejection: the rerun, then ticket-blocked

SONNET, OPUS_5_5, ASTRA = "claude-sonnet-5-5@medium", "claude-opus-5-5@medium", "openai/gpt-6-astra@medium"
TWO = {"implementer": [SONNET, OPUS_5_5], "reviewer": [ASTRA, OPUS_5_5]}
TB1 = "01-ticket-blocked-1"
NO_ACCESS = "no write access to fixtures/"
NO_FINDINGS = "report: 'findings' is a required property"
# 01 and 02 are independent, 03 waits on 01.
TRIO = {"01-export-rows.md": ticket("01", "Export rows"),
        "02-quote-fields.md": ticket("02", "Quote fields", touches="`demo/other.py: g`"),
        "03-cli-flag.md": ticket("03", "CLI flag", "01", touches="`demo/cli.py: main`")}


def stops(status="NEEDS_CONTEXT", concern="the ticket names no column order", role="implementer", **step):
    report = {"status": status, "commits": [], "files": [], "concerns": [concern], "green": [],
              **{"fixer": {"ticket": "01"}, "test-writer": {"locked": [], "red": []}}.get(role, {})}
    return {"report": report, **step}


def left_wip():
    """The rerun's stop, with a commit: the tip the ticket leaves."""
    step = stops("BLOCKED", NO_ACCESS, files={"demo/export.py": "wip = 1\n"}, commit="wip: 01")
    step["report"].update(commits=["$SHA"], files=["demo/export.py"])
    return step


def entries(rig, nn, role):
    return [agent["entry"] for agent in rig.state()["run"]["agents"] if (agent["ticket"], agent["role"]) == (nn, role)]


def trio(tmp_path, *implementers):
    """Run 1 of TRIO with 01's implementer steps; 02 writes its own file, so 01 merges after it with no conflict."""
    rig = Inside(tmp_path, TRIO, models=TWO)
    return rig, plan(**{"01/implementer": list(implementers), "02/implementer": [impl("02", "demo/other.py")]})


def test_a_stopped_implementer_runs_again_on_its_second_entry_with_its_concern_and_asks_nothing(tmp_path):
    rig = Inside(tmp_path, ONE, models=TWO).run(steps(stops(concern="which CSV dialect?"), impl("01")))
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert entries(rig, "01", "implementer") == [SONNET, OPUS_5_5]
    assert "which CSV dialect?" in rig.calls("01", "implementer")[1]["prompt"]
    assert not rig.state()["run"]["questions"] and rig.state()["run"]["stops"] == 0


def test_a_rejected_review_report_runs_again_on_the_second_reviewer_entry_with_the_error(tmp_path):
    bad = review("01")
    del bad["report"]["findings"]
    rig = Inside(tmp_path, ONE, models=TWO).run(plan(**{"01/reviewer": [bad]}))
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert entries(rig, "01", "reviewer") == [ASTRA, OPUS_5_5]
    assert NO_FINDINGS in rig.calls("01", "reviewer")[1]["prompt"]


def test_a_role_with_one_entry_that_stops_runs_again_on_that_entry(tmp_path):
    rig = Inside(tmp_path, ONE).run(plan(**{"01/test-writer": [stops(role="test-writer"), tw("01")]}))
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    (one,) = set(entries(rig, "01", "test-writer"))
    assert len(entries(rig, "01", "test-writer")) == 2 and one == "anthropic/claude-opus-5@medium"


def test_a_second_stop_blocks_the_ticket_with_one_ticket_blocked_question(tmp_path):
    rig = Inside(tmp_path, ONE, models=TWO).run(steps(stops(), stops("BLOCKED", NO_ACCESS)))
    assert rig.code == 2 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    (question,) = rig.state()["run"]["questions"]
    assert (question["id"], question["kind"], question["recommended"]) == (TB1, "ticket-blocked", "retry")
    assert [o["id"] for o in question["options"]] == ["retry", "edit"]
    assert "implementer" in question["subject"] and NO_ACCESS in question["subject"]
    assert question["evidence"][0] == ".agent/implement/01/implementer-0.json"
    assert entries(rig, "01", "implementer") == [SONNET, OPUS_5_5]


def test_a_ticket_blocked_question_holds_only_its_ticket_and_those_that_wait_on_it(tmp_path):
    rig, run = trio(tmp_path, stops(), stops("BLOCKED", NO_ACCESS))
    rig.run(run)
    assert rig.code == 2, rig.ledger()
    tickets = rig.state()["tickets"]
    assert [q["id"] for q in rig.state()["run"]["questions"]] == [TB1]
    assert (tickets["01"]["phase"], tickets["02"]["phase"], tickets["03"]["phase"]) == ("blocked", "merged", "pending")
    assert "feat: 02 rows" in rig.pr_subjects() and not rig.calls("03")
    assert rig.led("End-of-run work skipped") and not rig.calls("final", "final-reviewer")


def test_retry_runs_the_role_on_its_first_entry_from_the_tip_it_left_and_resumes_only_its_ticket(tmp_path):
    rig, run = trio(tmp_path, stops(), left_wip(), impl("01"))
    rig.run(run)
    wip, done_02 = rig.calls("01", "implementer")[1]["sha"], len(rig.calls("02"))
    assert rig.code == 2 and rig.state()["tickets"]["01"]["tip"] == wip, rig.ledger()
    rig.run(run, "--answer", f"{TB1}=retry")
    assert rig.code == 0 and {e["phase"] for e in rig.state()["tickets"].values()} == {"merged"}, rig.ledger()
    assert rig.led(f"Ruling: 01 runs again (retry): the implementer")
    resumed = rig.calls("01", "implementer")[2]
    assert entries(rig, "01", "implementer") == [SONNET, OPUS_5_5, SONNET] and resumed["head"] == wip
    assert len(rig.calls("02")) == done_02
    assert rig.calls("03")[0]["t0"] > resumed["t1"] and "feat: 01 rows" in rig.calls("03")[0]["pr_log"]


def test_edit_keeps_the_ticket_blocked_with_the_ruling_and_runs_no_agent_of_it(tmp_path):
    rig = Inside(tmp_path, ONE, models=TWO).run(steps(stops(), stops("BLOCKED", NO_ACCESS)))
    seen = len(rig.calls("01"))
    rig.run(steps(stops(), stops("BLOCKED", NO_ACCESS), impl("01")), "--answer", f"{TB1}=edit")
    assert rig.code == 1 and rig.state()["tickets"]["01"]["phase"] == "blocked", rig.ledger()
    assert rig.led("Ruling: 01 stays BLOCKED", "(edit)") and len(rig.calls("01")) == seen


def test_two_blocked_tickets_put_both_questions_in_decisions_and_on_the_one_page(tmp_path):
    rig, run = trio(tmp_path, stops(), stops())
    run["02/implementer"] = [stops(), stops()]
    rig.run(run)
    assert rig.code == 2, rig.ledger()
    ids = ["01-ticket-blocked-1", "02-ticket-blocked-1"]
    assert sorted(q["id"] for q in decisions(rig)["questions"]) == ids
    html = (rig.repo / PAGE).read_text()
    assert all(f'data-q="{qid}"' in html for qid in ids)


def test_a_fixer_that_stops_twice_blocks_and_retry_runs_its_round_again_after_a_review(tmp_path):
    """The round that stopped does not count: `retry` reviews the tip the fixer left, then a fixer runs that round."""
    run = plan(**{"01/reviewer": [review("01", [STUCK]), review("01", [STUCK]), review("01")],
                  "01/fixer": [stops(role="fixer"), stops(role="fixer"), fix("01", 1)]})
    rig = Inside(tmp_path, ONE).run(run)
    assert rig.code == 2 and rig.state()["tickets"]["01"]["round"] == 0, rig.ledger()
    (question,) = rig.state()["run"]["questions"]
    assert (question["id"], question["evidence"][0]) == (TB1, ".agent/implement/01/fixer-1.json")
    rig.run(run, "--answer", f"{TB1}=retry")
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert rig.state()["tickets"]["01"]["round"] == 1 and len(rig.calls("01", "fixer")) == 3


def test_the_rerun_of_fix_round_2_wraps_to_the_first_implementer_entry(tmp_path):
    run = plan(**{"01/reviewer": [review("01", [STUCK])] * 2 + [review("01")],
                  "01/fixer": [fix("01", 1), stops(role="fixer"), fix("01", 2)]})
    rig = Inside(tmp_path, ONE, models=TWO).run(run)
    assert rig.code == 0, rig.ledger()
    assert entries(rig, "01", "fixer") == [SONNET, OPUS_5_5, SONNET]


def test_the_removed_kinds_cannot_be_asked():
    removed = {"review-stopped", "review-rejected", "implementer-stopped", "fixer-stopped"}
    assert not removed & set(driver_decisions.KINDS) and "ticket-blocked" in driver_decisions.KINDS


# ---------------------------------------------------------------- a test-writer report; a question of a merged ticket


def test_a_test_writer_report_that_locks_a_file_it_deleted_is_rejected(tmp_path):
    """Issue #136: the correction of ticket 21 deleted a locked file and still listed it in `locked`; the driver
    accepted it, and no reviewer could prove that file."""
    deleted = two_locked()
    deleted["git_after"] = [["add", "-A"], ["commit", "-q", "-m", "test: 01 both"], ["rm", "-q", "tests/test_01b.py"]]
    rig = Inside(tmp_path, ONE).run(plan(**{"01/test-writer": [deleted, two_locked()]}))
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert rig.led("Ticket 01: test-writer ran on", "locked path tests/test_01b.py is not a file at the tip of")
    assert len(rig.calls("01", "test-writer")) == 2 and len(rig.calls("01", "reviewer")) == 1


def test_a_question_of_a_ticket_that_merged_is_dropped_and_does_not_park_the_run(tmp_path, capsys):
    """Issue #136: the operator set a stopped ticket to `merge` by hand; its question stayed open, with exit 2."""
    rig = Inside(tmp_path, ONE).run(steps(stops(), stops()))
    assert rig.code == 2, rig.ledger()
    capsys.readouterr()
    path = rig.repo / ".agent/implement/state.json"
    state = json.loads(path.read_text())
    state["tickets"]["01"]["phase"] = "merge"
    path.write_text(json.dumps(state))
    rig.run(steps(stops(), stops()))
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert decisions(rig)["questions"] == [] and TB1 not in capsys.readouterr().out
    assert rig.led(f"Ticket 01: the question {TB1} is dropped: the ticket merged.")


def test_dropping_the_questions_of_merged_tickets_keeps_the_answered_ones_and_those_of_other_tickets():
    state = {"tickets": {"01": {"phase": "merged"}, "02": {"phase": "blocked"}},
             "run": {"questions": [asked(), {**asked(stop=2), "id": "01-x", "answer": "accept"}, asked(ticket="02")]}}
    assert [q["id"] for q in driver_decisions.drop_merged(state)] == ["01-locked-test-1"]
    assert [q["id"] for q in state["run"]["questions"]] == ["01-x", "02-locked-test-1"]
