"""Overturn: after a run, `--answer <question id>=<other option>` on a question the driver or the arbiter answered
overturns its ruling. The new ruling says which ruling it overturns and who took it, and it takes effect from where the
run is: an accepted finding gets a fix batch, a left-out ticket runs again, a draft's stop runs again. What lands is
pushed on top of the open PR, with no force. An overturn that would undo work, or that cannot take effect, exits 3
before any agent and says why.

In process, on the rigs of test_arbiter, test_leave_out and test_ship_draft."""
import subprocess

import pytest

import driver_decisions
import driver_overturn
import driver_pr
from test_arbiter import Q, gh, open_after_batch, ruled
from test_arbitration import AMBIGUOUS, SECOND, disputes, ruling
from test_final import GH, THREE, final, fixer, fn
from test_fixloop import Inside as Local
from test_fixloop import inside  # noqa: F401  (the autouse fixture puts the rig's CLIs on PATH)
from test_lenses import rig_of, stopped
from test_scoped import ATOMIC
from test_review import ONE, review
from test_stops import UNATTENDED, blocked
from test_tidy import Inside
from test_tidy import tools  # noqa: F401  (tools is an autouse fixture)
from test_walk import impl, plan, tw

AUTONOMOUS = ("--no-ask", "--retries", "0", *UNATTENDED)


