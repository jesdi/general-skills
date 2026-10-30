"""A gated ticket tip in, a reviewed tip out: the review, the test-writer corrections and the fix rounds.

Reports of a ticket are `<role>-<K>.json`: K is the fix round the agent follows (reviewer) or is
(fixer). A test-writer correction after the review of round K is `test-writer-<K+1>.json`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import driver_decisions
import driver_gate
import driver_review
import driver_slots
from driver_run import OUT, Blocked, Run, during, ledger
from driver_tickets import Ticket

ROUNDS = 2


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
    corrected: bool = False  # the last step was a test-writer correction
    red: bool = False  # the gate of `tip` is red, so no review saw it

    def pointers(self, review: int) -> dict[str, str]:
        """The values of a prompt; `review` is the round whose findings the agent reads."""
        entry = self.run.state.read()["tickets"][self.ticket.number]
        findings = os.path.relpath(OUT / self.ticket.number / f"findings-{review}.json", self.slot)
        gate = os.path.relpath(driver_gate.record_path(OUT / self.ticket.number, driver_gate.FAST, self.tip), self.slot)
        return {**self.values, "start_sha": self.tip, "from_sha": self.previous, "round": str(self.round),
                "locked": " ".join(entry["locked"]), "test_writer_sha": str(entry["test_writer_sha"] or ""),
                "findings": findings, "gate_record": gate}


def entry(models: dict[str, list[str]], round: int) -> str:
    """The implementer entry of a fix round: the second one for round 2."""
    entries = models["implementer"]
    return entries[1] if round >= 2 else entries[0]


def _log_findings(number: str, findings: list[dict]) -> list[dict]:
    for finding in findings:
        ledger(f"Ticket {number}: {finding['severity']} finding of kind {finding['kind']} from the "
               f"{finding['source']} at {finding['file']}:{finding['line']}: {finding['title']}.")
    return findings


def _run_reviewer(job: Job) -> dict:
    """Run the reviewer in the slot. Returns its report. It gets the diff of each locked file of another ticket that
    the ticket changed: only its test-writer may, and the reviewer judges that change against the spec."""
    run, number = job.run, job.ticket.number
    values = job.pointers(job.round - 1)
    values["fixer_report"] = os.path.relpath(OUT / number / f"fixer-{job.round}.json", job.slot)
    values["elsewhere"] = driver_review.changed_elsewhere(
        job.slot, run.tip(), driver_review.others(run.state.read()["tickets"], number))
    notes = ["rereview"] * bool(job.previous)
    notes += ["unchanged"] * (job.previous == job.tip)  # a fixer with no commit: its reasons need a check
    notes += ["elsewhere"] * bool(values["elsewhere"])
    with during("the reviewer"):
        return run.agent("reviewer", number, job.slot, values, fix_round=job.round, notes=notes)


def _prior_open(job: Job) -> dict[tuple[str, str], str | None]:
    """The file, title and basis of the reviewer's findings that this re-review may revisit."""
    if not job.previous:
        return {}
    number = job.ticket.number
    previous = json.loads((OUT / number / f"findings-{job.round - 1}.json").read_text())
    return {(finding["file"], finding["title"]): finding.get("basis") for finding in previous
            if finding["severity"] in driver_review.OPEN and finding["source"] == "reviewer"}


def _partition_findings(report: dict, prior: dict, scoped: bool) -> tuple[list[dict], list[dict]]:
    """On a re-review, only the reviewer's previously open findings stay in ticket scope."""
    active, deferred = [], []
    for raw in report["findings"]:
        finding = {**raw, "source": "reviewer"}
        key = (finding["file"], finding["title"])
        if scoped and key not in prior:
            deferred.append(finding)
            continue
        if "basis" not in finding and prior.get(key):
            finding["basis"] = prior[key]
        active.append(finding)
    return active, deferred


def _reviewer_results(job: Job, report: dict) -> tuple[list[dict], list[dict]]:
    """Collect ticket findings and deferred observations from the reviewer's report."""
    active, deferred = _partition_findings(report, _prior_open(job), bool(job.previous))
    found = active + [{**finding, "source": "reviewer", "basis": finding.get("basis", "regression")}
                      for finding in report.get("regressions", [])]
    observations = deferred + [{**finding, "source": "reviewer"} for finding in report.get("observations", [])]
    return found, observations


def _store_observations(job: Job, observations: list[dict]) -> None:
    """Keep deferred evidence for the final reviewer without spending a ticket fix round."""
    if observations:
        (OUT / job.ticket.number / f"observations-{job.round}.json").write_text(json.dumps(observations, indent=1) + "\n")
        for observation in observations:
            ledger(f"Ticket {job.ticket.number}: deferred {observation['severity']} observation from {observation['source']} "
                   f"at {observation['file']}:{observation['line']}: {observation['title']}.")


