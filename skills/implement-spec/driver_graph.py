"""The frontier: which tickets may start now, and which wait on a running ticket's Touches."""
from __future__ import annotations

import re

from driver_tickets import Ticket


def touches(ticket: Ticket) -> set[tuple[str, str]]:
    """(file, function) per backticked entry of the Touches line; function "" is the whole file."""
    entries = re.findall(r"`([^`]+)`", ticket.touches or "")
    return {(file.strip(), function.strip()) for file, _, function in (entry.partition(":") for entry in entries)}


def overlap(one: Ticket, other: Ticket) -> str:
    """The first place both tickets name, "" when there is none. A whole file overlaps each function in it."""
    for file, function in sorted(touches(one)):
        for other_file, other_function in touches(other):
            if file == other_file and (not function or not other_function or function == other_function):
                return f"{file}: {function or other_function}".rstrip(": ")
    return ""


def _waits(ticket: Ticket, phases: dict[str, str], running: set[str]) -> bool:
    return (ticket.number not in running and phases[ticket.number] not in ("merged", "blocked")
            and all(phases[blocker] == "merged" for blocker in ticket.blocked_by))


def frontier(tickets: list[Ticket], phases: dict[str, str], running: set[str]) -> tuple[list[Ticket], dict[str, str]]:
    """(ready tickets in file order, held number -> why). Two ready tickets can overlap: start one, then ask again."""
    by = {t.number: t for t in tickets}
    ready: list[Ticket] = []
    holds: dict[str, str] = {}
    for ticket in tickets:
        if not _waits(ticket, phases, running):
            continue
        holder = next((n for n in sorted(running) if overlap(ticket, by[n])), None)
        if holder:
            holds[ticket.number] = f"held while {holder} runs: both touch {overlap(ticket, by[holder])}"
        else:
            ready.append(ticket)
    return ready, holds
