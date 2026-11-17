"""`leave-out`: once the driver's retries are spent on a ticket's stop, the driver leaves the ticket out of the run,
with each ticket it blocks; the merged tickets get the end of run and a draft PR that names them. The arbiter rules
`keep-test` or `correct-test` on a `spec-ambiguous` question, at most `--retries` stops of one ticket; the next one
leaves the ticket out. `--ask` (IMPLEMENT_SPEC_ASK=1, which conftest pins) asks the operator instead.

In process, on the rigs of test_tidy and test_arbitration."""
from test_arbiter import ARBITER, ruled
from test_arbitration import AMBIGUOUS, SECOND, SINGLE, correction, disputes, ruling
from test_cli import git, ticket
from test_final import RESOLVE, THREE, final, fixer, fn
from test_fixloop import Inside as Local
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_review import ONE, review
from test_scoped import ATOMIC
from test_stops import UNATTENDED, blocked, decisions, stage
from test_tidy import Inside
from test_tidy import tools  # noqa: F401  (tools is an autouse fixture)
from test_walk import impl, plan, tw

AUTONOMOUS = ("--no-ask", "--retries", "0", *UNATTENDED)
FOUR = {**THREE, "04-export-docs.md": ticket("04", "Export docs", "02", touches="`demo/docs.py: h`")}


def dirty(path, text="half = 1\n"):
    """A BLOCKED implementer that leaves uncommitted work in its slot."""
    return {**blocked(), "files": {path: text}}


def test_a_blocked_ticket_is_left_out_with_the_tickets_it_blocks_and_the_rest_ships_as_draft(tmp_path):
    rig = Inside(tmp_path, THREE)
    rig.run(plan(**{"01/implementer": [blocked(), blocked()]}), *AUTONOMOUS)
    assert rig.code == 0, rig.output()
    tickets = rig.state()["tickets"]
    assert (tickets["01"]["phase"], tickets["02"]["phase"], tickets["03"]["phase"]) == ("blocked", "pending", "merged")
    assert not rig.calls("02"), "a ticket that a left-out ticket blocks never starts"
    assert rig.led("(leave-out)") and rig.led("Ruled by the driver, not the operator.")
    (pr,) = rig.prs()
    assert "--draft" in pr["argv"]
    assert "Left out of the run, so not in this PR: 01 Export rows; 02 Quote fields." in pr["body"]
    assert rig.led("open as a draft: tickets are left out of the run: 01, 02")
    assert stage(rig) == {"stage": "implement", "status": "done", "draft": True, "decisions": 1}


