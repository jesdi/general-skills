"""A run that an older driver started: one lock per repository, ticket branches `ticket/NN` and the recovery ref
`refs/implement/recovery`.

Every run of a newer driver holds `lock` (driver._hold), so a newer and an older driver never run in one repository
at the same time. The driver calls `resume` once, after the locks and before any check reads a ref: a run with no
`schema` in state.json gets the new names of its refs; a run before schema 3 gets its gates as the full tier; a run
before schema 4 keeps the ruling of each answered question, and one with an open question of a removed kind stops.
ponytail: delete this module, tests/test_legacy.py and its one line in driver._hold and in driver._start_errors
once no older driver is left.
"""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import IO

import driver_decisions
import driver_slots
import driver_state
from driver_names import Names
from driver_run import ledger
from driver_slots import git

LOCK = "implement-spec.lock"  # in the git common directory: an older driver holds it for its whole run
RECOVERY = "refs/implement/recovery"


def _checked_out_elsewhere(branch: str) -> bool:
    """True when a worktree that is not this one nor one of its slots has `branch` checked out."""
    here = Path.cwd().resolve()
    for block in git(".", "worktree", "list", "--porcelain").stdout.split("\n\n"):
        lines = block.splitlines()
        if lines and f"branch refs/heads/{branch}" in lines:
            where = Path(lines[0].removeprefix("worktree ")).resolve()
            return where != here and here / ".agent" / "worktrees" not in where.parents
    return False


def _this_runs(entry: dict, legacy: str, start: str) -> bool:
    """A `ticket/NN` is this run's when it holds the commit the run started from and the tip the run recorded, and
    no worktree outside this run has it checked out."""
    if not driver_slots.ancestor(start, legacy) or _checked_out_elsewhere(legacy):
        return False
    return not entry["tip"] or driver_slots.ancestor(entry["tip"], legacy)


def _branch(entry: dict, number: str, names: Names, start: str) -> str:
    """Rename one `ticket/NN` of this run: "" or the error."""
    legacy, new = f"ticket/{number}", names.ticket(number)
    if entry["phase"] in ("pending", "merged") or not driver_slots.branch_exists(legacy):
        return ""
    if driver_slots.branch_exists(new) or not _this_runs(entry, legacy, start):
        return ""
    moved = git(".", "branch", "-m", legacy, new)  # git also moves the HEAD of a slot that has the branch
    if moved.returncode:
        return f"cannot rename the branch {legacy} of an older driver to {new}: {moved.stderr.strip()}"
    ledger(f"Ticket {number}: renamed the branch {legacy} of an older driver to {new}.")
    return ""


def _recovery(run: dict, names: Names) -> str:
    """Move `refs/implement/recovery` to the name of this run when it holds the recovery point that this run
    recorded: "" or the error."""
    recorded = run.get("final", {}).get("recovery")
    if not recorded or git(".", "rev-parse", "-q", "--verify", RECOVERY).stdout.strip() != recorded:
        return ""
    # The old ref goes only when the new one holds the commit: a move that stopped half way is finished next time.
    made = git(".", "update-ref", "-m", "implement-spec: rename", names.recovery, recorded)
    if made.returncode:
        return f"cannot move {RECOVERY} of an older driver to {names.recovery}: {made.stderr.strip()}"
    git(".", "update-ref", "-d", RECOVERY, recorded)
    ledger(f"Recovery point: moved {RECOVERY} of an older driver to {names.recovery}.")
    return ""


def lock(common_dir: Path) -> tuple[IO | None, str]:
    """The lock of an older driver, shared: (the held file, "") or (None, the error) while an older driver runs in
    the repository. Newer drivers hold it together, so they do not stop each other; an older driver holds it alone,
    so it never runs beside one."""
    if held := driver_state.lock(common_dir / LOCK, shared=True):
        return held, ""
    return None, (f"a driver of an older version runs in this repository: it holds the lock {common_dir / LOCK}. "
                  "Wait until it ends, then run the driver again.")


def _removed(run: dict) -> list[str]:
    """Schema 4: the open questions of a kind that this driver no longer has, each as an error."""
    return [f"the open question {q['id']} is of the kind {q['kind']}, which this driver no longer has: answer it with "
            "the driver version that asked it, or start the run again"
            for q in run.get("questions", []) if q["answer"] is None and q["kind"] not in driver_decisions.KINDS]


