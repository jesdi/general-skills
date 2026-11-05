"""Logistic metric scores, weighted pillar scores and the Fast feedback metrics."""
import math


def metric_score(x, target, bad, log=False):
    if log:
        x, target, bad = (math.log(max(v, 1e-9)) for v in (x, target, bad))
    k = 2 * math.log(9) / (target - bad)
    z = -k * (x - (target + bad) / 2)
    return 0.0 if z > 700 else 100 / (1 + math.exp(z))


def display(score):
    return min(99, max(1, round(score)))


def feedback_values(tests, runtime):
    durations = sorted((t["duration"] for t in tests.values()), reverse=True)
    n = len(durations)
    top = durations[: max(1, math.ceil(n / 10))] if n else []
    total = sum(durations)
    return {
        "total_runtime": runtime,
        "p95_duration": durations[min(n - 1, int(n * 0.05))] if n else 0,
        "slowest_decile_share": 100 * sum(top) / total if total else 0,
        "tests_over_10s": 100 * sum(d > 10 for d in durations) / n if n else 0,
    }


# Metrics read from the tests that give value (coverage, its test-file mapping, the kill matrix): a red suite leaves them partial.
VALUE_METRICS = {"mutation_score", "branch_coverage", "pseudo_tested_functions", "brittle_test_files", "no_unique_coverage",
                 "subsumed_in_kill_matrix"}


def score(rules, values, red=False):
    """Metrics {id: {value, score}} and pillars {key: {score, partial, red_suite}}; unmeasured metrics are left out and the weights re-normalized.

    Without the mutation tier (no mutation_score), a pillar that gives partial weights uses them, and its metrics without one do not count.
    With a red suite (a test that gives no value), every pillar using a value metric is partial (req 14) and red_suite, so
    the report can say why: a pillar can be partial for a missing metric, a red suite or both."""
    metrics, pillars = {}, {}
    for key, pillar in rules["pillars"].items():
        partial_weights = "mutation_score" not in values and any("partial_weight" in m for m in pillar["metrics"].values())
        scored = []
        for mid, m in pillar["metrics"].items():
            if mid in values:
                s = metric_score(values[mid], m["target"], m["bad"], m.get("log", False))
                metrics[mid] = {"value": values[mid], "score": s}
                weight = m.get("partial_weight", 0) if partial_weights else m["weight"]
                if weight:
                    scored.append((weight, s))
        weight = sum(w for w, _ in scored)
        red_suite = red and bool(VALUE_METRICS & pillar["metrics"].keys())
        pillars[key] = {
            "score": sum(w * s for w, s in scored) / weight if weight else None,
            "partial": not values.keys() >= pillar["metrics"].keys() or red_suite,
            "red_suite": red_suite,
        }
    return metrics, pillars


MIN_EVIDENCE = 2


def kill_matrix(tests, passing, mutation):
    """Flag each test hollow, subsumed or insufficient_evidence from the sampled kill matrix, and return the one metric the
    matrix alone owns, subsumed_in_kill_matrix; the metrics the flags change are computed from them afterwards. Only
    passing tests are judged.

    On its lines = a sampled mutant whose line the test executes. Subsumed = at least 2 of them, it kills some, and one
    other test kills all it kills."""
    matrix = mutation["matrix"]
    for node, test in tests.items():
        covered = {rel: set(lines) for rel, lines in test.get("coverage", {}).items()}
        on = [m for m, info in mutation["mutants"].items() if info["line"] in covered.get(info["file"], ())] if node in passing else []
        kills = {m for m in on if node in matrix[m]}
        enough = len(on) >= MIN_EVIDENCE
        every = {m for m in matrix if node in matrix[m]}
        test["insufficient_evidence"] = node in passing and not enough
        test["hollow"] = enough and not kills
        test["subsumed"] = enough and bool(kills) and any(
            other != node and every <= {m for m in matrix if other in matrix[m]} for other in {t for row in matrix.values() for t in row})
    return {"subsumed_in_kill_matrix": 100 * sum(t["subsumed"] for t in tests.values()) / (len(tests) or 1)}
