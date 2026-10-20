"""A gated ticket tip in, a reviewed tip out: the review, the test-writer corrections and the fix rounds.

Reports of a ticket are `<role>-<K>.json`: K is the fix round the agent follows (reviewer, correctness) or is
(fixer). A test-writer correction after the review of round K is `test-writer-<K+1>.json`.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import driver_agents
import driver_decisions
import driver_gate
import driver_merge
import driver_review
import driver_slots
from driver_run import OUT, Blocked, Run, Stopped, during, ledger
from driver_tickets import Ticket

ROUNDS = 4


@dataclass
class Job:
    """One ticket between its gate and its merge."""
    ticket: Ticket
    slot: str
    run: Run
    values: dict[str, str]  # the pointers every prompt of the ticket gets
    round: int  # fix rounds started
    tip: str
    previous: str = ""  # the tip before the last fix; "" makes the next review a full one
    proofs: set[tuple[str, int]] = field(default_factory=set)  # mutation proofs so far, at `tip`
    again: set[tuple[str, int]] = field(default_factory=set)  # each proof at `tip` that a fix made due again
    corrected: bool = False  # the last step was a test-writer correction
    red: bool = False  # the gate of `tip` is red, so no review saw it

    def pointers(self, review: int) -> dict[str, str]:
        """The values of a prompt; `review` is the round whose findings the agent reads."""
        entry = self.run.state.read()["tickets"][self.ticket.number]
        findings = os.path.relpath(OUT / self.ticket.number / f"findings-{review}.json", self.slot)
        gate = os.path.relpath(driver_gate.record_path(OUT / self.ticket.number, self.tip), self.slot)
        return {**self.values, "start_sha": self.tip, "from_sha": self.previous, "round": str(self.round),
                "locked": " ".join(entry["locked"]), "test_writer_sha": str(entry["test_writer_sha"] or ""),
                "findings": findings, "gate_record": gate,
                "proofs": " ".join(f"{file}:{line}" for file, line in sorted(self.again))}


def entry(models: dict[str, list[str]], round: int) -> str:
    """The implementer entry of a fix round: the second one from round 3, when the list has one."""
    entries = models["implementer"]
    return entries[1] if round > 2 and len(entries) > 1 else entries[0]


def _log_findings(job: Job, findings: list[dict]) -> list[dict]:
    for finding in findings:
        ledger(f"Ticket {job.ticket.number}: {finding['severity']} finding of kind {finding['kind']} from the "
               f"{finding['source']} at {finding['file']}:{finding['line']}: {finding['title']}.")
    return findings


def _run_reviewers(job: Job, cast: list[tuple[str, str]], locked: list[str]) -> list[dict]:
    """Run the (role, model entry) pairs of `cast` at the same time in the one slot. Returns their reports.
    A role that stops (BLOCKED, NEEDS_CONTEXT) asks a `review-stopped` question, unless an `accept` ruling on an
    earlier stop of that role lets the review go on with its report."""
    number = job.ticket.number
    values, running = job.pointers(job.round - 1), [role for role, _ in cast]
    errors, stops = {}, {}
    accepted = driver_decisions.accepted(job.run.state.read()["run"]["questions"], number, "review-stopped")

    def call(role: str, entry: str) -> dict:
        # A full review (no fix range) must give a mutation proof for each locked test file; the prompt names them.
        full = not job.previous and role == "reviewer"
        notes = ["rereview"] if job.previous else ["full-review"] if full else []
        try:
            return job.run.agent(role, job.ticket.number, job.slot, values, entry=entry, fix_round=job.round,
                                 notes=notes, check={"full": True, "locked": locked} if full else {},
                                 alone=lambda: len(running) == 1)
        except Stopped as stopped:
            if any(subject.startswith(f"the {role} ") for subject in accepted):
                ledger(f"Ticket {number}: {stopped}; by the ruling (accept), the review goes on with the other reports.")
                return stopped.report
            stops[role] = str(stopped)
            errors[role] = f"the {role}: {stopped}"
            return {}
        except Blocked as blocked:
            errors[role] = f"the {role}: {blocked}"
            return {}
        finally:
            running.remove(role)

    with ThreadPoolExecutor(len(cast)) as pool:
        reports = list(pool.map(lambda agent: call(*agent), cast))
    if stops:
        first = next(role for role, _ in cast if role in stops)
        job.run.ask(number, "review-stopped", [stops[role] for role, _ in cast if role in stops],
                    str(OUT / number / f"{first}-{job.round}.json"))
    if errors:
        raise Blocked("; ".join(errors[role] for role, _ in cast if role in errors))
    return reports


def _review(job: Job) -> list[dict]:
    """Review `job.tip`. Returns the findings of the driver and of each reviewer."""
    run, number = job.run, job.ticket.number
    state = run.state.ticket(number, phase="review")
    merged = driver_merge.changed(job.slot, run.tip(), driver_merge.protected(run.state.read()["tickets"], number))
    found = _log_findings(job, driver_review.locked_findings(job.slot, state) + driver_review.merged_changes(merged))
    cast = [("reviewer", "")]
    if job.ticket.risk:
        cast.append(("correctness", driver_agents.correctness_entry(run.models)))
    reports = run.read_only(number, job.slot, job.values["branch"], job.tip,
                            lambda: _run_reviewers(job, cast, state["locked"]))
    for (role, _), report in zip(cast, reports):
        found += _log_findings(job, [{**finding, "source": role} for finding in report["findings"]])
        job.proofs |= {(proof["file"], proof["line"]) for proof in report.get("mutations", [])}
    (OUT / number / f"findings-{job.round}.json").write_text(json.dumps(found, indent=1) + "\n")
    return found


def _correct(job: Job, findings: list[dict]) -> list[dict]:
    """One test-writer correction for the findings on the locked tests. It is outside the fix rounds. Returns the
    findings that it did not correct, as findings of kind code for the fixer (issue #111)."""
    run, number = job.run, job.ticket.number
    titles, evidence = [finding["title"] for finding in findings], str(OUT / number / f"findings-{job.round}.json")
    if job.corrected:
        run.ask(number, "locked-test-again", titles, evidence)
        raise Blocked(f"the review: a locked-test finding is open after the test-writer correction: {'; '.join(titles)}")
    restored = run.restore_locked_files(number, job.slot)
    job.tip = driver_slots.head(job.slot)  # the restore is a commit of the driver
    # The gate was green on the tip before the restore, so a red gate here is a restored file that fails with the
    # ticket's code. The driver decides that, not the correction (issue #90).
    with during("the gate of the restore"):
        record = run.red_gate(number, job.slot, job.tip) if restored else ""
    if not record and all(finding["source"] == "driver" for finding in findings):
        job.previous = ""  # no review has seen the restore commit
        ledger(f"Ticket {number}: the code does not need the change to {', '.join(restored)}: the restore closes it, "
               "with no correction.")
        return []
    values = {**job.pointers(job.round), "restored": " ".join(restored),
              "record": record and os.path.relpath(record, job.slot)}
    notes = ["correction"] + ["restored"] * bool(restored) + ["restored-red"] * bool(record)
    with during("the test-writer correction"):
        try:
            report = run.agent("test-writer", number, job.slot, values, notes=notes, fix_round=job.round + 1,
                               check={"correction": True, "restored": restored, "restore_red": bool(record),
                                      "findings": titles})
        except Blocked:
            run.ask(number, "locked-test-again", titles, evidence)  # a rejected correction is one that did not close
            raise
    job.tip, job.previous, job.corrected = driver_slots.head(job.slot), "", True
    entry = run.state.read()["tickets"][number]
    run.state.ticket(number, tip=job.tip, **driver_review.written(job.slot, entry, report["locked"], job.tip))
    return _uncorrected(job, findings, report.get("not_addressed", []))


def _uncorrected(job: Job, findings: list[dict], not_addressed: list[dict]) -> list[dict]:
    """Log what the correction did with each finding. Returns each one it did not correct as a finding of kind
    code, with its reason as the evidence: the test needs a production change first, so a fixer closes it."""
    number = job.ticket.number
    reasons = {item["finding"]: item["reason"] for item in not_addressed}
    for finding in findings:
        result = (f"The test-writer did not correct it: {reasons[finding['title']]}. It goes to the fixer as a finding "
                  "of kind code." if finding["title"] in reasons else f"The test-writer corrected it at {job.tip[:7]}.")
        ledger(f"Ticket {number}: {finding['kind']} correction in {finding['file']}, outside the fix rounds: "
               f"{finding['title']}: {finding['evidence']}. {result}")
    code = {finding["title"]: {**finding, "kind": "code", "evidence": f"the test-writer did not correct it: {reason}"}
            for finding in findings if (reason := reasons.get(finding["title"]))}
    if code:
        # The fixer reads the findings file of this review, so the finding is of kind code there too.
        path = OUT / number / f"findings-{job.round}.json"
        written = [code.get(finding["title"], finding) if finding in findings else finding
                   for finding in json.loads(path.read_text())]
        path.write_text(json.dumps(written, indent=1) + "\n")
    return list(code.values())


def _fix(job: Job) -> None:
    """Run the next fix round: a fixer on the implementer's list."""
    run, number = job.run, job.ticket.number
    job.round += 1
    on = entry(run.models, job.round)
    run.state.ticket(number, phase="fix", round=job.round)
    if job.round == 3 and on != run.models["implementer"][0]:
        ledger(f"Ticket {number} round 3: escalated to {on}.")
    elif job.round == 3:
        ledger(f"Ticket {number} round 3: the implementer has no next entry, so the round stays on {on}.")
    with during(f"fix round {job.round}"):
        run.agent("fixer", number, job.slot, job.pointers(job.round - 1), entry=on, fix_round=job.round)
    before, job.tip = job.tip, driver_slots.head(job.slot)
    run.state.ticket(number, tip=job.tip)
    followed = driver_review.follow(job.slot, before, job.tip, job.proofs)
    due = {proof for proof, changed in followed.items() if changed}
    if job.red:
        # No review saw `before`: the next review covers the fix before it too, and its due proofs stay due.
        due |= set(driver_review.follow(job.slot, before, job.tip, job.again))
    else:
        # No review has seen a test-writer correction, so the review after a fix on a corrected tip is a full one.
        job.previous = "" if job.corrected else before
    job.corrected, job.proofs, job.again = False, set(followed), due


def _unaccepted(job: Job, state: dict, to_correct: list[dict]) -> list[dict]:
    """The findings for a correction once an `accept` ruling closed the ticket's locked-test findings: only the
    driver's, since a changed locked file is still restored. With no such ruling, `to_correct`."""
    number = job.ticket.number
    if not driver_decisions.corrections(state["run"]["questions"], number)[1]:
        return to_correct
    for finding in to_correct:
        if finding["source"] != "driver":
            ledger(f"Ticket {number}: {finding['kind']} finding in {finding['file']} closed as it is by the ruling "
                   f"(accept), with no correction: {finding['title']}.")
    return [finding for finding in to_correct if finding["source"] == "driver"]


def _act_on(job: Job, found: list[dict]) -> bool:
    """Do what the findings of a review ask for: a correction, a fix round, or a stop. True: none is open."""
    state = job.run.state.read()
    to_correct, to_fix = driver_review.route(found, state["tickets"][job.ticket.number]["locked"])
    to_correct = _unaccepted(job, state, to_correct)
    if job.round == ROUNDS:
        # No correction after round 4: no fix round could make its new locked tests green, so `accept` could not merge.
        to_correct, to_fix = [], [finding for finding in found if finding in to_correct + to_fix]
    # A changed locked file is fixed first: a correction on top of it would make the changed file the reference.
    locked_file_changed = any(finding["source"] == "driver" for finding in to_fix)
    if to_correct and not locked_file_changed:
        to_fix += [finding for finding in _correct(job, to_correct) if finding["severity"] in driver_review.OPEN]
        if not to_fix:
            return False
    if not to_fix:
        return True
    if job.round == ROUNDS:
        titles = [f"{finding['title']} ({finding['file']}:{finding['line']})" for finding in to_fix]
        evidence = OUT / job.ticket.number / f"findings-{ROUNDS}.json"
        job.run.ask(job.ticket.number, "finding", titles, str(evidence))
        raise Blocked(f"the review: after round {ROUNDS} these findings are open: {'; '.join(titles)}")
    _fix(job)
    return False


def _gate(job: Job) -> list[dict]:
    """Gate the tip. A red gate on the implementer's commit or a fixer's commit before round 4 is a finding for the
    next round; a red gate after round 4 or on a test-writer correction blocks. Returns the findings: the red gate,
    or none."""
    run, number = job.run, job.ticket.number
    job.red = False
    with during("the gate"):
        if job.round == ROUNDS or job.corrected:
            run.gate(number, job.slot, job.tip)
            return []
        record = run.red_gate(number, job.slot, job.tip)
    if not record:
        return []
    job.red, path = True, os.path.relpath(record, job.slot)
    found = [{"severity": "critical", "file": path, "line": 0, "title": f"the gate is red on {job.tip[:7]}",
              "evidence": f"the output of the gate in {path}", "kind": "code", "source": "gate"}]
    (OUT / number / f"findings-{job.round}.json").write_text(json.dumps(found, indent=1) + "\n")
    return _log_findings(job, found)


def run(ticket: Ticket, slot: str, run: Run, values: dict[str, str]) -> None:
    """Gate, review and fix the ticket tip until a review leaves no finding open."""
    state = run.state.read()["tickets"][ticket.number]
    job = Job(ticket, slot, run, values, state["round"], state["tip"])
    while True:
        found = _gate(job)
        if not found:
            with during("the review"):
                found = _review(job)
        if _act_on(job, found):
            return
