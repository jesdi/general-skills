"""A locked test that an implementer or a fixer disputes: the reviewer's arbitration, the test-writer correction
(after a ruling, a review finding or a `correct-test` answer), and the `spec-ambiguous` question when a locked file is
disputed or corrected a second time in its ticket.

A dispute is keyed by its locked file, the part of the test id before `::`, so a renamed or parametrized test of a
ruled file is the same test. The ticket record keeps the ruling in `disputes` and the correction in `corrected`.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple, NoReturn

import driver_decisions
import driver_report
import driver_review
import driver_slots
from driver_decisions import Resume
from driver_report import Dispute
from driver_run import OUT, Blocked, Call, Run, during, ledger, retry_point

class Corrected(NamedTuple):
    """The reading a locked file is corrected to, which the ticket's `corrected` keeps: its one correction."""
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
    `correct-test` correction reads: one finding per locked file, to correct to the second reading. The file is named
    after the question: `spec-ambiguous-<n>.json` for `<NN>-spec-ambiguous-<n>`."""
    question = driver_decisions.question_id("spec-ambiguous", number, run.state.read()["run"]["questions"])
    path = OUT / number / f"{question.removeprefix(f'{number}-')}.json"  # the ticket's folder names the ticket
    subjects = [f"{one.test}{f', {one.requirement}' * bool(one.requirement)}: the first reading: {one.first}; the "
                f"second reading: {one.second}" for one in readings]
    _ruling_findings(path, [(one.file, subject, f"correct the test to the second reading: {one.second}")
                            for one, subject in zip(readings, subjects)])
    subject = "; ".join(subjects)
    run.ask(number, "spec-ambiguous", [subject], str(path), resume)
    raise Blocked(f"the spec reads two ways on a locked test: {subject}")


def kept_files(questions: list[dict], number: str) -> set[str]:
    """The locked files of the `spec-ambiguous` questions of `number` answered `keep-test`."""
    return {finding["file"] for q in questions if (q["ticket"], q["kind"], q["answer"]) ==
            (number, "spec-ambiguous", "keep-test") for finding in json.loads(Path(q["evidence"][0]).read_text())}


def correction(questions: list[dict], number: str) -> dict:
    """The last `spec-ambiguous` question of `number` answered `correct-test`, or {}: its evidence is the findings file
    of the correction, the phase of its `resume` the phase after it."""
    answered = [q for q in questions if (q["ticket"], q["kind"], q["answer"]) ==
                (number, "spec-ambiguous", "correct-test")]
    return answered[-1] if answered else {}


def second_readings(found: list[Dispute], record: dict) -> list[Reading]:
    """The readings of each dispute in `found` whose file `record` (`disputes` or `corrected`) has: the record's is
    the first, the dispute's the second."""
    return [Reading(one.file, one.test, record[one.file]["requirement"], record[one.file]["reason"], one.reason)
            for one in found if one.file in record]


def _ruling_findings(path: Path, rulings: list[tuple[str, str, str]]) -> list[dict]:
    """Write the findings file of a correction to rulings: a critical finding of kind `ruling` per (locked file,
    title, evidence). Returns the findings."""
    findings = [{"severity": "critical", "file": file, "line": 0, "title": title, "evidence": evidence, "kind": "ruling"}
                for file, title, evidence in rulings]
    path.write_text(json.dumps(findings, indent=1) + "\n")
    return findings


