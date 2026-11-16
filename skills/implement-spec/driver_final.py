"""The end of a run, when every ticket is merged and every slot is idle: tidy the PR branch into one commit per
ticket, rebase it onto the fetched base tip, gate it, run the final review, let one fix batch land as `fix(NN)`
commits with a scoped review, then stop before the push, or push and open the PR. A finding the scoped review leaves
open asks `final-finding-open-<n>`: a `retry` runs another batch and scoped review, an `accept` or a `follow-up` ships
the PR with it, as draft. A critical or must-fix one asks `final-finding-must-fix-<n>` instead, which only the
operator ships.

`run.final` of state.json is the resume contract (`driver_state._final` makes it with every key):
    skipped    why the end-of-run work did not start
    recovery   the PR tip before the rewrite; Run.names names its ref. None when origin had the history already
    tidied     the tip the rewrite made, or the tip as the tickets left it when the history is kept
    base, onto the --base branch (or the default branch of origin) and its commit under the history: its tip, or the merge-base
               with it when the history is kept; with no origin: "" and the start of the run
    commits    {ticket: its commit}
    reviewed   the last tip a review covered
    round      the reviews done
    open       the findings the fix batch is due for
    lenses     {lens role: {tip, report}}: the accepted report of each lens of the full review and the tip it covers
    batch      the tip of the fix batch that the driver validated and named, or of a ticket that merged after the
               rewrite, once an overturn let it run again (landed_after_tidy)
    locked     the commit whose locked files the final fixers keep: None for the tidied tip, else the tip that the
               last ticket that merged after the rewrite made
    phase      `blocked` at a stop with questions, until a `retry` answer makes it `pending` again
    green_gates, pushed, pr
    body       the tip the pr-writer's part of the PR body was written for (driver_pr)
    draft      the PR opened as draft
    ship       {tip, stops: [{subject, role}]} once a `ship-draft` answer settled (driver._settle): the gated tip
               that the push ships as draft, None when no full gate ran on the branch tip, and the stops it names
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import driver_agents
import driver_decisions
import driver_gate
import driver_final_gate
import driver_locked
import driver_merge
import driver_pr
import driver_review
import driver_tidy
from driver_run import FINAL, FINAL_RESUME, OUT, Blocked, Call, Halted, Run, during, final_values, ledger
from driver_slots import git, head
from driver_state import State
from driver_tickets import Ticket

SEVERITIES = ("critical", "important", "minor")


def _method(lens: driver_agents.Lens) -> Path | None:
    """The file `lens` follows, in its skill where that is installed. None when the skill or the file is missing."""
    skill = driver_agents.installed(lens.skill)
    method = skill and skill.parent / lens.method
    return method if method and method.is_file() else None


def _has_origin() -> bool:
    return git(".", "remote", "get-url", "origin").returncode == 0


def _method_errors() -> list[str]:
    """One error per lens of the final review whose method is missing."""
    return [f"the {lens.skill} skill is missing its {lens.method}: the {lens.source} lens of the final review follows "
            f"it; install it next to implement-spec with `npx skills add jesdi/general-skills --skill {lens.skill}`"
            for lens in driver_agents.LENSES.values() if not _method(lens)]


def precondition_errors(push: bool, base: str | None) -> list[str]:
    """[] or the errors that stop the run before any agent."""
    errors = _method_errors() + (_push_errors() if push else [])
    if base and not _has_origin():
        errors.append(f"--base {base} names a branch of origin, and the repository has no origin remote; "
                      "add the remote, or run without --base")
    return errors


def _push_errors() -> list[str]:
    """The errors that stop a run that is to push before any agent."""
    errors = []
    if not driver_pr.pr_skill():
        errors.append("the pr skill is missing: the PR body follows it; install it with "
                      "`npx skills add mattpocock/skills --skill pr`, or run without --push and --unattended")
    if not driver_pr.pr_risk_skill():
        errors.append(f"the pr-risk skill is missing: the PR body carries its merge-risk block; install it with "
                      f"{driver_pr.PR_RISK_INSTALL}, or run without --push and --unattended")
    if not _has_origin():
        errors.append("the repository has no origin remote: --unattended and --push push the PR branch to origin; "
                      "add the remote, or run without them to stop before the push")
    return errors


def finish_interrupted_move(state: State, pr: str) -> None:
    """A run that stopped between the record of the tidied tip and the move of the PR branch: move it now."""
    final = state.read()["run"]["final"]
    if final["tidied"] and git(".", "rev-parse", f"refs/heads/{pr}").stdout.strip() == final["recovery"]:
        git(".", "checkout", "-q", "--detach")
        driver_merge.land(pr, final["tidied"], final["recovery"])
        git(".", "checkout", "-q", pr)


def due(final: dict, subject: str) -> bool:
    """The last review leaves open the finding a finding question names `subject` (driver_decisions.subject)."""
    return subject in {driver_decisions.subject(finding) for finding in final["open"]}


def _last(commits: dict[str, str]) -> str:
    return list(commits.values())[-1]


# ---------------------------------------------------------------- the rewrite: tidy, rebase, move the branch

def _fetch_base(branch: str | None) -> tuple[str, str]:
    """Fetch every ref of origin. Returns (`branch`, or without it the default branch of origin, the tip of that
    branch)."""
    if not branch:
        shown = git(".", "ls-remote", "--symref", "origin", "HEAD")
        name = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", shown.stdout, re.M)
        if not name:
            raise Blocked(f"origin names no default branch: {shown.stderr.strip() or 'git ls-remote shows no HEAD'}")
        branch = name[1]
    fetched = git(".", "fetch", "-q", "origin")
    onto = git(".", "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").stdout.strip()
    if fetched.returncode or not onto:
        raise Blocked(f"git fetch origin did not give refs/remotes/origin/{branch}: {fetched.stderr.strip()}")
    return branch, onto


def _base(run: Run) -> tuple[str, str]:
    """(the --base branch or the default branch of origin, its tip): what the tidied history sits on. With no origin there is no base:
    ("", the commit the run started from)."""
    if not _has_origin():
        ledger("End of run: the repository has no origin remote, so the rebase and the push are skipped; the "
               "tidy, the gate and the final review run.")
        return "", run.state.read()["run"]["start"]
    base, onto = _fetch_base(run.args.base)
    if base == run.pr:
        which = "the base branch" if run.args.base else "the default branch of origin"
        raise Blocked(f"{run.pr} is {which}: the end of run needs a PR branch")
    return base, onto


def _origin_has_commits(pr: str, tip: str, onto: str) -> bool:
    """origin has the PR branch, or a ref of origin holds a commit of `tip` that the base tip `onto` does not."""
    def commits_not_in(*others: str) -> str:
        return git(".", "rev-list", "--count", tip, "--not", *others).stdout.strip()

    return (commits_not_in(onto) != commits_not_in("--remotes=origin")
            or bool(git(".", "ls-remote", "origin", f"refs/heads/{pr}").stdout.strip()))


def _keep_published_history(run: Run, tip: str, base: str, onto: str) -> None:
    """Record the history as it is: a history that origin has is never rewritten. It stays on its old base, so `onto`
    is its merge-base with the base tip: a review range from the base tip would show what the base gained since as
    reversions made by the branch."""
    under = git(".", "merge-base", onto, tip).stdout.strip()
    if not under:
        raise Blocked(f"{run.pr} at {tip[:7]} shares no history with {base} at {onto[:7]}")
    commits = dict(run.state.read()["run"]["landed"])
    run.state.update(lambda state: state["run"]["final"].update(tidied=tip, onto=under, base=base, commits=commits))
    ledger(f"End of run: origin has commits of {run.pr}, so its history is kept as it is: no tidy and no rebase, "
           f"because a published history is not rewritten. The gate and the final review run on {tip[:7]}.")


def _rebase_is_open() -> bool:
    """A rebase is open in slot 1."""
    return any(Path(git(".", "rev-parse", "--git-path", name).stdout.strip()).exists()
               for name in ("rebase-merge", "rebase-apply"))


def _files_in_conflict() -> list[str]:
    return git(".", "diff", "--name-only", "--diff-filter=U").stdout.split()


def _stop() -> str:
    """The commit the open rebase stopped at; "" when none."""
    return git(".", "rev-parse", "-q", "--verify", "REBASE_HEAD").stdout.strip()


def _rebase_check(run: Run, stop: str, context: dict) -> driver_locked.Locked | None:
    """The locked-file check of the rebase conflict agent when it ends: every ticket's locked files as the recovery
    point has them. The driver goes on with a rebase that the agent left open, so the check reads the rebased tip, and
    the report is checked on that tip in place of its `commits`, which may name HEAD before the driver's commit. A
    rebase that git cannot go on with at `stop`, the stop the agent was given, rejects a done report with git's error;
    one that stopped again at a later conflict has no tip to check (None), and the driver aborts it."""
    if _rebase_is_open():
        env = {**os.environ, **driver_merge.identity("HEAD")}
        done = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"], capture_output=True,
                              text=True, env=env)
        if done.returncode and _stop() == stop:
            why = (done.stderr or done.stdout).strip()  # git names an unstaged resolution on stdout
            context["rejected"] = [f"git rebase --continue failed at the stop you resolved: {why}"]
            return None
        if _rebase_is_open():
            return None
        context["commits"] = [head(".")]
    recovery = git(".", "rev-parse", run.names.recovery).stdout.strip()
    return driver_locked.for_end_of_run(_shipped(run), recovery)


def _run_rebase_conflict_agent(run: Run, values: dict) -> str:
    """Run the conflict agent on the open rebase; the driver goes on with the rebase after it, and its report is
    rejected while the rebased tip changes a locked file. Returns "" or why it blocked."""
    stop = _stop()
    try:
        run.agent(Call("conflict", FINAL, ".", values, template="rebase-conflict", detached=True,
                       locked=lambda context: _rebase_check(run, stop, context)))
    except Blocked as blocked:
        return str(blocked)
    except Halted:
        git(".", "rebase", "--abort")
        raise
    return ""


def _resolve_rebase_conflict(run: Run, base: str, onto: str) -> None:
    """Resolve the rebase in slot 1, with one agent rerun; the final integration gate checks its tip.
    No rebase is open afterwards, also when this raises."""
    files = _files_in_conflict()
    values = {**final_values(run), "branch": "HEAD", "start_sha": onto, "base": base, "files": " ".join(files)}
    error = _run_rebase_conflict_agent(run, values)
    if _rebase_is_open():
        again = _files_in_conflict()
        git(".", "rebase", "--abort")
        raise Blocked(f"the conflict agent: {error}" if error else
                      f"the rebase onto {base} conflicts a second time, in {', '.join(again)}")
    if error:
        raise Blocked(f"the conflict agent: {error}")
    ledger(f"Rebase: the conflict agent resolved the conflict with {base} in {', '.join(files)} at {head('.')[:7]}.")


def _rebase(run: Run, tip: str, base: str, onto: str) -> None:
    """`git rebase` of `tip` onto `onto` in slot 1, on a detached HEAD: no ref moves. HEAD is the rebased tip."""
    env = {**os.environ, **driver_merge.identity(tip)}
    git(".", "checkout", "-q", "--detach", tip)
    done = subprocess.run(["git", "-c", "core.editor=true", "rebase", onto], capture_output=True, text=True, env=env)
    if _rebase_is_open():
        _resolve_rebase_conflict(run, base, onto)
    elif done.returncode:
        raise Blocked(f"git rebase: {done.stderr.strip()}")


def _rebased(run: Run, commits: dict[str, str], base: str, onto: str) -> dict[str, str]:
    """Rebase the tidied history onto `onto`, the tip of `base`. Returns {ticket: its commit on the new base}."""
    tip = _last(commits)
    before = git(".", "rev-list", "--reverse", f"{onto}..{tip}").stdout.split()
    if not before:  # no ticket left a change, and the base tip holds the commit under them
        return dict.fromkeys(commits, onto)
    _rebase(run, tip, base, onto)
    after = git(".", "rev-list", "--reverse", f"{onto}..HEAD").stdout.split()
    if len(after) != len(before):
        raise Blocked(f"the rebase onto {base} left {len(after)} commits for {len(before)}")
    # A conflict resolution may have changed a message: each rebased commit gets its tidied message again.
    made = driver_tidy.rewrite(onto, [(new, driver_tidy.text(old)) for new, old in zip(after, before)])
    if not driver_tidy.same_tree(made[-1], head(".")):
        raise Blocked(f"the renamed commits do not have the tree of the rebased tip {head('.')[:7]}")
    moved = dict(zip(before, made))
    return {number: moved.get(commit, commit) for number, commit in commits.items()}


def _new_history(run: Run, tickets: list[Ticket], old: str, base: str, onto: str) -> dict[str, str]:
    """Make the new history of the PR branch, with no ref moved. Returns {ticket: its commit}."""
    record = run.state.read()["run"]
    if not driver_merge.ancestor(record["start"], old):
        raise Blocked(f"{run.pr} no longer holds {record['start'][:7]}, the commit the run started from")
    # The tidy comes before the rebase: its tree check is exact, and the rebase moves one commit per ticket.
    commits = driver_tidy.tidy(record["start"], record["landed"], {ticket.number: ticket for ticket in tickets})
    if not driver_tidy.same_tree(_last(commits), old):
        raise Blocked(f"the tidied history does not have the tree of {old[:7]}")
    if driver_merge.ancestor(onto, _last(commits)):
        return commits
    return _rebased(run, commits, base, onto)


def _move_branch(run: Run, commits: dict[str, str], old: str, base: str, onto: str) -> None:
    """Record the new history, then move the PR branch to it."""
    new = _last(commits)
    old_tips = {number: entry["tip"] for number, entry in run.state.read()["tickets"].items()}

    def record(state: dict, tips: dict, **fields) -> None:
        state["run"]["final"].update(fields)
        for number, tip in tips.items():
            state["tickets"][number]["tip"] = tip

    # The record comes first and names each ticket's new commit as its tip: a resume confirms a merged ticket by
    # that tip, and `finish_interrupted_move` moves the branch when the run stopped between the two writes.
    run.state.update(lambda state: record(state, commits, recovery=old, tidied=new, onto=onto, base=base,
                                          commits=commits))
    if error := driver_merge.land(run.pr, new, old):
        run.state.update(lambda state: record(state, old_tips, tidied=None))
        raise Blocked(f"cannot move {run.pr} to the tidied tip {new[:7]}: {error}")
    each = ", ".join(f"{number} is {commit[:7]}" for number, commit in commits.items())
    ledger(f"Tidy: {run.pr} moved from {old[:7]} to {new[:7]} on {base or 'the start of the run'} at {onto[:7]}, "
           f"with one commit per ticket in merge order: {each}.")


def _rewrite_once(run: Run, tickets: list[Ticket]) -> None:
    """The one rewrite of the PR branch: recovery point, tidy, rebase, then the move. When it raises, the branch
    is at its recovery point."""
    final = run.state.read()["run"]["final"]
    if final["tidied"]:
        if not driver_merge.ancestor(final["tidied"], run.tip()):
            raise Blocked(f"{run.pr} does not hold the tidied tip {final['tidied'][:7]}; {run.names.recovery} holds the "
                          "history before the tidy")
        return
    base, onto = _base(run)
    old = run.tip()
    if base and _origin_has_commits(run.pr, old, onto):
        _keep_published_history(run, old, base, onto)
        return
    tree = git(".", "rev-parse", f"{old}^{{tree}}").stdout.strip()
    git(".", "update-ref", "-m", "implement-spec: recovery point", run.names.recovery, old)
    ledger(f"Recovery point: {run.pr} was at {old} with the tree {tree} before the rebase and the tidy; "
           f"{run.names.recovery} holds that commit.")
    git(".", "checkout", "-q", "--detach")  # slot 1 is off the branch while the history is made
    try:
        commits = _new_history(run, tickets, old, base, onto)
        _move_branch(run, commits, old, base, onto)
    except Blocked as blocked:
        raise Blocked(f"the rewrite: {blocked}; {run.pr} is unrewritten at its recovery point {old[:7]}") from None
    finally:
        git(".", "checkout", "-q", run.pr)


# ---------------------------------------------------------------- the final review and the fix batch

def _carried_findings() -> str:
    """Ticket observations and minor findings for the final review, with their evidence files."""
    lines = []
    for path in sorted(OUT.glob("[0-9]*/findings-*.json")):
        lines += [f"- {path}: {driver_review.title(finding)}" for finding in json.loads(path.read_text())
                  if finding["severity"] == "minor"]
    for path in sorted(OUT.glob("[0-9]*/observations-*.json")):
        lines += [f"- {path}: {driver_review.title(finding)}" for finding in json.loads(path.read_text())]
    return "\n".join(dict.fromkeys(lines))


def _check_review_is_due(run: Run, final: dict, tip: str) -> None:
    """Only a tip the driver made gets a review: the tidied tip, then the tip of the validated fix batch."""
    made = final["batch"] or final["tidied"]
    if tip != made:
        raise Blocked(f"the tip {tip[:7]} of {run.pr} is not a tip the driver made: only {made[:7]} gets a review, "
                      f"so the run goes on when {run.pr} is at {made[:7]} again")


def _settle_review(run: Run, done: int, tip: str, covered: str, found: list[dict]) -> None:
    """An accepted review of the range `covered`, after `done` earlier rounds: its findings go to their file, the
    ledger and state.json."""
    folder = OUT / FINAL
    file = folder / f"findings-{done}.json"
    file.write_text(json.dumps(found, indent=1) + "\n")
    for finding in found:
        ledger(f"Final review round {done + 1}: {finding['severity']} finding of kind {finding['kind']} from "
               f"{finding['source']} at {finding['file']}:{finding['line']}: {finding['title']}.")
    found_open = [finding for finding in found if finding["severity"] in driver_review.OPEN]
    run.state.update(lambda state: state["run"]["final"].update(reviewed=tip, round=done + 1, open=found_open))
    ledger(f"Final review round {done + 1}: the range {covered} is reviewed, the reviewed tip is {tip[:7]}, and "
           f"{len(found_open)} critical or important finding{'s are' if len(found_open) != 1 else ' is'} open.")
    if done:
        _ask_open(run, file, found_open)


def _ask_open(run: Run, file: Path, found_open: list[dict]) -> None:
    """After the scoped review, another batch is a ruling: one question per finding that no answer ships with, of the
    kind of the finding, and the end of run stops."""
    if due := _to_fix(run, found_open):
        titles = [driver_decisions.subject(finding) for finding in due]
        run.ask(FINAL, [*zip(map(driver_decisions.finding_kind, due), titles)], str(file), FINAL_RESUME)
        raise Blocked(f"the scoped review leaves {len(titles)} finding{'s' * (len(titles) > 1)} open: "
                      + "; ".join(titles))


def _to_fix(run: Run, found: list[dict]) -> list[dict]:
    """The findings of `found` that a fix batch is due for: those that no `accept` or `follow-up` ships with
    (driver_decisions.waived)."""
    questions = run.state.read()["run"]["questions"]
    return [finding for finding in found if not driver_decisions.waived(questions, finding)]


def _review(run: Run, final: dict, tip: str) -> None:
    """One round of the final review on `tip`: the full review by the three lenses, or after it the scoped one, on
    the other-provider entry: another provider than the implementer's, and so than the fixer's when it shares
    the implementer's."""
    done, folder = final["round"], OUT / FINAL
    values = {**final_values(run), "start_sha": tip, "range": f"{final['reviewed'] or final['onto']}..{tip}",
              "gate": str(driver_gate.record_path(folder, driver_gate.FULL, tip)),
              "ledger": str(OUT / "ledger.md"), "carried": _carried_findings(), "reviewed": str(final["reviewed"]),
              "must_fix_list": "\n".join(f"- {driver_review.title(finding)}" for finding in final["must_fix"]),
              "rulings": "\n".join(driver_decisions.rulings(run.state.read()["run"]["questions"])),
              "findings": str(folder / f"findings-{done - 1}.json")}
    with during("the final review"):
        # The full review resolves no must-fix finding: only the scoped review verifies a fix.
        reviewed, resolved = _scoped(run, values, tip, done) if done else (_lenses(run, values, tip), set())
    carried = [finding for finding in final["must_fix"] if driver_review.title(finding) not in resolved]
    _settle_review(run, done, tip, values["range"], _round_findings(final, _merged(reviewed), carried))


