"""Ticket tw/s4-coverage/08: render dashboard.html from a fixture snapshot (no repo run): `map_fixture.py <out-dir>`.

Ten passing tests over demo/a.py. test_intent_1 and its twins test_twin_1..3 cover lines 1-10 (similarity 1.0);
test_other_1..4 cover lines 1-4 (similarity 0.4 with each of those four: 4 shared of 10 in the union).
test_edge_at covers lines 1-6 (similarity exactly 0.6 with test_intent_1), test_edge_below lines 1-5 (0.5 with it).
Writes the fixture snapshot to <out-dir>/snapshot.json and the page to <out-dir>/dashboard.html.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weigh_dashboard import write_dashboard
from weigh_rules import load
from weigh_score import feedback_values, score

WIDE = list(range(1, 11))
NARROW = list(range(1, 5))
COVERAGE = {
    "tests/test_intent.py::test_intent_1": WIDE,
    "tests/test_twin.py::test_twin_1": WIDE,
    "tests/test_twin.py::test_twin_2": WIDE,
    "tests/test_twin.py::test_twin_3": WIDE,
    "tests/test_other.py::test_other_1": NARROW,
    "tests/test_other.py::test_other_2": NARROW,
    "tests/test_other.py::test_other_3": NARROW,
    "tests/test_other.py::test_other_4": NARROW,
    "tests/test_edge.py::test_edge_at": list(range(1, 7)),
    "tests/test_edge.py::test_edge_below": list(range(1, 6)),
}


def build(out):
    rules = load()
    tests = {n: {"outcome": "passed", "duration": 0.05 * (i + 1), "coverage": {"demo/a.py": lines}, "unique_coverage": 0, "flaky": False}
             for i, (n, lines) in enumerate(COVERAGE.items())}
    metrics, pillars = score(rules, {**feedback_values(tests, 1.0), "no_unique_coverage": 100.0})
    pairs = [{"tests": [a, b], "jaccard": 1.0} for i, a in enumerate(list(COVERAGE)[:4]) for b in list(COVERAGE)[i + 1:4]]
    pairs += [{"tests": [a, b], "jaccard": 1.0} for i, a in enumerate(list(COVERAGE)[4:]) for b in list(COVERAGE)[4:][i + 1:]]
    data = {"commit": "abc1234", "dirty": False, "inventory": {"tests": 10, "cases": 10, "test_files": 3, "test_lines": 24, "source_lines": 10, "runtime_seconds": 1.0},
            "metrics": metrics, "pillars": pillars, "tests": tests, "overlaps": pairs}
    out.mkdir(parents=True, exist_ok=True)
    (out / "snapshot.json").write_text(json.dumps(data))
    write_dashboard(out, data, rules)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
