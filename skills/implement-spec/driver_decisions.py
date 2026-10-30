"""Human stops as bounded questions: the question kinds, decisions.json and what an answer does.

A question in state.json `run.questions`; `answer` is None while it is open:
    {id, title, why, evidence, options: [{id, description}], recommended, ticket, kind, subject, stop, answer,
     resume: {phase[, tip][, round]}, the record fields a `retry` or a `keep-test` sets (`Resume`),
     ruling: the ruling sentence, once answered}
decisions.json: {run: <run.revision>, questions: [the open ones, first six fields], revision: <SHA-256 of the rest>}."""
from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

PUBLIC = ("id", "title", "why", "evidence", "options", "recommended")
FINAL = "final"  # the end of run asks its questions, and has its record and its files, under this name


def owner(ticket: str) -> str:
    """Who stops: `Ticket NN` or the end of run."""
    return "End of run" if ticket == FINAL else f"Ticket {ticket}"


class Resume(NamedTuple):
    """Where a `retry` or a `keep-test` takes a ticket, or the end of run, back to."""
    phase: str
    tip: str | None = None  # the tip it goes on from, for a role that commits
    round: int | None = None  # the fix rounds done, for a fixer: its own round runs again

    def fields(self) -> dict:
        """The fields of the record; one that is None stays as it is."""
        return {name: value for name, value in self._asdict().items() if value is not None}


class Option(NamedTuple):
    description: str
    ruling: str
    resumes: bool  # the record goes back to the `resume` of the question
    fields: dict = {}  # what else the record gets when its stop is settled; no field at all keeps it BLOCKED


class Kind(NamedTuple):
    title: str
    why: str
    recommended: str  # "" for a kind that recommends no option
    options: dict[str, Option]


KINDS = {
    "spec-ambiguous": Kind(
        "Ticket {ticket}: the spec reads two ways on a locked test: {subject}",
        "This locked test was ruled on or corrected once in ticket {ticket}, and a second dispute or a second "
        "correction needs it again: the spec supports both readings. Ticket {ticket} is BLOCKED and each ticket that "
        "it blocks waits.",
        "",
        {"keep-test": Option(
            "The test stands as it is. The role that disputed it runs again and makes it green; a review finding that "
            "asks to correct it again is closed.",
            "Ruling: {ticket} locked test stands (keep-test): {subject}.", True),
         "correct-test": Option(
            "The test-writer corrects the test once more, to the second reading, then the role that disputed it runs "
            "again (a fixer after the review of its round); after a review finding, the review runs again.",
            "Ruling: {ticket} locked test is corrected to the second reading (correct-test): {subject}.", False,
            {"phase": "test-writer", "note": "correction"}),
         "edit-spec": Option(
            "Keep the ticket BLOCKED, so the spec can be edited to say which reading it means.",
            "Ruling: {ticket} stays BLOCKED for an edit of the spec (edit-spec): {subject}.", False)}),
    # The end of run asks it too, with the ticket `final`: for an agent, or for a finding open after the scoped review.
    "ticket-blocked": Kind(
        "{owner}: an agent stopped or was rejected, and its rerun too, or a final-review finding is open: {subject}",
        "An agent ran two times, the second time on the next entry of its model list, and stopped or was rejected "
        "both times; or the scoped review after the fix batch of the final review left a finding open. {owner} is "
        "BLOCKED, each ticket that it blocks waits, and nothing is pushed.",
        "retry",
        {"retry": Option(
            "The role runs again on its first entry from the tip the ticket left, with its one rerun on the next "
            "entry; for an open final-review finding, one more fix batch and scoped review run.",
            "Ruling: {ticket} runs again (retry): {subject}.", True),
         "edit": Option(
            "Keep it BLOCKED, so the spec or the ticket can be edited before more work on it.",
            "Ruling: {ticket} stays BLOCKED for an edit of the spec or the ticket (edit): {subject}.", False)}),
}


def drop_merged(state: dict) -> list[dict]:
    """Remove the open questions of each merged ticket from `state`: the stop that asked them is over (issue #136).
    Returns them."""
    merged = {number for number, entry in state["tickets"].items() if entry["phase"] == "merged"}
    run = state["run"]
    dropped = [q for q in run["questions"] if q["answer"] is None and q["ticket"] in merged]
    run["questions"] = [q for q in run["questions"] if q not in dropped]
    return dropped