def _review(job: Job) -> list[dict]:
    """Review `job.tip`. Returns the reviewer's findings."""
    run, number = job.run, job.ticket.number
    run.state.ticket(number, phase="review")
    report = run.read_only(number, job.slot, job.values["branch"], job.tip, lambda: _run_reviewer(job))
    reviewed, observations = _reviewer_results(job, report)
    found = _log_findings(number, reviewed)
    _store_observations(job, observations)
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
    with during("the test-writer correction"):
        try:
            report = run.agent("test-writer", number, job.slot, job.pointers(job.round), notes=["correction"],
                               fix_round=job.round + 1, check={"correction": True, "findings": titles})
        except Blocked:
            run.ask(number, "locked-test-again", titles, evidence)  # a rejected correction is one that did not close
            raise
    job.tip, job.previous, job.corrected = driver_slots.head(job.slot), "", True
    entry = run.state.read()["tickets"][number]
    run.state.ticket(number, tip=job.tip, **driver_review.written(entry, report["locked"], job.tip))
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


def _red(job: Job, landing: str) -> str:
    """The red gate that the fixer has to make green, or ""."""
    return (f"the fast gate is red on {job.tip[:7]}" if job.red else
            f"the full gate is red on {landing[:7]}" if landing else "")


def _fix(job: Job, findings: list[dict], landing: str = "") -> None:
    """Run the next fix round: a fixer on the implementer's list, for `findings`. A fixer that makes no commit gives
    a reason per finding, and the review of the same tip checks them (issue #123). `landing` is the commit whose full
    gate was red at the landing, when that started the round."""
    run, number = job.run, job.ticket.number
    job.round += 1
    on = entry(run.models, job.round)
    run.state.ticket(number, phase="fix", round=job.round, landing=landing or None)
    if job.round == 2:
        ledger(f"Ticket {number} round 2: escalated to {on}.")
    values, notes = job.pointers(job.round - 1), []
    if landing:
        record = driver_gate.record_path(OUT / number, driver_gate.FULL, landing)
        values.update(landing_sha=landing[:7], landing_record=os.path.relpath(record, job.slot))
        notes = ["landing-red"] + ["landing-merge"] * (landing != job.tip)
    with during(f"fix round {job.round}"):
        run.agent("fixer", number, job.slot, values, entry=on, entries=run.models["implementer"], fix_round=job.round,
                  notes=notes, check={"fix": [finding["title"] for finding in findings], "red": _red(job, landing)})
    before, job.tip = job.tip, driver_slots.head(job.slot)
    run.state.ticket(number, tip=job.tip, landing=None)
    # No review saw a red `before`, so the next review covers the fix before it too. No review has seen a test-writer
    # correction either, so the review after a fix on a corrected tip is a full one.
    if not job.red:
        job.previous = "" if job.corrected else before
    job.corrected = False


def _unaccepted(job: Job, state: dict, to_correct: list[dict]) -> list[dict]:
    """The findings for a correction: none once an `accept` ruling closed the ticket's locked-test findings, else
    `to_correct`."""
    number = job.ticket.number
    if not driver_decisions.corrections(state["run"]["questions"], number)[1]:
        return to_correct
    for finding in to_correct:
        ledger(f"Ticket {number}: {finding['kind']} finding in {finding['file']} closed as it is by the ruling "
               f"(accept), with no correction: {finding['title']}.")
    return []


def _save_must_fix(state: dict, number: str, findings: list[dict]) -> None:
    """Record each carried finding once, even when a ticket resumes at its cap."""
    saved = state["run"]["final"]["must_fix"]
    existing = {(item["ticket"], item["file"], item["title"]) for item in saved}
    for finding in findings:
        key = (number, finding["file"], finding["title"])
        if key not in existing:
            saved.append({**finding, "ticket": number, "must_fix": True})
            existing.add(key)


def _at_cap(job: Job, findings: list[dict]) -> bool:
    """Block unmet criteria; carry other open findings to the required final fix batch."""
    blocking = [finding for finding in findings if finding.get("basis") != "regression"]
    if blocking:
        titles = [f"{finding['title']} ({finding['file']}:{finding['line']})" for finding in blocking]
        raise Blocked(f"the review: after round {ROUNDS} these findings are open: {'; '.join(titles)}")
    job.run.state.update(lambda state: _save_must_fix(state, job.ticket.number, findings))
    ledger(f"Ticket {job.ticket.number}: the green gate and met criteria allow merge after round {ROUNDS}; "
           f"{len(findings)} finding{'s' if len(findings) != 1 else ''} go to the final must-fix list.")
    return True


