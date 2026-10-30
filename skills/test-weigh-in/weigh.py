"""Weigh a repo's test suite: `weigh.py <repo-path> [--pytest-args "..."]`."""
import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from weigh_env import SetupError, build_overlay, check_versions, find_interpreter
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
        covered = coverage_run(python, repo, overlay, args.pytest_args)
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
    # a suite that is not green is a value-metric blank: coverage is left out and the pillar is partial
    if covered is not None and all(t["outcome"] != "failed" for t in tests.values()):
        values["branch_coverage"] = covered
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
    }
    path = write_snapshot(home, data)
    dashboard = write_dashboard(home, data, rules)
    print(summary(data, rules, path, dashboard))
    if not args.no_open:
        webbrowser.open(dashboard.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