def on_top(rig, first):
    """The branch tip holds the first pushed tip: the second push needed no force."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", first, rig.branch()], cwd=rig.repo).returncode == 0


def test_an_accept_overturned_to_retry_gets_a_fix_batch_pushed_on_top_of_the_open_draft(tmp_path):
    rig, steps = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "accept"))])
    assert rig.code == 0 and len(rig.final_calls("fixer")) == 1, rig.out
    first = rig.branch()
    steps["final/fixer"].append(fixer(fn("02"), "write the ledger atomically"))
    steps["final/final-reviewer"].append(final())
    rig.run(steps, *AUTONOMOUS, "--answer", f"{Q}=retry")
    assert rig.code == 0, rig.out
    assert len(rig.final_calls("fixer")) == 2 and len(rig.prs()) == 1, "the open PR is not opened again"
    assert rig.pushes() == [f"refs/heads/feat/csv {first}", f"refs/heads/feat/csv {rig.branch()}"]
    assert on_top(rig, first)
    (q,) = rig.state()["run"]["questions"]
    assert q["answer"] == "retry" and "by" not in q
    line = ("Ruling: final runs again (retry): ledger write not atomic (ledger.py). It overturns the ruling accept of "
            "the arbiter.")
    summary = (rig.repo / ".agent/implement/summary.md").read_text()
    assert rig.led(line) and f"Overturned by the operator:\n- {Q}: {line}" in summary
    assert "Driver decisions, taken without the operator:" not in summary
    (edit,) = gh(rig, ["pr", "edit"])
    assert edit["argv"][2] == "https://github.example/pull/1" and "--ready" not in edit["argv"]
    assert line in edit["body"] and f"Branch tip and reviewed tip: {rig.branch()}" in edit["body"]
    assert edit["body"].startswith(rig.prs()[0]["body"].split("\n\n## Tickets")[0]), "the pr-writer's part stays"
    assert rig.led("The body of the PR https://github.example/pull/1 is written again")
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and len(gh(rig, ["pr", "edit"])) == 1, "a rerun that changes nothing edits no body"


def test_an_accept_overturned_to_follow_up_files_the_issue_for_the_open_pr(tmp_path):
    rig, steps = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "accept"))])
    assert rig.code == 0 and not gh(rig, ["issue", "create"]), rig.out
    rig.run(steps, *AUTONOMOUS, "--answer", f"{Q}=follow-up")
    assert rig.code == 0, rig.out
    (issue,) = gh(rig, ["issue", "create"])
    assert "It overturns the ruling accept of the arbiter." in issue["body"]
    assert len(rig.prs()) == 1 and len(rig.final_calls("fixer")) == 1
    (edit,) = gh(rig, ["pr", "edit"])
    assert "Follow-up issue: https://github.example/issues/1" in edit["body"]


def test_a_left_out_ticket_overturned_to_retry_runs_again_and_lands_on_top_of_the_open_draft(tmp_path):
    rig = Inside(tmp_path, THREE)
    steps = plan(**{"01/implementer": [blocked(), blocked()]})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and len(rig.prs()) == 1, rig.output()
    first = rig.branch()
    steps["01/implementer"].append(impl("01"))
    rig.run(steps, *AUTONOMOUS, "--answer", "01-ticket-blocked-1=retry")
    assert rig.code == 0, rig.output()
    assert {entry["phase"] for entry in rig.state()["tickets"].values()} == {"merged"}
    assert rig.calls("02"), "the ticket the left-out one blocked runs too"
    assert rig.pushes()[-1] == f"refs/heads/feat/csv {rig.branch()}" and on_top(rig, first)
    assert len(rig.prs()) == 1
    assert rig.led("Ruling: 01 runs again (retry)", "It overturns the ruling leave-out of the driver.")
    assert len(rig.final_calls("pr-writer")) == 2, "the pr-writer writes its part again for the tickets that landed"
    (edit,) = gh(rig, ["pr", "edit"])
    assert "- 01: " in edit["body"] and "Left out of the run" not in edit["body"]


def test_a_left_out_ticket_lands_only_its_own_commits_on_the_rewritten_branch(tmp_path):
    rig = Inside(tmp_path, THREE)
    steps = plan(**{"02/implementer": [blocked(), blocked()]})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.output()
    recovery, first = rig.final()["recovery"], rig.branch()
    steps["02/implementer"].append(impl("02"))
    rig.run(steps, *AUTONOMOUS, "--answer", "02-ticket-blocked-1=retry")
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.output()
    assert on_top(rig, first) and not on_top(rig, recovery), "no commit of the history before the tidy comes back"
    assert rig.led("Ticket 02: merged into feat/csv as a fast-forward")
    assert rig.led("Ticket 02: its own work is replayed onto")


def test_a_left_out_ticket_that_lands_after_a_ship_draft_gets_its_tip_reviewed_and_pushed(tmp_path):
    rig = Inside(tmp_path, THREE)
    steps = plan(**{"02/implementer": [blocked(), blocked()], "final/final-structure": [stopped(), stopped()]})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and rig.final()["ship"] and len(rig.prs()) == 1, rig.output()
    first = rig.branch()
    steps["02/implementer"].append(impl("02"))
    steps["final/final-structure"].append(final())
    rig.run(steps, *AUTONOMOUS, "--answer", "02-ticket-blocked-1=retry")
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.output()
    assert not rig.final()["ship"], "the draft ruling shipped the tip before the ticket landed"
    assert rig.final()["reviewed"] == rig.branch() != first
    assert rig.pushes()[-1] == f"refs/heads/feat/csv {rig.branch()}" and on_top(rig, first)


def test_a_ship_draft_overturned_to_retry_runs_the_failed_lens_again(tmp_path):
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-structure": [stopped(), stopped()]})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and len(rig.prs()) == 1, rig.out
    rig.run(steps, *AUTONOMOUS, "--answer", "final-ticket-blocked-1=retry")
    assert rig.code == 0, rig.out
    assert len(rig.final_calls("final-structure")) == 3 and len(rig.prs()) == 1
    assert rig.final()["reviewed"] == rig.branch()
    assert rig.led("(retry)", "It overturns the ruling ship-draft of the driver.")
    (edit,) = gh(rig, ["pr", "edit"])
    assert "It overturns the ruling ship-draft of the driver." in edit["body"] and "--ready" not in edit["argv"]
    assert rig.final()["draft"] and not gh(rig, ["pr", "ready"]), "the PR stays a draft: the operator readies it"


def test_an_overturn_of_an_answer_of_the_operator_exits_3_before_any_agent(tmp_path):
    rig = rig_of(tmp_path)
    steps = plan(**{"final/final-structure": [stopped(), stopped()]})
    rig.run(steps, "--retries", "0", *UNATTENDED)
    assert rig.code == 2, rig.out
    rig.run(steps, "--answer", "final-ticket-blocked-1=ship-draft", *UNATTENDED)
    assert rig.code == 0, rig.out
    calls = len(rig.calls())
    rig.run(steps, "--answer", "final-ticket-blocked-1=retry", *UNATTENDED)
    assert rig.code == 3 and len(rig.calls()) == calls
    assert ("--answer final-ticket-blocked-1=retry: you answered final-ticket-blocked-1: only a ruling of the driver "
            "or the arbiter is overturned") in rig.out


# ---------------------------------------------------------------- the refusals, on a state.json of their own

def q(id, answer, ticket="final", kind="finding-open", stop=1, by="driver", subject="s (f.py)", **more):
    options = {"finding-open": ["retry", "accept", "follow-up"], "finding-must-fix": ["retry", "accept", "follow-up"],
               "ticket-blocked": ["retry", "ship-draft", "edit"],
               "spec-ambiguous": ["keep-test", "correct-test", "leave-out", "edit-spec"]}[kind]
    return {"id": id, "ticket": ticket, "kind": kind, "stop": stop, "answer": answer, "subject": subject,
            "options": [{"id": option} for option in options], **({"by": by} if by else {}), **more}


def state(*questions, open=(), pr=None, phases=None):
    return {"run": {"questions": list(questions),
                    "final": {"open": [{"title": t, "file": "f.py"} for t in open], "pr": pr}},
            "tickets": {number: {"phase": phase} for number, phase in (phases or {}).items()}}


@pytest.mark.parametrize("asked, pair, why", [
    (state(), ("zz", "retry"), "there is no question zz"),
    (state(q("a", "accept", by=None)), ("a", "retry"),
     "you answered a: only a ruling of the driver or the arbiter is overturned"),
    (state(q("a", "accept", by="arbiter")), ("a", "nope"), "a offers retry, accept, follow-up"),
    (state(q("a", "accept", by="arbiter")), ("a", "accept"), "a is answered accept already"),
    (state(q("a", "retry", open=["s"])), ("a", "accept"),
     "the retry of the driver ran: its work is on the branch, and an overturn undoes no work"),
    (state(q("a", "retry", kind="finding-must-fix"), open=["s"]), ("a", "accept"),
     "the retry of the driver ran: its work is on the branch, and an overturn undoes no work"),
    (state(q("a", "ship-draft", kind="ticket-blocked")), ("a", "edit"),
     "an overturn takes an option that lets the run go on: edit the spec or the ticket, then overturn a to an option "
     "that runs the work again"),
    (state(q("a", "accept", by="arbiter"), q("b", "retry", stop=2), open=["s"]), ("a", "retry"),
     "a later stop of the end of run asked b: only the newest stop of the end of run is overturned"),
    (state(q("a", "keep-test", ticket="01", kind="spec-ambiguous", by="arbiter"), phases={"01": "merged"}),
     ("a", "correct-test"), "ticket 01 merged since: its work is on the PR branch"),
    (state(q("a", "accept", by="arbiter"), open=["other"]), ("a", "retry"),
     "the last review leaves the finding closed: no fix batch is due for it"),
    (state(q("a", "ship-draft", kind="ticket-blocked", subject="the pr-writer: it reported BLOCKED",
                 role="pr-writer"), pr="https://pr"),
     ("a", "retry"), "the PR is open, so the pr-writer does not run again: edit its body on GitHub"),
])
def test_an_overturn_that_cannot_take_effect_is_refused_with_why(asked, pair, why):
    assert driver_overturn.refusals([pair], asked) == [f"--answer {pair[0]}={pair[1]}: {why}"]


@pytest.mark.parametrize("asked, pair", [
    (state(q("a", "accept", by="arbiter"), open=["s"]), ("a", "retry")),
    (state(q("a", "accept", by="arbiter")), ("a", "follow-up")),
    (state(q("a", "leave-out", ticket="01", kind="spec-ambiguous"), phases={"01": "blocked"}), ("a", "correct-test")),
    (state(q("a", "ship-draft", kind="ticket-blocked", subject="the final-spec: x"), pr="https://pr"), ("a", "retry")),
])
def test_an_overturn_that_takes_effect_is_not_refused(asked, pair, monkeypatch):
    monkeypatch.setattr(driver_pr, "state", lambda url: "OPEN")
    assert driver_overturn.refusals([pair], asked) == []


@pytest.mark.parametrize("pair, draft", [
    (("a", "follow-up"), ""),
    (("a", "retry"), ""),
    (None, "a finding ships open: s (f.py:1)"),
])
def test_an_agent_s_ship_overturned_is_the_operator_s_ruling_and_drafts_no_pr(pair, draft):
    questions = [q("a", "accept", by="arbiter", resume={"phase": "final"})]
    final = {"open": [{"title": "s", "file": "f.py", "line": 1}], "ship": None}
    if pair:
        driver_decisions.overturn(questions, dict([pair]))
    assert driver_pr._draft(final, questions, []) == draft


def test_a_body_edit_that_fails_is_made_again_by_the_next_run(tmp_path):
    rig, steps = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "accept"))])
    gh_path = tmp_path / "bin/gh"
    down = 'if sys.argv[1:3] == ["pr", "edit"]:\n    sys.exit("gh down")\n'
    gh_path.write_text(GH.replace("log = Path", f"{down}log = Path", 1))
    rig.run(steps, *AUTONOMOUS, "--answer", f"{Q}=follow-up")
    assert rig.code != 0 and "gh pr edit failed: gh down" in rig.out, rig.out
    gh_path.write_text(GH)
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0, rig.out
    (edit,) = gh(rig, ["pr", "edit"])
    assert "Follow-up issue: https://github.example/issues/1" in edit["body"]
    assert (rig.repo / ".agent/implement/final/pr-body.md").read_text() == edit["body"]


@pytest.mark.parametrize("shown", ["MERGED", "CLOSED"])
def test_an_overturn_once_the_pr_is_merged_or_closed_exits_3_before_any_agent(tmp_path, shown):
    rig, steps = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "accept"))])
    (tmp_path / "gh-pr-state").write_text(f"{shown}\n")
    calls = len(rig.calls())
    rig.run(steps, *AUTONOMOUS, "--answer", f"{Q}=retry")
    assert rig.code == 3 and len(rig.calls()) == calls and len(rig.pushes()) == 1
    assert (f"--answer {Q}=retry: the PR https://github.example/pull/1 is {shown.lower()}: an overturn adds work to "
            "an open PR only") in rig.out


@pytest.mark.parametrize("shown", ["MERGED", "CLOSED"])
def test_an_overturn_stopped_before_its_push_pushes_nothing_once_the_pr_is_merged_or_closed(tmp_path, shown,
                                                                                            monkeypatch):
    rig, steps = open_after_batch(tmp_path, ATOMIC, [ruled((Q, "accept"))])
    steps["final/fixer"].append(fixer(fn("02"), "write the ledger atomically"))
    steps["final/final-reviewer"].append(final())
    import driver_final

    def killed(*_, **__):
        raise SystemExit(137)

    with monkeypatch.context() as patch:
        patch.setattr(driver_final, "_push", killed)
        with pytest.raises(SystemExit):
            rig.run(steps, *AUTONOMOUS, "--answer", f"{Q}=retry")
    (tmp_path / "gh-pr-state").write_text(f"{shown}\n")
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 1 and len(rig.pushes()) == 1 and not gh(rig, ["pr", "edit"]), rig.out
    assert f"the PR https://github.example/pull/1 is {shown.lower()}: nothing goes out to it" in rig.out


def test_a_final_fixer_after_a_ticket_landed_late_keeps_that_ticket_s_locked_test_as_it_landed(tmp_path):
    rig = Inside(tmp_path, THREE)
    steps = plan(**{"02/implementer": [blocked(), blocked()]})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.output()
    steps["02/implementer"].append(impl("02"))
    steps["final/fixer"] = [fixer(fn("02"), "repair the gate")]
    rig.gate_conf(full={"subjects": ["feat(02): Quote fields"]})  # red on the commit the late ticket lands as
    rig.run(steps, *AUTONOMOUS, "--answer", "02-ticket-blocked-1=retry")
    assert rig.code == 0, rig.output()
    assert len(rig.final_calls("fixer")) == 1 and rig.led("Fix batch of the integration gate: 1 commit"), rig.ledger()
    assert rig.final()["locked"] == rig.state()["run"]["landed"][-1][1]
    assert "def test_x" in subprocess.run(["git", "show", "feat/csv:tests/test_02.py"], cwd=rig.repo,
                                          capture_output=True, text=True).stdout


QUOTE = "demo/quote.py"


def conflicted(tmp_path, conflict):
    """Ticket 02 is left out, then overturned to `retry`: its work adds QUOTE, which the base the tidy moved onto
    adds too, so its replay onto the rewritten branch conflicts. `conflict` is the conflict agent's steps."""
    rig = Inside(tmp_path, THREE)
    rig.move_base({QUOTE: "base = 1\n"})
    steps = plan(**{"02/implementer": [blocked(), blocked(), impl("02", QUOTE)], "02/conflict": conflict})
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "blocked", rig.output()
    return rig, steps


