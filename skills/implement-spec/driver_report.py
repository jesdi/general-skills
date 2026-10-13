"""Report validation: one agent report in, an error list out. Stdlib plus jsonschema.

validate(role, path, context) -> list of str, empty when the report is accepted.
  context: repo (slot path), branch, start (HEAD sha when the role started), log (the agent's log file),
           round (fix-loop round, default 0).
The schema of a role is the json block of its prompt template, so the agent reads the text the driver enforces.
"""
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


def _git(repo: str, *args: str) -> list[str]:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout.splitlines()


def _seen(line: str, log: str) -> bool:
    # A stream-json log holds tool output as JSON strings, so the escaped form of the line counts too.
    return line in log or json.dumps(line)[1:-1] in log


def _commit_errors(report: dict, context: dict) -> list[str]:
    """Every commit is new since the role started and on the branch; every locked path is in those commits."""
    repo, branch, commits = context["repo"], context["branch"], report["commits"]
    new = _git(repo, "rev-list", f"{context['start']}..{branch}")
    errors = [f"commit {sha[:7]} is not a new commit on {branch}"
              for sha in commits if not any(n.startswith(sha) for n in new)]
    if locked := report.get("locked"):
        changed = _git(repo, "show", "--name-only", "--format=", *commits)
        errors += [f"locked path {path} is not in the report's commits" for path in locked if path not in changed]
    return errors


def _proof_errors(report: dict, context: dict) -> list[str]:
    """Every claimed red line (a test-writer `red`, a reviewer `mutations`) is in the agent's log."""
    lines = [r["line"] for r in report.get("red", [])] + [m["red"] for m in report.get("mutations", [])]
    errors = []
    if lines and not all(_seen(line, Path(context["log"]).read_text(errors="replace")) for line in lines):
        errors.append(RED)
    if context.get("round") == 1 and "findings" in report and "mutations" not in report:
        errors.append("mutations: required in round 1")
    return errors


def validate(role: str, path: str, context: dict) -> list[str]:
    try:
        report = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return [f"report {path} is not readable JSON: {exc}"]
    rules = schema(role)
    found = sorted(jsonschema.Draft202012Validator(rules).iter_errors(report), key=lambda e: str(list(e.path)))
    if found:
        return [f"{'.'.join(map(str, e.path)) or 'report'}: {e.message}" for e in found]
    # Only the role's own fields are typed by its schema: any other field of the report is ignored.
    report = {name: value for name, value in report.items() if name in rules["properties"]}
    # Reports are a trust boundary: git and the log must show what the report claims.
    return _commit_errors(report, context) + _proof_errors(report, context)
