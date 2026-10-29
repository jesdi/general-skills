"""Report validation: validate(role, path, context) -> list of errors, empty when the report is accepted.

context: repo (slot path), branch, start (HEAD when the role started), log, full (a full review: no fix range), locked,
  correction (a test-writer corrects locked tests), restored (locked files the driver put back before it),
  restore_red (the gate is red on the commit of that restore), findings (the titles a correction gets),
  owners (ticket numbers, for the final fix batch), merged (locked files of merged tickets, not its own), pr (tip),
  fix (the titles of the findings a fixer gets), red (the red gate a fix must make green, as
  `the <tier> gate is red on <sha7>`)."""
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


def _present(repo: str, commit: str, paths: list[str]) -> list[str]:
    """The `paths` that are files at `commit`."""
    return _git(repo, "ls-tree", "-r", "--name-only", "-z", commit, "--", *paths, end="\0") if paths else []


def _commit_errors(report: dict, context: dict) -> list[str]:
    """Every commit is new since the role started and on the branch; every locked path is in such a commit and is a
    file at the branch tip: a deleted file holds no test that a review could prove (issue #136)."""
    repo, branch, commits = context["repo"], context["branch"], _shas(report)
    new = _git(repo, "rev-list", f"{context['start']}..{branch}")
    errors = [f"commit {sha[:7]} is not a new commit on {branch}"
              for sha in commits if not any(n.startswith(sha) for n in new)]
    if locked := report.get("locked"):
        changed = _git(repo, "log", "--name-only", "-z", "--format=", f"{context['start']}..{branch}", end="\0")
        errors += [f"locked path {path} is not in the role's commits" for path in locked if path not in changed]
        present = _present(repo, branch, locked)
        errors += [f"locked path {path} is not a file at the tip of {branch}: list in `locked` only files you keep"
                   for path in locked if path in changed and path not in present]
    return errors


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
    """A correction changes only the files it lists in `locked` and the files the driver `restored`."""
    if not context.get("correction"):
        return []
    changed = _git(context["repo"], "diff", "--name-only", "-z", context["start"], context["branch"], end="\0")
    more = [path for path in changed if path not in report.get("locked", []) + context.get("restored", [])]
    errors = [f"a correction may change only the files it lists in `locked`: its commits also change {', '.join(more)}"
              ] if more else []
    if context.get("restore_red") and report["status"] in ("DONE", "DONE_WITH_CONCERNS") and not (
            set(report["locked"]) & set(context["restored"])):
        errors.append("the gate is red on the restore commit, so a restored file fails with the code of this ticket: "
                      "update that file and list it in `locked`, or report BLOCKED with a concern that starts with "
                      "`locked test contradicts spec:`")
    return errors + [f"not_addressed: {item['finding']} is not the title of a finding of this correction"
                     for item in report.get("not_addressed", []) if item["finding"] not in context.get("findings", [])]


def _merged_errors(report: dict, context: dict) -> list[str]:
    """A test-writer changes a locked file of a merged ticket only when it lists the file in `locked`."""
    def blob(commit: str, path: str) -> list[str]:
        return _git(context["repo"], "rev-parse", "-q", "--verify", f"{commit}:{path}")

    return [f"{path} is a locked file of a merged ticket, and `locked` does not list it: list it in `locked`, or "
            "leave the file as it was" for path in context.get("merged", []) if path not in report.get("locked", [])
            and blob(context["branch"], path) not in (blob(context["start"], path), blob(context["pr"], path))]


def _proof_errors(report: dict, context: dict) -> list[str]:
    """Every claimed red line (a test-writer `red`, a reviewer `mutations`) is in the agent's log."""
    lines = ([(r["test"], r["line"]) for r in report.get("red", [])]
             + [(m["test"], m["red"]) for m in report.get("mutations", [])])
    log = Path(context["log"]).read_text(errors="replace") if lines else ""
    errors = [f"{RED}: {test}: {line if len(line) <= 200 else line[:197] + '...'}"
              for test, line in lines if not _seen(line, log)]
    if context.get("full") and "findings" in report and "mutations" not in report:
        errors.append("mutations: required in a full review")
    return errors


def _locked_errors(report: dict, context: dict) -> list[str]:
    """In a full review, each locked test file has a mutation proof (`mutations[].test` is `<file>::<test>`).
    `locked` names no fixture of the ticket: a fixture holds no test that a proof could name (#118). A locked file
    that is not at the reviewed tip holds no test either (issue #136)."""
    if not context.get("full") or "findings" not in report:
        return []
    locked = context.get("locked", [])
    tests, present = [proof["test"] for proof in report.get("mutations", [])], _present(context["repo"], context["start"], locked)
    return [f"mutations: no mutation proof for the locked test file {path}" for path in locked
            if path in present and not any(test.startswith(path) for test in tests)]


def _fixture_errors(report: dict) -> list[str]:
    """Each fixture of a test-writer report (a locked file that holds no test) is in its `locked`."""
    return [f"fixtures: {path} is not in `locked`" for path in report.get("fixtures", [])
            if path not in report.get("locked", [])]


def _space_errors(report: dict) -> list[str]:
    """No locked path holds whitespace: the fast gate gets the locked paths as one space-separated list."""
    return [f"locked path {path!r} contains whitespace: the fast gate gets the locked paths separated by spaces, so "
            "rename the file" for path in report.get("locked", []) if re.search(r"\s", path)]


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
    return (_space_errors(report) + _commit_errors(report, context) + _owner_errors(report, context) + _must_fix_errors(report, context)
            + _unchanged_errors(role, report, context) + _correction_errors(report, context)
            + _merged_errors(report, context) + _proof_errors(report, context) + _locked_errors(report, context)
            + _fixture_errors(report))
