"""Ticket files in, task graph or error list out. Stdlib only.

parse(path) -> (tickets, errors)
  tickets: list of Ticket; empty when errors is not empty.
  errors:  list of str, one per problem, each starting with the file name.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_FILE = re.compile(r"(\d+)-.*\.md$")
_TITLE = re.compile(r"# \d+: *(\S.*)")
_FIELD = re.compile(r"\*\*(Blocked by|Seam|Touches|Risk|Suspicions):\*\* *(\S.*)")
_CRITERION = re.compile(r"- \[[ xX]\] *(\S.*)")
_NONE = re.compile(r"none\b", re.IGNORECASE)  # "None (can start immediately)", "none — refactor"


@dataclass
class Ticket:
    number: str  # as in the file name: "01"
    file: str    # "01-export-rows.md"
    title: str | None
    blocked: str | None  # the Blocked by text; blocked_by holds the resolved numbers
    seam: str | None
    touches: str | None
    risk: str | None
    suspicions: str | None
    criteria: list[str]
    lines: dict[str, int]  # field name -> 1-based line
    blocked_by: list[str] = field(default_factory=list)


def _read(path: Path) -> Ticket:
    found: dict[str, tuple[str, int]] = {}  # the first entry of each field wins
    criteria: list[str] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if m := _TITLE.match(line):
            found.setdefault("title", (m.group(1).strip(), number))
        elif m := _FIELD.match(line):
            found.setdefault(m.group(1), (m.group(2).strip(), number))
        elif m := _CRITERION.match(line):
            criteria.append(m.group(1).strip())
    text = {name: value for name, (value, _) in found.items()}
    return Ticket(
        number=_FILE.match(path.name).group(1), file=path.name, title=text.get("title"),
        blocked=text.get("Blocked by"), seam=text.get("Seam"), touches=text.get("Touches"),
        risk=text.get("Risk"), suspicions=text.get("Suspicions"), criteria=criteria,
        lines={name: line for name, (_, line) in found.items()},
    )


def _missing(ticket: Ticket, alone: bool) -> list[str]:
    required = (("title", ticket.title), ("Blocked by", ticket.blocked), ("Seam", ticket.seam))
    errors = [f"{ticket.file}: missing {name}" for name, value in required if not value]
    if not ticket.criteria and not _NONE.match(ticket.seam or ""):
        errors.append(f"{ticket.file}: missing criterion")
    if not ticket.touches and not alone:
        where = f" (expected after Seam, line {ticket.lines['Seam']})" if ticket.seam else ""
        errors.append(f"{ticket.file}: missing Touches{where}")
    return errors


def _blockers(ticket: Ticket, numbers: dict[int, str], titles: dict[str, int]) -> list[str]:
    errors: list[str] = []
    # ponytail: entries split on commas, so a title that holds a comma must be named by number.
    for part in (p.strip() for p in ticket.blocked.split(",")):
        m = re.match(r"\d+", part)
        key = int(m.group()) if m else titles.get(part.lower())
        if key in numbers:
            ticket.blocked_by.append(numbers[key])
        else:
            name = m.group() if m else repr(part)
            errors.append(f"{ticket.file}: Blocked by {name} names no ticket (line {ticket.lines['Blocked by']})")
    return errors


def _link(tickets: list[Ticket]) -> list[str]:
    """Fill blocked_by from each Blocked by text: numbers, titles, or both."""
    numbers = {int(t.number): t.number for t in tickets}
    titles = {t.title.lower(): int(t.number) for t in tickets if t.title}
    blocked = [t for t in tickets if t.blocked and not _NONE.match(t.blocked)]
    return [e for t in blocked for e in _blockers(t, numbers, titles)]


def _cycles(tickets: list[Ticket]) -> list[str]:
    by = {t.number: t for t in tickets}
    done: set[str] = set()
    errors: list[str] = []

    def visit(number: str, path: list[str]) -> None:
        if number in path:
            files = [by[n].file for n in path[path.index(number):]]
            errors.append(f"{files[0]}: Blocked by cycle: {' -> '.join(files + files[:1])}")
        elif number not in done:
            for blocker in by[number].blocked_by:
                visit(blocker, path + [number])
            done.add(number)

    for number in sorted(by):
        visit(number, [])
    return errors


def parse(path) -> tuple[list[Ticket], list[str]]:
    files = sorted(p for p in Path(path).glob("*.md") if _FILE.match(p.name))
    if not files:
        return [], [f"{path}: no ticket files (NN-name.md)"]
    tickets = [_read(p) for p in files]
    errors = [e for t in tickets for e in _missing(t, alone=len(tickets) == 1)]
    errors += _link(tickets)
    errors += _cycles(tickets)
    return ([], errors) if errors else (tickets, [])


def max_frontier_width(tickets: list[Ticket]) -> int:
    """The most tickets ready at once when every ready ticket merges before the next start."""
    by = {t.number: t for t in tickets}
    depths: dict[str, int] = {}

    def depth(number: str) -> int:
        if number not in depths:
            depths[number] = 1 + max((depth(b) for b in by[number].blocked_by), default=0)
        return depths[number]

    # ponytail: blocking edges only; Touches holds belong to the scheduler (ticket 03).
    return max(Counter(depth(n) for n in by).values())
