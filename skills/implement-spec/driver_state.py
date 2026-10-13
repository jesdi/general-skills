"""state.json: the resume contract. One lock, every write a read-modify-write, published by rename. Stdlib only.

    {"run": {"revision", "pr_branch", "gates", "stops", "agents": [{ticket, role, entry, seconds, cost}],
             "questions": [the questions of every stop, see driver_decisions],
             "start": the PR tip when the run began, "landed": [[ticket, the PR tip its merge made]] in merge order,
             "final": the record of the end of run, see `_final` and driver_final},
     "tickets": {"NN": {phase, round, tip, test_writer_sha, locked, green_gates, slot, note, held}}}

`note` names the prompt template of the ruling that sent the ticket back to a role; "" when there is none.
`held` maps a locked file to the test-writer commit that is its reference when that is not `test_writer_sha`.

Ticket threads are the only writers and they share one State, so no update is lost between two tickets.
`lock` keeps a second driver process out for the whole run: the lock of State is for the threads of one process.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from pathlib import Path
from typing import IO

from driver_slots import git, path

PHASES = ("pending", "test-writer", "implement", "gate", "review", "fix", "merge", "merged", "blocked")


def _ticket() -> dict:
    return {"phase": "pending", "round": 0, "tip": None, "test_writer_sha": None, "locked": [], "green_gates": [],
            "slot": None, "note": "", "held": {}}


def _final() -> dict:
    """The record of the end of run before it starts. Every key is here, so a reader takes each one with [] and a
    wrong name fails. driver_final says what each key means."""
    return {"skipped": "", "recovery": None, "tidied": None, "onto": None, "base": None, "commits": {},
            "reviewed": None, "round": 0, "open": [], "batch": None, "green_gates": [], "pushed": None, "pr": None}


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
                "revision": uuid.uuid4().hex, "pr_branch": pr_branch, "gates": 0, "stops": 0, "agents": [],
                "questions": [], "start": git(".", "rev-parse", f"refs/heads/{pr_branch}").stdout.strip(),
                "landed": [], "final": _final()})
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


def lock(path: Path) -> IO | None:
    """One driver per repository: the exclusive kernel lock on `path`, held while the returned file is open, or
    None when another process holds it. The kernel frees the lock when its holder ends, also when it is killed,
    so no lock is stale and no file is removed by hand. A child process does not get the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    held = path.open("a")
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        held.close()
        return None
    return held


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
    """ticket number -> slot number, for each ticket that an interrupted run left in the middle of a role, or that
    waits for the answer to an open question, with uncommitted work in its slot. That slot stays as it is, no other
    ticket gets it, and the ticket's next role runs there (the driver drops no work)."""
    found = {}
    asking = {question["ticket"] for question in state.get("run", {}).get("questions", []) if question["answer"] is None}
    for number, entry in state.get("tickets", {}).items():
        if (entry["phase"] in ("test-writer", "implement") or number in asking) and entry["slot"]:
            slot = path(entry["slot"])
            on = git(slot, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
            if on == f"ticket/{number}" and git(slot, "status", "--porcelain").stdout.strip():
                found[number] = entry["slot"]
    return found
