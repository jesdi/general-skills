"""Ticket 01 fixtures, no repo run: `metric_page.py <out-dir> <scenario>` writes <out-dir>/dashboard.html through
weigh_dashboard.write_dashboard and prints its path.

Interface the acceptance tests pin (the spec leaves the snapshot key open): snapshot["unmeasured"] = {metric id: reason}
records why a metric was not measured; a snapshot without it (older data) or without the metric's entry has no recorded cause.
Scenarios:
  values   total_runtime 64.16 and branch_coverage 94.84, rules with non-default total_runtime anchors (30 / 300)
  zero     refactoring has 0 of 4 metrics measured (all four have reasons); feedback has tests_over_10s measured at 0 and
           p95_duration missing with no recorded cause
  scores   metric scores 0.2, 100 and (zero tests over 10 s) 90
  default  the default-run shape: regression lacks mutation_score and pseudo_tested_functions, with the recorded reason
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rules_fixture
from weigh_dashboard import write_dashboard
from weigh_rules import load
from weigh_score import score

TIER = "the mutation tier did not run"
VALUES = {
    "values": {"total_runtime": 64.16, "branch_coverage": 94.84},
    "zero": {"tests_over_10s": 0, "total_runtime": 5.0, "slowest_decile_share": 50.0},
    "scores": {"smelly_tests": 20.0, "long_tests": 1.0, "tests_over_10s": 0},
    "default": {"branch_coverage": 80.0, "hollow_tests": 1.0},
}
UNMEASURED = {
    "zero": {"brittle_test_files": "no git history", "private_access": "no ruff", "mock_heavy": "no ruff", "interaction_only": "no ruff"},
    "default": {"mutation_score": TIER, "pseudo_tested_functions": TIER},
}


def build(out, scenario):
    out.mkdir(parents=True, exist_ok=True)
    rules_path = rules_fixture.write_rules(out / "rules.toml", **({"total_runtime": dict(target=30, bad=300)} if scenario == "values" else {}))
    import os
    os.environ["TEST_WEIGH_IN_RULES"] = str(rules_path)
    rules = load()
    metrics, pillars = score(rules, VALUES[scenario])
    if scenario == "scores":
        metrics["smelly_tests"]["score"], metrics["long_tests"]["score"] = 0.2, 100.0
        pillars["maintainability"]["score"] = 100.0
    tests = {"tests/test_a.py::test_a": {"outcome": "passed", "duration": 0.1, "coverage": {}, "unique_coverage": 0, "flaky": False, "smells": [], "lines": 2}}
    data = {"commit": "abc1234", "dirty": False, "inventory": {"tests": 1, "cases": 1, "test_files": 1, "test_lines": 2, "source_lines": 2, "runtime_seconds": 0.1},
            "metrics": metrics, "pillars": pillars, "tests": tests, "overlaps": []}
    if scenario in UNMEASURED:
        data["unmeasured"] = UNMEASURED[scenario]
    return write_dashboard(out, data, rules)


if __name__ == "__main__":
    print(build(Path(sys.argv[1]), sys.argv[2]))
