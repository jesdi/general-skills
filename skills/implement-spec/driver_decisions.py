"""Human stops as bounded questions: the question kinds, decisions.json and what an answer does.

A question in state.json `run.questions`; `answer` is None while it is open:
    {id, title, why, evidence, options: [{id, description}], recommended, ticket, kind, subject, stop, answer,
     resume: {phase[, tip][, round][, gate_allowance]}, the record fields a `retry` or a `keep-test` sets (`Resume`),
     ruling: the ruling sentence, once answered; by: DRIVER or ARBITER when no operator answered it;
     issue: the URL of the follow-up issue the driver filed for a `follow-up` answer}
decisions.json: {run: <run.revision>, questions: [the open ones, first six fields], revision: <SHA-256 of the rest>}."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import NamedTuple

PUBLIC = ("id", "title", "why", "evidence", "options", "recommended")
DRIVER = "driver"  # `by` of a question the driver answered itself
ARBITER = "arbiter"  # `by` of a question the arbiter agent answered
SHIPS = ("accept", "follow-up")  # the answers to a finding question that ship the PR with the finding open
FINDING_OPEN = "finding-open"  # the kind of an important finding: the arbiter may ship it, as draft
MUST_FIX = "finding-must-fix"  # the kind of a critical or must-fix finding: no agent ships it
FINDINGS = (FINDING_OPEN, MUST_FIX)  # the kinds of a final-review finding that the scoped review leaves open
FINAL = "final"  # the end of run asks its questions, and has its record and its files, under this name


def owner(ticket: str) -> str:
    """Who stops: `Ticket NN` or the end of run."""
    return "End of run" if ticket == FINAL else f"Ticket {ticket}"


class Resume(NamedTuple):
    """Where a `retry` or a `keep-test` takes a ticket, or the end of run, back to."""
    phase: str
    tip: str | None = None  # the tip it goes on from, for a role that commits
    round: int | None = None  # the fix rounds done, for a fixer: its own round runs again
    gate_allowance: int | None = None  # explicit operator grant for final integration repair

    def fields(self) -> dict:
        """The fields of the record; one that is None stays as it is."""
        return {name: value for name, value in self._asdict().items() if value is not None}

    def checkpoint(self) -> dict:
        """Retain completed work at a stop without applying a future operator grant."""
        return self._replace(gate_allowance=None).fields()


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
    arbiter: bool = False  # the arbiter rules on it once the driver's retries are spent; else only the operator ships


FINDING_OPTIONS = {  # both finding kinds: they differ only in who may ship the finding (`Kind.arbiter`)
    "retry": Option(
        "One more fix batch and scoped review.",
        "Ruling: {ticket} runs again (retry): {subject}.", True),
    "accept": Option(
        "Push and open the PR as draft, with the finding open and listed in its rulings.",
        "Ruling: {ticket} ships with the finding open (accept): {subject}.", True),
    "follow-up": Option(
        "As `accept`, and the driver files the finding as an issue before it opens the PR.",
        "Ruling: {ticket} ships with the finding open, filed as a follow-up issue (follow-up): {subject}.", True)}


KINDS = {
    "spec-ambiguous": Kind(
        "Ticket {ticket}: the spec reads two ways on a locked test: {subject}",
        "This locked test was ruled on or corrected once in ticket {ticket}, and a second dispute or a second "
        "correction needs it again: the spec supports both readings. Or the locked tests of ticket {ticket} were "
        "arbitrated or corrected so often that the driver asks before one more: the first reading says so. Ticket "
        "{ticket} is BLOCKED and each ticket that it blocks waits.",
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
    # The end of run asks it too, with the ticket `final`: for an agent, or for the integration gate.
    "ticket-blocked": Kind(
        "{owner}: an agent stopped or was rejected, and its rerun too, or the integration gate remains red: {subject}",
        "An agent ran two times, using the same model for a timeout or moved branch and the next entry otherwise, "
        "and stopped or was rejected both times; or the integration gate stayed red after its repair. {owner} is "
        "BLOCKED, each ticket that it blocks waits, and nothing is pushed.",
        "retry",
        {"retry": Option(
            "The role runs again on its first entry from the tip the ticket left, with one rerun (the same model "
            "for a timeout or moved branch, the next entry otherwise); an open final-review finding gets one more "
            "fix batch and scoped review, and a red integration gate gets one more repair batch from its saved "
            "log, followed by the full gate and review.",
            "Ruling: {ticket} runs again (retry): {subject}.", True),
         "edit": Option(
            "Keep it BLOCKED, so the spec or the ticket can be edited before more work on it.",
            "Ruling: {ticket} stays BLOCKED for an edit of the spec or the ticket (edit): {subject}.", False)}),
    # Only the end of run asks the two finding kinds: the subject is the finding's title and file (`subject`).
    FINDING_OPEN: Kind(
        "End of run: a final-review finding is open after the scoped review: {subject}",
        "The scoped review after the fix batch left this important finding open. Nothing is pushed until it is ruled "
        "on.",
        "retry", FINDING_OPTIONS, arbiter=True),
    MUST_FIX: Kind(
        "End of run: a critical or must-fix finding is open after the scoped review: {subject}",
        "The scoped review after the fix batch left this critical or must-fix finding open, and the driver's own fix "
        "batches are spent. No agent ships it: only you may. Nothing is pushed until it is ruled on.",
        "retry", FINDING_OPTIONS),
}


def finding_kind(finding: dict) -> str:
    """The kind of the question that an open final-review finding asks."""
    return MUST_FIX if finding["severity"] == "critical" or finding.get("must_fix") else FINDING_OPEN


def subject(finding: dict) -> str:
    """A final-review finding as its question names it: its title and file, not its line, which a fix batch moves."""
    return f"{finding['title']} ({finding['file']})"


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
    n counts the ids of `asked`, the whole run, so a question whose kind changed (driver_legacy) keeps its id."""
    prefix = f"{ticket}-{kind}-"
    return f"{prefix}{1 + sum(q['id'].startswith(prefix) for q in asked)}"