def question_id(kind: str, ticket: str, asked: list[dict]) -> str:
    """The id of the next question of `kind` for `ticket`: `<NN>-<kind>-<n>` (`final-<kind>-<n>` for the end of run);
    n counts in `asked`, the whole run."""
    return f"{ticket}-{kind}-{1 + sum((q['ticket'], q['kind']) == (ticket, kind) for q in asked)}"


def question(kind: str, ticket: str, subject: str, evidence: list[str], asked: list[dict], stop: int,
             resume: Resume) -> dict:
    """A new open question."""
    made, values = KINDS[kind], {"ticket": ticket, "subject": subject, "owner": owner(ticket)}
    return {"id": question_id(kind, ticket, asked), "title": made.title.format(**values),
            "why": made.why.format(**values),
            "evidence": evidence, "recommended": made.recommended,
            "options": [{"id": name, "description": option.description} for name, option in made.options.items()],
            "ticket": ticket, "kind": kind, "subject": subject, "stop": stop, "resume": resume.fields(), "answer": None}


def stop(run: dict, kind: str, ticket: str, subjects: list[str], evidence: list[str], resume: Resume) -> None:
    """Count one stop in `run` and add one open question of `kind` per subject. No subject is no stop."""
    run["stops"] += bool(subjects)
    for subject in subjects:
        run["questions"].append(question(kind, ticket, subject, evidence, run["questions"], run["stops"], resume))


def decides(kind: str, subjects: int) -> str:
    return f"the operator decides on {subjects} question{'s' * (subjects > 1)} of kind {kind}"


def document(run: dict) -> dict:
    """decisions.json for `run` of state.json."""
    body = {"run": run["revision"],
            "questions": [{name: q[name] for name in PUBLIC} for q in run["questions"] if q["answer"] is None]}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {"revision": hashlib.sha256(canonical.encode()).hexdigest(), **body}


def rulings(questions: list[dict]) -> list[str]:
    """The ruling sentence of each answered question, without the note of the operator."""
    return [q["ruling"] for q in questions if q["answer"] is not None]


def ruling(q: dict) -> str:
    """The ruling sentence of the answer to `q`. An answered question keeps it, so a kind can go away."""
    return KINDS[q["kind"]].options[q["answer"]].ruling.format(**q)


def parked(questions: list[dict]) -> bool:
    """An open question ends the run with exit 2."""
    return any(q["answer"] is None for q in questions)


def _goes_on(q: dict) -> dict:
    """The record fields of the answer to `q`: its `resume` whole, when the option resumes, and the option's own."""
    option = KINDS[q["kind"]].options[q["answer"]]
    return {**(q["resume"] if option.resumes else {}), **option.fields}


def _settled(questions: list[dict], ticket: str, stop: int) -> dict:
    """The record fields from the answers of one stop; {} while one is open or one keeps the record BLOCKED."""
    fields = [q["answer"] and _goes_on(q) for q in questions if (q["ticket"], q["stop"]) == (ticket, stop)]
    return {name: value for one in fields for name, value in one.items()} if all(fields) else {}


def apply(questions: list[dict], answers: dict) -> tuple[list[str], dict[str, dict]]:
    """Answer the open questions in `answers`. Returns (a ruling per answer, ticket or FINAL -> the fields that let its
    record go on)."""
    rulings, stops = [], {}
    for q in questions:
        if q["answer"] is None and q["id"] in answers:
            q["answer"] = answers[q["id"]]
            q["ruling"] = ruling(q)
            note = answers.get(f"{q['id']}.note")
            rulings.append(q["ruling"] + f" Note of the operator: {note}" * bool(note))
            stops[q["ticket"]] = q["stop"]
    moved = {ticket: _settled(questions, ticket, stop) for ticket, stop in stops.items()}
    return rulings, {ticket: fields for ticket, fields in moved.items() if fields}


def text(open_questions: list[dict], answers: str, page: str) -> str:
    """What the operator reads at a stop in local mode."""
    lines = []
    for q in open_questions:
        lines += [f"Question {q['id']}: {q['title']}", f"  Why: {q['why']}", f"  Evidence: {', '.join(q['evidence'])}"]
        lines += [f"  {o['id']}{' (recommended)' * (o['id'] == q['recommended'])}: {o['description']}"
                  for o in q["options"]]
    if lines:
        lines.append(f"Answer each question and run the driver again: --answer <question id>=<option id>, or submit "
                     f"{page}, which writes {answers}.")
    return "".join(line + "\n" for line in lines)
