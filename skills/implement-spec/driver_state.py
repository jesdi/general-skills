"""state.json: the resume contract. One lock, every write a read-modify-write, published by rename. Stdlib only.

    {"run": {"revision", "pr_branch", "gates", "stops", "agents": [{ticket, role, entry, seconds, cost}]},
     "tickets": {"NN": {phase, round, tip, test_writer_sha, locked, green_gates, slot}}}

Ticket threads are the only writers and they share one State, so no update is lost between two tickets.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from driver_slots import git, path

PHASES = ("pending", "test-writer", "implement", "gate", "review", "fix", "merge", "merged", "blocked")


def _ticket() -> dict:
    return {"phase": "pending", "round": 0, "tip": None, "test_writer_sha": None, "locked": [], "green_gates": [],
            "slot": None}


class State:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    def start(self, pr_branch: str, numbers: list[str]) -> dict:
        """Create the file for a new run, or add the tickets a resumed run does not know yet."""
        def change(state: dict) -> None:
            state.setdefault("run", {"revision": uuid.uuid4().hex, "pr_branch": pr_branch, "gates": 0, "stops": 0,
                                     "agents": []})
            tickets = state.setdefault("tickets", {})
            for number in numbers:
                tickets.setdefault(number, _ticket())
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


def _ancestor(commit: str, of: str) -> bool:
    return git(".", "merge-base", "--is-ancestor", commit, of).returncode == 0


def verified(entry: dict, branch: str, pr_branch: str) -> tuple[dict, str]:
    """(the fields to resume from, why) when git contradicts the entry; ({}, "") when it does not.

    Requirement 15: a tip that is not on its branch, or the tip of a merged ticket that is not in the PR branch,
    returns the ticket to `implement` from the test-writer's commit. A ticket that has no accepted implementation
    yet starts its phase again, so it has no tip to check. A green gate needs no check: it settles only the commit
    it names, and a commit that is gone is never gated again.
    """
    phase, tip = entry["phase"], str(entry["tip"])
    if phase not in ("gate", "review", "fix", "merge", "merged"):
        return {}, ""
    # The branch of a merged ticket is deleted, so its commits are trusted only while the PR branch has them.
    where, word = (pr_branch, "in") if phase == "merged" else (branch, "on")
    if _ancestor(tip, where):
        return {}, ""
    return {"phase": "implement", "tip": entry["test_writer_sha"]}, f"the recorded tip {tip[:7]} is not {word} {where}"


def kept(state: dict) -> dict[str, int]:
    """ticket number -> slot number, for each ticket that an interrupted run left in the middle of a role with
    uncommitted work in its slot. That slot stays as it is and the role runs there again (the driver drops no work)."""
    found = {}
    for number, entry in state.get("tickets", {}).items():
        if entry["phase"] in ("test-writer", "implement") and entry["slot"]:
            slot = path(entry["slot"])
            on = git(slot, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
            if on == f"ticket/{number}" and git(slot, "status", "--porcelain").stdout.strip():
                found[number] = entry["slot"]
    return found
