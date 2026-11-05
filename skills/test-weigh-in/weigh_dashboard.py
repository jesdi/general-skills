"""Render dashboard.html from the template and print the chat summary."""
import json
from pathlib import Path

from weigh_coverage import left_out, similarity_map, valued, value_kind
from weigh_score import VALUE_METRICS, display
from weigh_smells import PILLAR_SMELLS, pillar_smells, worst_tests

TEMPLATE = Path(__file__).resolve().parent / "dashboard.html"


def inventory_line(inv):
    return (f"{inv['tests']} tests ({inv['cases']} cases) in {inv['test_files']} files, "
            f"{inv['test_lines']} test lines, {inv['source_lines']} source lines, {inv['runtime_seconds']:.1f} s")


def _failing(tests):
    return sum(value_kind(t) == "failing" for t in tests.values())


def _failing_names(tests):
    return [n for n, t in tests.items() if value_kind(t) == "failing"]


def _flaky(tests):
    # every flaky test, a failing one too: this warns about flakiness, left_out about the value a test gives
    return sum(t.get("flaky", False) for t in tests.values())


def _tests(n, what):
    return f"{n} {'test' if n == 1 else 'tests'} {what}"


def left_out_line(kinds):
    """Every kind with its count, e.g. "3 tests failing, 4 skipped", for the Metrics tab."""
    return ", ".join([_tests(*kinds[0]), *(f"{n} {kind}" for n, kind in kinds[1:])])


def _names(words):
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def pillar_status(data, rules):
    """{pillar key: status}, the words for how complete each pillar's score is, said the same in the chat summary and
    on the dashboard:
      head     "partial:", or "all <n> metrics measured" for a pillar that is not partial
      reasons  why it is partial, [] when it is not: how many metrics were measured and the tests a red suite leaves out;
               short enough for two lines of a 150 px gauge, so one kind of test is named and several summed as not green
      note     with a red suite, the sentence for the Metrics tab naming the pillar's value metrics and every kind left
               out; None otherwise"""
    kinds = left_out(data["tests"])
    status = {}
    for key, pillar in rules["pillars"].items():
        snap, total = data["pillars"][key], len(pillar["metrics"])
        measured = sum(i in data["metrics"] for i in pillar["metrics"])
        reasons, note = [], None
        if snap["red_suite"]:
            red = left_out_line(kinds) if len(kinds) == 1 else _tests(sum(n for n, _ in kinds), "not green")
            reasons = [f"{measured} of {total} measured", red] if measured < total else [red]
            value = [m["name"] for i, m in pillar["metrics"].items() if i in VALUE_METRICS]
            note = (f"Partial: {_names(value)} {'counts' if len(value) == 1 else 'count'} only the tests that passed in both "
                    "the plain and the coverage run. "
                    f"Left out: {left_out_line(kinds)}.")
        elif snap["partial"]:
            reasons = [f"{measured} of {total} metrics measured"]
        status[key] = {"head": "partial:" if reasons else f"all {total} metrics measured", "reasons": reasons, "note": note}
    return status


def _slow(rules):
    """A test is slow when it takes longer than the target of the p95 test duration."""
    return rules["pillars"]["feedback"]["metrics"]["p95_duration"]["target"]


def write_dashboard(home, data, rules):
    failing, status = _failing(data["tests"]), pillar_status(data, rules)
    page = {
        "banner": f"{failing} tests are failing" if failing > 1 else "1 test is failing" if failing else None,
        "failing": _failing_names(data["tests"]),
        "skipped": [n for n, t in data["tests"].items() if value_kind(t) == "skipped"],
        "insufficient": [n for n, t in data["tests"].items() if t.get("insufficient_evidence")],
        "overlaps": data.get("overlaps", []),
        "map": [{**d, "runtime": data["tests"][d["test"]]["duration"], "unique": data["tests"][d["test"]].get("unique_coverage", 0)}
                for d in similarity_map(valued(data["tests"]))],
        "inventory": inventory_line(data["inventory"]),
        "pillars": [
            {"key": k, "name": p["name"], **data["pillars"][k], "status": status[k],
             "metrics": [{"name": m["name"], "unit": m["unit"], "target": m["target"], "bad": m["bad"], **data["metrics"][i]}
                         for i, m in p["metrics"].items() if i in data["metrics"]],
             # an entry the snapshot does not record (older data) has no known cause
             "missing": [{"name": m["name"], "reason": data.get("unmeasured", {}).get(i)} for i, m in p["metrics"].items() if i not in data["metrics"]],
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
    status = pillar_status(data, rules)
    for k, p in rules["pillars"].items():
        s, st = data["pillars"][k], status[k]
        shown = "not measured" if s["score"] is None else str(display(s["score"]))
        lines.append(f"{p['name']}: {shown}" + (f" ({st['head']} {'; '.join(st['reasons'])})" if st["reasons"] else ""))
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
