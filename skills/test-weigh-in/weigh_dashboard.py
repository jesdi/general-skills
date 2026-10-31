"""Render dashboard.html from the template and print the chat summary."""
import json
from pathlib import Path

from weigh_coverage import similarity_map, valued
from weigh_score import display
from weigh_smells import PILLAR_SMELLS, pillar_smells, worst_tests

TEMPLATE = Path(__file__).resolve().parent / "dashboard.html"


def inventory_line(inv):
    return (f"{inv['tests']} tests ({inv['cases']} cases) in {inv['test_files']} files, "
            f"{inv['test_lines']} test lines, {inv['source_lines']} source lines, {inv['runtime_seconds']:.1f} s")


def _failing(tests):
    return sum(t["outcome"] == "failed" for t in tests.values())


def _flaky(tests):
    return sum(t.get("flaky", False) for t in tests.values())


def _slow(rules):
    """A test is slow when it takes longer than the target of the p95 test duration."""
    return rules["pillars"]["feedback"]["metrics"]["p95_duration"]["target"]


def write_dashboard(home, data, rules):
    failing = _failing(data["tests"])
    page = {
        "banner": f"{failing} tests are failing" if failing > 1 else "1 test is failing" if failing else None,
        "skipped": [n for n, t in data["tests"].items() if t["outcome"] == "skipped"],
        "overlaps": data.get("overlaps", []),
        "map": [{**d, "runtime": data["tests"][d["test"]]["duration"], "unique": data["tests"][d["test"]].get("unique_coverage", 0)}
                for d in similarity_map(valued(data["tests"]))],
        "inventory": inventory_line(data["inventory"]),
        "pillars": [
            {"key": k, "name": p["name"], **data["pillars"][k],
             "metrics": [{"name": m["name"], **data["metrics"][i]} for i, m in p["metrics"].items() if i in data["metrics"]],
             "total": len(p["metrics"]),
             "worst": worst_tests(data["tests"], k, _slow(rules)),
             "smells": pillar_smells(data["tests"], PILLAR_SMELLS.get(k, ()))}
            for k, p in rules["pillars"].items()
        ],
    }
    # "</" and U+2028/9 would end the script block or the literal early
    blob = json.dumps(page).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    path = home / "dashboard.html"
    path.write_text(TEMPLATE.read_text().replace("__DATA__", blob))
    return path


def summary(data, rules, snapshot, dashboard):
    lines = [inventory_line(data["inventory"])]
    for k, p in rules["pillars"].items():
        s = data["pillars"][k]
        shown = "not measured" if s["score"] is None else str(display(s["score"]))
        lines.append(f"{p['name']}: {shown}" + (" (partial)" if s["partial"] else ""))
    failing, flaky = _failing(data["tests"]), _flaky(data["tests"])
    if failing:
        lines.append(f"Warning: {failing} tests are failing" if failing > 1 else "Warning: 1 test is failing")
    if flaky:
        lines.append(f"Warning: {flaky} flaky tests" if flaky > 1 else "Warning: 1 flaky test")
    scored = {k: p["score"] for k, p in data["pillars"].items() if p["score"] is not None}
    if scored:
        lowest = min(scored, key=scored.get)
        worst = worst_tests(data["tests"], lowest, _slow(rules), 3)
        lines += [f"Worst tests ({rules['pillars'][lowest]['name']}):", *(f"  {w['test']} ({w['reason']})" for w in worst)]
    return "\n".join([*lines, f"Snapshot: {snapshot}", f"Dashboard: {dashboard}"])
