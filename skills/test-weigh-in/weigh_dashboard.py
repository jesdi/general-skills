"""Render dashboard.html from the template and print the chat summary."""
import json
from pathlib import Path

from weigh_score import display

TEMPLATE = Path(__file__).resolve().parent / "dashboard.html"


def inventory_line(inv):
    return (f"{inv['tests']} tests ({inv['cases']} cases) in {inv['test_files']} files, "
            f"{inv['test_lines']} test lines, {inv['source_lines']} source lines, {inv['runtime_seconds']:.1f} s")


def write_dashboard(home, data, rules):
    page = {
        "inventory": inventory_line(data["inventory"]),
        "pillars": [
            {"key": k, "name": p["name"], **data["pillars"][k],
             "metrics": [{"name": m["name"], **data["metrics"][i]} for i, m in p["metrics"].items() if i in data["metrics"]],
             "total": len(p["metrics"])}
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
    return "\n".join([*lines, f"Snapshot: {snapshot}", f"Dashboard: {dashboard}"])
