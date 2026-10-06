"""Pure ranking engine for `backlog next`. Stdlib only — no third-party deps."""
import json
import os
import re
import subprocess
import sys

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
    """Join Project field values (item-list) with issue bodies/state (issue list).

    Join key is the issue number when the token can expand item content; when
    content is redacted (project-scope token + private repo), fall back to the
    title — GitHub syncs linked-item titles to issue titles. Ambiguous titles
    (duplicates) and titles matching no issue are skipped, as are drafts.
    """
    bodies = {row["number"]: row for row in issue_rows}
    by_title, dup_titles = {}, set()
    for row in issue_rows:
        title = row.get("title")
        if title in by_title:
            dup_titles.add(title)
        elif title is not None:
            by_title[title] = row
    merged = []
    for item in project_items:
        content = item.get("content") or {}
        number = content.get("number")
        if number is None:
            row = by_title.get(item.get("title"))
            if row is None or item.get("title") in dup_titles:
                continue  # draft, board-only, or ambiguous — not joinable
            number = row["number"]
            content = {"title": row.get("title", ""), "url": row.get("url", "")}
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
            "labels": [l["name"] for l in row.get("labels") or []],
            "boost": (lambda v: 0 if v is None else v)(_as_int(item.get("boost"))),
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
        -issue.get("boost", 0),          # band first: higher boost above everything
        0 if value is not None else 1,   # scored issues first within a band
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


def to_json_rows(result):
    """Flat machine-readable rows for `--json` consumers (e.g. dispatchers):
    available issues in rank order, then blocked, then in-progress."""
    rows = []
    for issue, blocked in (
        [(i, False) for i in result["available"]]
        + [(i, True) for i in result["blocked"]]
        + [(i, False) for i in result["in_progress"]]
    ):
        rows.append({
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["url"],
            "status": issue.get("status"),
            "labels": issue.get("labels", []),
            "blocked": blocked,
            "score": score(issue),
            "boost": issue.get("boost", 0),
        })
    return rows


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
        boost = issue.get("boost", 0)
        if boost > 0:
            flag += f"  ↑{boost}"
        elif boost < 0:
            flag += f"  ↓{-boost}"
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


def _gh_json(args, env=None):
    try:
        completed = subprocess.run(args, capture_output=True, text=True,
                                   check=True, env=env)
    except subprocess.CalledProcessError as error:
        # gh's own message (rate limit, auth, scope) is the diagnosis; a
        # traceback quoting the whole query buries it.
        sys.exit(f"gh {' '.join(args[1:3])} failed: "
                 f"{(error.stderr or '').strip() or error}")
    return json.loads(completed.stdout)


def _project_env():
    """User-owned Projects v2 are invisible to fine-grained PATs, so when
    GH_PROJECT_TOKEN is set, `gh project` calls run with GH_TOKEN swapped to
    it; every other gh call keeps the stored auth (which can read the repo)."""
    token = os.environ.get("GH_PROJECT_TOKEN")
    return {**os.environ, "GH_TOKEN": token} if token else None


# One page of board items with only what ranking reads. `gh project item-list`
# asks for every field and several nested connections per item, which costs
# on the order of 100 rate-limit points per call on a ~100-item board; this
# costs about 1 per page. Aliases mirror the item-list JSON keys so
# merge_sources takes either shape.
_BOARD_QUERY = """
query($project: ID!, $cursor: String) {
  rateLimit { remaining }
  node(id: $project) { ... on ProjectV2 { items(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      content { ... on Issue { number title url } }
      title: fieldValueByName(name: "Title") { ... on ProjectV2ItemFieldTextValue { text } }
      status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
      area: fieldValueByName(name: "Area") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
      impact: fieldValueByName(name: "Impact") { ... on ProjectV2ItemFieldNumberValue { number } }
      effort: fieldValueByName(name: "Effort") { ... on ProjectV2ItemFieldNumberValue { number } }
      boost: fieldValueByName(name: "Boost") { ... on ProjectV2ItemFieldNumberValue { number } }
    }
  } } }
}
"""
LOW_BUDGET = 500  # GraphQL points left (of 5000/h) below which main() warns


def _field(node, alias):
    value = node.get(alias) or {}
    return next(iter(value.values()), None)


def fetch_board(project_id):
    """Every board item as item-list-shaped rows, plus the GraphQL budget left."""
    items, cursor, remaining = [], None, None
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={_BOARD_QUERY}",
                "-f", f"project={project_id}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        data = _gh_json(args, env=_project_env())["data"]
        remaining = data["rateLimit"]["remaining"]
        page = data["node"]["items"]
        for node in page["nodes"]:
            items.append({
                "id": node["id"], "content": node.get("content") or {},
                **{alias: _field(node, alias) for alias in
                   ("title", "status", "area", "impact", "effort", "boost")},
            })
        if not page["pageInfo"]["hasNextPage"]:
            return items, remaining
        cursor = page["pageInfo"]["endCursor"]


def main(project_id, repo, as_json=False):
    project_items, remaining = fetch_board(project_id)
    if remaining < LOW_BUDGET:
        print(f"warning: GitHub GraphQL budget is low ({remaining} points left "
              f"this hour)", file=sys.stderr)
    issue_rows = _gh_json([
        "gh", "issue", "list", "--repo", repo, "--state", "all",
        "--limit", "500", "--json", "number,title,url,body,state,labels",
    ])
    issues = merge_sources(project_items, issue_rows)
    result = rank_issues(issues)
    if as_json:
        print(json.dumps(to_json_rows(result)))
    else:
        print(render(result))


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
    main(meta["projectId"], meta["repo"], as_json="--json" in sys.argv[1:])