def _scoped(run: Run, values: dict[str, str], tip: str, done: int) -> tuple[list[dict], set[str]]:
    """The scoped review after the fix batch, one `final-reviewer`. Returns (its findings, each with the source
    `final-reviewer`; the titles of the must-fix findings it resolved)."""
    entries = driver_agents.correctness_entries(run.models)
    report = run.read_only(FINAL, ".", run.pr, tip, lambda: run.agent(Call(
        "final-reviewer", FINAL, ".", values, entry=entries[0], entries=entries, fix_round=done)))
    return ([{**finding, "source": "final-reviewer"} for finding in report["findings"]],
            set(report.get("resolved_must_fix", [])))


def _lenses(run: Run, values: dict[str, str], tip: str) -> list[dict]:
    """The full review: the three lenses at the same time in slot 1, inside one read-only check. Returns their
    findings, each with the source of its lens. A lens with an accepted report for `tip` does not run again, unless
    a lens changed the slot: then all three run again. The accepted reports are kept only once the slot is as the
    review found it."""
    values = {role: _lens_values(lens, values) for role, lens in driver_agents.LENSES.items()}
    lenses = run.state.read()["run"]["final"]["lenses"]
    kept = {role: one["report"] for role, one in lenses.items() if one["tip"] == tip}
    # The first run keeps the reports; the second, after a lens changed the slot, keeps none.
    passes = iter((kept, {}))
    reports, errors = run.read_only(FINAL, ".", run.pr, tip, lambda: _run_lenses(run, values, next(passes)))
    run.state.update(lambda state: state["run"]["final"]["lenses"].update(
        {role: {"tip": tip, "report": report} for role, report in reports.items()}))
    if errors:
        raise Blocked("; ".join(errors))
    return [{**finding, "source": lens.source} for role, lens in driver_agents.LENSES.items()
            for finding in reports[role]["findings"]]


