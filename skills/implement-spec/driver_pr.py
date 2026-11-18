"""The PR of the end of run: its body, the pr-writer's part and the facts of the driver, and its opening with `gh`,
once. Once it is open, a push only adds commits to it, and its body follows what an overturn changed: the facts each
time, the pr-writer's part when a ticket landed after the tidy (driver_final.landed_after_tidy clears `final.body`).

`run.final` of state.json (driver_final) keeps what this module needs: `body`, the tip the pr-writer's part was written
for, None until it is and again once a ticket lands after the tidy; `pr`, its URL; `draft`."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import driver_agents
import driver_decisions
import driver_final_gate
from driver_review import title
from driver_run import FINAL, OUT, Blocked, Call, Run, during, final_values, ledger
from driver_slots import git
from driver_tickets import Ticket

BODY, WRITER = OUT / FINAL / "pr-body.md", OUT / FINAL / "pr-writer-body.md"


def pr_skill() -> Path | None:
    """The SKILL.md of the pr skill, which the PR body follows."""
    return driver_agents.installed("pr")


def pr_risk_skill() -> Path | None:
    """The SKILL.md of the pr-risk skill, which parses and validates the merge-risk block of the PR body."""
    return driver_agents.installed("pr-risk")


PR_RISK_INSTALL = "`npx skills add jesdi/general-skills --skill pr-risk`"


def _risk_errors(method: Path, body: str) -> str:
    """The errors of the merge-risk block of `body` as `pr-risk` names them, or "" when the block is valid."""
    checked = OUT / FINAL / "pr-risk-check.md"
    checked.write_text(body)
    done = subprocess.run([sys.executable, str(method.parent / "merge_risk.py"), "validate", str(checked)],
                          capture_output=True, text=True)
    return (done.stdout + done.stderr).strip() if done.returncode else ""


def left_out(run: Run) -> list[str]:
    """The tickets left out of the run (driver_decisions.left_out), which the end of run ships without."""
    return sorted(driver_decisions.left_out(run.state.read()["run"]["questions"], run.tickets))


def _name(run: Run) -> str:
    """The title of the PR: the first heading of the spec, else the PR branch."""
    spec = Path(run.args.spec)
    lines = spec.read_text(errors="replace").splitlines() if spec.is_file() else []
    return next((line[2:].strip() for line in lines if line.startswith("# ")), run.pr)


def _coverage(final: dict, tip: str) -> dict[str, str]:
    """The review coverage of the pushed tip `tip` in the PR body: the tips and the commits after the reviewed tip
    that no review covered; the full review, which may include gate repairs made after tidy; the scoped review of the
    remaining range. A draft shipped before any review was settled has neither review."""
    reviewed = final["reviewed"]
    full_tip = next(iter(final["lenses"].values()), {"tip": final["tidied"]})["tip"]
    fields = {"tips": f"Branch tip and reviewed tip: {tip}", "full_review": f"{final['onto']}..{full_tip}",
              "scoped": f"{full_tip}..{reviewed}" if full_tip != reviewed else "not needed"}
    if not reviewed:
        fields.update(full_review="not run", scoped="not run")
    if tip != reviewed:
        since = f"{reviewed or final['onto']}..{tip}"
        unreviewed = git(".", "log", "--reverse", "--format=%h %s", since).stdout.strip().splitlines()
        fields["tips"] = f"Branch tip: {tip}; reviewed tip: {reviewed or 'none'}; not reviewed: {'; '.join(unreviewed)}"
    return fields


def _ticket_map(final: dict, titles: dict[str, str], left: list[str]) -> str:
    """The ticket-to-commit map, then the tickets `left` out of the run."""
    out = "; ".join(f"{number} {titles[number]}" for number in left)
    return ("\n".join(f"- {number}: {commit[:7]} {titles[number]}" for number, commit in final["commits"].items())
            + f"\n\nLeft out of the run, so not in this PR: {out}." * bool(left))


def _facts(run: Run, final: dict, tickets: list[Ticket], tip: str) -> str:
    """The end of the PR body for the pushed tip `tip`: the ticket-to-commit map, the fix commits, the rulings and the
    review coverage."""
    titles = {ticket.number: ticket.title for ticket in tickets}
    fixes = git(".", "log", "--reverse", "--format=- %h %s", f"{final['tidied']}..{tip}").stdout.strip()
    rulings = driver_decisions.rulings(run.state.read()["run"]["questions"])
    return driver_agents.render("pr-body", {
        "map": _ticket_map(final, titles, left_out(run)),
        "fixes": fixes or "None.", "rulings": "\n".join(f"- {ruling}" for ruling in rulings) or "None.",
        **_coverage(final, tip), "gate": _old_failures(final, tip),
        "providers": _review_providers(run.state.read()["run"]["agents"])})


def _old_failures(final: dict, tip: str) -> str:
    """The body's line on the failures of the full gate on the pushed tip `tip` that its base has too; "" for none."""
    old = driver_final_gate.preexisting(final, tip)
    return f"- Pre-existing failures: {old}" if old else ""