RESOLVED = {"files": {QUOTE: "base = 1\n" + fn("02")}, "commit": "replay 02",
            "report": {"status": "DONE", "commits": ["$SHA"], "files": [QUOTE], "concerns": []}}


def test_a_replay_that_conflicts_goes_to_the_conflict_agent_and_lands(tmp_path):
    rig, steps = conflicted(tmp_path, [RESOLVED])
    rig.run(steps, *AUTONOMOUS, "--answer", "02-ticket-blocked-1=retry")
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.output()
    (agent,) = rig.calls("02", "conflict")
    assert "replayed onto the PR branch after the end of run rewrote it, is open in this worktree" in agent["prompt"]
    shown = subprocess.run(["git", "show", f"feat/csv:{QUOTE}"], cwd=rig.repo, capture_output=True, text=True)
    assert shown.stdout == "base = 1\n" + fn("02")
    assert rig.led("Ticket 02: the conflict agent resolved the merge conflict in demo/quote.py")


def test_a_replay_whose_conflict_agent_stops_leaves_the_slot_clean_and_retry_runs_the_agent_again(tmp_path):
    rig, steps = conflicted(tmp_path, [blocked(), blocked(), RESOLVED])
    rig.run(steps, "--retries", "0", "--answer", "02-ticket-blocked-1=retry", *UNATTENDED)
    assert rig.code == 2, rig.output()
    (question,) = [q for q in rig.state()["run"]["questions"] if q["answer"] is None]
    assert (question["id"], question["role"]) == ("02-ticket-blocked-2", "conflict")
    entry = rig.state()["tickets"]["02"]
    slot = rig.repo if entry["slot"] == 1 else rig.repo / f".agent/worktrees/slot-{entry['slot']}"
    run = lambda *a: subprocess.run(["git", *a], cwd=slot, capture_output=True, text=True).stdout.strip()
    assert run("status", "--porcelain") == "" and not run("rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD")
    assert "02" in run("for-each-ref", "--format=%(refname)", "--points-at", entry["tip"], "refs/heads"), \
        "the ticket branch is back on its tip before the replay"
    rig.run(steps, "--retries", "0", "--answer", "02-ticket-blocked-2=retry", *UNATTENDED)
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.output()
    assert len(rig.calls("02", "conflict")) == 3