def test_a_left_out_ticket_s_work_in_slot_2_is_saved_and_the_tickets_it_blocks_through_another_are_left_out(
        tmp_path):
    rig = Inside(tmp_path, FOUR)
    rig.run(plan(**{"03/implementer": [dirty("demo/cli.py"), dirty("demo/cli.py")]}), *AUTONOMOUS)
    assert rig.code == 0, rig.output()
    phases = {number: entry["phase"] for number, entry in rig.state()["tickets"].items()}
    assert phases == {"01": "merged", "02": "pending", "03": "blocked", "04": "pending"}
    (pr,) = rig.prs()
    assert "Left out of the run, so not in this PR: 02 Quote fields; 03 CLI flag; 04 Export docs." in pr["body"]
    patch = ".agent/implement/03/left-out.patch"
    assert rig.led(f"Ticket 03: left out; its uncommitted work in slot 2 is saved as {patch}, and the slot is free.")
    assert "+half = 1" in (rig.repo / patch).read_text()
    slot = rig.repo / ".agent/worktrees/slot-2"
    assert git(slot, "status", "--porcelain") == "" and git(slot, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"


def test_a_left_out_ticket_s_work_in_slot_1_is_saved_and_the_next_ticket_starts_there_clean(tmp_path):
    rig = Inside(tmp_path, THREE)
    rig.run(plan(**{"01/implementer": [dirty("demo/export.py"), dirty("demo/export.py")]}), *AUTONOMOUS, parallel="1")
    assert rig.code == 0, rig.output()
    assert rig.state()["tickets"]["03"]["phase"] == "merged"
    assert rig.led("Ticket 01: left out; its uncommitted work in slot 1 is saved as .agent/implement/01/left-out.patch")
    (writer,) = rig.calls("03", "test-writer")
    assert writer["status"] == "" and writer["cwd"] == str(rig.repo.resolve())
    assert "half = 1" not in git(rig.repo, "show", "feat/csv:demo/export.py")


def test_an_operator_s_leave_out_saves_the_work_of_slot_1_before_the_next_ticket_starts_there(tmp_path):
    rig = Inside(tmp_path, THREE)
    steps = plan(**{"01/implementer": [dirty("demo/export.py"), dirty("demo/export.py")]})
    rig.run(steps, "--retries", "0", *UNATTENDED, parallel="1")
    assert rig.code == 2, rig.output()
    rig.run(steps, "--retries", "0", "--answer", "01-ticket-blocked-1=leave-out", *UNATTENDED, parallel="1")
    assert rig.code == 0 and rig.state()["tickets"]["03"]["phase"] == "merged", rig.output()
    assert rig.led("Ticket 01: left out; its uncommitted work in slot 1 is saved as .agent/implement/01/left-out.patch")
    (writer,) = rig.calls("03", "test-writer")
    assert writer["status"] == ""


def test_the_rebase_conflict_agent_locks_only_the_merged_tickets(tmp_path):
    rig = Inside(tmp_path, THREE)
    rig.move_base({"demo/cli.py": "def main():\n    return 'base'\n"})
    # The resolution writes the locked test of 01, which is left out: no merged ticket locks it.
    resolve = {**RESOLVE, "files": {"demo/cli.py": "x = 1\n", "tests/test_01.py": "def test_x():\n    assert True\n"}}
    rig.run(plan(**{"01/implementer": [blocked(), blocked()], "final/conflict": [resolve]}), *AUTONOMOUS)
    assert rig.code == 0, rig.output()
    (line,) = rig.led("End of run: conflict ran on")
    assert line.endswith("report accepted."), line


def test_the_final_fixer_owns_and_locks_only_the_merged_tickets(tmp_path):
    rig = Inside(tmp_path, THREE)
    rig.run(plan(**{"01/implementer": [blocked(), blocked()], "final/final-spec": [final(ATOMIC)],
                    "final/final-reviewer": [final()],
                    "final/fixer": [fixer(fn("03"), "fix it", ticket_of="01"),
                                    fixer(fn("03"), "fix it", ticket_of="03")]}),
            *AUTONOMOUS)
    assert rig.code == 0, rig.output()
    first, second = rig.final_calls("fixer")
    assert "IMPLEMENT_SPEC_LOCKED='tests/test_03.py'" in first["prompt"]
    rejected, _ = rig.led("End of run: fixer ran on")
    assert "01" in rejected and "accepted" not in rejected
    assert [c["subject"] for c in rig.commits(rig.start) if c["subject"].startswith("fix(")] == ["fix(03): fix it"]


def test_ask_keeps_the_park_and_a_ticket_question_offers_leave_out(tmp_path):
    rig = Inside(tmp_path, THREE)
    rig.run(plan(**{"01/implementer": [blocked(), blocked()]}), "--retries", "0", *UNATTENDED)
    assert rig.code == 2 and not rig.pushes(), rig.out
    (question,) = decisions(rig)["questions"]
    assert [o["id"] for o in question["options"]] == ["retry", "leave-out", "edit"]


def test_ask_on_spec_ambiguous_asks_the_operator_with_leave_out_and_no_arbiter(tmp_path):
    rig = ambiguous(tmp_path, "--retries", "0", "--ask", then=())
    assert rig.code == 2 and not rig.calls("final", "arbiter"), rig.ledger()
    (question,) = decisions(rig)["questions"]
    assert question["id"] == AMBIGUOUS and "leave-out" in [o["id"] for o in question["options"]]


def test_no_ticket_is_left_out_when_it_would_leave_out_every_ticket(tmp_path):
    rig = Inside(tmp_path, ONE)
    rig.run(plan(**{"01/implementer": [blocked(), blocked()]}), *AUTONOMOUS)
    assert rig.code == 2 and not rig.pushes(), rig.out
    assert [q["id"] for q in decisions(rig)["questions"]] == ["01-ticket-blocked-1"]


def ambiguous(tmp_path, *extra, files=ONE, arbiter=(), then=(impl("01"),), writer=(tw("01"),)):
    """Ticket 01 after a `code-wrong` ruling and a second dispute on the same test, run with `extra`."""
    rig = Local(tmp_path, files, models=ARBITER)
    return rig.run(plan(**{"01/implementer": [disputes(), disputes(), *then], "01/test-writer": list(writer),
                           "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01")],
                           "final/arbiter": list(arbiter)}), "--no-ask", *extra)


