"""Pure ranking engine for `backlog next`. Stdlib only — no third-party deps."""
import json
import os
import re
import subprocess

_BLOCKED_LINE = re.compile(r"^\s*Blocked by:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_ISSUE_REF = re.compile(r"#(\d+)")


def parse_blocked_by(body):
    """Return sorted, unique issue numbers from every `Blocked by: #a, #b` line."""
    if not body:
        return []
    nums = set()
    for line in _BLOCKED_LINE.finditer(body):
        for ref in _ISSUE_REF.findall(line.group(1)):
            nums.add(int(ref))
    return sorted(nums)


def _as_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def merge_sources(project_items, issue_rows):
    """Join Project field values (item-list) with issue bodies/state (issue list)."""
    bodies = {row["number"]: row for row in issue_rows}
    merged = []
    for item in project_items:
        content = item.get("content") or {}
        number = content.get("number")
        if number is None:
            continue  # draft item (no linked issue) — not part of the work graph
        row = bodies.get(number, {})
        merged.append({
            "number": number,
            "title": content.get("title", ""),
            "url": content.get("url", ""),
            "state": (row.get("state") or "OPEN").upper(),
            "body": row.get("body") or "",
            "status": item.get("status"),
            "impact": _as_int(item.get("impact")),
            "effort": _as_int(item.get("effort")),
            "area": item.get("area"),
        })
    return merged


def _blocker_satisfied(blocker):
    return blocker["state"] == "CLOSED" or blocker.get("status") == "Done"


def blockers_of(issue, by_number):
    """Unsatisfied blocker numbers. Unknown blockers are dropped (mistyped-# tolerance)."""
    waiting = []
    for number in parse_blocked_by(issue["body"]):
        blocker = by_number.get(number)
        if blocker is None:
            continue  # dropped edge
        if not _blocker_satisfied(blocker):
            waiting.append(number)
    return waiting


def is_available(issue, by_number):
    return not blockers_of(issue, by_number)


def score(issue):
    impact, effort = issue.get("impact"), issue.get("effort")
    if impact is None or effort is None or effort == 0:
        return None
    return impact / effort


def _sort_key(issue):
    value = score(issue)
    return (
        0 if value is not None else 1,   # scored issues first
        -(value or 0.0),                 # higher score first
        -(issue.get("impact") or 0),     # tiebreak: higher impact
        issue["number"],                 # stable, deterministic
    )


def rank_issues(issues):
    by_number = {i["number"]: i for i in issues}
    candidates = [
        i for i in issues
        if i["state"] != "CLOSED" and i.get("status") != "Done"
    ]
    in_progress = [i for i in candidates if i.get("status") == "In progress"]
    actionable = [i for i in candidates if i.get("status") != "In progress"]
    available, blocked = [], []
    for issue in actionable:
        (available if is_available(issue, by_number) else blocked).append(issue)
    available.sort(key=_sort_key)
    blocked.sort(key=lambda i: i["number"])
    in_progress.sort(key=lambda i: i["number"])
    return {
        "available": available,
        "blocked": blocked,
        "in_progress": in_progress,
        "by_number": by_number,
    }


def render(result):
    lines = []
    if result["in_progress"]:
        lines.append("In progress (claimed):")
        for issue in result["in_progress"]:
            lines.append(f"  #{issue['number']} {issue['title']}")
        lines.append("")
    lines += [
        "Rank  Score  Issue                                   Area",
        "----  -----  --------------------------------------  ----------",
    ]
    for idx, issue in enumerate(result["available"], start=1):
        value = score(issue)
        score_str = f"{value:.2f}" if value is not None else "  — "
        flag = "" if value is not None else "  [needs triage]"
        label = f"#{issue['number']} {issue['title']}"[:38].ljust(38)
        area = (issue.get("area") or "-")
        lines.append(f"{idx:>4}  {score_str:>5}  {label}  {area:<10}{flag}")

    if result["blocked"]:
        lines.append("")
        lines.append("Blocked (not actionable yet):")
        for issue in result["blocked"]:
            waits = ", ".join(f"#{n}" for n in blockers_of(issue, result["by_number"]))
            lines.append(f"  #{issue['number']} {issue['title']}  — waiting on {waits}")
    return "\n".join(lines)


def _gh_json(args):
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def main(owner, project_number, repo):
    payload = _gh_json([
        "gh", "project", "item-list", str(project_number),
        "--owner", owner, "--format", "json", "--limit", "200",
    ])
    project_items = payload.get("items", [])
    issue_rows = _gh_json([
        "gh", "issue", "list", "--repo", repo, "--state", "all",
        "--limit", "500", "--json", "number,body,state",
    ])
    issues = merge_sources(project_items, issue_rows)
    print(render(rank_issues(issues)))


def find_project_meta(start=None):
    """Walk up from `start` (default cwd) to the nearest `.backlog/project-meta.json`.

    Returns its absolute path. Raises FileNotFoundError with an actionable
    message if no ancestor directory contains one.
    """
    origin = os.path.abspath(start or os.getcwd())
    directory = origin
    while True:
        candidate = os.path.join(directory, ".backlog", "project-meta.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:  # reached the filesystem root
            raise FileNotFoundError(
                f"No .backlog/project-meta.json found walking up from {origin}. "
                "Run `backlog setup` to provision the board and write it."
            )
        directory = parent


if __name__ == "__main__":
    config_path = find_project_meta()
    with open(config_path) as handle:
        meta = json.load(handle)
    main(meta["owner"], meta["projectNumber"], meta["repo"])
