"""Ticket 04 fixture, no repo run: `layout_page.py <out-dir>` writes <out-dir>/dashboard.html through
weigh_dashboard.write_dashboard and prints its path. The real-suite shape of the spec: 2,172 tests (all drawn on the
Map), 3,196 cases, 6,757 overlap pairs with 200-character ids.
Fast feedback is fully measured, Resistance to refactoring has 0 of its 4 metrics measured (reasons recorded).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import overlap_page
from weigh_dashboard import write_dashboard
from weigh_rules import load
from weigh_score import feedback_values, score

FIXTURE = "2172 tests, 3196 cases, 6757 pairs"
NOT_MEASURED = ("brittle_test_files", "private_access", "mock_heavy", "interaction_only")


def build(out):
    out.mkdir(parents=True, exist_ok=True)
    rules = load()
    tests = {}
    for i in range(overlap_page.N_TESTS):
        lo = (i % 300) * 10 + 1
        tests[overlap_page.name(i)] = {"outcome": "passed", "duration": 0.01 + (i % 9) / 100, "coverage": {"src/m.py": list(range(lo, lo + 6 + i % 3))},
                                       "unique_coverage": 0, "flaky": False, "smells": [], "lines": 2}
    values = {**feedback_values(tests, 1.0), "no_unique_coverage": 100.0, "branch_coverage": 80.0, "hollow_tests": 1.0}
    metrics, pillars = score(rules, values)
    data = {"commit": "abc1234", "dirty": False, "inventory": {"tests": overlap_page.N_TESTS, "cases": 3196, "test_files": 44, "test_lines": 9000, "source_lines": 9000, "runtime_seconds": 64.2},
            "metrics": metrics, "pillars": pillars, "tests": tests, "overlaps": overlap_page.pairs(),
            "unmeasured": {m: "no git history" for m in NOT_MEASURED}}
    return write_dashboard(out, data, rules)


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
