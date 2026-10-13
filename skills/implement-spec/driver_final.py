"""The end of a run (requirements 18 and 19). When every ticket is merged and every slot is idle: tidy the PR
branch into one commit per ticket, rebase it onto the fetched base tip, gate it, run the final review, let one fix
batch land as `fix(NN)` commits with a scoped review, then stop before the push or push and open the PR.
Stdlib only.

`run` is the driver's Run. The agents and the gates of this module go through it under the number `final`, so their
reports and gate records are in `.agent/implement/final/`. `run.final` of state.json is the resume contract;
`driver_state._final` makes it with every key:
    {skipped, recovery, tidied, onto, base, commits: {NN: commit}, reviewed, round, open, batch, green_gates,
     pushed, pr}
`skipped` is why the end-of-run work did not start, `recovery` is the PR tip before the rewrite (the ref RECOVERY holds it), `tidied` the tip the rewrite made, `onto`
the base tip under it (with no `origin`: `base` is "" and `onto` is the start of the run), `reviewed` the last tip a review covered, `round` the reviews done, `open` the findings the
fix batch is due for, `batch` the tip of the fix batch that the driver validated and named. The branch is rewritten
once, before the first review, and never after it. A history that origin already has is never rewritten: then
there is no `recovery`, and `tidied` is the tip as the tickets left it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import driver_agents
import driver_decisions
import driver_merge
import driver_review
import driver_tidy
from driver_slots import git, head
from driver_state import State
from driver_tickets import Ticket

FINAL = "final"
RECOVERY = "refs/implement/recovery"
KIND = "final-finding"
ROUNDS = 2  # the full review and the scoped one after the fix batch
# The final review runs through the review-diff skill, which is installed next to this one.
REVIEW_DIFF = driver_agents.SKILL.parent / "review-diff" / "SKILL.md"


def _remote() -> bool:
    return git(".", "remote", "get-url", "origin").returncode == 0


def missing(push: bool) -> list[str]:
    """[] or the errors that stop the run before any agent: every run gets the final review, and a run that is to
    `push` needs the `origin` it pushes to (requirement 19)."""
    errors = [] if REVIEW_DIFF.exists() else [
        f"{REVIEW_DIFF} is missing: the final review runs through it; install the review-diff skill next to "
        "implement-spec"]
    if push and not _remote():
        errors.append("the repository has no origin remote: --unattended and --push push the PR branch to origin; "
                      "add the remote, or run without them to stop before the push")
    return errors


def finish(state: State, pr: str) -> None:
    """Before the resume checks: a run that was stopped between the record of the tidied tip and the move of the
    PR branch left the branch at its recovery point. Move it now, so that git confirms the recorded ticket tips."""
    final = state.read()["run"]["final"]
    if final["tidied"] and git(".", "rev-parse", f"refs/heads/{pr}").stdout.strip() == final["recovery"]:
        git(".", "checkout", "-q", "--detach")
        driver_merge.land(pr, final["tidied"], final["recovery"])
        git(".", "checkout", "-q", pr)


def _values(run) -> dict[str, str]:
    """The pointers every prompt of the end of run gets. Its agents work in slot 1, the PR worktree."""
    return {"spec": run.args.spec, "tickets": run.args.tickets, "pr_branch": run.pr, "branch": run.pr}


def _title(finding: dict) -> str:
    return f"{finding['title']} ({finding['file']}:{finding['line']})"


# ---------------------------------------------------------------- the rewrite: tidy, rebase, move the branch

def _base() -> tuple[str, str, str]:
    """(the default branch of origin, its fetched tip, "") or ("", "", the error). Every ref of origin is
    fetched: `_published` needs each one."""
    shown = git(".", "ls-remote", "--symref", "origin", "HEAD")
    name = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", shown.stdout, re.M)
    if not name:
        return "", "", f"origin names no default branch: {shown.stderr.strip() or 'git ls-remote shows no HEAD'}"
    fetched = git(".", "fetch", "-q", "origin")
    onto = git(".", "rev-parse", "-q", "--verify", f"refs/remotes/origin/{name[1]}^{{commit}}").stdout.strip()
    if fetched.returncode or not onto:
        return "", "", f"git fetch origin did not give refs/remotes/origin/{name[1]}: {fetched.stderr.strip()}"
    return name[1], onto, ""


def _under(run) -> tuple[str, str, str]:
    """What the tidied history sits on: (the default branch of origin, its fetched tip, "") or ("", "", the error).
    With no `origin` there is no base and nothing to rebase onto: ("", the commit the run started from, "")."""
    if not _remote():
        run.ledger("End of run: the repository has no origin remote, so the rebase and the push are skipped; the "
                   "tidy, the gate and the final review run.")
        return "", run.state.read()["run"]["start"], ""
    base, onto, error = _base()
    if base == run.pr:
        error = f"{run.pr} is the default branch of origin: the end of run needs a PR branch"
    return base, onto, error


def _locked(run) -> list[str]:
    """The locked files of every ticket."""
    return sorted({path for entry in run.state.read()["tickets"].values() for path in entry["locked"]})


def _published(pr: str, old: str, onto: str) -> bool:
    """origin has the PR branch, or it has, under any ref, a commit that `old` holds and the base tip `onto` does
    not. README.md, "End of run": once pushed, a history is preserved."""
    own = [git(".", "rev-list", "--count", old, "--not", *others).stdout.strip() for others in ([onto], ["--remotes=origin"])]
    return own[0] != own[1] or bool(git(".", "ls-remote", "origin", f"refs/heads/{pr}").stdout.strip())


def _kept(run, old: str, base: str, onto: str) -> str:
    """Origin has commits of the PR branch: record its history as it is. No commit is rewritten; the gate, the
    final review and a fast-forward push follow. Returns ""."""
    commits = dict(run.state.read()["run"]["landed"])
    run.state.update(lambda state: state["run"]["final"].update(tidied=old, onto=onto, base=base, commits=commits))
    run.ledger(f"End of run: origin has commits of {run.pr}, so its history is kept as it is: no tidy and no rebase, "
               f"because a published history is not rewritten. The gate and the final review run on {old[:7]}.")
    return ""


def _gated(run, commit: str, name: str) -> str:
    """Gate `commit`, which slot 1 has checked out; "" or the error."""
    red = run.gated(FINAL, ".", commit)
    return f"{name}: {red}" if red else ""


def _open() -> bool:
    """A rebase is open in slot 1."""
    return any(Path(git(".", "rev-parse", "--git-path", name).stdout.strip()).exists()
               for name in ("rebase-merge", "rebase-apply"))


def _resolved(run, env: dict, base: str, onto: str) -> str:
    """The conflict agent, once (requirement 19), on the rebase that is open in slot 1, then the gate on the
    rebased tip. Returns "" or the error; then no rebase is open. A second conflict is an error."""
    files = git(".", "diff", "--name-only", "--diff-filter=U").stdout.split()
    values = {**_values(run), "branch": "HEAD", "start_sha": onto, "base": base, "files": " ".join(files)}
    error, _ = run.agent("conflict", FINAL, ".", values, template="rebase-conflict", detached=True)
    if not error and _open():  # the agent resolved and did not go on
        subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"], capture_output=True, env=env)
    if _open():
        again = git(".", "diff", "--name-only", "--diff-filter=U").stdout.split()
        git(".", "rebase", "--abort")
        return f"the conflict agent: {error}" if error else (
            f"the rebase onto {base} conflicts a second time, in {', '.join(again)}")
    # The resolution may not touch a locked test (requirement 7): each one is as the recovery point has it.
    locked = driver_review.locked_changes(".", git(".", "rev-parse", RECOVERY).stdout.strip(), _locked(run))
    if error := error or "; ".join(finding["title"] for finding in locked):
        return f"the conflict agent: {error}"
    run.ledger(f"Rebase: the conflict agent resolved the conflict with {base} in {', '.join(files)} at "
               f"{head('.')[:7]}.")
    return _gated(run, head("."), "the gate after the rebase")


def _replayed(run, tip: str, base: str, onto: str) -> str:
    """`git rebase` of `tip` onto `onto` in slot 1, on a detached HEAD: no ref moves. Returns "" with HEAD on the
    rebased tip, or the error with no rebase open."""
    env = {**os.environ, **driver_merge.identity(tip)}
    git(".", "checkout", "-q", "--detach", tip)
    done = subprocess.run(["git", "-c", "core.editor=true", "rebase", onto], capture_output=True, text=True, env=env)
    if _open():
        return _resolved(run, env, base, onto)
    return f"git rebase: {done.stderr.strip()}" if done.returncode else ""


def _rebased(run, commits: dict[str, str], base: str, onto: str) -> tuple[dict[str, str], str]:
    """Rebase the tidied history onto `onto`, the fetched tip of `base`. Returns ({ticket: its commit on the new
    base}, "") or ({}, the error). The rebased commits get the messages of the tidied ones again: a conflict
    resolution may have changed one."""
    tip = list(commits.values())[-1]
    before = git(".", "rev-list", "--reverse", f"{onto}..{tip}").stdout.split()
    if not before:  # no ticket left a change, and the base tip holds the commit under them
        return dict.fromkeys(commits, onto), ""
    if error := _replayed(run, tip, base, onto):
        return {}, error
    after = git(".", "rev-list", "--reverse", f"{onto}..HEAD").stdout.split()
    if len(after) != len(before):
        return {}, f"the rebase onto {base} left {len(after)} commits for {len(before)}"
    made = driver_tidy.rewrite(onto, [(new, driver_tidy.text(old)) for new, old in zip(after, before)])
    if not driver_tidy.same_tree(made[-1], head(".")):
        return {}, f"the renamed commits do not have the tree of the rebased tip {head('.')[:7]}"
    moved = dict(zip(before, made))
    return {number: moved.get(commit, commit) for number, commit in commits.items()}, ""


def _history(run, tickets: list[Ticket], old: str, base: str, onto: str) -> tuple[dict[str, str], str]:
    """The new history of the PR branch, with no ref moved: ({ticket: its commit}, "") or ({}, the error).
    The tidy comes first, so the tree-equality check is exact, and the rebase moves one commit per ticket."""
    record = run.state.read()["run"]
    if not driver_merge.ancestor(record["start"], old):
        return {}, f"{run.pr} no longer holds {record['start'][:7]}, the commit the run started from"
    commits = driver_tidy.tidy(record["start"], record["landed"], {ticket.number: ticket for ticket in tickets})
    tip = list(commits.values())[-1]
    if not driver_tidy.same_tree(tip, old):
        return {}, f"the tidied history does not have the tree of {old[:7]}"
    return (commits, "") if driver_merge.ancestor(onto, tip) else _rebased(run, commits, base, onto)


def _landed(run, commits: dict[str, str], old: str, base: str, onto: str) -> str:
    """Record the new history, then move the PR branch to it; "" or the error. The record comes first and names
    each ticket's new commit as its tip: a resume confirms a merged ticket by that tip (requirement 15), and
    `finish` moves the branch when the run was stopped between the two writes."""
    new = list(commits.values())[-1]
    tips = {number: entry["tip"] for number, entry in run.state.read()["tickets"].items()}

    def record(state: dict, tips: dict, **fields) -> None:
        state["run"]["final"].update(fields)
        for number, tip in tips.items():
            state["tickets"][number]["tip"] = tip

    run.state.update(lambda state: record(state, commits, recovery=old, tidied=new, onto=onto, base=base,
                                          commits=commits))
    if error := driver_merge.land(run.pr, new, old):
        run.state.update(lambda state: record(state, tips, tidied=None))
        return f"cannot move {run.pr} to the tidied tip {new[:7]}: {error}"
    run.ledger(f"Tidy: {run.pr} moved from {old[:7]} to {new[:7]} on {base or 'the start of the run'} at {onto[:7]}, "
               f"with one commit per ticket in merge order: {', '.join(f'{number} is {commit[:7]}' for number, commit in commits.items())}.")
    return ""


def _prepared(run, tickets: list[Ticket]) -> str:
    """The one rewrite of the PR branch: recovery point, tidy, rebase, then the move. Returns "" or the error;
    after an error the branch is at its recovery point. Slot 1 is off the branch while the history is made."""
    final = run.state.read()["run"]["final"]
    if final["tidied"]:
        return "" if driver_merge.ancestor(final["tidied"], run.tip()) else (
            f"{run.pr} does not hold the tidied tip {final['tidied'][:7]}; {RECOVERY} holds the history before the tidy")
    base, onto, error = _under(run)
    if error:
        return error
    old = run.tip()
    if base and _published(run.pr, old, onto):
        return _kept(run, old, base, onto)
    tree = git(".", "rev-parse", f"{old}^{{tree}}").stdout.strip()
    git(".", "update-ref", "-m", "implement-spec: recovery point", RECOVERY, old)
    run.ledger(f"Recovery point: {run.pr} was at {old} with the tree {tree} before the rebase and the tidy; "
               f"{RECOVERY} holds that commit.")
    git(".", "checkout", "-q", "--detach")
    try:
        commits, error = _history(run, tickets, old, base, onto)
        error = error or _landed(run, commits, old, base, onto)
        return error and f"the rewrite: {error}; {run.pr} is unrewritten at its recovery point {old[:7]}"
    finally:
        git(".", "checkout", "-q", run.pr)


# ---------------------------------------------------------------- the final review and the fix batch

def _minors(run) -> str:
    """One line per minor finding that a ticket review left for the final review, with its findings file."""
    lines = []
    for path in sorted(run.out.glob("[0-9]*/findings-*.json")):
        lines += [f"- {path}: {_title(finding)}" for finding in json.loads(path.read_text())
                  if finding["severity"] == "minor"]
    return "\n".join(dict.fromkeys(lines))


def _due(run, final: dict, tip: str) -> str:
    """"" when `tip` gets the next review, else why not: the two rounds are done, or the tip is not one the driver
    made. Only the tidied tip and the tip of the validated fix batch get a review: a commit that has the name of a
    fix commit and no validated owner does not."""
    if final["round"] >= ROUNDS:
        return (f"the two review rounds are done and the tip {tip[:7]} of {run.pr} is not the reviewed tip "
                f"{final['reviewed'][:7]}")
    made = final["batch"] or final["tidied"]
    return "" if tip == made else (f"the tip {tip[:7]} of {run.pr} is not a tip the driver made: only {made[:7]} gets "
                                   f"a review, so the run goes on when {run.pr} is at {made[:7]} again")


def _recorded(state: dict, tip: str, todo: list[dict], evidence: list[str]) -> None:
    """One write for the review of `tip`: the reviewed tip, the round, and what its open findings start. After
    the first round that is the fix batch; after the second, one question each (requirement 17), and no third."""
    record, final = state["run"], state["run"]["final"]
    final.update(reviewed=tip, round=final["round"] + 1, open=todo if final["round"] == 0 else [])
    if not final["open"]:
        driver_decisions.stop(record, KIND, "", [_title(finding) for finding in todo], evidence)


def _review(run, final: dict, tip: str) -> str:
    """One round of the final review on `tip`: the full review, or after it the scoped one. "" or the error."""
    done, folder = final["round"], run.out / FINAL
    values = {**_values(run), "start_sha": tip, "range": f"{final['reviewed'] or final['onto']}..{tip}",
              "gate": str(folder / f"gate-{tip[:7]}.log"), "review_diff": str(REVIEW_DIFF),
              "ledger": str(run.out / "ledger.md"), "minors": _minors(run), "reviewed": str(final["reviewed"]),
              "rulings": "\n".join(driver_decisions.rulings(run.state.read()["run"]["questions"])),
              "findings": str(folder / f"findings-{done - 1}.json")}
    # Requirement 7, before each review: a locked file that differs from the tidied tip is a critical finding.
    # The tidied tip is the reference: the driver made it, and no agent ran between the tickets' own checks and it
    # but the conflict agent of the rebase, which has the same check.
    found = driver_review.locked_changes(".", final["tidied"], _locked(run))
    # The role is read-only: a review that changed slot 1 is restored and runs one more time, as a ticket review.
    error, report = driver_review.undisturbed(
        lambda: run.agent("final-reviewer", FINAL, ".", values, notes=["final-scoped"] * bool(done), fix_round=done),
        ".", run.pr, tip, run.ledger, "End of run", "slot 1", "the final review")
    if error:
        return f"the final review: {error}"
    _settled(run, done, tip, values["range"], found + report["findings"])
    return ""


def _settled(run, done: int, tip: str, covered: str, found: list[dict]) -> None:
    """An accepted review of the range `covered`, after `done` earlier rounds: its findings go to their file, the
    ledger and the record."""
    folder = run.out / FINAL
    file = folder / f"findings-{done}.json"
    file.write_text(json.dumps(found, indent=1) + "\n")
    for finding in found:
        run.ledger(f"Final review round {done + 1}: {finding['severity']} finding of kind {finding['kind']} at "
                   f"{finding['file']}:{finding['line']}: {finding['title']}.")
    todo = [finding for finding in found if finding["severity"] in driver_review.OPEN]
    run.state.update(lambda state: _recorded(state, tip, todo, [str(file), str(folder / f"final-reviewer-{done}.json")]))
    run.ledger(f"Final review round {done + 1}: the range {covered} is reviewed, the reviewed tip is {tip[:7]}, and "
               f"{len(todo)} critical or important finding{'s are' if len(todo) != 1 else ' is'} open.")
    if todo and done:
        run.ledger(f"End of run: {driver_decisions.decides(KIND, len(todo))} in {run.out / 'decisions.json'}: a third "
                   "review round is never started.")


def _named(commit: str, report: dict) -> str:
    """The message of a fix commit: `fix(NN): ` and the fixer's own message, NN from the report."""
    owner = next(one["ticket"] for one in report["commits"] if commit.startswith(one["sha"]))
    words = driver_tidy.text(commit)
    return words if words.startswith(f"fix({owner}): ") else f"fix({owner}): {words}"


