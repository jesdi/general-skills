"""A locked test that an implementer or a fixer disputes: the reviewer's arbitration, the test-writer correction
(after a ruling, a review finding or a `correct-test` answer), and the `spec-ambiguous` question when a locked test is
disputed or corrected a second time in its ticket, or when its locked tests were settled `SETTLEMENTS` times.

A dispute is keyed by its locked test, `<locked file>::<test>` (driver_report.test_key), so a parametrized case is its
test and another test of a ruled file is a first dispute. The ticket record keeps the ruling in `disputes` and the
correction in `corrected`; a review finding that names no test asks for one to be added and is in neither.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple, NoReturn

import driver_decisions
import driver_locked
import driver_report
import driver_slots
from driver_decisions import Resume
from driver_report import Dispute
from driver_run import OUT, Blocked, Call, Run, during, ledger, retry_point

SETTLEMENTS = 4  # arbitrations and review corrections per ticket before one more asks: no new test dodges the bound

class Corrected(NamedTuple):
    """The reading a locked test is corrected to, which the ticket's `corrected` keeps: its one correction."""
    test: str
    requirement: str
    reason: str


class Reading(NamedTuple):
    """The two readings of a locked test that a `spec-ambiguous` question asks about."""
    file: str  # the locked file
    test: str
    requirement: str
    first: str  # the reading of its ruling or of its one correction
    second: str  # the reading of the new dispute or finding


def _rulings(entry: dict) -> str:
    return "".join(f"- {ruling['test']}, {ruling['requirement']}: {ruling['ruling']}: {ruling['reason']}\n"
                   for ruling in entry["disputes"].values())


def agent(run: Run, call: Call) -> dict:
    """`run.agent` for a role of a ticket. While the implementer or a fixer disputes a locked test, the dispute is
    settled and the role runs again from the tip, with the rulings of the ticket in its prompt."""
    while True:
        entry = run.state.read()["tickets"][call.number]
        values = {**call.values, "locked": " ".join(entry["locked"]), "rulings": _rulings(entry)}
        ruled = call.role in driver_report.DISPUTING and bool(entry["disputes"])
        report = run.agent(replace(call, values=values, notes=[*call.notes, *["ruled"] * ruled], may_dispute=True))
        if not (disputed := driver_report.disputes(call.role, report, entry["locked"])):
            return report
        _settle(run, replace(call, values=values), disputed)
        call = replace(call, values={**call.values, "start_sha": driver_slots.head(call.slot)})


def ambiguous(run: Run, number: str, readings: list[Reading], resume: Resume) -> NoReturn:
    """Ask one `spec-ambiguous` question on `readings` and block the ticket. Its evidence is a findings file that a
    `correct-test` correction reads: one finding per locked test, to correct to the second reading. The file is named
    after the question: `spec-ambiguous-<n>.json` for `<NN>-spec-ambiguous-<n>`."""
    question = driver_decisions.question_id("spec-ambiguous", number, run.state.read()["run"]["questions"])
    path = OUT / number / f"{question.removeprefix(f'{number}-')}.json"  # the ticket's folder names the ticket
    subjects = [f"{one.test or one.file}{f', {one.requirement}' * bool(one.requirement)}: the first reading: "
                f"{one.first}; the second reading: {one.second}" for one in readings]
    _ruling_findings(path, [(one.file, one.test, subject, f"correct the test to the second reading: {one.second}")
                            for one, subject in zip(readings, subjects)])
    subject = "; ".join(subjects)
    run.ask(number, "spec-ambiguous", [subject], str(path), resume)
    raise Blocked(f"the spec reads two ways on a locked test: {subject}")


def finding_key(finding: dict) -> str:
    """The locked test a finding corrects, "" when it asks for a test to be added (driver_report.test_key)."""
    return driver_report.test_key(finding["file"], finding.get("test", ""))


