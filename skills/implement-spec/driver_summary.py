"""The summary of a run, from state.json: plain sentences and the measurement table (requirement 20). Stdlib only."""
from __future__ import annotations

from collections import Counter, defaultdict


def _rows(run: dict, tickets: dict) -> list[tuple[str, object]]:
    agents = Counter((agent["role"], agent["entry"]) for agent in run["agents"])
    seconds: dict[str, float] = defaultdict(float)
    for agent in run["agents"]:
        seconds[agent["role"]] += agent["seconds"]
    rounds = ", ".join(f"{number}: {entry['round']}" for number, entry in tickets.items())
    return [("gates run", run["gates"]),
            *((f"agents, {role} on {entry}", number) for (role, entry), number in agents.items()),
            *((f"wall time, {role}", f"{total:.1f} s") for role, total in seconds.items()),
            ("fix rounds per ticket", rounds),
            ("human stops", run["stops"]),
            ("cost", f"{sum(agent['cost'] for agent in run['agents']):.2f} USD")]


def _end(run: dict) -> list[str]:
    """What the end of run did, from its record: without this sentence a run with no final review reads as done."""
    final = run["final"]
    if final["skipped"]:
        return [f"The end-of-run work (rebase, tidy, final review, push) did not run: {final['skipped']}."]
    if final["pushed"]:
        return [f"{run['pr_branch']} is pushed at {final['pushed'][:7]}; its PR is {final['pr'] or 'not open'}."]
    if final["reviewed"]:
        return [f"The final review covers {run['pr_branch']} up to {final['reviewed'][:7]}. Nothing is pushed."]
    return []


def render(state: dict) -> str:
    run, tickets = state["run"], state["tickets"]
    merged = sum(entry["phase"] == "merged" for entry in tickets.values())
    lines = [f"{merged} of {len(tickets)} tickets are merged into {run['pr_branch']}."]
    lines += [f"Ticket {number} is not merged: its phase is {entry['phase']}."
              for number, entry in tickets.items() if entry["phase"] != "merged"] + _end(run)
    table = ["| Measure | Value |", "|---|---|"] + [f"| {name} | {value} |" for name, value in _rows(run, tickets)]
    return "\n".join(lines + ["", *table]) + "\n"
