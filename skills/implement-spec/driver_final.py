"""The end of a run, when every ticket is merged and every slot is idle: tidy the PR branch into one commit per
ticket, rebase it onto the fetched base tip, gate it, run the final review, let one fix batch land as `fix(NN)`
commits with a scoped review, then stop before the push, or push and open the PR. A finding the scoped review leaves
open asks `final-ticket-blocked-<n>`: only a `retry` runs another batch and scoped review.

`run.final` of state.json is the resume contract (`driver_state._final` makes it with every key):
    skipped    why the end-of-run work did not start
    recovery   the PR tip before the rewrite; Run.names names its ref. None when origin had the history already
    tidied     the tip the rewrite made, or the tip as the tickets left it when the history is kept
    base, onto the --base branch (or the default branch of origin) and its tip under the history; with no origin: "" and the start of the run
    commits    {ticket: its commit}
    reviewed   the last tip a review covered
    round      the reviews done
    open       the findings the fix batch is due for
    lenses     {lens role: {tip, report}}: the accepted report of each lens of the full review and the tip it covers
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
from concurrent.futures import ThreadPoolExecutor
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

# The final review runs through the review-diff skill, which must sit next to this one: `_installed` looks nowhere else.
REVIEW_DIFF = driver_agents.SKILL.parent / "review-diff" / "SKILL.md"
SEVERITIES = ("critical", "important", "minor")


def _installed(name: str) -> Path | None:
    """The SKILL.md of a skill: next to this skill, else in the skill folder of the Claude home or of the shared
    home of Codex and OpenCode. review-diff is only next to this skill."""
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    homes = (REVIEW_DIFF.parent.parent,) if name == REVIEW_DIFF.parent.name else (
        driver_agents.SKILL.parent, claude / "skills", Path.home() / ".agents" / "skills")
    return next((home / name / "SKILL.md" for home in homes if (home / name / "SKILL.md").is_file()), None)


def pr_skill() -> Path | None:
    """The SKILL.md of the pr skill, which the PR body follows."""
    return _installed("pr")


def _method(lens: driver_agents.Lens) -> Path | None:
    """The file `lens` follows, in its skill where that is installed. None when the skill or the file is missing."""
    skill = _installed(lens.skill)
    method = skill and skill.parent / lens.method
    return method if method and method.is_file() else None


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
    if not pr_skill():
        errors.append("the pr skill is missing: the PR body follows it; install it with "
                      "`npx skills add mattpocock/skills --skill pr`, or run without --push and --unattended")
    if not pr_risk_skill():
        errors.append(f"the pr-risk skill is missing: the PR body carries its merge-risk block; install it with "
                      f"{PR_RISK_INSTALL}, or run without --push and --unattended")
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


def _prompt_values(run: Run) -> dict[str, str]:
    """The pointers every prompt of the end of run gets. Its agents work in slot 1, the PR worktree."""
    return {"spec": run.args.spec, "tickets": run.args.tickets, "pr_branch": run.pr, "branch": run.pr,
            "gate": run.args.fast_gate}


def _title(finding: dict) -> str:
    return f"{finding['title']} ({finding['file']}:{finding['line']})"


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
    """Record the history as it is: a history that origin has is never rewritten."""
    commits = dict(run.state.read()["run"]["landed"])
    run.state.update(lambda state: state["run"]["final"].update(tidied=tip, onto=onto, base=base, commits=commits))
    ledger(f"End of run: origin has commits of {run.pr}, so its history is kept as it is: no tidy and no rebase, "
           f"because a published history is not rewritten. The gate and the final review run on {tip[:7]}.")


def _run_rebase_conflict_agent(run: Run, values: dict) -> str:
    """Run the conflict agent on the open rebase; the driver goes on with the rebase after it, and its report is
    rejected while the rebased tip changes a locked file. Returns "" or why it blocked."""
    try:
        run.agent("conflict", FINAL, ".", values, template="rebase-conflict", detached=True)
    except Blocked as blocked:
        return str(blocked)
    except Halted:
        git(".", "rebase", "--abort")
        raise
    return ""


def _resolve_rebase_conflict(run: Run, base: str, onto: str) -> None:
    """Run the conflict agent, with its one rerun, on the rebase that is open in slot 1, then gate the rebased tip.
    No rebase is open afterwards, also when this raises."""
    files = driver_merge.files_in_conflict()
    values = {**_prompt_values(run), "branch": "HEAD", "start_sha": onto, "base": base, "files": " ".join(files)}
    error = _run_rebase_conflict_agent(run, values)
    if driver_merge.rebase_is_open():
        again = driver_merge.files_in_conflict()
        git(".", "rebase", "--abort")
        raise Blocked(f"the conflict agent: {error}" if error else
                      f"the rebase onto {base} conflicts a second time, in {', '.join(again)}")
    if error:
        raise Blocked(f"the conflict agent: {error}")
    ledger(f"Rebase: the conflict agent resolved the conflict with {base} in {', '.join(files)} at {head('.')[:7]}.")
    with during("the gate after the rebase"):
        run.gate(FINAL, ".", head("."), driver_gate.FULL)


def _rebase(run: Run, tip: str, base: str, onto: str) -> None:
    """`git rebase` of `tip` onto `onto` in slot 1, on a detached HEAD: no ref moves. HEAD is the rebased tip."""
    env = {**os.environ, **driver_merge.identity(tip)}
    git(".", "checkout", "-q", "--detach", tip)
    done = subprocess.run(["git", "-c", "core.editor=true", "rebase", onto], capture_output=True, text=True, env=env)
    if driver_merge.rebase_is_open():
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
        lines += [f"- {path}: {_title(finding)}" for finding in json.loads(path.read_text())
                  if finding["severity"] == "minor"]
    for path in sorted(OUT.glob("[0-9]*/observations-*.json")):
        lines += [f"- {path}: {_title(finding)}" for finding in json.loads(path.read_text())]
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
    to_fix = [finding for finding in found if finding["severity"] in driver_review.OPEN]
    run.state.update(lambda state: state["run"]["final"].update(reviewed=tip, round=done + 1, open=to_fix))
    ledger(f"Final review round {done + 1}: the range {covered} is reviewed, the reviewed tip is {tip[:7]}, and "
           f"{len(to_fix)} critical or important finding{'s are' if len(to_fix) != 1 else ' is'} open.")
    if to_fix and done:
        # Another batch is the operator's call: one `final-ticket-blocked` question per finding.
        titles = [_title(finding) for finding in to_fix]
        run.ask(FINAL, "ticket-blocked", [f"a finding open after the scoped review: {title}" for title in titles],
                str(file))
        raise Blocked(f"the scoped review leaves {len(titles)} finding{'s' * (len(titles) > 1)} open: "
                      + "; ".join(titles))


def _review(run: Run, final: dict, tip: str) -> None:
    """One round of the final review on `tip`: the full review by the three lenses, or after it the scoped one, on
    the other-provider entry: another provider than the implementer's, and so than the fixer's when it shares
    the implementer's."""
    done, folder = final["round"], OUT / FINAL
    values = {**_prompt_values(run), "start_sha": tip, "range": f"{final['reviewed'] or final['onto']}..{tip}",
              "gate": str(driver_gate.record_path(folder, driver_gate.FULL, tip)),
              "ledger": str(OUT / "ledger.md"), "carried": _carried_findings(), "reviewed": str(final["reviewed"]),
              "must_fix_list": "\n".join(f"- {_title(finding)}" for finding in final["must_fix"]),
              "rulings": "\n".join(driver_decisions.rulings(run.state.read()["run"]["questions"])),
              "findings": str(folder / f"findings-{done - 1}.json")}
    with during("the final review"):
        # The full review resolves no must-fix finding: only the scoped review verifies a fix.
        reviewed, resolved = _scoped(run, values, tip, done) if done else (_lenses(run, values, tip), set())
    carried = [finding for finding in final["must_fix"] if _title(finding) not in resolved]
    _settle_review(run, done, tip, values["range"], _round_findings(final, _merged(reviewed), carried))


