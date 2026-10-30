"""A locked test that an implementer or a fixer disputes: the reviewer's arbitration, one correction of a test ruled
wrong, and the `spec-ambiguous` question when a locked file is disputed or corrected a second time in its ticket.

A dispute is keyed by its locked file, the part of the test id before `::`, so a renamed or parametrized test of a
ruled file is the same test. The ticket record keeps the ruling in `disputes` and the correction in `corrected`.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import driver_report
import driver_review
import driver_slots
from driver_run import OUT, Blocked, Call, Disputed, Run, during, ledger, retry_point

Reading = tuple[str, str, str, str, str]  # (locked file, test, requirement, the first reading, the second reading)


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
        try:
            return run.agent(replace(call, values=values, notes=[*call.notes, *["ruled"] * ruled]))
        except Disputed as disputed:
            _settle(run, replace(call, values=values), disputed.report)
        call = replace(call, values={**call.values, "start_sha": driver_slots.head(call.slot)})


def ambiguous(run: Run, number: str, readings: list[Reading], resume: str, fields: dict) -> NoReturn:
    """Ask one `spec-ambiguous` question on `readings` and block the ticket. Its evidence is a findings file that a
    `correct-test` correction reads: one finding per locked file, to correct to the second reading."""
    asked = run.state.read()["run"]["questions"]
    n = 1 + sum((q["ticket"], q["kind"]) == (number, "spec-ambiguous") for q in asked)
    path = OUT / number / f"spec-ambiguous-{n}.json"
    subjects = [f"{test}{f', {requirement}' * bool(requirement)}: the first reading: {first}; the second reading: "
                f"{second}" for _, test, requirement, first, second in readings]
    findings = [{"severity": "critical", "file": file, "line": 0, "title": subject, "kind": "ruling",
                 "evidence": f"correct the test to the second reading: {second}"}
                for (file, *_, second), subject in zip(readings, subjects)]
    path.write_text(json.dumps(findings, indent=1) + "\n")
    subject = "; ".join(subjects)
    run.ask(number, "spec-ambiguous", [subject], str(path), resume, **fields)
    raise Blocked(f"the spec reads two ways on a locked test: {subject}")


def kept(questions: list[dict], number: str) -> set[str]:
    """The locked files of the `spec-ambiguous` questions of `number` answered `keep-test`."""
    return {finding["file"] for q in questions if (q["ticket"], q["kind"], q["answer"]) ==
            (number, "spec-ambiguous", "keep-test") for finding in json.loads(Path(q["evidence"][0]).read_text())}


def correction(questions: list[dict], number: str) -> dict:
    """The last `spec-ambiguous` question of `number` answered `correct-test`, or {}: its evidence is the findings file
    of the correction, its `resume` the phase after it."""
    answered = [q for q in questions if (q["ticket"], q["kind"], q["answer"]) ==
                (number, "spec-ambiguous", "correct-test")]
    return answered[-1] if answered else {}


def seconds(found: list[tuple[str, str, str]], record: dict) -> list[Reading]:
    """The readings of each (file, test, reason) in `found` whose file `record` (`disputes` or `corrected`) has."""
    return [(file, test, record[file]["requirement"], record[file]["reason"], reason) for file, test, reason in found
            if file in record]


def _record(entry: dict, ruled: dict[str, dict], wrong: dict[str, dict]) -> None:
    entry["disputes"].update(ruled)
    entry["corrected"].update({file: {name: ruling[name] for name in ("test", "requirement", "reason")}
                               for file, ruling in wrong.items()})


def _settle(run: Run, call: Call, report: dict) -> None:
    """A locked file disputed before asks `spec-ambiguous`. Else the reviewer rules on each disputed test, a test
    ruled `test-wrong` gets its one correction (a file corrected before asks `spec-ambiguous`), and the ticket record
    keeps the rulings."""
    number = call.number
    entry = run.state.read()["tickets"][number]
    disputed = driver_report.disputes(call.role, report, entry["locked"])
    resume = retry_point(call.role, driver_slots.head(call.slot), call.fix_round)
    if again := seconds(disputed, entry["disputes"]):
        ambiguous(run, number, again, *resume)
    n = 1 + len(entry["disputes"])
    ruled = _arbitrate(run, call, disputed, n)
    wrong = {file: ruling for file, ruling in ruled.items() if ruling["ruling"] == "test-wrong"}
    if again := seconds([(file, ruling["test"], ruling["reason"]) for file, ruling in wrong.items()],
                         entry["corrected"]):
        run.state.update(lambda state: _record(state["tickets"][number], ruled, {}))  # the file is arbitrated
        ambiguous(run, number, again, *resume)
    if wrong:
        _correct(run, call, wrong, n)
    run.state.update(lambda state: _record(state["tickets"][number], ruled, wrong))


def _arbitrate(run: Run, call: Call, disputed: list[tuple[str, str, str]], n: int) -> dict[str, dict]:
    """The reviewer's ruling on each disputed locked file, from `arbitration-<n>.json`."""
    number, slot = call.number, call.slot
    tip, files = driver_slots.head(slot), {test: file for file, test, _ in disputed}
    asked = {**call.values, "role": call.role, "start_sha": tip,
             "disputed": "".join(f"- {test}: {reason}\n" for _, test, reason in disputed),
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
    """One test-writer correction of the tests ruled `test-wrong`, each ruling a finding of kind `ruling`."""
    number, slot = call.number, call.slot
    path = OUT / number / f"ruled-{n}.json"
    findings = [{"severity": "critical", "file": file, "line": 0, "title": f"{ruling['test']}, {ruling['requirement']}",
                 "evidence": ruling["reason"], "kind": "ruling"} for file, ruling in wrong.items()]
    path.write_text(json.dumps(findings, indent=1) + "\n")
    asked = {**call.values, "start_sha": driver_slots.head(slot), "findings": os.path.relpath(path, slot)}
    with during("the test-writer correction"):
        report = run.agent(Call("test-writer", number, slot, asked, notes=["correction"], name=f"correction-{n}",
                                retry=call.role, fix_round=call.fix_round,
                                check={"correction": True, "findings": [finding["title"] for finding in findings]}))
    tip, entry = driver_slots.head(slot), run.state.read()["tickets"][number]
    run.state.ticket(number, tip=tip, **driver_review.written(entry, report["locked"], tip))
    ledger(f"Ticket {number}: the test-writer corrected {', '.join(wrong)} at {tip[:7]} after the ruling, outside the "
           "fix rounds.")
    for item in report.get("not_addressed", []):
        ledger(f"Ticket {number}: the test-writer correction did not correct {item['finding']}: {item['reason']}.")