def _unmade(run, tip: str) -> str:
    """After a rejected fix batch: move only the branch back to the reviewed tip `tip`. Commits that the driver
    did not validate may never get a review or a push; they are the fixer's own, made after the review, so this
    rewrites no reviewed history. No file changes: the driver drops no work of a role that commits. The ledger
    names the tip of the commits; what they changed, and what the fixer did not commit, stays in slot 1, where
    the next run finds it as uncommitted changes and stops (exit 3) until the operator commits or discards it.
    Returns "" or what to add to the error when the branch could not move."""
    left = run.tip()
    if error := left != tip and driver_merge.land(run.pr, tip, left):
        return f"; {run.pr} stays at {left[:7]}: {error}"
    commits = f"the commits of the batch end at {left} and stay in the reflog" if left != tip else "the fixer made no commit"
    run.ledger(f"End of run: the fix batch was rejected, so the driver moved {run.pr} back to its reviewed tip "
               f"{tip[:7]}; {commits}. The files of the fixer stay in slot 1 as they are.")
    return ""


def _fixed(run, tickets: list[Ticket], final: dict, tip: str) -> str:
    """The one fix batch, on the PR branch in slot 1, then the names of its commits; "" or the error. The commits
    are named before the gate and the scoped review see them: nothing is rewritten after a review."""
    folder = run.out / FINAL
    values = {**_values(run), "start_sha": tip, "findings": str(folder / f"findings-{final['round'] - 1}.json"),
              "locked": " ".join(_locked(run)),
              "findings_list": "\n".join(f"- {finding['severity']}: {_title(finding)}" for finding in final["open"])}
    error, report = run.agent("fixer", FINAL, ".", values, template="final-fixer", fix_round=final["round"],
                              check={"owners": [ticket.number for ticket in tickets]})
    if error:
        return f"the fix batch: {error}{_unmade(run, tip)}"
    made = git(".", "rev-list", "--reverse", f"{tip}..HEAD").stdout.split()
    named = driver_tidy.rewrite(tip, [(commit, _named(commit, report)) for commit in made])
    if not driver_tidy.same_tree(named[-1], made[-1]):
        return f"the fix batch: the named commits do not have the tree of {made[-1][:7]}"
    # Slot 1 has the branch checked out: the new tip has the same tree, so the index and the files stay right.
    if error := driver_merge.land(run.pr, named[-1], made[-1]):
        return f"the fix batch: {error}"
    run.state.update(lambda state: state["run"]["final"].update(open=[], batch=named[-1]))
    run.ledger(f"Fix batch of the final review: {len(named)} commit{'s' * (len(named) > 1)} on {tip[:7]}: "
               + ", ".join(f"{commit[:7]} {driver_tidy.text(commit).splitlines()[0]}" for commit in named) + ".")
    return ""