_REDESIGN = "Ruling: {ticket} stays BLOCKED, the design changes first (redesign): {subject}."
# The ruling sentences of the removed kinds, as the older driver wrote them: kind -> option -> sentence.
_RULINGS = MappingProxyType({
    "locked-test": {
        "overrule-test": "Ruling: {ticket} locked test overruled: the test-writer corrects {subject}, then the "
                         "implementer runs again.",
        "overrule-implementer": "Ruling: {ticket} locked test stands: the implementer is overruled on {subject} and "
                                "runs again."},
    "finding": {
        "accept": "Ruling: {ticket} finding closed as it is (accept): {subject}.",
        "split": "Ruling: {ticket} stays BLOCKED, the open part becomes a new ticket (split): {subject}.",
        "redesign": _REDESIGN},
    "locked-test-again": {
        "correct-again": "Ruling: {ticket} locked-test finding goes back to the test-writer for one more correction "
                         "(correct-again): {subject}.",
        "accept": "Ruling: {ticket} locked-test finding closed as it is (accept): {subject}.",
        "redesign": _REDESIGN},
    "review-stopped": {
        "retry": "Ruling: {ticket} review runs again (retry): {subject}.",
        "accept": "Ruling: {ticket} review goes on past this stop (accept): {subject}.",
        "redesign": _REDESIGN},
    "review-rejected": {
        "retry": "Ruling: {ticket} review runs again (retry): {subject}.",
        "redesign": _REDESIGN},
    "implementer-stopped": {
        "retry": "Ruling: {ticket} implementer runs again (retry): {subject}.",
        "accept-tip": "Ruling: {ticket} tip goes to the gate and the review as it is (accept-tip): {subject}.",
        "redesign": _REDESIGN},
    "fixer-stopped": {
        "retry": "Ruling: {ticket} fix round runs again (retry): {subject}.",
        "redesign": _REDESIGN},
    "merged-locked-file": {
        "restore": "Ruling: {ticket} lands with {subject} put back to the version of the PR branch (restore).",
        "adopt": "Ruling: {ticket} locks {subject} and lands its change to it (adopt).",
        "redesign": _REDESIGN},
    "final-finding": {
        "fix": "Ruling: final-review finding stays open and nothing is pushed (fix): {subject}.",
        "accept": "Ruling: final-review finding closed as it is (accept): {subject}."},
})


def _ruled(q: dict) -> str:
    """Schema 4: the ruling sentence of an answered question of an older driver; for a removed kind, the older
    driver's sentence, or a generic one for a kind or an answer it did not have."""
    if q["kind"] in driver_decisions.KINDS:
        return driver_decisions.ruling(q)
    if template := _RULINGS.get(q["kind"], {}).get(q["answer"]):
        return template.format(**q)
    return f"Ruling: {q['ticket'] or 'end of run'} {q['kind']} ({q['answer']}): {q['subject']}."


def _upgraded(state: dict) -> None:
    """Schema 3: every gate of an older driver was the full gate, so its greens and its count are of the full tier.
    Schema 4: an answered question keeps its ruling sentence."""
    run = state["run"]
    for q in run.get("questions", []):
        if q["answer"] is not None:
            q.setdefault("ruling", _ruled(q))
    for record in [run.get("final", {}), *state["tickets"].values()]:
        if isinstance(record.get("green_gates"), list):
            record["green_gates"] = {**driver_state.tiers(), "full": record["green_gates"]}
    if isinstance(run.get("gates"), int):
        run["gates"] = {"fast": 0, "full": run["gates"]}
    run["schema"] = driver_state.SCHEMA


def resume(state: driver_state.State) -> list[str]:
    """Give each ref of a run that an older driver started its new name, then mark the run: the errors, or []."""
    if not state.path.exists() or (schema := state.read()["run"].get("schema", 1)) >= driver_state.SCHEMA:
        return []
    run = state.read()["run"]
    if errors := _removed(run):
        return errors
    if schema < 2:  # the names of driver_names
        names = Names(run["pr_branch"])
        moves = [_branch(entry, number, names, run["start"]) for number, entry in state.read()["tickets"].items()]
        if errors := [error for error in [*moves, _recovery(run, names)] if error]:
            return errors
    state.update(_upgraded)
    return []