def _routed(job: Job, found: list[dict]) -> tuple[list[dict], list[dict]]:
    """(the findings for a correction, those for a fix round). A locked file of another ticket is protected."""
    state = job.run.state.read()
    locked = state["tickets"][job.ticket.number]["locked"]
    protected = [path for path in driver_review.others(state["tickets"], job.ticket.number) if path not in locked]
    to_correct, to_fix = driver_review.route(found, locked, protected)
    to_correct = _unaccepted(job, state, to_correct)
    if job.round == ROUNDS:
        # No correction at the cap: no fix round could make its new locked tests green.
        return [], [finding for finding in found if finding in to_correct + to_fix]
    return to_correct, to_fix


def _act_on(job: Job, found: list[dict]) -> bool:
    """Do what the findings of a review ask for: a correction, a fix round, or a stop. True: none is open."""
    to_correct, to_fix = _routed(job, found)
    if to_correct:
        to_fix += [finding for finding in _correct(job, to_correct) if finding["severity"] in driver_review.OPEN]
        if not to_fix:
            return False
    if not to_fix:
        return True
    if job.round == ROUNDS:
        return _at_cap(job, to_fix)
    _fix(job, to_fix)
    return False


class Unlanded(Exception):
    """The full gate is red on the commit that would land, and the next fix round of the ticket has started."""


def red_landing(run: Run, number: str, slot: str, commit: str, record: str) -> None:
    """The full gate is red on `commit`, the commit that would land: the PR branch does not move. At round 2 that
    blocks. Before it, the record is a critical `gate` finding for the next round, which starts in the same write of
    state.json that leaves the landing, so a resumed run starts that round's fixer and never gates `commit` again.
    Call it with the ticket branch checked out in the slot: the fixer works there."""
    entry = run.state.read()["tickets"][number]
    if entry["round"] == ROUNDS:
        raise Blocked(f"the full gate is red on {commit[:7]}, record {record}")
    path = os.path.relpath(record, slot)
    finding = {"severity": "critical", "file": path, "line": 0, "title": f"the full gate is red on {commit[:7]}",
               "evidence": f"the output of the gate in {path}", "kind": "code", "source": "gate"}
    # The fixer reads the findings of the last review: its minor findings stay there for the final review.
    file = OUT / number / f"findings-{entry['round']}.json"
    found = json.loads(file.read_text()) if file.exists() else []
    if finding["title"] not in [old["title"] for old in found]:  # a run stopped before the round gated it again
        file.write_text(json.dumps(found + [finding], indent=1) + "\n")
    _log_findings(number, [finding])
    run.state.ticket(number, phase="fix", round=entry["round"] + 1, landing=commit)
    ledger(f"Ticket {number}: the full gate is red on {commit[:7]} at the landing, so {run.pr} does not move and fix "
           f"round {entry['round'] + 1} starts.")
    raise Unlanded()


def _gate(job: Job) -> list[dict]:
    """Fast-gate the candidate tip. A red gate before round 2 is a finding for the next round;
    a red gate after round 2 or on a test-writer correction blocks. Returns the findings: the red gate,
    or none."""
    run, number = job.run, job.ticket.number
    job.red = False
    with during("the gate"):
        if job.round == ROUNDS or job.corrected:
            run.gate(number, job.slot, job.tip, driver_gate.FAST)
            return []
        record = run.red_gate(number, job.slot, job.tip, driver_gate.FAST)
    if not record:
        return []
    job.red, path = True, os.path.relpath(record, job.slot)
    found = [{"severity": "critical", "file": path, "line": 0, "title": f"the fast gate is red on {job.tip[:7]}",
              "evidence": f"the output of the gate in {path}", "kind": "code", "source": "gate"}]
    (OUT / number / f"findings-{job.round}.json").write_text(json.dumps(found, indent=1) + "\n")
    return _log_findings(number, found)


def run(ticket: Ticket, slot: str, run: Run, values: dict[str, str]) -> None:
    """Gate, review and fix the ticket tip until a review leaves no finding open."""
    state = run.state.read()["tickets"][ticket.number]
    job = Job(ticket, slot, run, values, state["round"], state["tip"])
    if state["phase"] == "fix" and state["landing"]:
        # A red full gate at the landing started this round, and its fixer has not reported yet.
        job.round -= 1
        _fix(job, [{"title": f"the full gate is red on {state['landing'][:7]}"}], state["landing"])
    while True:
        found = _gate(job)
        if not found:
            with during("the review"):
                found = _review(job)
        if _act_on(job, found):
            return