def test_a_run_killed_in_a_replay_replays_again_and_keeps_the_ticket_s_work(tmp_path, monkeypatch):
    rig, steps = conflicted(tmp_path, [RESOLVED])
    import driver

    def killed(*_, **__):  # the driver dies while the conflict agent works: the ticket branch is on the PR tip
        raise SystemExit(137)

    with monkeypatch.context() as patch:
        patch.setattr(driver, "_resolve_conflict", killed)
        with pytest.raises(SystemExit):
            rig.run(steps, *AUTONOMOUS, "--answer", "02-ticket-blocked-1=retry")
    entry = rig.state()["tickets"]["02"]
    slot = rig.repo if entry["slot"] == 1 else rig.repo / f".agent/worktrees/slot-{entry['slot']}"
    subprocess.run(["git", "cherry-pick", "--abort"], cwd=slot, check=True)  # the driver starts on no open change
    rig.run(steps, *AUTONOMOUS)
    assert rig.code == 0 and rig.state()["tickets"]["02"]["phase"] == "merged", rig.output()
    assert len(rig.calls("02", "implementer")) == 3, "the ticket's work is not thrown away"
    shown = subprocess.run(["git", "show", f"feat/csv:{QUOTE}"], cwd=rig.repo, capture_output=True, text=True)
    assert shown.stdout == "base = 1\n" + fn("02")


def test_a_spec_ambiguous_leave_out_overturned_to_keep_test_runs_the_implementer_again_and_lands(tmp_path):
    rig = Local(tmp_path, {**ONE, **SECOND})
    steps = plan(**{"01/implementer": [disputes(), disputes(), impl("01")], "01/test-writer": [tw("01")],
                    "02/implementer": [impl("02", "demo/other.py")],
                    "01/reviewer": [ruling("code-wrong", "R3 says double quotes"), review("01")]})
    rig.run(steps, "--no-ask", "--retries", "0")
    assert rig.code == 0, rig.ledger()
    (q,) = [q for q in rig.state()["run"]["questions"] if q["id"] == AMBIGUOUS]
    assert q["answer"] == "leave-out" and rig.state()["tickets"]["02"]["phase"] == "merged"
    rig.run(steps, "--no-ask", "--retries", "0", "--answer", f"{AMBIGUOUS}=keep-test")
    assert rig.code == 0 and rig.state()["tickets"]["01"]["phase"] == "merged", rig.ledger()
    assert len(rig.calls("01", "implementer")) == 3
    assert rig.led("(keep-test)", "It overturns the ruling leave-out of the driver.")
    assert rig.led("Ticket 01: its own work is replayed onto")