# The roles that review code, with their line in the PR body, and the roles that write it.
_REVIEWS = {"reviewer": "Ticket reviews", "final-correctness": "Correctness lens", "final-reviewer": "Scoped review"}
_AUTHORS = ("implementer", "fixer", "conflict")


def _review_providers(agents: list[dict]) -> str:
    """One line per reviewing role that ran: the entries it ran on, and whether one of them shares a provider with an
    entry that wrote code. It reads the agents that ran, so a preflight fallback of an earlier invocation that a later
    one undid is not claimed."""
    wrote = {driver_agents.split(entry)[0] for entry in _entries(agents, _AUTHORS)}
    lines = []
    for role, label in _REVIEWS.items():
        if used := _entries(agents, (role,)):
            same = any(driver_agents.split(entry)[0] in wrote for entry in used)
            lines.append(f"- {label} ran on: {', '.join(used)}" + (", on a provider that also wrote code" * same))
    return "\n".join(lines)


def _entries(agents: list[dict], roles: tuple[str, ...]) -> list[str]:
    """The entries that the agents of `roles` ran on."""
    return sorted({agent["entry"] for agent in agents if agent["role"] in roles})


def _run_pr_writer(run: Run, tip: str, risk: Path, values: dict) -> str:
    """Run the pr-writer, and once more with the errors of its merge-risk block. Returns the body it wrote."""
    for round_ in range(2):
        with during("the PR body"):
            report = run.read_only(FINAL, ".", run.pr, tip,
                                   lambda: run.agent(Call("pr-writer", FINAL, ".", values, fix_round=round_)))
        if not (errors := _risk_errors(risk, report["body"])):
            return report["body"]
        values["risk_errors"] = (f"Your previous body has an invalid merge-risk block. Fix it with the pr-risk "
                                 f"skill; its errors:\n{errors}\n")
    raise Blocked(f"the merge-risk block of the PR body is invalid after a second run of the pr-writer: {errors}")


def _body_skills() -> tuple[Path, Path]:
    """The `pr` skill the body follows and the `pr-risk` skill its merge-risk block follows; Blocked when one is
    missing."""
    method, risk = pr_skill(), pr_risk_skill()
    if not method:
        raise Blocked("the pr skill is missing: the PR body follows it; install it with "
                      "`npx skills add mattpocock/skills --skill pr`")
    if not risk:
        raise Blocked(f"the pr-risk skill is missing: the PR body carries its merge-risk block; install it with "
                      f"{PR_RISK_INSTALL}")
    return method, risk


def _writer_body(run: Run, final: dict, tickets: list[Ticket], tip: str) -> str:
    """What the pr-writer wrote for the pushed tip `tip` with the method of the pr skill, its merge-risk block
    validated. A body written for that tip is kept when its merge-risk block still validates, so a PR that failed to
    open costs no second agent; once the PR is open, a body written for any tip is kept, until a ticket lands after
    the tidy and its work is in no word of it."""
    if kept := _kept(final, tip):
        return kept
    method, risk = _body_skills()
    values = {**final_values(run), "start_sha": tip, "range": f"{final['onto']}..{tip}",
              "gate": driver_final_gate.evidence(run, tip), "pr_skill": str(method),
              "pr_risk_skill": str(risk), "risk_errors": "", "facts": _facts(run, final, tickets, tip)}
    WRITER.write_text(_run_pr_writer(run, tip, risk, values).strip() + "\n")
    run.state.update(lambda state: state["run"]["final"].update(body=tip))
    return WRITER.read_text()


def _kept(final: dict, tip: str) -> str:
    """The body the pr-writer wrote that `_writer_body` keeps, when its merge-risk block still validates; "" else."""
    risk = pr_risk_skill()
    if not (_written(final, tip) and WRITER.is_file() and risk):
        return ""
    body = WRITER.read_text()
    return "" if _risk_errors(risk, body) else body


def _written(final: dict, tip: str) -> bool:
    """The pr-writer's body is for `tip`, or, once the PR is open, for any tip since no ticket landed after the tidy."""
    return final["body"] == tip or bool(final["pr"] and final["body"])


