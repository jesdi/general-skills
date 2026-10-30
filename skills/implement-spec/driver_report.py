"""Report validation: validate(role, path, context) -> list of errors, empty when the report is accepted.

context: repo (slot path), branch, start (HEAD when the role started), log, correction (a test-writer corrects locked
  tests), findings (the titles a correction gets), owners (ticket numbers, for the final fix batch), locked (the
  locked-file check, a driver_locked.Locked, or None), fix (the titles of the findings a fixer gets), red (the red gate
  a fix must make green, as `the <tier> gate is red on <sha7>`), disputed (the test ids an arbitration rules on), rejected
  (errors the driver found when the agent ended, for a done report)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import jsonschema

import driver_locked

PROMPTS = Path(__file__).resolve().parent / "prompts"
RED = "red line not found in log"
CONTRADICTS = "locked test contradicts spec:"
DISPUTING = ("implementer", "fixer")  # the roles whose BLOCKED report can dispute a locked test


def schema(role: str) -> dict:
    return json.loads(re.search(r"```json\n(.*?)\n```", (PROMPTS / f"{role}.md").read_text(), re.S).group(1))


def _git(repo: str, *args: str, end: str = "\n") -> list[str]:
    """The lines of a git answer. For a list of paths, pass `-z` and `end="\\0"`: git quotes no path there."""
    out = subprocess.run(["git", "-C", repo, "-c", "core.quotePath=false", *args], capture_output=True, text=True).stdout
    return [line for line in out.split(end) if line]


def _squash(text: str) -> str:
    """Each run of whitespace other than a line break as one space."""
    return re.sub(r"[^\S\n]+", " ", text)


def _seen(line: str, log: str) -> bool:
    # A stream-json log holds tool output as JSON strings, so the escaped forms of the line count too: json.dumps
    # escapes non-ASCII, the claude log keeps it raw. Spacing does not count: pytest pads some lines.
    line, log = _squash(line.strip()), _squash(log)
    return any(form in log for form in (line, json.dumps(line)[1:-1], json.dumps(line, ensure_ascii=False)[1:-1]))


def _shas(report: dict) -> list[str]:
    """`commits` as SHAs: the fix batch of the final review gives each commit with its ticket. A read-only role has
    no `commits` (issue #129)."""
    return [commit["sha"] if isinstance(commit, dict) else commit for commit in report.get("commits", [])]


def _owner_errors(report: dict, context: dict) -> list[str]:
    """The fix batch of the final review: each new commit on the branch is in the report with a ticket of the run."""
    if "owners" not in context:
        return []
    given, branch = _shas(report), context["branch"]
    new = _git(context["repo"], "rev-list", f"{context['start']}..{branch}")
    errors = [f"commit {sha[:7]} is on {branch} and not in `commits`: each commit of the batch needs its ticket"
              for sha in new if not any(sha.startswith(one) for one in given)]
    return errors + [f"commit {commit['sha'][:7]}: {commit['ticket']} is not a ticket of this run"
                     for commit in report["commits"] if commit["ticket"] not in context["owners"]]


def _must_fix_errors(report: dict, context: dict) -> list[str]:
    """A final fixer must account for every finding carried from a capped ticket."""
    if report["status"] not in ("DONE", "DONE_WITH_CONCERNS"):
        return []
    return [f"addressed_must_fix: missing {title}" for title in context.get("must_fix", [])
            if title not in report.get("addressed_must_fix", [])]


def _rejected_errors(report: dict, context: dict) -> list[str]:
    """What the driver found wrong when the agent ended, for a report that says the agent is done."""
    return context.get("rejected", []) if report.get("status") in ("DONE", "DONE_WITH_CONCERNS") else []


def _commit_errors(report: dict, context: dict) -> list[str]:
    """Every commit is new since the role started and on the branch; every locked path is in such a commit and is a
    file at the branch tip: a deleted file holds no test that a review could prove (issue #136)."""
    repo, branch, commits = context["repo"], context["branch"], _shas(report)
    new = _git(repo, "rev-list", f"{context['start']}..{branch}")
    return [f"commit {sha[:7]} is not a new commit on {branch}"
            for sha in commits if not any(n.startswith(sha) for n in new)] + _locked_path_errors(report, context)


def _locked_path_errors(report: dict, context: dict) -> list[str]:
    """Every locked path is in a commit of the role and is a file at the branch tip."""
    repo, branch, locked = context["repo"], context["branch"], report.get("locked", [])
    if not locked:
        return []
    changed = _git(repo, "log", "--name-only", "-z", "--format=", f"{context['start']}..{branch}", end="\0")
    present = driver_locked.present(repo, branch, locked)
    return [f"locked path {path} is not in the role's commits" for path in locked if path not in changed] + [
        f"locked path {path} is not a file at the tip of {branch}: list in `locked` only files you keep"
        for path in locked if path in changed and path not in present]


def _unchanged_errors(role: str, report: dict, context: dict) -> list[str]:
    """A DONE implementer or fixer with no commit (issue #123): the branch did not move, no change is left
    uncommitted, the gate of the start is green, and a concern says why no change is needed: for a fixer, one
    concern names each finding."""
    done = report["status"] in ("DONE", "DONE_WITH_CONCERNS")
    if role not in ("implementer", "fixer") or report["commits"] or not done:
        return []
    titles = context.get("fix", [])
    errors = _moved_errors(context) + [
        f"no commit, and no concern names the finding {title}: give each finding a concern that says why it needs no "
        "change" for title in titles if not any(title in concern for concern in report["concerns"])]
    if not (titles or report["concerns"]):
        errors.append("no commit, and no concern: say in `concerns` why the start needs no change")
    return errors


def _moved_errors(context: dict) -> list[str]:
    """What a report with no commit leaves out: a commit on the branch, a change in the tree, a red gate."""
    repo, branch = context["repo"], context["branch"]
    errors = [f"commit {sha[:7]} is on {branch} and not in `commits`: list each commit you made"
              for sha in _git(repo, "rev-list", f"{context['start']}..{branch}")]
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        errors.append("no commit, and the tree has uncommitted changes: commit the fix and list the commit")
    if context.get("red"):
        errors.append(f"no commit, and {context['red']}: a fix of a red gate is a commit")
    return errors


def _correction_errors(report: dict, context: dict) -> list[str]:
    """A correction changes only the files it lists in `locked`."""
    if not context.get("correction"):
        return []
    changed = _git(context["repo"], "diff", "--name-only", "-z", context["start"], context["branch"], end="\0")
    more = [path for path in changed if path not in report.get("locked", [])]
    errors = [f"a correction may change only the files it lists in `locked`: its commits also change {', '.join(more)}"
              ] if more else []
    return errors + [f"not_addressed: {item['finding']} is not the title of a finding of this correction"
                     for item in report.get("not_addressed", []) if item["finding"] not in context.get("findings", [])]


def _red_errors(report: dict, context: dict) -> list[str]:
    """Every red line that a test-writer or a correction claims is in the agent's log."""
    lines = [(r["test"], r["line"]) for r in report.get("red", [])]
    log = Path(context["log"]).read_text(errors="replace") if lines else ""
    return [f"{RED}: {test}: {line if len(line) <= 200 else line[:197] + '...'}"
            for test, line in lines if not _seen(line, log)]


def _space_errors(report: dict) -> list[str]:
    """No locked path holds whitespace: the fast gate gets the locked paths as one space-separated list."""
    return [f"locked path {path!r} contains whitespace: the fast gate gets the locked paths separated by spaces, so "
            "rename the file" for path in report.get("locked", []) if re.search(r"\s", path)]


def _locked_file(name: str, locked: list[str]) -> str:
    """The locked file that `name` names: itself, or the one locked path that ends with it; "" for none."""
    if name in locked:
        return name
    found = [path for path in locked if path.endswith("/" + name)]
    return found[0] if len(found) == 1 else ""


class Dispute(NamedTuple):
    """One concern `locked test contradicts spec: <file>::<test>: <reason>` of a BLOCKED implementer or fixer."""
    file: str  # the locked file it names; "" when it names no file of the ticket's `locked`
    test: str
    reason: str


def disputes(role: str, report: dict, locked: list[str]) -> list[Dispute]:
    """The disputes of a BLOCKED implementer or fixer report, each file resolved against `locked`."""
    if role not in DISPUTING or report.get("status") != "BLOCKED":
        return []
    found = []
    for concern in report["concerns"]:
        if concern.startswith(CONTRADICTS):
            test, _, reason = concern[len(CONTRADICTS):].strip().partition(": ")
            found.append(Dispute(_locked_file(test.partition("::")[0], locked), test, reason))
    return found


def _dispute_errors(role: str, report: dict, context: dict) -> list[str]:
    """A dispute names a locked file of the ticket, and leaves no uncommitted change: the arbitration is read-only
    and the correction commits."""
    locked = context.get("locked")
    found = disputes(role, report, locked.own if locked else [])
    errors = [f"the dispute of {dispute.test or 'no test'} names no locked file of the ticket: write it as "
              f"`{CONTRADICTS} <locked file>::<test>: <reason>`" for dispute in found if not dispute.file]
    if found and _git(context["repo"], "status", "--porcelain"):
        errors.append("a dispute leaves no uncommitted change: commit your work, then report the dispute")
    return errors


def _ruling_errors(report: dict, context: dict) -> list[str]:
    """An arbitration rules on each disputed test."""
    given = {ruling["test"] for ruling in report.get("rulings", [])}
    return [f"rulings: no ruling on {test}: give one ruling per disputed test, its id as the list gives it"
            for test in context.get("disputed", []) if test not in given]


def locked_files(listed: list[str]) -> list[str]:
    """The files of a `locked` list: a test node id `<file>::<test>` names its file. In order, each file once."""
    return list(dict.fromkeys(entry.partition("::")[0] for entry in listed))


def validate(role: str, path: str, context: dict) -> list[str]:
    try:
        report = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return [f"report {path} is not readable JSON: {exc}"]
    rules = schema(role)
    found = sorted(jsonschema.Draft202012Validator(rules).iter_errors(report), key=lambda e: str(list(e.path)))
    if found:
        return [f"{'.'.join(map(str, e.path)) or 'report'}: {e.message}" for e in found]
    # The schema types only the role's own fields: any other field of the report is dropped.
    report = {name: value for name, value in report.items() if name in rules["properties"]}
    if "locked" in report:
        report["locked"] = locked_files(report["locked"])
    return (_rejected_errors(report, context) + _space_errors(report) + _commit_errors(report, context)
            + _owner_errors(report, context) + _must_fix_errors(report, context)
            + _unchanged_errors(role, report, context) + _correction_errors(report, context)
            + driver_locked.errors(role, report, context) + _red_errors(report, context)
            + _dispute_errors(role, report, context) + _ruling_errors(report, context))
