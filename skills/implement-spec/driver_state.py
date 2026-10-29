"""state.json, the resume contract: one lock, every write a read-modify-write, published by rename.

    {"run": {revision, pr_branch, schema, gates: {fast, full}, stops, agents: [{ticket, role, entry, seconds, cost}], questions, start,
             landed: [[ticket, the PR tip its merge made]], final},
     "tickets": {"NN": {phase, round, tip, test_writer_sha, locked, fixtures, green_gates, slot, note, held, rejected,
                     landing}}}

green_gates is {"fast": [commit], "full": [commit]}, in a ticket and in run.final: a fast green never stands in for a
full one. landing is the commit whose full gate was red at the landing while the fixer of the round it started has
not reported, else None."""
from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from pathlib import Path
from typing import IO

from driver_names import Names
from driver_slots import ancestor, git, on_branch, path

SCHEMA = 3  # the version of this contract; 2: the names of driver_names, 3: gate tiers. A run of an older driver has none.
PHASES = ("pending", "test-writer", "implement", "gate", "review", "fix", "merge", "merged", "blocked")


def tiers() -> dict:
    """The green gates of a ticket or of the end of run, per tier."""
    return {"fast": [], "full": []}


def _ticket() -> dict:
    return {"phase": "pending", "round": 0, "tip": None, "test_writer_sha": None, "locked": [], "green_gates": tiers(),
            "slot": None, "note": "", "held": {}, "rejected": {},
            "fixtures": [], "landing": None}


def _final() -> dict:
    """The end-of-run record before it starts. Every key is here, so a reader uses [] and a wrong name fails."""
    return {"skipped": "", "recovery": None, "tidied": None, "onto": None, "base": None, "commits": {},
            "reviewed": None, "round": 0, "open": [], "must_fix": [], "batch": None, "green_gates": tiers(), "pushed": None, "body": None,
            "pr": None}


class State:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    def start(self, pr_branch: str, numbers: list[str]) -> dict:
        """Create the file for a new run, or add the tickets a resumed run does not know yet."""
        def change(state: dict) -> None:
            state.setdefault("run", {
                "revision": uuid.uuid4().hex, "pr_branch": pr_branch, "schema": SCHEMA, "gates": {"fast": 0, "full": 0}, "stops": 0, "agents": [],
                "questions": [], "start": git(".", "rev-parse", f"refs/heads/{pr_branch}").stdout.strip(),
                "landed": [], "final": _final()})
            for key, value in _final().items():  # a run of an older driver lacks the newer keys
                state["run"]["final"].setdefault(key, value)
            tickets = state.setdefault("tickets", {})
            for number in numbers:
                tickets.setdefault(number, _ticket())
            for entry in tickets.values():  # a run of an older driver lacks the newer keys
                for key, value in _ticket().items():
                    entry.setdefault(key, value)
        return self.update(change)

    def update(self, change) -> dict:
        """Apply `change(state)` to the file as it is now. A reader sees the old file or the new one, never a part."""
        with self._lock:
            state = self.read() if self.path.exists() else {}
            change(state)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            scratch = self.path.with_suffix(".json.tmp")
            scratch.write_text(json.dumps(state, indent=1) + "\n")
            os.replace(scratch, self.path)
            return state

    def ticket(self, number: str, **fields) -> dict:
        return self.update(lambda state: state["tickets"][number].update(fields))["tickets"][number]


def lock(path: Path, shared: bool = False) -> IO | None:
    """The kernel lock on `path`, exclusive or `shared`, held while the returned file is open; None when another
    process has it in a way that excludes this one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    held = path.open("a")
    try:
        fcntl.flock(held, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except BlockingIOError:
        held.close()
        return None
    return held


def verified(entry: dict, branch: str, pr_branch: str) -> tuple[dict, str]:
    """(the fields to resume from, why) when git contradicts the entry; ({}, "") when it does not."""
    phase, tip = entry["phase"], str(entry["tip"])
    if phase not in ("gate", "review", "fix", "merge", "merged"):
        return {}, ""
    # The branch of a merged ticket is deleted, so its commits are trusted only while the PR branch has them.
    where, word = (pr_branch, "in") if phase == "merged" else (branch, "on")
    if ancestor(tip, where):
        return {}, ""
    return {"phase": "implement", "tip": entry["test_writer_sha"]}, f"the recorded tip {tip[:7]} is not {word} {where}"


def kept(state: dict) -> dict[str, int]:
    """ticket number -> slot, per ticket stopped mid-role or at an open question, with uncommitted work in the slot."""
    found = {}
    asking = {question["ticket"] for question in state.get("run", {}).get("questions", []) if question["answer"] is None}
    for number, entry in state.get("tickets", {}).items():
        if (entry["phase"] in ("test-writer", "implement") or number in asking) and entry["slot"]:
            slot = path(entry["slot"])
            on = on_branch(slot)
            if on == Names(state["run"]["pr_branch"]).ticket(number) and git(slot, "status", "--porcelain").stdout.strip():
                found[number] = entry["slot"]
    return found