def test_the_arbiter_rules_keep_test_on_spec_ambiguous_and_the_ticket_goes_on(tmp_path):
    rig = ambiguous(tmp_path, "--retries", "1", arbiter=[ruled((AMBIGUOUS, "keep-test"))])
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    (arbiter,) = rig.calls("final", "arbiter")
    assert f"- {AMBIGUOUS}: " in arbiter["prompt"] and "Options: keep-test, correct-test." in arbiter["prompt"]
    assert rig.led("(keep-test)", "Ruled by the arbiter, not the operator: why keep-test. Cost if wrong: cost of")


def test_the_arbiter_rules_correct_test_and_the_test_writer_corrects_the_test_then_the_ticket_merges(tmp_path):
    rig = ambiguous(tmp_path, "--retries", "1", arbiter=[ruled((AMBIGUOUS, "correct-test"))],
                    writer=[tw("01"), correction()])
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert rig.led("(correct-test)", "Ruled by the arbiter, not the operator: why correct-test.")
    assert len(rig.calls("01", "test-writer")) == 2


def test_a_second_spec_ambiguous_stop_past_the_arbiter_s_budget_leaves_the_ticket_out(tmp_path):
    rig = ambiguous(tmp_path, "--retries", "1", files={**ONE, **SECOND}, arbiter=[ruled((AMBIGUOUS, "keep-test"))],
                    then=(disputes(),))
    assert rig.code == 0, rig.ledger()
    assert len(rig.calls("final", "arbiter")) == 1 and rig.state()["tickets"]["02"]["phase"] == "merged"
    assert rig.led("Ruling: 01 is left out of the run, with each ticket it blocks (leave-out)")
    assert [q["id"] for q in rig.state()["run"]["questions"] if q["answer"] == "leave-out"] == ["01-spec-ambiguous-2"]


def test_the_arbiter_rules_in_its_own_checkout_while_slot_1_holds_a_stopped_ticket_s_work(tmp_path):
    test, question = "tests/test_02.py::test_x", "02-spec-ambiguous-1"
    rig = Local(tmp_path, {**ONE, **SECOND}, models=ARBITER)
    rig.run(plan(**{"01/implementer": [dirty("demo/export.py")] * 4,
                    "02/implementer": [disputes((test, SINGLE)), disputes((test, "the example in R3 uses '")),
                                       impl("02")],
                    "02/reviewer": [ruling("code-wrong", "R3 says double quotes", test=test), review("02")],
                    "final/arbiter": [ruled((question, "keep-test"))]}), "--no-ask", "--retries", "1")
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.ledger()
    (arbiter,) = rig.calls("final", "arbiter")
    assert arbiter["cwd"].endswith(".agent/worktrees/arbiter") and arbiter["status"] == ""
    assert "../../../specs/csv/spec.md" in arbiter["prompt"]
    assert rig.led("Ticket 01: left out; its uncommitted work in slot 1 is saved")
    assert not (rig.repo / ".agent/worktrees/arbiter").exists()


def test_an_arbiter_ruling_that_the_question_does_not_offer_is_rejected(tmp_path):
    rig = ambiguous(tmp_path, "--retries", "1",
                    arbiter=[ruled((AMBIGUOUS, "accept")), ruled((AMBIGUOUS, "keep-test"))])
    assert rig.code == 0, rig.ledger()
    first, _ = rig.led("End of run: arbiter ran on")
    assert f"rulings: {AMBIGUOUS} takes keep-test or correct-test, not accept" in first


def test_a_spec_ambiguous_stop_past_the_budget_of_the_arbiter_leaves_the_ticket_out(tmp_path):
    rig = ambiguous(tmp_path, "--retries", "0", files={**ONE, **SECOND}, then=())
    assert rig.code == 0, rig.ledger()
    assert not rig.calls("final", "arbiter")
    assert rig.state()["tickets"]["02"]["phase"] == "merged"
    assert rig.led("Ruling: 01 is left out of the run, with each ticket it blocks (leave-out)")


def test_the_left_out_tickets_are_the_ones_answered_leave_out_and_every_ticket_they_block():
    import driver_decisions
    from driver_tickets import Ticket
    make = lambda number, *blockers: Ticket(number, "", "", "", "", "", "", "", [], {}, list(blockers))
    tickets = [make("01"), make("02", "01", "03"), make("03"), make("04", "02")]
    answered = lambda *answers: [{"ticket": number, "answer": answer} for number, answer in answers]
    assert driver_decisions.left_out(answered(("03", "leave-out"), ("01", "retry")), tickets) == {"02", "03", "04"}
    assert driver_decisions.left_out(answered(("01", None)), tickets) == set()