def correct(run: Run, call: Call, findings: list[dict], readings: dict[str, Corrected], fate: str) -> dict[str, str]:
    """One test-writer correction of `findings`, outside the fix rounds. `call` is the test-writer's, its values point
    at the findings file. Its commit becomes the baseline of the ticket's own locked files, and the `corrected` of the
    ticket keeps the reading in `readings` of each file it corrected. A ledger line per finding says what it did,
    with `fate` for one it did not correct. Returns {title: reason} of each finding it did not correct."""
    number = call.number
    report = run.agent(replace(call, notes=["correction"],
                               check={"correction": True, "findings": [finding["title"] for finding in findings]}))
    tip, reasons = driver_slots.head(call.slot), {item["finding"]: item["reason"] for item in report.get("not_addressed", [])}
    kept = {finding["file"]: readings[finding["file"]]._asdict() for finding in findings
            if finding["file"] in readings and finding["title"] not in reasons}

    def record(state: dict) -> None:
        entry = state["tickets"][number]
        entry.update(tip=tip, **driver_review.written(entry, report["locked"], tip))
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
    """A locked file disputed before asks `spec-ambiguous`. Else the reviewer rules on each disputed test, a test
    ruled `test-wrong` gets its one correction (a file corrected before asks `spec-ambiguous`), and the ticket record
    keeps the rulings."""
    number = call.number
    entry = run.state.read()["tickets"][number]
    resume = retry_point(call.role, driver_slots.head(call.slot), call.fix_round)
    if again := second_readings(disputed, entry["disputes"]):
        ambiguous(run, number, again, resume)
    n = 1 + len(entry["disputes"])
    ruled = _arbitrate(run, call, disputed, n)
    wrong = {file: ruling for file, ruling in ruled.items() if ruling["ruling"] == "test-wrong"}
    if again := second_readings([Dispute(file, ruling["test"], ruling["reason"]) for file, ruling in wrong.items()],
                                entry["corrected"]):
        run.state.update(lambda state: state["tickets"][number]["disputes"].update(ruled))  # the file is arbitrated
        ambiguous(run, number, again, resume)
    if wrong:
        _correct(run, call, wrong, n)
    run.state.update(lambda state: state["tickets"][number]["disputes"].update(ruled))


def _arbitrate(run: Run, call: Call, disputed: list[Dispute], n: int) -> dict[str, dict]:
    """The reviewer's ruling on each disputed locked file, from `arbitration-<n>.json`."""
    number, slot = call.number, call.slot
    tip, files = driver_slots.head(slot), {one.test: one.file for one in disputed}
    asked = {**call.values, "role": call.role, "start_sha": tip,
             "disputed": "".join(f"- {one.test}: {one.reason}\n" for one in disputed),
             "dispute_report": os.path.relpath(OUT / number / f"{call.role}-{call.fix_round}.json", slot)}
    with during("the arbitration"):
        report = run.read_only(number, slot, call.values["branch"], tip, lambda: run.agent(Call(
            "reviewer", number, slot, asked, template="arbitration", name=f"arbitration-{n}", retry=call.role,
            fix_round=call.fix_round, check={"disputed": list(files)})))
    for ruling in report["rulings"]:
        ledger(f"Ticket {number}: the reviewer ruled {ruling['ruling']} on the locked test {ruling['test']}, "
               f"{ruling['requirement']}: {ruling['reason']}.")
    return {files[ruling["test"]]: ruling for ruling in report["rulings"] if ruling["test"] in files}


def _correct(run: Run, call: Call, wrong: dict[str, dict], n: int) -> None:
    """The correction of the tests ruled `test-wrong`, each ruling a finding of kind `ruling` in `ruled-<n>.json`."""
    number, slot = call.number, call.slot
    path = OUT / number / f"ruled-{n}.json"
    findings = _ruling_findings(path, [(file, f"{ruling['test']}, {ruling['requirement']}", ruling["reason"])
                                       for file, ruling in wrong.items()])
    values = {**call.values, "start_sha": driver_slots.head(slot), "findings": os.path.relpath(path, slot)}
    readings = {file: Corrected(ruling["test"], ruling["requirement"], ruling["reason"]) for file, ruling in wrong.items()}
    with during("the test-writer correction"):
        correct(run, Call("test-writer", number, slot, values, name=f"correction-{n}", retry=call.role,
                          fix_round=call.fix_round), findings, readings, "")