def question(kind: str, ticket: str, subject: str, evidence: list[str], asked: list[dict], stop: int,
             resume: Resume) -> dict:
    """A new open question."""
    made, values = KINDS[kind], {"ticket": ticket, "subject": subject, "owner": owner(ticket)}
    return {"id": question_id(kind, ticket, asked), "title": made.title.format(**values),
            "why": made.why.format(**values),
            "evidence": evidence, "recommended": made.recommended,
            "options": [{"id": name, "description": option.description} for name, option in made.options.items()],
            "ticket": ticket, "kind": kind, "subject": subject, "stop": stop, "resume": resume.fields(), "answer": None}


def stop(run: dict, ticket: str, asked: list[tuple[str, str]], evidence: list[str], resume: Resume) -> None:
    """Count one stop in `run` and add one open question per (kind, subject) of `asked`. Nothing asked is no stop."""
    run["stops"] += bool(asked)
    for kind, subject in asked:
        run["questions"].append(question(kind, ticket, subject, evidence, run["questions"], run["stops"], resume))


def decides(asked: list[tuple[str, str]]) -> str:
    """What the operator decides on at a stop that asks `asked`, (kind, subject) pairs."""
    counts = Counter(kind for kind, _ in asked)
    return "the operator decides on " + " and ".join(
        f"{n} question{'s' * (n > 1)} of kind {kind}" for kind, n in counts.items())


def document(run: dict) -> dict:
    """decisions.json for `run` of state.json."""
    body = {"run": run["revision"],
            "questions": [{name: q[name] for name in PUBLIC} for q in run["questions"] if q["answer"] is None]}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {"revision": hashlib.sha256(canonical.encode()).hexdigest(), **body}


def rulings(questions: list[dict]) -> list[str]:
    """The ruling sentence of each answered question, without the note of the operator, with its follow-up issue."""
    return [q["ruling"] + (f" Follow-up issue: {q['issue']}" if q.get("issue") else "")
            for q in questions if q["answer"] is not None]


def waived(questions: list[dict], finding: dict) -> bool:
    """An answer to a question on `finding` ships it: no fix batch is due. A finding that is critical or must-fix now
    is shipped only by the operator's answer, not by the driver's or the arbiter's (`by`) to it as an important one."""
    by = [q.get("by") for q in questions if _ships(q, subject(finding))]  # "" or none for the operator
    return not all(by) if finding_kind(finding) == MUST_FIX else bool(by)