def _lens_values(lens: driver_agents.Lens, values: dict[str, str]) -> dict[str, str]:
    """The prompt values of `lens`: those of the review, its name and its lines of the shared lens prompt. Its method
    file is read only when its lines inline it."""
    path = _method(lens)
    # A lens that may run tests runs them on a copy: the other lenses work in the same worktree.
    slots = {**values, "path": str(path), "copy": driver_agents.render("lens-copy", values),
             **({"text": path.read_text()} if lens.inline else {})}
    # The shared prompt puts the lines on their own lines.
    return {**values, "lens": lens.source, "method": driver_agents.render(f"lens-{lens.source}", slots).rstrip("\n")}


def _run_lenses(run: Run, values: dict[str, dict[str, str]],
                kept: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Run each lens with no report in `kept` at the same time, each with its `values`. Returns (the report of each lens that has one, why
    each lens that stopped or whose report was rejected twice stops the round)."""
    due = [role for role in driver_agents.LENSES if role not in kept]
    running, reports, errors, halted = list(due), dict(kept), {}, []
    run.asks.pop(FINAL, None)  # a pass that a lens changed the slot in asks nothing

    def call(role: str) -> None:
        entries = driver_agents.lens_entries(run.models, role)
        try:
            reports[role] = run.agent(Call(role, FINAL, ".", values[role], entry=entries[0], entries=entries,
                                           template="final-lens", alone=lambda: len(running) == 1))
        except Blocked as blocked:  # it stopped or was rejected two times
            errors[role] = f"the {role}: {blocked}"
        except Halted as failed:
            halted.append(failed)
        finally:
            running.remove(role)

    with ThreadPoolExecutor(len(due) or 1) as pool:
        list(pool.map(call, due))
    if halted:
        raise halted[0]
    return reports, [errors[role] for role in driver_agents.LENSES if role in errors]


def _merged(reviewed: list[dict]) -> list[dict]:
    """The `reviewed` findings with each (file, line, title) once: the more severe finding stays, a tie keeps the
    earlier one."""
    kept: dict[tuple, dict] = {}
    for finding in reviewed:
        key = (finding["file"], finding["line"], finding["title"])
        if key not in kept or SEVERITIES.index(finding["severity"]) < SEVERITIES.index(kept[key]["severity"]):
            kept[key] = finding
    return list(kept.values())


def _round_findings(final: dict, found: list[dict], carried: list[dict]) -> list[dict]:
    """The findings of the round: each one `found`, a must-fix one with its ticket, then each `carried` one that
    was not found again."""
    must_by_key = {(finding["file"], finding["title"]): finding for finding in final["must_fix"]}
    reported = [{**finding, "must_fix": True, "ticket": must_by_key[key]["ticket"]} if
                (key := (finding["file"], finding["title"])) in must_by_key else finding
                for finding in found]
    identity = {(finding["file"], finding["title"]) for finding in reported}
    return reported + [finding for finding in carried if (finding["file"], finding["title"]) not in identity]


def _fix_commit_message(commit: str, report: dict) -> str:
    """`fix(NN): ` and the fixer's own message; NN is the ticket the report gives for the commit."""
    owner = next(one["ticket"] for one in report["commits"] if commit.startswith(one["sha"]))
    words = driver_tidy.text(commit)
    return words if words.startswith(f"fix({owner}): ") else f"fix({owner}): {words}"


def _undo_rejected_batch(run: Run, tip: str) -> str:
    """Move the PR branch back to the reviewed tip `tip`: commits the driver did not validate get no review and
    no push. The files stay in slot 1, where the next run stops on them as uncommitted changes.
    Returns "" or, for the error message, why the branch did not move."""
    left = run.tip()
    if left != tip and (error := driver_merge.land(run.pr, tip, left)):
        return f"; {run.pr} stays at {left[:7]}: {error}"
    if left != tip:
        commits = f"the commits of the batch end at {left} and stay in the reflog"
    else:
        commits = "the fixer made no commit"
    ledger(f"End of run: the fix batch was rejected, so the driver moved {run.pr} back to its pre-batch tip "
           f"{tip[:7]}; {commits}. The files of the fixer stay in slot 1 as they are.")
    return ""


def _findings_list(run: Run, final: dict) -> str:
    """The lines of the findings a fix batch is due for, for the final fixer's prompt."""
    return "\n".join(f"- {finding['severity']}: {driver_review.title(finding)}"
                     for finding in _to_fix(run, final["open"]))


def _fixer_values(run: Run, final: dict, tip: str, gate_record: str) -> dict[str, str]:
    """The prompt values of the final fixer: the review findings, and the saved gate failure for a gate repair."""
    folder = OUT / FINAL
    values = {**final_values(run), "start_sha": tip, "findings": str(folder / f"findings-{final['round'] - 1}.json"),
              "locked": " ".join(driver_locked.every(_shipped(run))),
              "must_fix_list": "\n".join(f"- {driver_review.title(finding)}" for finding in final["must_fix"]),
              "findings_list": _findings_list(run, final)}
    return {**values, **(driver_final_gate.pointers(final, gate_record, tip) if gate_record else {})}


def _fixer_check(owners: list[str], final: dict, values: dict[str, str], gate_record: str) -> dict:
    """What the final fixer's report is checked against: a commit of a merged ticket in `owners`, a gate repair that
    fixes the gate, a review batch that fixes the must-fix findings."""
    return {"owners": owners,
            "must_fix": [] if gate_record else [driver_review.title(finding) for finding in final["must_fix"]],
            "fix": [values["gate_title"]] if gate_record else []}


def _fixer_call(run: Run, final: dict, tip: str, gate_record: str = "") -> Call:
    """Build a final fixer call from review findings or the saved integration-gate failure."""
    values = _fixer_values(run, final, tip, gate_record)
    name = f"gate-fixer-{final['gate_repairs']}" if gate_record else ""
    baseline = final["locked"] or final["tidied"]
    return Call("fixer", FINAL, ".", values, template="final-gate-fixer" if gate_record else "final-fixer",
                fix_round=final["round"], name=name,
                check=_fixer_check(list(_shipped(run)), final, values, gate_record),
                locked=lambda _: driver_locked.for_end_of_run(_shipped(run), baseline))


def _run_fixer(run: Run, final: dict, tip: str, gate_record: str = "") -> dict:
    """Run the fixer on the PR branch in slot 1 and return its report. A rejected batch is undone."""
    try:
        return run.agent(_fixer_call(run, final, tip, gate_record))
    except Blocked as blocked:
        raise Blocked(f"{blocked}{_undo_rejected_batch(run, tip)}") from None
    except Halted as halted:
        raise Halted(f"{halted}{_undo_rejected_batch(run, tip)}") from None


def _name_batch(run: Run, tip: str, report: dict) -> list[str]:
    """Attribute new commits before gating/review; a validated gate-only no-op leaves the tip unchanged."""
    made = git(".", "rev-list", "--reverse", f"{tip}..HEAD").stdout.split()
    if not made:
        return []
    named = driver_tidy.rewrite(tip, [(commit, _fix_commit_message(commit, report)) for commit in made])
    if not driver_tidy.same_tree(named[-1], made[-1]):
        raise Blocked(f"the named commits do not have the tree of {made[-1][:7]}")
    # Same tree: slot 1's index and working files remain correct as the ref moves.
    if error := driver_merge.land(run.pr, named[-1], made[-1]):
        raise Blocked(error)
    return named


def _fix_batch(run: Run, final: dict, tip: str, gate_record: str = "") -> None:
    """Run one bounded batch and settle its attributed tip before the next full gate."""
    with during("the fix batch"):
        report = _run_fixer(run, final, tip, gate_record)
        named = _name_batch(run, tip, report)
    fields = dict(open=[], batch=named[-1] if named else tip)
    if gate_record:
        fields.update(gate_repairs=final["gate_repairs"] + 1, gate_failure=None)
    run.state.update(lambda state: state["run"]["final"].update(fields))
    _led_batch("integration gate" if gate_record else "final review", tip, named)


def _led_batch(label: str, tip: str, named: list[str]) -> None:
    """The ledger sentence of a fix batch of the `label` on `tip` that made the commits `named`."""
    subjects = ", ".join(f"{commit[:7]} {driver_tidy.text(commit).splitlines()[0]}" for commit in named)
    ledger(f"Fix batch of the {label}: {len(named)} commit{'s' if len(named) != 1 else ''} on {tip[:7]}: "
           f"{subjects or 'no code change'}.")


def _review_until_reviewed(run: Run) -> None:
    """Gate and review the tip, and run the fix batch, until the tip is the reviewed tip, or until a `ship-draft`
    ruling ships the tip as it is."""
    while True:
        final, tip = run.state.read()["run"]["final"], run.tip()
        if final["ship"]:
            return
        if final["reviewed"] != tip:
            _check_review_is_due(run, final, tip)
            with during("the integration gate"):
                if record := driver_final_gate.failure(run, tip):
                    _fix_batch(run, final, tip, record)
                    continue
            _review(run, final, tip)
        elif _to_fix(run, final["open"]):  # after the full review, or after a `retry` of a finding left open
            _fix_batch(run, final, tip)
        else:
            return


# ---------------------------------------------------------------- a ticket that lands after the tidy

def replay(ticket: Ticket, slot: str, run: Run, tip: str, resolve: Callable[[str, list[str]], str]) -> str:
    """A ticket that lands after the rewrite of the end of run, as an overturned `leave-out` does, lands as one commit
    of its own work on the PR tip, as the tidy makes one: the change from where its branch leaves the history before
    the rewrite (the recovery point) to its tip. The PR gets no second copy of the commits the tidy rewrote. Until
    the replay is the recorded tip, `tip` is recorded as `replayed`: a run stopped in between replays again from it
    (driver._restart_replay). A
    conflict is open in the slot as a cherry-pick for `resolve`, the conflict agent of the ticket (from the PR tip, the
    files in conflict), whose stop leaves the slot on `tip` and whose `retry` runs it again. Returns the ticket tip;
    the slot is on it. A branch built on the PR tip as it stands carries no rewritten history and lands as it is: its
    work can be the very commit of the history before the rewrite (the same change in the same second), where the
    fork from the recovery point is the ticket tip itself and the replay would land nothing."""
    final, pr_tip = run.state.read()["run"]["final"], run.tip()
    if not final["recovery"] or driver_merge.ancestor(final["tidied"], tip) or driver_merge.ancestor(pr_tip, tip):
        return tip
    number, branch = ticket.number, run.names.ticket(ticket.number)
    run.state.ticket(number, replayed=tip)
    fork = git(".", "merge-base", final["recovery"], tip).stdout.strip()
    tree = git(".", "merge-tree", "--write-tree", "--no-messages", f"--merge-base={fork}", pr_tip, tip)
    if tree.returncode == 0:
        own = _commit_of(tree.stdout.strip(), pr_tip, ticket, tip)
        git(slot, "checkout", "-q", "-f", "-B", branch, own)
    else:
        work = _commit_of(f"{tip}^{{tree}}", fork, ticket, tip)
        git(slot, "checkout", "-q", "-f", "-B", branch, pr_tip)
        own = _replay_conflict(slot, branch, work, tip, lambda files: resolve(pr_tip, files))
    run.state.ticket(number, tip=own)
    ledger(f"Ticket {number}: its own work is replayed onto {run.pr} at {pr_tip[:7]}, after the rewrite of the end "
           f"of run, as {own[:7]}.")
    return own


def _commit_of(tree: str, parent: str, ticket: Ticket, tip: str) -> str:
    """The commit of `tree` on `parent` with the message the tidy gives `ticket`."""
    made = driver_merge.commit_tree([tree, "-p", parent], driver_tidy.message(ticket), driver_merge.identity(tip))
    if made.returncode:
        raise Blocked(f"git commit-tree failed: {made.stderr.strip()}")
    return made.stdout.strip()


def _replay_conflict(slot: str, branch: str, work: str, tip: str, resolve: Callable[[list[str]], str]) -> str:
    """Cherry-pick `work`, the ticket's own work on its fork from the recovery point, onto the PR tip the slot is on,
    and let `resolve` finish it. When it stops, the slot is back on `tip`."""
    env = {**os.environ, **driver_merge.identity(tip, slot)}
    subprocess.run(["git", "-C", slot, "cherry-pick", work], capture_output=True, env=env)
    files = git(slot, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    try:
        if not files:
            raise Blocked(f"the replay of {branch} onto the rewritten PR branch failed with no file in conflict")
        return resolve(files)
    except (Blocked, Halted):
        git(slot, "cherry-pick", "--abort")
        git(slot, "checkout", "-q", "-f", "-B", branch, tip)
        raise


def landed_after_tidy(state: dict, number: str, pr_tip: str) -> None:
    """Record ticket `number`, which merged at `pr_tip` after the rewrite, as an overturned `leave-out` does: the end of
    run reviews that tip (`batch`), the final fixers keep every ticket's locked files as it has them (`locked`), the
    ticket map names its commit, and the pr-writer writes its part of the PR body again (`body`). A `ship-draft` ruling
    shipped the tip before it (`ship`): the new tip is gated and reviewed before it is pushed."""
    final = state["run"]["final"]
    if final["tidied"]:
        final.update(batch=pr_tip, locked=pr_tip, body=None, ship=None)
        final["commits"][number] = pr_tip


# ---------------------------------------------------------------- the push

def _shipped(run: Run) -> dict:
    """The records of the tickets the end of run ships, every one but the left-out ones: its to own and lock. Each of
    them is merged, as the end of run starts only then."""
    left = set(driver_pr.left_out(run))
    return {number: entry for number, entry in run.state.read()["tickets"].items() if number not in left}


def _require_pushable(run: Run, tip: str) -> None:
    """Block the push when a commit that origin lacks has no good signature with commit.gpgsign on, or has a
    Co-authored-by trailer."""
    if bad := driver_merge.unpushable(tip):
        raise Blocked(f"commits of {run.pr} may not be pushed: {'; '.join(bad)}: nothing is pushed; sign or reword "
                      "them, then run with --push again")


def _gated(final: dict, tip: str) -> str:
    """`tip` when a full gate ran on it, green or red; "" otherwise."""
    red = (final["gate_failure"] or {}).get("tip") == tip
    return tip if red or tip in final["green_gates"][driver_gate.FULL] else ""


def _must_fix_open(run: Run, final: dict) -> bool:
    """A critical or must-fix finding is open that no ruling of the operator ships with (driver_decisions.waived)."""
    return any(driver_decisions.finding_kind(finding) == driver_decisions.MUST_FIX
               for finding in _to_fix(run, _unverified(final)))


def _unverified(final: dict) -> list[dict]:
    """The findings that no review has verified fixed. After a settled review, its findings file: a fix batch clears
    `open` before any review verifies its fix, and only the next settled review replaces the file. Before one, the
    findings of the lens reports accepted so far and the must-fix findings carried from the tickets."""
    if final["round"]:
        return json.loads((OUT / FINAL / f"findings-{final['round'] - 1}.json").read_text())
    return [finding for one in final["lenses"].values() for finding in one["report"]["findings"]] + final["must_fix"]


def draft_ruling(run: Run) -> dict[str, str]:
    """The driver's end-of-run draft policy, once its retries are spent and unless --ask: `ship-draft` for each open
    end-of-run `ticket-blocked` question, when a full gate ran on the branch tip and no critical or must-fix finding
    is open: no agent ships one. Else {}, and the stop parks for the operator, who may still answer `ship-draft`."""
    state = run.state.read()["run"]
    final = state["final"]
    stops = [q["id"] for q in state["questions"]
             if (q["answer"], q["kind"], q["ticket"]) == (None, "ticket-blocked", FINAL)]
    if not stops or not _gated(final, run.tip()) or _must_fix_open(run, final):
        return {}
    return dict.fromkeys(stops, "ship-draft")


def ship_record(run: dict, answered: list[dict]) -> dict | None:
    """What a `ship-draft` answer among the `answered` questions ships, for `run.final.ship`: the tip of the PR
    branch when a full gate ran on it, green or red (else None, and the push refuses), and each stop with the role
    that stopped. None with no such answer. The end of run reviews no more, and the push reads only this record."""
    stops = [{"subject": q["subject"], "role": q.get("role")} for q in answered if q["answer"] == "ship-draft"]
    if not stops:
        return None
    return {"tip": _gated(run["final"], _branch_tip(run)) or None, "stops": stops}


def refused_ship(run: dict, answers: dict[str, str]) -> list[str]:
    """Why each `ship-draft` among the operator's `answers` is refused: no full gate ran on the tip of the PR branch,
    as after a failed rebase conflict agent, so there is no tip to ship. The question stays open."""
    tip = _branch_tip(run) if "ship-draft" in answers.values() else ""
    if not tip or _gated(run["final"], tip):
        return []
    return [f"--answer {question}=ship-draft: no full gate ran on {tip[:7]}, the tip of {run['pr_branch']}, so there "
            "is no gated tip to ship; the question stays open, and `retry` remains"
            for question, option in answers.items() if option == "ship-draft"]


def _branch_tip(run: dict) -> str:
    return git(".", "rev-parse", f"refs/heads/{run['pr_branch']}").stdout.strip()


def _shipped_tip(run: Run, ship: dict) -> str:
    """The tip that the `ship-draft` ruling recorded, while the PR branch is still at it."""
    if not ship["tip"]:
        raise Blocked(f"the ship-draft ruling found no full gate run on the tip of {run.pr}: nothing is pushed")
    if ship["tip"] != run.tip():
        raise Blocked(f"{run.pr} moved from {ship['tip'][:7]}, the gated tip that the ship-draft ruling ships, to "
                      f"{run.tip()[:7]}: nothing is pushed")
    return ship["tip"]


def _pushable_tip(run: Run, final: dict) -> str:
    """The tip to push: the reviewed tip with a green full gate, or the tip that a `ship-draft` ruling recorded."""
    if final["ship"]:
        return _shipped_tip(run, final["ship"])
    tip = final["reviewed"]
    if tip != run.tip() or tip not in final["green_gates"][driver_gate.FULL]:
        raise Blocked(f"the reviewed tip {str(tip)[:7]} is not the tip of {run.pr} with a green full gate record: "
                      "nothing is pushed")
    return tip


def _push(run: Run, final: dict, tickets: list[Ticket]) -> list[str]:
    """Push the reviewed tip, or the gated tip of a draft, to origin, once and with no force, then open the PR, once.
    Returns the lines for the operator."""
    tip = _pushable_tip(run, final)
    if not final["base"]:
        raise Blocked(f"the end of run tidied {run.pr} when the repository had no origin remote, so it is on no base "
                      "branch of origin: nothing is pushed")
    if not shutil.which("gh"):
        raise Blocked("gh is not on PATH: nothing is pushed")
    if final["pushed"] != tip:
        driver_pr.require_open(final)
        _require_pushable(run, tip)
        # The commit, not the branch name: what leaves the machine is the tip the review covered.
        sent = git(".", "push", "-q", "origin", f"{tip}:refs/heads/{run.pr}")
        if sent.returncode:
            raise Blocked(f"git push origin {run.pr} failed: {sent.stderr.strip()}")
        run.state.update(lambda state: state["run"]["final"].update(pushed=tip))
        ledger(f"Pushed {run.pr} at {tip[:7]} to origin.")
    return [f"Pushed {run.pr} at {tip} to origin.", f"PR: {driver_pr.pr(run, final, tickets, tip)}"]


def again(argv: list[str]) -> str:
    """The command of this run with `--push` and without its answers: an answer is given once."""
    kept, skip = [], False
    for word in argv:
        if not (skip or word.startswith("--answer")):
            kept.append(word)
        skip = word == "--answer"
    return shlex.join(["sh", str(driver_agents.SKILL / "run.sh"), *kept, "--push"])


def _close(run: Run, tickets: list[Ticket]) -> list[str]:
    """The tip is reviewed and its gate is green: push when the run may. Returns the lines for the operator."""
    final = run.state.read()["run"]["final"]
    if run.args.push or run.args.unattended:
        return _push(run, final, tickets)
    if final["base"]:
        how = f"To push {run.pr} and open the PR, run: {again(run.args.argv)}"
    else:
        how = "The repository has no origin remote."
    return [f"Branch tip: {run.tip()}", f"Reviewed tip: {final['reviewed']}", f"Nothing is pushed. {how}"]


def _unmerged_tickets(state: dict, tickets: list[Ticket]) -> str:
    """"" or why the end-of-run work does not start. A ticket that is not merged can still merge after an answer,
    and its branch starts from the history as it is: a tidy would rewrite its branch point. A left-out ticket, and
    each ticket it blocks, does not count: no answer in this run merges it."""
    left = driver_decisions.left_out(state["run"]["questions"], tickets)
    waiting = [number for number, entry in state["tickets"].items()
               if entry["phase"] != "merged" and number not in left]
    return f"these tickets are not merged: {', '.join(waiting)}" if waiting else ""


def run(run: Run, tickets: list[Ticket]) -> tuple[bool, list[str]]:
    """Do the end of the run, from where state.json says it is. Call it only when no slot runs a ticket.
    Returns (the end of run is BLOCKED, the lines the operator reads last)."""
    state = run.state.read()
    reason, before = _unmerged_tickets(state, tickets), state["run"]["final"]["skipped"]
    run.state.update(lambda new: new["run"]["final"].update(skipped=reason))
    if reason:
        if reason != before:
            ledger(f"End-of-run work skipped: {reason}.")
        return False, []
    try:
        if _parked(state):
            return False, []  # the driver prints the questions
        _rewrite_once(run, tickets)
        _review_until_reviewed(run)
        return False, _close(run, tickets)
    except Blocked as blocked:
        line = ledger(f"End of run: BLOCKED at {blocked}.")
        if FINAL in run.asks:  # with no question, the end of run is not recorded BLOCKED: the next run tries again
            run.block(FINAL, line)
        return True, [f"End of run: BLOCKED at {blocked}"]


def _parked(state: dict) -> bool:
    """True while the end of run is BLOCKED at a question that is open. Raises Blocked when it is BLOCKED with every
    question answered, so by an `edit`: nothing runs and nothing is pushed."""
    if state["run"]["final"]["phase"] != "blocked":
        return False
    if any(q["ticket"] == FINAL and q["answer"] is None for q in state["run"]["questions"]):
        return True
    raise Blocked("the operator keeps the end of run BLOCKED for an edit (edit): nothing is pushed")