def kept(questions: list[dict], number: str) -> set[str]:
    """The locked tests of the `spec-ambiguous` questions of `number` answered `keep-test`. The evidence of a schema 4
    question names no test, so its whole file is kept, as that driver kept it."""
    return {finding_key(finding) or finding["file"] for q in questions if (q["ticket"], q["kind"], q["answer"]) ==
            (number, "spec-ambiguous", "keep-test") for finding in json.loads(Path(q["evidence"][0]).read_text())}


def correction(questions: list[dict], number: str) -> dict:
    """The last `spec-ambiguous` question of `number` answered `correct-test`, or {}: its evidence is the findings file
    of the correction, the phase of its `resume` the phase after it."""
    answered = [q for q in questions if (q["ticket"], q["kind"], q["answer"]) ==
                (number, "spec-ambiguous", "correct-test")]
    return answered[-1] if answered else {}


def second_readings(found: list[Dispute], record: dict) -> list[Reading]:
    """The readings of each dispute in `found` whose test `record` (`disputes` or `corrected`) has: the record's is
    the first, the dispute's the second."""
    return [Reading(one.file, one.test, record[one.key]["requirement"], record[one.key]["reason"], one.reason)
            for one in found if one.key in record]


def capped(found: list[Dispute], entry: dict) -> list[Reading]:
    """The readings of each dispute in `found` once the ticket's locked tests were settled `SETTLEMENTS` times, else
    []: the count is the first reading."""
    first = (f"the locked tests of this ticket were arbitrated or corrected {entry['settled']} times, the most the "
             "driver settles without asking")
    return [Reading(one.file, one.test, "", first, one.reason)
            for one in found] if entry["settled"] >= SETTLEMENTS else []


def settled(run: Run, number: str) -> None:
    """Count one arbitration or review correction of the ticket's locked tests, toward `SETTLEMENTS`."""
    run.state.update(lambda state: state["tickets"][number].update(settled=state["tickets"][number]["settled"] + 1))


def _ruling_findings(path: Path, rulings: list[tuple[str, str, str, str]]) -> list[dict]:
    """Write the findings file of a correction to rulings: a critical finding of kind `ruling` per (locked file,
    test, title, evidence). Returns the findings."""
    findings = [{"severity": "critical", "file": file, "test": test, "line": 0, "title": title, "evidence": evidence,
                 "kind": "ruling"} for file, test, title, evidence in rulings]
    path.write_text(json.dumps(findings, indent=1) + "\n")
    return findings


def correct(run: Run, call: Call, findings: list[dict], readings: dict[str, Corrected], fate: str) -> dict[str, str]:
    """One test-writer correction of `findings`, outside the fix rounds. `call` is the test-writer's, its values point
    at the findings file. Its commit becomes the baseline of the ticket's own locked files, and the `corrected` of the
    ticket keeps the reading in `readings` of each locked test it corrected. A ledger line per finding says what it
    did, with `fate` for one it did not correct. Returns {title: reason} of each finding it did not correct."""
    number = call.number
    report = run.agent(replace(call, notes=["correction"],
                               check={"correction": True, "findings": [finding["title"] for finding in findings]}))
    tip = driver_slots.head(call.slot)
    reasons = {item["finding"]: item["reason"] for item in report.get("not_addressed", [])}
    kept = {key: readings[key]._asdict() for finding in findings
            if (key := finding_key(finding)) in readings and finding["title"] not in reasons}

    def record(state: dict) -> None:
        entry = state["tickets"][number]
        entry.update(tip=tip, **driver_locked.written(entry, report["locked"], tip))
        entry["corrected"].update(kept)

    run.state.update(record)
    for finding in findings:
        title = finding["title"]
        result = (f"The test-writer did not correct it: {reasons[title]}.{fate}" if title in reasons else
                  f"The test-writer corrected it at {tip[:7]}.")
        ledger(f"Ticket {number}: {finding['kind']} correction in {finding['file']}, outside the fix rounds: {title}: "
               f"{finding['evidence']}. {result}")
    return reasons


