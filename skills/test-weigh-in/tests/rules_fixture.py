"""Rules file the scoring tests hand to weigh.py through TEST_WEIGH_IN_RULES=<path>.

Interface the tests pin (the spec leaves the TOML keys and the snapshot JSON keys open):
  rules.toml:  version = 1; [pillars.<key>] name = "..."; [pillars.<key>.metrics.<id>] name, weight,
               optional partial_weight, target, bad, optional log = true. Percentages are 0-100,
               durations are seconds. Pillar keys: regression, refactoring, feedback,
               maintainability, economy.
  snapshot:    rules {version, sha256}; metrics {<id>: {value, score}}; pillars {<key>: {score, partial}}.
               Scores are unrounded floats; only the dashboard rounds and clamps.
"""
import hashlib

# pillar key: (name, [(metric id, name, weight, partial weight, target, bad, log scale)])
STARTER = {
    "regression": ("Protection against regressions", [
        ("mutation_score", "Mutation score", .45, None, 80, 40, False),
        ("branch_coverage", "Branch coverage", .25, .60, 90, 50, False),
        ("hollow_tests", "Hollow tests", .20, .40, 0, 10, False),
        ("pseudo_tested_functions", "Pseudo-tested functions", .10, None, 0, 15, False),
    ]),
    "refactoring": ("Resistance to refactoring", [
        ("brittle_test_files", "Brittle test files", .40, None, 15, 60, False),
        ("private_access", "Tests with private access", .25, None, 2, 30, False),
        ("mock_heavy", "Mock-heavy tests", .20, None, 5, 30, False),
        ("interaction_only", "Interaction-only tests", .15, None, 5, 30, False),
    ]),
    "feedback": ("Fast feedback", [
        ("total_runtime", "Total runtime", .40, None, 60, 600, True),
        ("p95_duration", "p95 test duration", .30, None, .1, 2, True),
        ("slowest_decile_share", "Runtime share of slowest 10% of tests", .15, None, 40, 90, False),
        ("tests_over_10s", "Tests over 10 s", .15, None, 0, 2, False),
    ]),
    "maintainability": ("Maintainability", [
        ("smelly_tests", "Tests with a maintainability smell", .50, None, 5, 40, False),
        ("long_tests", "Tests longer than 50 lines", .20, None, 2, 20, False),
        ("test_source_ratio", "Test : source lines-of-code ratio", .30, None, 1.5, 4.0, False),
    ]),
    "economy": ("Economy", [
        ("no_unique_coverage", "Tests with no unique coverage", .35, .60, 10, 50, False),
        ("subsumed_in_kill_matrix", "Tests subsumed in the kill matrix", .40, None, 5, 35, False),
        ("duplicate_clusters", "Tests in a duplicate-body cluster", .25, .40, 2, 20, False),
    ]),
}


def write_rules(path, **overrides):
    """Write the version 1 starter rules; overrides are `<metric id>={weight=..., target=..., bad=...}`."""
    lines = ["version = 1", ""]
    for key, (name, metrics) in STARTER.items():
        lines += [f"[pillars.{key}]", f'name = "{name}"', ""]
        for mid, mname, weight, partial, target, bad, log in metrics:
            m = {**dict(weight=weight, target=target, bad=bad), **overrides.get(mid, {})}
            lines += [f"[pillars.{key}.metrics.{mid}]", f'name = "{mname}"']
            lines += [f"{k} = {v!r}" for k, v in m.items()]
            if partial is not None:
                lines.append(f"partial_weight = {partial!r}")
            if log:
                lines.append("log = true")
            lines.append("")
    path.write_text("\n".join(lines))
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