def _scoped(run: Run, values: dict[str, str], tip: str, done: int) -> tuple[list[dict], set[str]]:
    """The scoped review after the fix batch, one `final-reviewer`. Returns (its findings, each with the source
    `final-reviewer`; the titles of the must-fix findings it resolved)."""
    entries = driver_agents.other_provider_entries(run.models)
    report = run.read_only(FINAL, ".", run.pr, tip, lambda: run.agent(
        "final-reviewer", FINAL, ".", values, entry=entries[0], entries=entries, fix_round=done))
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
            reports[role] = run.agent(role, FINAL, ".", values[role], entry=entries[0], entries=entries,
                                      template="final-lens", alone=lambda: len(running) == 1)
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
    ledger(f"End of run: the fix batch was rejected, so the driver moved {run.pr} back to its reviewed tip "
           f"{tip[:7]}; {commits}. The files of the fixer stay in slot 1 as they are.")
    return ""


def _run_fixer(run: Run, tickets: list[Ticket], final: dict, tip: str) -> dict:
    """Run the fixer on the PR branch in slot 1 and return its report. A rejected batch is undone."""
    folder = OUT / FINAL
    values = {**_prompt_values(run), "start_sha": tip, "findings": str(folder / f"findings-{final['round'] - 1}.json"),
              "locked": " ".join(driver_review.others(run.state.read()["tickets"], FINAL)),
              "must_fix_list": "\n".join(f"- {_title(finding)}" for finding in final["must_fix"]),
              "findings_list": "\n".join(f"- {finding['severity']}: {_title(finding)}" for finding in final["open"])}
    try:
        return run.agent("fixer", FINAL, ".", values, template="final-fixer", fix_round=final["round"],
                         check={"owners": [ticket.number for ticket in tickets],
                                "must_fix": [_title(finding) for finding in final["must_fix"]]})
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
                run.gate(FINAL, ".", tip, driver_gate.FULL)
            _review(run, final, tip)
        elif final["open"]:  # after the full review, or after a `retry` of a finding the scoped review left open
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


