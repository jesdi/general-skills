"""The end of a run, when every ticket is merged and every slot is idle: tidy the PR branch into one commit per
ticket, rebase it onto the fetched base tip, gate it, run the final review, let one fix batch land as `fix(NN)`
commits with a scoped review, then stop before the push, or push and open the PR.

`run.final` of state.json is the resume contract (`driver_state._final` makes it with every key):
    skipped    why the end-of-run work did not start
    recovery   the PR tip before the rewrite; Run.names names its ref. None when origin had the history already
    tidied     the tip the rewrite made, or the tip as the tickets left it when the history is kept
    base, onto the default branch of origin and its tip under the history; with no origin: "" and the start of the run
    commits    {ticket: its commit}
    reviewed   the last tip a review covered
    round      the reviews done
    open       the findings the fix batch is due for
    batch      the tip of the fix batch that the driver validated and named
    green_gates, pushed, pr
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import driver_agents
import driver_decisions
import driver_gate
import driver_merge
import driver_review
import driver_tidy
from driver_run import FINAL, OUT, Blocked, Halted, Run, during, ledger
from driver_slots import git, head
from driver_state import State
from driver_tickets import Ticket

KIND = "final-finding"
ROUNDS = 2  # the full review and the scoped one after the fix batch
# The final review runs through the review-diff skill, which is installed next to this one.
REVIEW_DIFF = driver_agents.SKILL.parent / "review-diff" / "SKILL.md"


def _installed(name: str) -> Path | None:
    """The SKILL.md of a skill: next to this skill, else in the skill folder of the Claude home or of the shared
    home of Codex and OpenCode."""
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    homes = (driver_agents.SKILL.parent, claude / "skills", Path.home() / ".agents" / "skills")
    return next((home / name / "SKILL.md" for home in homes if (home / name / "SKILL.md").is_file()), None)


def pr_skill() -> Path | None:
    """The SKILL.md of the pr skill, which the PR body follows."""
    return _installed("pr")


def pr_risk_skill() -> Path | None:
    """The SKILL.md of the pr-risk skill, which parses and validates the merge-risk block of the PR body."""
    return _installed("pr-risk")


PR_RISK_INSTALL = "`npx skills add jesdi/general-skills --skill pr-risk`"


def _risk_errors(method: Path, body: str) -> str:
    """The errors of the merge-risk block of `body` as `pr-risk` names them, or "" when the block is valid."""
    checked = OUT / FINAL / "pr-risk-check.md"
    checked.write_text(body)
    done = subprocess.run([sys.executable, str(method.parent / "merge_risk.py"), "validate", str(checked)],
                          capture_output=True, text=True)
    return (done.stdout + done.stderr).strip() if done.returncode else ""


def _has_origin() -> bool:
    return git(".", "remote", "get-url", "origin").returncode == 0


def precondition_errors(push: bool) -> list[str]:
    """[] or the errors that stop the run before any agent."""
    errors = []
    if not REVIEW_DIFF.exists():
        errors.append(f"{REVIEW_DIFF} is missing: the final review runs through it; install the review-diff skill "
                      "next to implement-spec")
    if push and not pr_skill():
        errors.append("the pr skill is missing: the PR body follows it; install it with "
                      "`npx skills add mattpocock/skills --skill pr`, or run without --push and --unattended")
    if push and not pr_risk_skill():
        errors.append(f"the pr-risk skill is missing: the PR body carries its merge-risk block; install it with "
                      f"{PR_RISK_INSTALL}, or run without --push and --unattended")
    if push and not _has_origin():
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


def _prompt_values(run: Run) -> dict[str, str]:
    """The pointers every prompt of the end of run gets. Its agents work in slot 1, the PR worktree."""
    return {"spec": run.args.spec, "tickets": run.args.tickets, "pr_branch": run.pr, "branch": run.pr,
            "gate": run.args.gate}


def _title(finding: dict) -> str:
    return f"{finding['title']} ({finding['file']}:{finding['line']})"


def _locked_files(run: Run) -> list[str]:
    return sorted({path for entry in run.state.read()["tickets"].values() for path in entry["locked"]})


def _last(commits: dict[str, str]) -> str:
    return list(commits.values())[-1]


# ---------------------------------------------------------------- the rewrite: tidy, rebase, move the branch

def _fetch_base() -> tuple[str, str]:
    """Fetch every ref of origin. Returns (its default branch, the tip of that branch)."""
    shown = git(".", "ls-remote", "--symref", "origin", "HEAD")
    name = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", shown.stdout, re.M)
    if not name:
        raise Blocked(f"origin names no default branch: {shown.stderr.strip() or 'git ls-remote shows no HEAD'}")
    fetched = git(".", "fetch", "-q", "origin")
    onto = git(".", "rev-parse", "-q", "--verify", f"refs/remotes/origin/{name[1]}^{{commit}}").stdout.strip()
    if fetched.returncode or not onto:
        raise Blocked(f"git fetch origin did not give refs/remotes/origin/{name[1]}: {fetched.stderr.strip()}")
    return name[1], onto


def _base(run: Run) -> tuple[str, str]:
    """(the default branch of origin, its tip): what the tidied history sits on. With no origin there is no base:
    ("", the commit the run started from)."""
    if not _has_origin():
        ledger("End of run: the repository has no origin remote, so the rebase and the push are skipped; the "
               "tidy, the gate and the final review run.")
        return "", run.state.read()["run"]["start"]
    base, onto = _fetch_base()
    if base == run.pr:
        raise Blocked(f"{run.pr} is the default branch of origin: the end of run needs a PR branch")
    return base, onto


def _origin_has_commits(pr: str, tip: str, onto: str) -> bool:
    """origin has the PR branch, or a ref of origin holds a commit of `tip` that the base tip `onto` does not."""
    def commits_not_in(*others: str) -> str:
        return git(".", "rev-list", "--count", tip, "--not", *others).stdout.strip()

    return (commits_not_in(onto) != commits_not_in("--remotes=origin")
            or bool(git(".", "ls-remote", "origin", f"refs/heads/{pr}").stdout.strip()))


def _keep_published_history(run: Run, tip: str, base: str, onto: str) -> None:
    """Record the history as it is: a history that origin has is never rewritten."""
    commits = dict(run.state.read()["run"]["landed"])
    run.state.update(lambda state: state["run"]["final"].update(tidied=tip, onto=onto, base=base, commits=commits))
    ledger(f"End of run: origin has commits of {run.pr}, so its history is kept as it is: no tidy and no rebase, "
           f"because a published history is not rewritten. The gate and the final review run on {tip[:7]}.")


def _rebase_is_open() -> bool:
    return any(Path(git(".", "rev-parse", "--git-path", name).stdout.strip()).exists()
               for name in ("rebase-merge", "rebase-apply"))


def _files_in_conflict() -> list[str]:
    return git(".", "diff", "--name-only", "--diff-filter=U").stdout.split()


def _resolve_rebase_conflict(run: Run, env: dict, base: str, onto: str) -> None:
    """Run the conflict agent, once, on the rebase that is open in slot 1, then gate the rebased tip. No rebase is
    open afterwards, also when this raises."""
    files = _files_in_conflict()
    values = {**_prompt_values(run), "branch": "HEAD", "start_sha": onto, "base": base, "files": " ".join(files)}
    error = ""
    try:
        run.agent("conflict", FINAL, ".", values, template="rebase-conflict", detached=True)
    except Blocked as blocked:
        error = str(blocked)
    except Halted:
        git(".", "rebase", "--abort")
        raise
    if not error and _rebase_is_open():  # the agent resolved the files and did not go on
        subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"], capture_output=True, env=env)
    if _rebase_is_open():
        again = _files_in_conflict()
        git(".", "rebase", "--abort")
        raise Blocked(f"the conflict agent: {error}" if error else
                      f"the rebase onto {base} conflicts a second time, in {', '.join(again)}")
    # The resolution may not change a locked test: each one is as the recovery point has it.
    before = git(".", "rev-parse", run.names.recovery).stdout.strip()
    changed = driver_review.locked_changes(".", before, _locked_files(run))
    if error := error or "; ".join(finding["title"] for finding in changed):
        raise Blocked(f"the conflict agent: {error}")
    ledger(f"Rebase: the conflict agent resolved the conflict with {base} in {', '.join(files)} at {head('.')[:7]}.")
    with during("the gate after the rebase"):
        run.gate(FINAL, ".", head("."))


def _rebase(run: Run, tip: str, base: str, onto: str) -> None:
    """`git rebase` of `tip` onto `onto` in slot 1, on a detached HEAD: no ref moves. HEAD is the rebased tip."""
    env = {**os.environ, **driver_merge.identity(tip)}
    git(".", "checkout", "-q", "--detach", tip)
    done = subprocess.run(["git", "-c", "core.editor=true", "rebase", onto], capture_output=True, text=True, env=env)
    if _rebase_is_open():
        _resolve_rebase_conflict(run, env, base, onto)
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

def _minor_findings() -> str:
    """One line per minor finding that a ticket review left for the final review, with its findings file."""
    lines = []
    for path in sorted(OUT.glob("[0-9]*/findings-*.json")):
        lines += [f"- {path}: {_title(finding)}" for finding in json.loads(path.read_text())
                  if finding["severity"] == "minor"]
    return "\n".join(dict.fromkeys(lines))


def _check_review_is_due(run: Run, final: dict, tip: str) -> None:
    """Only a tip the driver made gets a review: the tidied tip, then the tip of the validated fix batch."""
    if final["round"] >= ROUNDS:
        raise Blocked(f"the two review rounds are done and the tip {tip[:7]} of {run.pr} is not the reviewed tip "
                      f"{final['reviewed'][:7]}")
    made = final["batch"] or final["tidied"]
    if tip != made:
        raise Blocked(f"the tip {tip[:7]} of {run.pr} is not a tip the driver made: only {made[:7]} gets a review, "
                      f"so the run goes on when {run.pr} is at {made[:7]} again")


def _record_review(state: dict, tip: str, to_fix: list[dict], evidence: list[str]) -> None:
    """The open findings of the first round start the fix batch; those of the second become one question each."""
    record, final = state["run"], state["run"]["final"]
    first_round = final["round"] == 0
    final.update(reviewed=tip, round=final["round"] + 1, open=to_fix if first_round else [])
    if not first_round:
        driver_decisions.stop(record, KIND, "", [_title(finding) for finding in to_fix], evidence)


def _settle_review(run: Run, done: int, tip: str, covered: str, found: list[dict]) -> None:
    """An accepted review of the range `covered`, after `done` earlier rounds: its findings go to their file, the
    ledger and state.json."""
    folder = OUT / FINAL
    file = folder / f"findings-{done}.json"
    file.write_text(json.dumps(found, indent=1) + "\n")
    for finding in found:
        ledger(f"Final review round {done + 1}: {finding['severity']} finding of kind {finding['kind']} at "
               f"{finding['file']}:{finding['line']}: {finding['title']}.")
    to_fix = [finding for finding in found if finding["severity"] in driver_review.OPEN]
    evidence = [str(file), str(folder / f"final-reviewer-{done}.json")]
    run.state.update(lambda state: _record_review(state, tip, to_fix, evidence))
    ledger(f"Final review round {done + 1}: the range {covered} is reviewed, the reviewed tip is {tip[:7]}, and "
           f"{len(to_fix)} critical or important finding{'s are' if len(to_fix) != 1 else ' is'} open.")
    if to_fix and done:
        ledger(f"End of run: {driver_decisions.decides(KIND, len(to_fix))} in {OUT / 'decisions.json'}: a third "
               "review round is never started.")


def _review(run: Run, final: dict, tip: str) -> None:
    """One round of the final review on `tip`: the full review, or after it the scoped one."""
    done, folder = final["round"], OUT / FINAL
    values = {**_prompt_values(run), "start_sha": tip, "range": f"{final['reviewed'] or final['onto']}..{tip}",
              "gate": str(driver_gate.record_path(folder, tip)), "review_diff": str(REVIEW_DIFF),
              "ledger": str(OUT / "ledger.md"), "minors": _minor_findings(), "reviewed": str(final["reviewed"]),
              "rulings": "\n".join(driver_decisions.rulings(run.state.read()["run"]["questions"])),
              "findings": str(folder / f"findings-{done - 1}.json")}
    # A locked file that differs from the tidied tip is a critical finding. The driver made that tip, and since
    # the tickets' own checks only the conflict agent of the rebase ran, which has the same check.
    found = driver_review.locked_changes(".", final["tidied"], _locked_files(run))
    with during("the final review"):
        report = run.read_only(FINAL, ".", run.pr, tip, lambda: run.agent(
            "final-reviewer", FINAL, ".", values, notes=["final-scoped"] if done else [], fix_round=done))
    _settle_review(run, done, tip, values["range"], found + report["findings"])


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
    ledger(f"End of run: the fix batch was rejected, so the driver moved {run.pr} back to its reviewed tip "
           f"{tip[:7]}; {commits}. The files of the fixer stay in slot 1 as they are.")
    return ""


def _run_fixer(run: Run, tickets: list[Ticket], final: dict, tip: str) -> dict:
    """Run the fixer on the PR branch in slot 1 and return its report. A rejected batch is undone."""
    folder = OUT / FINAL
    values = {**_prompt_values(run), "start_sha": tip, "findings": str(folder / f"findings-{final['round'] - 1}.json"),
              "locked": " ".join(_locked_files(run)),
              "findings_list": "\n".join(f"- {finding['severity']}: {_title(finding)}" for finding in final["open"])}
    try:
        return run.agent("fixer", FINAL, ".", values, template="final-fixer", fix_round=final["round"],
                         check={"owners": [ticket.number for ticket in tickets]})
    except Blocked as blocked:
        raise Blocked(f"{blocked}{_undo_rejected_batch(run, tip)}") from None
    except Halted as halted:
        raise Halted(f"{halted}{_undo_rejected_batch(run, tip)}") from None


def _fix_batch(run: Run, tickets: list[Ticket], final: dict, tip: str) -> None:
    """Run the one fix batch, then name its commits `fix(NN)`."""
    with during("the fix batch"):
        report = _run_fixer(run, tickets, final, tip)
        # The commits are named before the gate and the scoped review see them: nothing is rewritten after a review.
        made = git(".", "rev-list", "--reverse", f"{tip}..HEAD").stdout.split()
        named = driver_tidy.rewrite(tip, [(commit, _fix_commit_message(commit, report)) for commit in made])
        if not driver_tidy.same_tree(named[-1], made[-1]):
            raise Blocked(f"the named commits do not have the tree of {made[-1][:7]}")
        # Slot 1 has the branch checked out: the new tip has the same tree, so the index and the files stay right.
        if error := driver_merge.land(run.pr, named[-1], made[-1]):
            raise Blocked(error)
    run.state.update(lambda state: state["run"]["final"].update(open=[], batch=named[-1]))
    subjects = ", ".join(f"{commit[:7]} {driver_tidy.text(commit).splitlines()[0]}" for commit in named)
    ledger(f"Fix batch of the final review: {len(named)} commit{'s' if len(named) > 1 else ''} on {tip[:7]}: "
           f"{subjects}.")


def _review_until_reviewed(run: Run, tickets: list[Ticket]) -> None:
    """Gate and review the tip, and run the fix batch, until the tip is the reviewed tip."""
    while True:
        final, tip = run.state.read()["run"]["final"], run.tip()
        if final["reviewed"] != tip:
            _check_review_is_due(run, final, tip)
            with during("the gate"):
                run.gate(FINAL, ".", tip)
            _review(run, final, tip)
        elif final["open"]:
            _fix_batch(run, tickets, final, tip)
        else:
            return


# ---------------------------------------------------------------- the push

def _name(run: Run) -> str:
    """The title of the PR: the first heading of the spec, else the PR branch."""
    spec = Path(run.args.spec)
    lines = spec.read_text(errors="replace").splitlines() if spec.is_file() else []
    return next((line[2:].strip() for line in lines if line.startswith("# ")), run.pr)


def _facts(run: Run, final: dict, tickets: list[Ticket]) -> str:
    """The end of the PR body: the ticket-to-commit map, the fix commits, the rulings and the review coverage."""
    titles, tip = {ticket.number: ticket.title for ticket in tickets}, final["reviewed"]
    fixes = git(".", "log", "--reverse", "--format=- %h %s", f"{final['tidied']}..{tip}").stdout.strip()
    rulings = driver_decisions.rulings(run.state.read()["run"]["questions"])
    return driver_agents.render("pr-body", {
        "map": "\n".join(f"- {number}: {commit[:7]} {titles[number]}" for number, commit in final["commits"].items()),
        "fixes": fixes or "None.", "rulings": "\n".join(f"- {ruling}" for ruling in rulings) or "None.",
        "tip": tip, "onto": final["onto"], "tidied": final["tidied"],
        "scoped": f"{final['tidied']}..{tip}" if fixes else "not needed"})


def _write_body(run: Run, final: dict, tickets: list[Ticket]) -> Path:
    """Write the PR body: what the pr-writer wrote with the method of the pr skill, then the facts of the run. A body
    written for the reviewed tip is kept when its merge-risk block still validates, so a PR that failed to open costs
    no second agent."""
    body, tip = OUT / FINAL / "pr-body.md", final["reviewed"]
    method, risk, facts = pr_skill(), pr_risk_skill(), _facts(run, final, tickets)
    if final["body"] == tip and body.is_file() and risk and not _risk_errors(risk, body.read_text()):
        return body
    if not method:
        raise Blocked("the pr skill is missing: the PR body follows it; install it with "
                      "`npx skills add mattpocock/skills --skill pr`")
    if not risk:
        raise Blocked(f"the pr-risk skill is missing: the PR body carries its merge-risk block; install it with "
                      f"{PR_RISK_INSTALL}")
    values = {**_prompt_values(run), "start_sha": tip, "range": f"{final['onto']}..{tip}",
              "gate": str(driver_gate.record_path(OUT / FINAL, tip)), "pr_skill": str(method),
              "pr_risk_skill": str(risk), "risk_errors": "", "facts": facts}
    for round_ in range(2):  # the pr-writer gets one more run with the errors of the block
        with during("the PR body"):
            report = run.read_only(FINAL, ".", run.pr, tip,
                                   lambda: run.agent("pr-writer", FINAL, ".", values, fix_round=round_))
        if not (errors := _risk_errors(risk, report["body"])):
            break
        values["risk_errors"] = (f"Your previous body has an invalid merge-risk block. Fix it with the pr-risk "
                                 f"skill; its errors:\n{errors}\n")
    else:
        raise Blocked(f"the merge-risk block of the PR body is invalid after a second run of the pr-writer: {errors}")
    body.write_text(f"{report['body'].strip()}\n\n{facts}")
    run.state.update(lambda state: state["run"]["final"].update(body=tip))
    return body


def _open_pr(run: Run, final: dict, tickets: list[Ticket]) -> str:
    """Open the PR, ready for review, and return its URL."""
    body = _write_body(run, final, tickets)
    made = subprocess.run(["gh", "pr", "create", "--base", final["base"], "--head", run.pr, "--title", _name(run),
                           "--body-file", str(body)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if made.returncode:
        raise Blocked(f"gh pr create failed: {(made.stderr or made.stdout).strip()}")
    url = (made.stdout.split() or [""])[-1]
    run.state.update(lambda state: state["run"]["final"].update(pr=url))
    ledger(f"The PR of {run.pr} into {final['base']} is open: {url}.")
    return url


def _push(run: Run, final: dict, tickets: list[Ticket]) -> list[str]:
    """Push the reviewed tip to origin, once and with no force, then open the PR, once. Returns the lines for the
    operator."""
    tip = final["reviewed"]
    if tip != run.tip() or tip not in final["green_gates"]:
        raise Blocked(f"the reviewed tip {tip[:7]} is not the tip of {run.pr} with a green gate record: nothing is "
                      "pushed")
    if not final["base"]:
        raise Blocked(f"the end of run tidied {run.pr} when the repository had no origin remote, so it is on no base "
                      "branch of origin: nothing is pushed")
    if not shutil.which("gh"):
        raise Blocked("gh is not on PATH: nothing is pushed")
    if final["pushed"] != tip:
        # The commit, not the branch name: what leaves the machine is the tip the review covered.
        sent = git(".", "push", "-q", "origin", f"{tip}:refs/heads/{run.pr}")
        if sent.returncode:
            raise Blocked(f"git push origin {run.pr} failed: {sent.stderr.strip()}")
        run.state.update(lambda state: state["run"]["final"].update(pushed=tip))
        ledger(f"Pushed {run.pr} at {tip[:7]} to origin.")
    url = final["pr"] or _open_pr(run, final, tickets)
    return [f"Pushed {run.pr} at {tip} to origin.", f"PR: {url}"]


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
    record = run.state.read()["run"]
    answers = [question["answer"] for question in record["questions"] if question["kind"] == KIND]
    if None in answers:
        return []  # parked: the driver prints the questions
    if kept := answers.count("fix"):
        raise Blocked(f"the operator keeps {kept} finding{'s' if kept > 1 else ''} of the final review open (fix): "
                      "nothing is pushed")
    final = record["final"]
    if run.args.push or run.args.unattended:
        return _push(run, final, tickets)
    if final["base"]:
        how = f"To push {run.pr} and open the PR, run: {again(run.args.argv)}"
    else:
        how = "The repository has no origin remote."
    return [f"Branch tip: {run.tip()}", f"Reviewed tip: {final['reviewed']}", f"Nothing is pushed. {how}"]


def _unmerged_tickets(state: dict) -> str:
    """"" or why the end-of-run work does not start. A ticket that is not merged can still merge after an answer,
    and its branch starts from the history as it is: a tidy would rewrite its branch point."""
    waiting = [number for number, entry in state["tickets"].items() if entry["phase"] != "merged"]
    return f"these tickets are not merged: {', '.join(waiting)}" if waiting else ""


def run(run: Run, tickets: list[Ticket]) -> tuple[bool, list[str]]:
    """Do the end of the run, from where state.json says it is. Call it only when no slot runs a ticket.
    Returns (the end of run is BLOCKED, the lines the operator reads last)."""
    state = run.state.read()
    reason, before = _unmerged_tickets(state), state["run"]["final"]["skipped"]
    run.state.update(lambda new: new["run"]["final"].update(skipped=reason))
    if reason:
        if reason != before:
            ledger(f"End-of-run work skipped: {reason}.")
        return False, []
    try:
        _rewrite_once(run, tickets)
        _review_until_reviewed(run, tickets)
        return False, _close(run, tickets)
    except Blocked as blocked:
        ledger(f"End of run: BLOCKED at {blocked}.")
        return True, [f"End of run: BLOCKED at {blocked}"]