def correct_as_answered(run: Run, call: Call, question: dict) -> None:
    """The correction of a `spec-ambiguous` question answered `correct-test`, to the findings of its evidence file.
    No answer changes `corrected`."""
    path = question["evidence"][0]
    values = {**call.values, "findings": os.path.relpath(path, call.slot)}
    correct(run, replace(call, values=values), json.loads(Path(path).read_text()), {}, "")


def _settle(run: Run, call: Call, disputed: list[Dispute]) -> None:
    """A locked test disputed before, or a ticket settled `SETTLEMENTS` times, asks `spec-ambiguous`. Else the
    reviewer rules on each disputed test, a test ruled `test-wrong` gets its one correction (a test corrected before
    asks `spec-ambiguous`), and the ticket record keeps the rulings."""
    number = call.number
    entry = run.state.read()["tickets"][number]
    resume = retry_point(call.role, driver_slots.head(call.slot), call.fix_round)
    if again := second_readings(disputed, entry["disputes"]) or capped(disputed, entry):
        ambiguous(run, number, again, resume)
    n = 1 + len(entry["disputes"])
    ruled = _arbitrate(run, call, disputed, n)
    settled(run, number)
    record = {one.key: ruling for one, ruling in ruled}
    wrong = [(one._replace(reason=ruling["reason"]), ruling) for one, ruling in ruled
             if ruling["ruling"] == "test-wrong"]
    if again := second_readings([one for one, _ in wrong], entry["corrected"]):
        run.state.update(lambda state: state["tickets"][number]["disputes"].update(record))  # the test is arbitrated
        ambiguous(run, number, again, resume)
    if wrong:
        _correct(run, call, wrong, n)
    run.state.update(lambda state: state["tickets"][number]["disputes"].update(record))


def _arbitrate(run: Run, call: Call, disputed: list[Dispute], n: int) -> list[tuple[Dispute, dict]]:
    """The reviewer's ruling on each disputed locked test, from `arbitration-<n>.json`, with its dispute."""
    number, slot = call.number, call.slot
    tip, by_test = driver_slots.head(slot), {one.test: one for one in disputed}
    asked = {**call.values, "role": call.role, "start_sha": tip,
             "disputed": "".join(f"- {one.test}: {one.reason}\n" for one in disputed),
             "dispute_report": os.path.relpath(OUT / number / f"{call.role}-{call.fix_round}.json", slot)}
    with during("the arbitration"):
        report = run.read_only(number, slot, call.values["branch"], tip, lambda: run.agent(Call(
            "reviewer", number, slot, asked, template="arbitration", name=f"arbitration-{n}", retry=call.role,
            fix_round=call.fix_round, check={"disputed": list(by_test)})))
    for ruling in report["rulings"]:
        ledger(f"Ticket {number}: the reviewer ruled {ruling['ruling']} on the locked test {ruling['test']}, "
               f"{ruling['requirement']}: {ruling['reason']}.")
    return [(by_test[ruling["test"]], ruling) for ruling in report["rulings"] if ruling["test"] in by_test]


def _correct(run: Run, call: Call, wrong: list[tuple[Dispute, dict]], n: int) -> None:
    """The correction of the tests ruled `test-wrong`, each ruling a finding of kind `ruling` in `ruled-<n>.json`."""
    number, slot = call.number, call.slot
    path = OUT / number / f"ruled-{n}.json"
    findings = _ruling_findings(path, [(one.file, one.test, f"{one.test}, {ruling['requirement']}", ruling["reason"])
                                       for one, ruling in wrong])
    values = {**call.values, "start_sha": driver_slots.head(slot), "findings": os.path.relpath(path, slot)}
    readings = {one.key: Corrected(one.test, ruling["requirement"], ruling["reason"]) for one, ruling in wrong}
    with during("the test-writer correction"):
        correct(run, Call("test-writer", number, slot, values, name=f"correction-{n}", retry=call.role,
                          fix_round=call.fix_round), findings, readings, "")
