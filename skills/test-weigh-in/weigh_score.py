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


def score(rules, values):
    """Metrics {id: {value, score}} and pillars {key: {score, partial}}; unmeasured metrics are left out and the weights re-normalized.

    The mutation tier does not run, so a pillar that gives partial weights uses them, and its metrics without one do not count."""
    metrics, pillars = {}, {}
    for key, pillar in rules["pillars"].items():
        partial_weights = any("partial_weight" in m for m in pillar["metrics"].values())
        scored = []
        for mid, m in pillar["metrics"].items():
            if mid in values:
                s = metric_score(values[mid], m["target"], m["bad"], m.get("log", False))
                metrics[mid] = {"value": values[mid], "score": s}
                weight = m.get("partial_weight", 0) if partial_weights else m["weight"]
                if weight:
                    scored.append((weight, s))
        weight = sum(w for w, _ in scored)
        pillars[key] = {
            "score": sum(w * s for w, s in scored) / weight if weight else None,
            "partial": not values.keys() >= pillar["metrics"].keys(),
        }
    return metrics, pillars
