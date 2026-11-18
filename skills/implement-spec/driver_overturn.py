"""Overturn a ruling after the run: `--answer <question id>=<other option>` on a question the driver or the arbiter
answered with no operator. The overturn takes effect as an answer to that question would, from where the run is: an
accepted finding gets a fix batch, a left-out ticket runs again, an end-of-run stop shipped as draft runs again. The
work it adds lands on top of the PR branch, so the push needs no force. It never undoes work, so an overturn that
would is refused, as is one that cannot take effect. No agent ships a `finding-must-fix` finding, so the only ruling of
one to overturn is the driver's `retry`, whose work ran: an overturn never ships it but by the operator's own answer."""
from __future__ import annotations

import driver_final
import driver_pr
from driver_decisions import FINAL, FINDINGS, OPERATOR, ruled_by

EDITS = ("edit", "edit-spec")  # they keep a stop BLOCKED; an overturn lets the run go on


def split(pairs: list[tuple[str, str]], document: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(the `--answer` pairs of an open question of `document`, the overturns: every other pair)."""
    open_ids = {q["id"] for q in document["questions"]}
    return [pair for pair in pairs if pair[0] in open_ids], [pair for pair in pairs if pair[0] not in open_ids]


def refusals(pairs: list[tuple[str, str]], state: dict) -> list[str]:
    """The usage error of each overturn in `pairs` that state.json `state` refuses."""
    asked = {q["id"]: q for q in state["run"]["questions"]}
    return [f"--answer {question}={option}: {why}" for question, option in pairs
            if (why := _refusal(asked.get(question), question, option, state))]


def _refusal(q: dict | None, question: str, option: str, state: dict) -> str:
    """Why the overturn of `question` to `option` is refused; "" when it takes effect."""
    if q is None:
        return f"there is no question {question}"
    return next((why for check in _CHECKS if (why := check(q, option, state))), "")


def _operators(q: dict, option: str, state: dict) -> str:
    if ruled_by(q) == OPERATOR:
        return f"you answered {q['id']}: only a ruling of the driver or the arbiter is overturned"
    return ""


def _offered(q: dict, option: str, state: dict) -> str:
    offered = [o["id"] for o in q["options"]]
    if option not in offered:
        return f"{q['id']} offers {', '.join(offered)}"
    return f"{q['id']} is answered {option} already" if option == q["answer"] else ""


def _ran(q: dict, option: str, state: dict) -> str:
    """A `retry` or a `correct-test` started work that is on the branch now."""
    if q["answer"] in ("retry", "correct-test"):
        return f"the {q['answer']} of the {q['by']} ran: its work is on the branch, and an overturn undoes no work"
    return ""


def _edits(q: dict, option: str, state: dict) -> str:
    if option in EDITS:
        return (f"an overturn takes an option that lets the run go on: edit the spec or the ticket, then overturn "
                f"{q['id']} to an option that runs the work again")
    return ""


def _owner(ticket: str) -> str:
    return "the end of run" if ticket == FINAL else f"ticket {ticket}"


def _later(q: dict, option: str, state: dict) -> str:
    """Only the newest stop of a ticket, or of the end of run, is where the run is."""
    later = [other["id"] for other in state["run"]["questions"]
             if other["ticket"] == q["ticket"] and other["stop"] > q["stop"]]
    if later:
        owner = _owner(q["ticket"])
        return f"a later stop of {owner} asked {later[-1]}: only the newest stop of {owner} is overturned"
    return ""


def _merged(q: dict, option: str, state: dict) -> str:
    if state["tickets"].get(q["ticket"], {}).get("phase") == "merged":
        return f"ticket {q['ticket']} merged since: its work is on the PR branch"
    return ""


def _closed(q: dict, option: str, state: dict) -> str:
    """A `retry` of a finding needs it open after the last review: the fix batch is due for the open findings."""
    if q["kind"] in FINDINGS and option == "retry" and not driver_final.due(state["run"]["final"], q["subject"]):
        return "the last review leaves the finding closed: no fix batch is due for it"
    return ""


def _written(q: dict, option: str, state: dict) -> str:
    """The pr-writer runs only before the PR opens."""
    if q.get("role") == "pr-writer" and state["run"]["final"]["pr"]:
        return "the PR is open, so the pr-writer does not run again: edit its body on GitHub"
    return ""


def _gone(q: dict, option: str, state: dict) -> str:
    """The work of an overturn goes onto the open PR: a merged or closed one takes none. Last, as it asks gh."""
    url = state["run"]["final"]["pr"]
    shown = driver_pr.state(url) if url else "OPEN"
    if shown == "OPEN":
        return ""
    return f"the PR {url} is {shown.lower() or 'not readable with gh'}: an overturn adds work to an open PR only"


_CHECKS = (_operators, _offered, _ran, _edits, _later, _merged, _closed, _written, _gone)
