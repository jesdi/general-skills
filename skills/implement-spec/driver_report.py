"""Report validation: validate(role, path, context) -> list of errors, empty when the report is accepted.

context: repo (slot path), branch, start (HEAD when the role started), log, round (1 = first review), locked,
  correction (a test-writer corrects locked tests), restored (locked files the driver put back before it),
  owners (ticket numbers, for the final fix batch), merged (locked files of merged tickets, not its own), pr (tip)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import jsonschema

PROMPTS = Path(__file__).resolve().parent / "prompts"
RED = "red line not found in log"


def schema(role: str) -> dict:
    return json.loads(re.search(r"```json\n(.*?)\n```", (PROMPTS / f"{role}.md").read_text(), re.S).group(1))


def _git(repo: str, *args: str, end: str = "\n") -> list[str]:
    """The lines of a git answer. For a list of paths, pass `-z` and `end="\\0"`: git quotes no path there."""
    out = subprocess.run(["git", "-C", repo, "-c", "core.quotePath=false", *args], capture_output=True, text=True).stdout
    return [line for line in out.split(end) if line]


def _seen(line: str, log: str) -> bool:
    # A stream-json log holds tool output as JSON strings, so the escaped form of the line counts too.
    return line in log or json.dumps(line)[1:-1] in log


def _shas(report: dict) -> list[str]:
    """`commits` as SHAs: the fix batch of the final review gives each commit with its ticket."""
    return [commit["sha"] if isinstance(commit, dict) else commit for commit in report["commits"]]


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


def _commit_errors(report: dict, context: dict) -> list[str]:
    """Every commit is new since the role started and on the branch; every locked path is in such a commit."""
    repo, branch, commits = context["repo"], context["branch"], _shas(report)
    new = _git(repo, "rev-list", f"{context['start']}..{branch}")
    errors = [f"commit {sha[:7]} is not a new commit on {branch}"
              for sha in commits if not any(n.startswith(sha) for n in new)]
    if locked := report.get("locked"):
        changed = _git(repo, "log", "--name-only", "-z", "--format=", f"{context['start']}..{branch}", end="\0")
        errors += [f"locked path {path} is not in the role's commits" for path in locked if path not in changed]
    return errors


def _correction_errors(report: dict, context: dict) -> list[str]:
    """A correction changes only the files it lists in `locked` and the files the driver `restored`."""
    if not context.get("correction"):
        return []
    changed = _git(context["repo"], "diff", "--name-only", "-z", context["start"], context["branch"], end="\0")
    more = [path for path in changed if path not in report.get("locked", []) + context.get("restored", [])]
    return [f"a correction may change only the files it lists in `locked`: its commits also change {', '.join(more)}"
            ] if more else []


def _merged_errors(report: dict, context: dict) -> list[str]:
    """A test-writer changes a locked file of a merged ticket only when it lists the file in `locked`."""
    def blob(commit: str, path: str) -> list[str]:
        return _git(context["repo"], "rev-parse", "-q", "--verify", f"{commit}:{path}")

    return [f"{path} is a locked file of a merged ticket, and `locked` does not list it: list it in `locked`, or "
            "leave the file as it was" for path in context.get("merged", []) if path not in report.get("locked", [])
            and blob(context["branch"], path) not in (blob(context["start"], path), blob(context["pr"], path))]


def _proof_errors(report: dict, context: dict) -> list[str]:
    """Every claimed red line (a test-writer `red`, a reviewer `mutations`) is in the agent's log."""
    lines = [r["line"] for r in report.get("red", [])] + [m["red"] for m in report.get("mutations", [])]
    errors = []
    if lines and not all(_seen(line, Path(context["log"]).read_text(errors="replace")) for line in lines):
        errors.append(RED)
    if context.get("round") == 1 and "findings" in report and "mutations" not in report:
        errors.append("mutations: required in round 1")
    return errors


def _locked_errors(report: dict, context: dict) -> list[str]:
    """In the first review, each locked test file has a mutation proof (`mutations[].test` is `<file>::<test>`)."""
    if context.get("round") != 1 or "findings" not in report:
        return []
    tests = [proof["test"] for proof in report.get("mutations", [])]
    return [f"mutations: no mutation proof for the locked test file {path}" for path in context.get("locked", [])
            if not any(test.startswith(path) for test in tests)]


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
    return (_commit_errors(report, context) + _owner_errors(report, context) + _correction_errors(report, context)
            + _merged_errors(report, context) + _proof_errors(report, context) + _locked_errors(report, context))