def _run_pr_writer(run: Run, tip: str, risk: Path, values: dict) -> str:
    """Run the pr-writer, and once more with the errors of its merge-risk block. Returns the body it wrote."""
    for round_ in range(2):
        with during("the PR body"):
            report = run.read_only(FINAL, ".", run.pr, tip,
                                   lambda: run.agent("pr-writer", FINAL, ".", values, fix_round=round_))
        if not (errors := _risk_errors(risk, report["body"])):
            return report["body"]
        values["risk_errors"] = (f"Your previous body has an invalid merge-risk block. Fix it with the pr-risk "
                                 f"skill; its errors:\n{errors}\n")
    raise Blocked(f"the merge-risk block of the PR body is invalid after a second run of the pr-writer: {errors}")


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
              "gate": str(driver_gate.record_path(OUT / FINAL, driver_gate.FULL, tip)), "pr_skill": str(method),
              "pr_risk_skill": str(risk), "risk_errors": "", "facts": facts}
    body.write_text(f"{_run_pr_writer(run, tip, risk, values).strip()}\n\n{facts}")
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


def _require_signed(run: Run, tip: str) -> None:
    """Block the push when commit.gpgsign is on and a commit that origin lacks has no good signature."""
    if bad := driver_merge.unsigned(tip):
        raise Blocked(f"commit.gpgsign is on but {len(bad)} commit{'s' if len(bad) > 1 else ''} of {run.pr} "
                      f"{'have' if len(bad) > 1 else 'has'} no good signature ({', '.join(bad)}): nothing is pushed")


def _push(run: Run, final: dict, tickets: list[Ticket]) -> list[str]:
    """Push the reviewed tip to origin, once and with no force, then open the PR, once. Returns the lines for the
    operator."""
    tip = final["reviewed"]
    if tip != run.tip() or tip not in final["green_gates"][driver_gate.FULL]:
        raise Blocked(f"the reviewed tip {tip[:7]} is not the tip of {run.pr} with a green full gate record: nothing "
                      "is pushed")
    if not final["base"]:
        raise Blocked(f"the end of run tidied {run.pr} when the repository had no origin remote, so it is on no base "
                      "branch of origin: nothing is pushed")
    if not shutil.which("gh"):
        raise Blocked("gh is not on PATH: nothing is pushed")
    if final["pushed"] != tip:
        _require_signed(run, tip)
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
    final = run.state.read()["run"]["final"]
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
        if _parked(state):
            return False, []  # the driver prints the questions
        _rewrite_once(run, tickets)
        _review_until_reviewed(run, tickets)
        return False, _close(run, tickets)
    except Blocked as blocked:
        _ask(run, ledger(f"End of run: BLOCKED at {blocked}."))
        return True, [f"End of run: BLOCKED at {blocked}"]


def _parked(state: dict) -> bool:
    """True while a question of the last stop of the end of run is open. Raises Blocked when the operator answered
    `edit`: nothing runs and nothing is pushed until the run starts again."""
    asked = [question for question in state["run"]["questions"] if question["ticket"] == FINAL]
    last = max((question["stop"] for question in asked), default=0)
    answers = [question["answer"] for question in asked if question["stop"] == last]
    if "edit" in answers:
        raise Blocked("the operator keeps the end of run BLOCKED for an edit (edit): nothing is pushed")
    return None in answers


def _ask(run: Run, line: int) -> None:
    """The `final-ticket-blocked` questions of the stop that the ledger line `line` names, when it has any."""
    ask = run.asks.pop(FINAL, None)
    if ask:
        evidence = [ask.report, f"{OUT / 'ledger.md'}:{line}"]
        run.state.update(lambda state: driver_decisions.stop(state["run"], ask.kind, FINAL, ask.subjects, evidence))
        ledger(f"End of run: {driver_decisions.decides(ask.kind, len(ask.subjects))} in {OUT / 'decisions.json'}.")
