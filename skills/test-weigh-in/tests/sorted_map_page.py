"""Ticket 03 fixture, no repo run: `sorted_map_page.py <out-dir>` writes <out-dir>/dashboard.html through weigh_dashboard.write_dashboard.

PICKED covers lines 1-100. Its neighbours are fed in the order SIM_091 (0.91), SIM_100 (1.00), SIM_095 (0.95).
BELOW covers lines 1-50 (0.50 with PICKED, not eligible); LONELY covers lines 200-210 (similar to nothing).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weigh_dashboard import write_dashboard
from weigh_rules import load
from weigh_score import feedback_values, score

PICKED = "tests/test_pick.py::test_picked"
SIM_091, SIM_100, SIM_095 = (f"tests/test_pick.py::test_sim_{n}" for n in ("091", "100", "095"))
BELOW = "tests/test_pick.py::test_below"
LONELY = "tests/test_pick.py::test_lonely"
COVERAGE = {PICKED: range(1, 101), SIM_091: range(1, 92), SIM_100: range(1, 101), SIM_095: range(1, 96),
            BELOW: range(1, 51), LONELY: range(200, 211)}


def build(out):
    out.mkdir(parents=True, exist_ok=True)
    rules = load()
    tests = {n: {"outcome": "passed", "duration": 0.05, "coverage": {"demo/a.py": list(lines)}, "unique_coverage": 0, "flaky": False, "smells": [], "lines": 2}
             for n, lines in COVERAGE.items()}
    metrics, pillars = score(rules, {**feedback_values(tests, 1.0), "no_unique_coverage": 100.0})
    data = {"commit": "abc1234", "dirty": False, "inventory": {"tests": 6, "cases": 6, "test_files": 1, "test_lines": 12, "source_lines": 210, "runtime_seconds": 1.0},
            "metrics": metrics, "pillars": pillars, "tests": tests, "overlaps": []}
    return write_dashboard(out, data, rules)


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