def agent_shipped(questions: list[dict], finding: dict) -> bool:
    """The driver or the arbiter (`by`), not the operator, answered a question on `finding` with an option that ships it."""
    return any(q.get("by") for q in questions if _ships(q, subject(finding)))


def _ships(q: dict, named: str) -> bool:
    """`q` is a finding question on the subject `named`, answered with an option that ships it."""
    return q["kind"] in FINDINGS and q["answer"] in SHIPS and q["subject"] == named


def follow_ups(questions: list[dict]) -> list[dict]:
    """The finding questions answered `follow-up` with no issue filed yet."""
    return [q for q in questions if q["kind"] in FINDINGS and q["answer"] == "follow-up" and not q.get("issue")]


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


def ruled(questions: list[dict]) -> Counter:
    """ticket (or FINAL) -> the stops of it that the driver ruled on."""
    return Counter(ticket for ticket, _ in {(q["ticket"], q["stop"]) for q in questions if q.get("by") == DRIVER})


def automatic(questions: list[dict], retries: int, must_fix_retries: int) -> dict:
    """The answers the driver gives itself: `retry` for each open question that recommends it, while the driver ruled
    on fewer than `retries` stops of its ticket (or of the end of run). A `finding-must-fix` question counts only the
    stops with one that the driver ruled, up to `retries + must_fix_retries`: that many fix batches run while a
    critical or must-fix finding is open, whatever else stopped the end of run. No other option is the driver's to
    take: a kind that recommends one still asks the operator."""
    every = (ruled(questions), retries)
    scopes = {MUST_FIX: (ruled([q for q in questions if q["kind"] == MUST_FIX]), retries + must_fix_retries)}
    return {q["id"]: "retry" for q in questions if _due(q, *scopes.get(q["kind"], every))}


def _due(q: dict, spent: Counter, budget: int) -> bool:
    """`q` is open, recommends `retry`, and the driver ruled on fewer than `budget` stops of its ticket, as `spent`
    counts them."""
    return q["answer"] is None and q["recommended"] == "retry" and spent[q["ticket"]] < budget


def _answer(q: dict, answers: dict, by: str, why: str) -> str:
    """Answer `q` from `answers` and return its ruling, with the note of the operator."""
    q["answer"], q["by"] = answers[q["id"]], by
    q["ruling"] = ruling(q) + (f" Ruled by the {by}, not the operator{why}." if by else "")
    note = answers.get(f"{q['id']}.note")
    return q["ruling"] + f" Note of the operator: {note}" * bool(note)


def apply(questions: list[dict], answers: dict, by: str = "", why: dict[str, str] | None = None
          ) -> tuple[list[str], dict[str, dict]]:
    """Answer the open questions in `answers`; `by` is DRIVER for the answers of `automatic` and ARBITER for those of
    the arbiter, whose `why` gives the end of the ruling sentence per question. Returns (a ruling per answer, ticket or
    FINAL -> the fields that let its record go on)."""
    rulings, stops = [], {}
    for q in questions:
        if q["answer"] is None and q["id"] in answers:
            rulings.append(_answer(q, answers, by, (why or {}).get(q["id"], "")))
            stops[q["ticket"]] = q["stop"]
    return rulings, _moved(questions, stops)


def _moved(questions: list[dict], stops: dict) -> dict[str, dict]:
    """ticket or FINAL -> the fields that let its record go on, for each of `stops` whose answers settle it."""
    moved = {ticket: _settled(questions, ticket, stop) for ticket, stop in stops.items()}
    return {ticket: fields for ticket, fields in moved.items() if fields}


def without_operator(questions: list[dict]) -> list[dict]:
    """The questions the driver or its arbiter answered, with no operator."""
    return [q for q in questions if q.get("by") in (DRIVER, ARBITER)]


def arbitrated(questions: list[dict]) -> list[dict]:
    """The open questions the arbiter rules on once the driver's retries are spent: those of a `Kind.arbiter` kind."""
    return [q for q in questions if q["answer"] is None and KINDS[q["kind"]].arbiter]


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
