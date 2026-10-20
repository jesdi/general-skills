"""Human stops as bounded questions: the question kinds, decisions.json and what an answer does.

A question in state.json `run.questions`; `answer` is None while it is open:
    {id, title, why, evidence, options: [{id, description}], recommended, ticket, kind, subject, stop, answer}
decisions.json: {run: <run.revision>, questions: [the open ones, first six fields], revision: <SHA-256 of the rest>}."""
from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

PUBLIC = ("id", "title", "why", "evidence", "options", "recommended")
CONTRADICTS = "locked test contradicts spec:"


class Option(NamedTuple):
    description: str
    ruling: str
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
            "again.", {"phase": "test-writer", "note": "overruled", "round": 0}),  # new tests, new fix rounds
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
    "locked-test-again": Kind(
        True,
        "Ticket {ticket}: a locked-test finding is open after the test-writer correction: {subject}",
        "The test-writer correction did not close this finding on the locked tests: the review after it found it "
        "again, or the driver rejected the correction (the ledger line says which). Ticket {ticket} is BLOCKED and "
        "each ticket that it blocks waits.",
        "correct-again",
        {"correct-again": Option(
            "The test-writer corrects the locked tests once more, from the next review. Then the fix rounds go on.",
            "Ruling: {ticket} locked-test finding goes back to the test-writer for one more correction "
            "(correct-again): {subject}.", {"phase": "review"}),
         "accept": Option(
            "Close the finding as it is: the locked tests stay as they are, and no more correction runs for the "
            "ticket. The ticket goes on to its fix rounds with its other findings.",
            "Ruling: {ticket} locked-test finding closed as it is (accept): {subject}.", {"phase": "review"}),
         "redesign": Option(
            "Keep the ticket BLOCKED. The design changes before more work on it.",
            "Ruling: {ticket} stays BLOCKED, the design changes first (redesign): {subject}.", {})}),
    # A final-review question belongs to no ticket: its `ticket` is "".
    "final-finding": Kind(
        True,
        "Final review: a finding is open after the second review round: {subject}",
        "The final review ran its two rounds and this finding is open. Nothing is pushed while it is open.",
        "fix",
        {"fix": Option(
            "Keep the finding open. The run ends with nothing pushed, and the finding is fixed before a push.",
            "Ruling: final-review finding stays open and nothing is pushed (fix): {subject}.", {}),
         "accept": Option(
            "Close the finding as it is. The branch can be pushed when each open finding is accepted.",
            "Ruling: final-review finding closed as it is (accept): {subject}.", {})}),
}


AGAIN = 2  # the extra corrections a ticket may get: a test-writer and a reviewer that never agree cannot loop


def corrections(questions: list[dict], ticket: str) -> tuple[int, bool]:
    """(the extra corrections that the `locked-test-again` stops of `ticket` gave, whether one stop accepted its
    findings). A stop with one `correct-again` answer gives one correction; a stop with only `accept` accepts."""
    stops: dict[int, set] = {}
    for q in questions:
        if (q["ticket"], q["kind"]) == (ticket, "locked-test-again") and q["answer"]:
            stops.setdefault(q["stop"], set()).add(q["answer"])
    return sum("correct-again" in answers for answers in stops.values()), {"accept"} in stops.values()


def contradicted(role: str, report: dict) -> str:
    """The locked tests that a BLOCKED report of an implementer or a fixer says contradict the spec; "" when it is not
    that stop."""
    if role not in ("implementer", "fixer") or report.get("status") != "BLOCKED":
        return ""
    return "; ".join(filter(None, (concern[len(CONTRADICTS):].strip() for concern in report["concerns"]
                                   if concern.startswith(CONTRADICTS))))


def question(kind: str, ticket: str, subject: str, evidence: list[str], asked: list[dict], stop: int) -> dict:
    """A new open question, id `<NN>-<kind>-<n>` (`<kind>-<n>` with no ticket); n counts in `asked`, the whole run."""
    made, values = KINDS[kind], {"ticket": ticket, "subject": subject}
    number = 1 + sum((q["ticket"], q["kind"]) == (ticket, kind) for q in asked)
    options, recommended = made.options, made.recommended
    if kind == "locked-test-again" and corrections(asked, ticket)[0] >= AGAIN:
        options, recommended = {name: options[name] for name in ("accept", "redesign")}, "accept"
    return {"id": f"{ticket}-{kind}-{number}".lstrip("-"), "title": made.title.format(**values), "why": made.why.format(**values),
            "evidence": evidence, "recommended": recommended,
            "options": [{"id": name, "description": option.description} for name, option in options.items()],
            "ticket": ticket, "kind": kind, "subject": subject, "stop": stop, "answer": None}


def stop(run: dict, kind: str, ticket: str, subjects: list[str], evidence: list[str]) -> None:
    """Count one stop in `run` and add one open question of `kind` per subject. No subject is no stop."""
    run["stops"] += bool(subjects)
    for subject in subjects:
        run["questions"].append(question(kind, ticket, subject, evidence, run["questions"], run["stops"]))


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
    return [KINDS[q["kind"]].options[q["answer"]].ruling.format(**q) for q in questions if q["answer"] is not None]


def parked(questions: list[dict]) -> bool:
    return any(q["answer"] is None and KINDS[q["kind"]].parks for q in questions)


def _settled(questions: list[dict], ticket: str, stop: int) -> dict:
    """The ticket fields from the answers of one stop; {} while one is open or one keeps the ticket BLOCKED."""
    fields = [q["answer"] and KINDS[q["kind"]].options[q["answer"]].fields
              for q in questions if (q["ticket"], q["stop"]) == (ticket, stop)]
    return {name: value for one in fields for name, value in one.items()} if all(fields) else {}


def apply(questions: list[dict], answers: dict) -> tuple[list[str], dict[str, dict]]:
    """Answer the open questions in `answers`. Returns (a ruling per answer, ticket -> fields that let it go on)."""
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
