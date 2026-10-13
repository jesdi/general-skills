"""Human stops as bounded questions (requirement 17): the question kinds, decisions.json and what an answer does.
Stdlib only.

A question lives in state.json (`run.questions`) from its stop to the end of the run:
    {id, title, why, evidence, options: [{id, description}], recommended, ticket, kind, subject, stop, answer}
`answer` is None while the question is open. decisions.json holds `run` (the random `run.revision` of state.json),
the open questions with the first six fields only, and a `revision`: the hex SHA-256 of
`json.dumps(<the file without revision>, sort_keys=True, separators=(",", ":"))`. Answers bind to that revision, so
an answers file of an earlier run never fits the same question of a new run.
Every title, why, description and ruling is a fixed sentence of this file filled with pointers (requirements 14, 16).
"""
from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

PUBLIC = ("id", "title", "why", "evidence", "options", "recommended")
# The concern of an implementer that makes its BLOCKED report a stop.
CONTRADICTS = "locked test contradicts spec:"


class Option(NamedTuple):
    description: str
    ruling: str  # the ledger sentence of this answer
    fields: dict  # what the ticket record gets when its stop is settled; {} keeps the ticket BLOCKED


class Kind(NamedTuple):
    parks: bool  # an open question of this kind ends the run with exit 2
    title: str
    why: str
    recommended: str
    options: dict[str, Option]


KINDS = {
    "locked-test": Kind(
        True,
        "Ticket {ticket}: the implementer reports that a locked test contradicts the spec: {subject}",
        "The implementer of ticket {ticket} stopped with BLOCKED. The ticket and each ticket that it blocks wait "
        "until the test or the implementer is overruled.",
        "overrule-implementer",
        {"overrule-test": Option(
            "The test is wrong. The test-writer corrects it in one correction run, then the implementer runs again.",
            "Ruling: {ticket} locked test overruled: the test-writer corrects {subject}, then the implementer runs "
            "again.", {"phase": "test-writer", "note": "overruled"}),
         "overrule-implementer": Option(
            "The test is right. The implementer runs again and makes the test green as it is.",
            "Ruling: {ticket} locked test stands: the implementer is overruled on {subject} and runs again.",
            {"phase": "implement", "note": "stands"})}),
    "finding": Kind(
        False,
        "Ticket {ticket}: a finding is open after fix round 4: {subject}",
        "Four fix rounds did not close this finding, so ticket {ticket} is BLOCKED and each ticket that it blocks "
        "waits.",
        "split",
        {"accept": Option(
            "Close the finding as it is. The ticket merges when each of its open findings is accepted.",
            "Ruling: {ticket} finding closed as it is (accept): {subject}.", {"phase": "merge"}),
         "split": Option(
            "Keep the ticket BLOCKED. The open part becomes a ticket of its own.",
            "Ruling: {ticket} stays BLOCKED, the open part becomes a new ticket (split): {subject}.", {}),
         "redesign": Option(
            "Keep the ticket BLOCKED. The design changes before more work on it.",
            "Ruling: {ticket} stays BLOCKED, the design changes first (redesign): {subject}.", {})}),
}


def contradicted(role: str, report: dict) -> str:
    """The locked tests that an implementer's BLOCKED report says contradict the spec; "" when the report is not
    that stop. Any other BLOCKED report is an ordinary BLOCKED ticket."""
    if role != "implementer" or report.get("status") != "BLOCKED":
        return ""
    return "; ".join(filter(None, (concern[len(CONTRADICTS):].strip() for concern in report["concerns"]
                                   if concern.startswith(CONTRADICTS))))


def question(kind: str, ticket: str, subject: str, evidence: list[str], asked: list[dict], stop: int) -> dict:
    """A new open question of stop number `stop`. Its id is `<NN>-<kind>-<n>`; n counts the questions of that ticket
    and kind in the whole run (`asked` is every question of the run), so a second stop never takes an id again."""
    made, values = KINDS[kind], {"ticket": ticket, "subject": subject}
    number = 1 + sum((q["ticket"], q["kind"]) == (ticket, kind) for q in asked)
    return {"id": f"{ticket}-{kind}-{number}", "title": made.title.format(**values), "why": made.why.format(**values),
            "evidence": evidence, "recommended": made.recommended,
            "options": [{"id": name, "description": option.description} for name, option in made.options.items()],
            "ticket": ticket, "kind": kind, "subject": subject, "stop": stop, "answer": None}


def document(run: dict) -> dict:
    """decisions.json for `run` of state.json: the run, its open questions, and the revision that answers bind to."""
    body = {"run": run["revision"],
            "questions": [{name: q[name] for name in PUBLIC} for q in run["questions"] if q["answer"] is None]}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {"revision": hashlib.sha256(canonical.encode()).hexdigest(), **body}


def parked(questions: list[dict]) -> bool:
    return any(q["answer"] is None and KINDS[q["kind"]].parks for q in questions)


def _settled(questions: list[dict], ticket: str, stop: int) -> dict:
    """The fields the ticket gets from the answers of one stop: {} while a question of the stop is open, and {}
    when one answer keeps the ticket BLOCKED."""
    fields = [q["answer"] and KINDS[q["kind"]].options[q["answer"]].fields
              for q in questions if (q["ticket"], q["stop"]) == (ticket, stop)]
    return {name: value for one in fields for name, value in one.items()} if all(fields) else {}


def apply(questions: list[dict], answers: dict) -> tuple[list[str], dict[str, dict]]:
    """Give each open question its answer from `answers` (question id -> option id, `<id>.note` -> the operator's
    note). Returns (one ruling sentence per answer, ticket number -> the fields that let the ticket go on).
    An answered question is never open again, so an answer is applied once."""
    rulings, stops = [], {}
    for q in questions:
        if q["answer"] is None and q["id"] in answers:
            q["answer"] = answers[q["id"]]
            note = answers.get(f"{q['id']}.note")
            rulings.append(KINDS[q["kind"]].options[q["answer"]].ruling.format(**q)
                           + f" Note of the operator: {note}" * bool(note))
            stops[q["ticket"]] = q["stop"]
    moved = {ticket: _settled(questions, ticket, stop) for ticket, stop in stops.items()}
    return rulings, {ticket: fields for ticket, fields in moved.items() if fields}


def text(open_questions: list[dict], answers: str, page: str) -> str:
    """What the operator reads at a stop in local mode: each open question with its options, and how to answer."""
    lines = []
    for q in open_questions:
        lines += [f"Question {q['id']}: {q['title']}", f"  Why: {q['why']}", f"  Evidence: {', '.join(q['evidence'])}"]
        lines += [f"  {o['id']}{' (recommended)' * (o['id'] == q['recommended'])}: {o['description']}"
                  for o in q["options"]]
    if lines:
        lines.append(f"Answer each question and run the driver again: --answer <question id>=<option id>, or submit "
                     f"{page}, which writes {answers}.")
    return "".join(line + "\n" for line in lines)
