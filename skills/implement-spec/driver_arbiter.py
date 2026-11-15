"""The arbiter: when the driver's own retries are spent, one read-only agent rules on each open question that
driver_decisions.arbitrated names, on another provider than the implementer, with a reason and the cost if it is
wrong. An arbiter that cannot rule leaves the questions to the operator."""
from __future__ import annotations

import driver_agents
import driver_decisions
from driver_run import FINAL, OUT, Blocked, Call, Run, ledger


def _values(run: Run, asked: list[dict]) -> dict[str, str]:
    tip = run.tip()
    return {"spec": run.args.spec, "tickets": run.args.tickets, "pr_branch": run.pr, "branch": run.pr,
            "start_sha": tip, "ledger": str(OUT / "ledger.md"),
            "questions": "".join(f"- {q['id']}: {q['title']}. Evidence: {', '.join(q['evidence'])}\n" for q in asked)}


def _why(ruling: dict) -> str:
    """The end of the ruling sentence: the reason of the arbiter and the cost if it is wrong."""
    return f": {ruling['reason'].rstrip('.')}. Cost if wrong: {ruling['cost'].rstrip('.')}"


def rule(run: Run) -> tuple[dict[str, str], dict[str, str]]:
    """(question id -> the arbiter's option, question id -> the end of its ruling sentence); both {} when no question
    is the arbiter's or the arbiter could not rule."""
    asked = driver_decisions.arbitrated(run.state.read()["run"]["questions"])
    if not asked:
        return {}, {}
    entries = driver_agents.arbiter_entries(run.models)
    call = Call("arbiter", FINAL, ".", _values(run, asked), entry=entries[0], entries=entries,
                name=f"arbiter-{asked[0]['stop']}", check={"questions": [q["id"] for q in asked]})
    try:
        report = run.read_only(FINAL, ".", run.pr, call.values["start_sha"], lambda: run.agent(call))
    except Blocked as blocked:
        run.asks.pop(FINAL, None)  # the operator answers the questions the arbiter left, not one about the arbiter
        ledger(f"End of run: the arbiter could not rule, so the operator decides: {blocked}.")
        return {}, {}
    return _answers(report["rulings"], call.check["questions"])


def _answers(rulings: list[dict], asked: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """The rulings on the questions in `asked`, as `rule` returns them."""
    rulings = [one for one in rulings if one["question"] in asked]
    return {one["question"]: one["ruling"] for one in rulings}, {one["question"]: _why(one) for one in rulings}