def _reviewed(run, tickets: list[Ticket]) -> str:
    """Gate and review the tip, and run the fix batch, until the tip is the reviewed tip. Returns "" or the error.
    Each tip gets its gate before its review, and a green record is not repeated."""
    while True:
        final, tip = run.state.read()["run"]["final"], run.tip()
        if final["reviewed"] != tip:
            error = _due(run, final, tip) or _gated(run, tip, "the gate") or _review(run, final, tip)
        elif final["open"]:
            error = _fixed(run, tickets, final, tip)
        else:
            return ""
        if error:
            return error


# ---------------------------------------------------------------- the push

def _name(run) -> str:
    """The title of the PR: the first heading of the spec, else the PR branch."""
    spec = Path(run.args.spec)
    lines = spec.read_text(errors="replace").splitlines() if spec.is_file() else []
    return next((line[2:].strip() for line in lines if line.startswith("# ")), run.pr)


def _body(run, final: dict, tickets: list[Ticket]) -> str:
    """The PR body: the ticket-to-commit map, the fix commits, the rulings and the review coverage."""
    titles, tip = {ticket.number: ticket.title for ticket in tickets}, final["reviewed"]
    fixes = git(".", "log", "--reverse", "--format=- %h %s", f"{final['tidied']}..{tip}").stdout.strip()
    rulings = driver_decisions.rulings(run.state.read()["run"]["questions"])
    return driver_agents.render("pr-body", {
        "map": "\n".join(f"- {number}: {commit[:7]} {titles[number]}" for number, commit in final["commits"].items()),
        "fixes": fixes or "None.", "rulings": "\n".join(f"- {ruling}" for ruling in rulings) or "None.",
        "tip": tip, "onto": final["onto"], "tidied": final["tidied"],
        "scoped": f"{final['tidied']}..{tip}" if fixes else "not needed"})


