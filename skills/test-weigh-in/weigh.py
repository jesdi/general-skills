"""Weigh a repo's test suite: `weigh.py <repo-path> [--pytest-args "..."]`."""
import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from weigh_env import SetupError, build_overlay, check_versions, find_interpreter
from weigh_coverage import branch_percent, overlaps, per_test, unique_coverage
from weigh_dashboard import summary, write_dashboard
from weigh_inventory import count_lines
from weigh_rules import RulesError, load as load_rules
from weigh_runs import aggregate, coverage_run, plain_run
from weigh_score import feedback_values, score
from weigh_snapshot import commit_state, home_dir, write_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(prog="weigh.py")
    parser.add_argument("repo_path")
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--python")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo_path).resolve()
    try:
        rules = load_rules()
    except (RulesError, OSError, KeyError) as e:
        print(f"weigh.py: {e}", file=sys.stderr)
        return 2
    try:
        home = home_dir(repo)
        python = find_interpreter(repo, args.python)
        check_versions(python)
        overlay = build_overlay(python)
        run = plain_run(python, repo, overlay, args.pytest_args)
        eligible = {n for n, t in aggregate(run["cases"]).items() if t["outcome"] == "passed"}
        cov_cases, report, value_report = coverage_run(python, repo, overlay, args.pytest_args, eligible)
    except SetupError as e:
        print(f"weigh.py: {e}", file=sys.stderr)
        return 1
    tests = aggregate(run["cases"])
    commit, dirty = commit_state(repo)
    inventory = {
        "tests": len(tests),
        "cases": len(run["cases"]),
        **count_lines(repo),
        "runtime_seconds": max(run["runtime_seconds"], sum(c["duration"] for c in run["cases"].values())),
    }
    values = feedback_values(tests, inventory["runtime_seconds"])
    # failing, erroring and skipped tests cost (runtime, lines, smells) but give no value: no coverage, overlap or uniqueness
    covered = per_test(report, repo) if report else {}
    cov_tests = aggregate(cov_cases)
    for node, test in tests.items():
        test["coverage"] = covered.get(node, {})
        test["flaky"] = node in cov_tests and cov_tests[node]["outcome"] != test["outcome"]
    # value metrics count only tests that passed in both runs
    passing = {n: t["coverage"] for n, t in tests.items()
               if t["outcome"] == "passed" and cov_tests.get(n, {}).get("outcome") == "passed"}
    unique = unique_coverage(passing)
    for node, test in tests.items():
        test["unique_coverage"] = unique.get(node, 0)
    if value_report and branch_percent(value_report) is not None:
        values["branch_coverage"] = branch_percent(value_report)
    if passing:
        values["no_unique_coverage"] = 100 * sum(not u for u in unique.values()) / len(passing)
    metrics, pillars = score(rules, values)
    data = {
        "commit": commit,
        "dirty": dirty,
        "pytest_args": args.pytest_args,
        "pytest_version": run["pytest_version"],
        "python_version": run["python_version"],
        "rules": {"version": rules["version"], "sha256": rules["sha256"]},
        "inventory": inventory,
        "metrics": metrics,
        "pillars": pillars,
        "tests": tests,
        "overlaps": overlaps(passing),
    }
    path = write_snapshot(home, data)
    dashboard = write_dashboard(home, data, rules)
    print(summary(data, rules, path, dashboard))
    if not args.no_open:
        webbrowser.open(dashboard.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