def _gh(argv: list[str], what: str) -> str:
    """Run `gh` and return the URL it prints last."""
    made = subprocess.run(["gh", *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if made.returncode:
        raise Blocked(f"gh {what} failed: {(made.stderr or made.stdout).strip()}")
    return (made.stdout.split() or [""])[-1]


def _draft(final: dict, questions: list[dict], left: list[str], tip: str) -> str:
    """Why the PR of `tip` opens as draft; "" for a PR ready for review: the end-of-run stops that a `ship-draft` ruling
    ships with, the tickets `left` out, the findings still open that the draft or an agent's ruling ships, and a full
    gate red only as its base is, which a match of failure lines does not prove (driver_final_gate)."""
    reasons = []
    if final["ship"]:
        reasons.append(f"the end of run stopped: {'; '.join(stop['subject'] for stop in final['ship']['stops'])}")
    if left:
        reasons.append(f"tickets are left out of the run: {', '.join(left)}")
    if shipped := _shipped_open(final, questions):
        reasons.append(f"a finding ships open: {'; '.join(shipped)}")
    if preexisting := driver_final_gate.preexisting(final, tip):
        reasons.append(preexisting)
    return "; ".join(reasons)


def _shipped_open(final: dict, questions: list[dict]) -> list[str]:
    """The titles of the findings still open that make a draft: all of them under a `ship-draft` ruling, else those an
    agent's ruling shipped; the operator's own `accept` or `follow-up` keeps the PR ready."""
    ship = final["ship"]
    return [title(one) for one in final["open"] if ship or driver_decisions.agent_shipped(questions, one)]


def _file_follow_ups(run: Run) -> None:
    """File each finding ruled `follow-up` as an issue, once: the PR body then links it. The issue carries a marker
    of the run and the question, so a run that stopped after `gh` filed it and before state.json kept its URL finds it
    again instead of filing it twice."""
    state = run.state.read()["run"]
    for q in driver_decisions.follow_ups(state["questions"]):
        marker = f"implement-spec follow-up {state['revision']}/{q['id']}"
        body = (f"A finding the final review of {run.pr} left open, shipped with its PR by this ruling:\n\n"
                f"{q['ruling']}\n\nEvidence: {q['evidence'][0]}\n\n{marker}\n")
        url = (_gh(["issue", "list", "--state", "all", "--search", f'"{marker}" in:body', "--json", "url", "--jq",
                    ".[].url"], "issue list")
               or _gh(["issue", "create", "--title", q["subject"], "--body", body], "issue create"))

        def record(state: dict, ruled: str = q["id"], issue: str = url) -> None:
            next(one for one in state["run"]["questions"] if one["id"] == ruled)["issue"] = issue

        run.state.update(record)
        ledger(f"End of run: the follow-up {q['subject']} is filed as {url}.")


def _body(run: Run, final: dict, tickets: list[Ticket], tip: str) -> str:
    """The PR body for the pushed tip `tip`: the pr-writer's part, then the facts of the run; only the facts when the
    pr-writer is an end-of-run stop that a `ship-draft` ruling ships. The follow-up issues are filed once the
    pr-writer's part is valid, so a run that cannot open its PR files none, and the facts link them."""
    stops = final["ship"]["stops"] if final["ship"] else []
    writer = "" if any(stop["role"] == "pr-writer" for stop in stops) else _writer_body(run, final, tickets, tip)
    _file_follow_ups(run)
    return "\n\n".join(filter(None, [writer.strip(), _facts(run, final, tickets, tip)]))


def _open(run: Run, final: dict, tickets: list[Ticket], tip: str) -> str:
    """Open the PR of the pushed tip `tip`, ready for review or as draft, and return its URL."""
    BODY.write_text(_body(run, final, tickets, tip))
    draft = _draft(final, run.state.read()["run"]["questions"], left_out(run), tip)
    url = _gh(["pr", "create", "--base", final["base"], "--head", run.pr, "--title", _name(run), "--body-file",
               str(BODY), *["--draft"] * bool(draft)], "pr create")
    run.state.update(lambda state: state["run"]["final"].update(pr=url, draft=bool(draft)))
    ledger(f"The PR of {run.pr} into {final['base']} is open{f' as a draft: {draft}' * bool(draft)}: {url}.")
    return url


def _refresh(run: Run, final: dict, tickets: list[Ticket], tip: str) -> None:
    """Write the body of the open PR again once what it says changed: an overturn's work, its ruling, its follow-up
    issue, a ticket that landed after the tidy. The draft state stays. BODY is the body GitHub has: the new one
    replaces it only once `gh pr edit` took it, so a failed edit is tried again by the next run."""
    text = _body(run, final, tickets, tip)
    if BODY.is_file() and BODY.read_text() == text:
        return
    require_open(final)
    pending = BODY.with_name("pr-body.pending.md")
    pending.write_text(text)
    _gh(["pr", "edit", final["pr"], "--body-file", str(pending)], "pr edit")
    pending.replace(BODY)
    ledger(f"The body of the PR {final['pr']} is written again with the facts of the run at {tip[:7]}.")


def pr(run: Run, final: dict, tickets: list[Ticket], tip: str) -> str:
    """The URL of the PR of the pushed tip `tip`: open it, once; once it is open, its body follows the run."""
    if not final["pr"]:
        return _open(run, final, tickets, tip)
    _refresh(run, final, tickets, tip)
    return final["pr"]


def require_open(final: dict) -> None:
    """Once the PR is open, a push or a body goes out only while it is open: a run of an overturn that stopped may
    resume after the PR was merged or closed."""
    if final["pr"] and (shown := state(final["pr"])) != "OPEN":
        raise Blocked(f"the PR {final['pr']} is {shown.lower() or 'not readable with gh'}: nothing goes out to it")


def state(url: str) -> str:
    """The state of the PR at `url` as gh gives it: OPEN, MERGED or CLOSED; "" when gh cannot tell."""
    shown = subprocess.run(["gh", "pr", "view", url, "--json", "state", "--jq", ".state"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
    return shown.stdout.strip() if shown.returncode == 0 else ""