def _opened(run, final: dict, tickets: list[Ticket]) -> tuple[str, str]:
    """(the URL of the PR, "") or ("", the error). The PR is ready for review, not a draft."""
    body = run.out / FINAL / "pr-body.md"
    body.write_text(_body(run, final, tickets))
    made = subprocess.run(["gh", "pr", "create", "--base", final["base"], "--head", run.pr, "--title", _name(run),
                           "--body-file", str(body)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if made.returncode:
        return "", f"gh pr create failed: {(made.stderr or made.stdout).strip()}"
    url = (made.stdout.split() or [""])[-1]
    run.state.update(lambda state: state["run"]["final"].update(pr=url))
    run.ledger(f"The PR of {run.pr} into {final['base']} is open: {url}.")
    return url, ""


def _pushed(run, final: dict, tickets: list[Ticket]) -> tuple[str, list[str]]:
    """Push the reviewed tip as the PR branch to origin, once and with no force, then open the PR, once.
    Returns ("", the lines for the operator) or (the error, [])."""
    tip = final["reviewed"]
    # Requirement 18, checked where the push is: the tip is the reviewed tip and its gate record is green.
    if tip != run.tip() or tip not in final["green_gates"]:
        return f"the reviewed tip {tip[:7]} is not the tip of {run.pr} with a green gate record: nothing is pushed", []
    if not final["base"]:
        return (f"the end of run tidied {run.pr} when the repository had no origin remote, so it is on no base "
                "branch of origin: nothing is pushed"), []
    if not shutil.which("gh"):
        return "gh is not on PATH: nothing is pushed", []
    if final["pushed"] != tip:
        # The commit, not the branch name: what leaves the machine is the tip the review covered.
        sent = git(".", "push", "-q", "origin", f"{tip}:refs/heads/{run.pr}")
        if sent.returncode:
            return f"git push origin {run.pr} failed: {sent.stderr.strip()}", []
        run.state.update(lambda state: state["run"]["final"].update(pushed=tip))
        run.ledger(f"Pushed {run.pr} at {tip[:7]} to origin.")
    url, error = (final["pr"], "") if final["pr"] else _opened(run, final, tickets)
    return error, [] if error else [f"Pushed {run.pr} at {tip} to origin.", f"PR: {url}"]


def again(argv: list[str]) -> str:
    """The command of this run with `--push` and without its answers: an answer is given once."""
    kept, skip = [], False
    for word in argv:
        if not (skip or word.startswith("--answer")):
            kept.append(word)
        skip = word == "--answer"
    return shlex.join(["sh", str(driver_agents.SKILL / "run.sh"), *kept, "--push"])


def _closed(run, tickets: list[Ticket]) -> tuple[str, list[str]]:
    """The tip is the reviewed tip and its gate is green: ("", the lines for the operator) or (the error, []).
    Nothing is pushed while a question of the final review is open or its answer keeps a finding open, nor in
    local mode without `--push` (requirement 18)."""
    record = run.state.read()["run"]
    answers = [question["answer"] for question in record["questions"] if question["kind"] == KIND]
    if None in answers:
        return "", []  # parked: the driver prints the questions
    if kept := answers.count("fix"):
        return f"the operator keeps {kept} finding{'s' * (kept > 1)} of the final review open (fix): nothing is pushed", []
    final = record["final"]
    if run.args.push or run.args.unattended:
        return _pushed(run, final, tickets)
    how = f"To push {run.pr} and open the PR, run: {again(run.args.argv)}" if final["base"] else (
        "The repository has no origin remote.")
    return "", [f"Branch tip: {run.tip()}", f"Reviewed tip: {final['reviewed']}", f"Nothing is pushed. {how}"]


def _skip(state: dict) -> str:
    """"" or why the end-of-run work does not start. A ticket that is not merged can still merge after an answer
    of the operator, and its branch starts from the history as it is: a tidy would rewrite its branch point."""
    waiting = [number for number, entry in state["tickets"].items() if entry["phase"] != "merged"]
    return f"these tickets are not merged: {', '.join(waiting)}" if waiting else ""


def run(run, tickets: list[Ticket]) -> tuple[bool, list[str]]:
    """The end of the run, from where state.json says it is. Call it only when no slot runs a ticket.
    Returns (the end of run is BLOCKED, the lines the operator reads last)."""
    state = run.state.read()
    reason, before = _skip(state), state["run"]["final"]["skipped"]
    run.state.update(lambda new: new["run"]["final"].update(skipped=reason))
    if reason:
        if reason != before:
            run.ledger(f"End-of-run work skipped: {reason}.")
        return False, []
    error, lines = _prepared(run, tickets) or _reviewed(run, tickets), []
    if not error:
        error, lines = _closed(run, tickets)
    if error:
        run.ledger(f"End of run: BLOCKED at {error}.")
        lines = [f"End of run: BLOCKED at {error}"]
    return bool(error), lines
