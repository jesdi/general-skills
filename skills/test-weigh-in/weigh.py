"""Weigh a repo's test suite: `weigh.py <repo-path> [--pytest-args "..."]`."""
import argparse
import faulthandler
import signal
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from weigh_env import SetupError, build_overlay, check_versions, find_interpreter
from weigh_coverage import branch_percent, left_out, overlaps, per_test, test_file_sources, unique_coverage, valued
from weigh_git import brittle_percent, brittleness, is_repo
from weigh_dashboard import summary, write_dashboard
from weigh_inventory import count_lines
from weigh_rules import RulesError, load as load_rules
from weigh_ruff import violations
from weigh_runs import aggregate, coverage_run, plain_run
from weigh_smells import find_smells, smell_values
from weigh_mutation import refuse_dirty, refuse_red, remove_stale, score as mutation_score
from weigh_score import feedback_values, kill_matrix, score
from weigh_snapshot import commit_state, home_dir, write_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(prog="weigh.py")
    parser.add_argument("repo_path")
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--python")
    parser.add_argument("--mutation", action="store_true")
    parser.add_argument("--matrix-per-function", default="3", type=lambda v: None if v == "all" else int(v))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo_path).resolve()
    try:
        rules = load_rules()
    except (RulesError, OSError, KeyError) as e:
        print(f"weigh.py: {e}", file=sys.stderr)
        return 2
    try:
        if args.mutation:
            remove_stale()  # before any refusal: an interrupted run's worktree never outlives the next start
            if not is_repo(repo):
                raise SetupError("--mutation needs a git repository with a commit")
            refuse_dirty(repo)
        home = home_dir(repo)
        python = find_interpreter(repo, args.python)
        check_versions(python)
        overlay = build_overlay(python)
        run = plain_run(python, repo, overlay, args.pytest_args)
        if args.mutation:
            refuse_red(run["cases"])
        eligible = {n for n, t in aggregate(run["cases"]).items() if t["outcome"] == "passed"}
        cov = coverage_run(python, repo, overlay, args.pytest_args, eligible)
        mutation = mutation_score(python, repo, overlay, args.matrix_per_function) if args.mutation else None
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
    unmeasured = {}

    def measure(mid, value, reason):
        """An optional metric records its value, or the reason it has none, here and only here."""
        if value is None:
            unmeasured[mid] = reason
        else:
            values[mid] = value

    tier = "the mutation tier did not run"
    measure("mutation_score", None if mutation is None else mutation["score"], tier)
    measure("pseudo_tested_functions", None if mutation is None else mutation["pseudo_tested"], tier)
    found, clusters = find_smells(repo, tests, violations(repo, rules.get("ruff", {}).get("pt", [])))
    for node, info in found.items():
        tests[node].update(info)
    measure("test_source_ratio", inventory["test_lines"] / inventory["source_lines"] if inventory["source_lines"] else None,
            "no source lines found")
    # failing, erroring and skipped tests cost (runtime, lines, smells) but give no value: no coverage, overlap or uniqueness
    covered = per_test(cov["report"], cov["arcs"], repo)
    cov_tests = aggregate(cov["cases"])
    for node, test in tests.items():
        test.update(covered.get(node, {"coverage": {}, "branches": {}}))
        test["flaky"] = cov_tests.get(node, {}).get("outcome") != test["outcome"]
    # value metrics count only tests that passed in both runs
    passing = valued(tests)
    unique = unique_coverage(passing)
    for node, test in tests.items():
        test["unique_coverage"] = unique.get(node, 0)
    measure("branch_coverage", branch_percent(cov["value_report"]), "no branch data in the coverage report")
    # flags tests before the metrics they change are computed
    measure("subsumed_in_kill_matrix", None if mutation is None else kill_matrix(tests, passing, mutation)["subsumed_in_kill_matrix"], tier)
    values.update(smell_values(tests))
    # a count-based metric is a percentage of all tests (req 17); only valued tests can lack unique coverage, and an
    # insufficient-evidence test counts in no Economy metric
    measure("no_unique_coverage",
            100 * sum(not u and not tests[n].get("insufficient_evidence") for n, u in unique.items()) / len(tests) if passing else None,
            "no test passed")
    test_files = brittleness(repo, test_file_sources(tests))
    measure("brittle_test_files", brittle_percent(test_files),
            "no source file maps to a test file" if is_repo(repo) else "no git history")
    metrics, pillars = score(rules, values, red=bool(left_out(tests)))
    data = {
        "commit": commit,
        "dirty": dirty,
        "pytest_args": args.pytest_args,
        "pytest_version": run["pytest_version"],
        "python_version": run["python_version"],
        "rules": {"version": rules["version"], "sha256": rules["sha256"]},
        "inventory": inventory,
        "metrics": metrics,
        "unmeasured": unmeasured,
        "pillars": pillars,
        "mutation_settings": {"mutation": args.mutation, "matrix_per_function": "all" if args.matrix_per_function is None else args.matrix_per_function},
        **({"mutation": {k: mutation[k] for k in ("sampled", "mutants", "matrix")}} if mutation is not None else {}),
        "tests": tests,
        "test_files": test_files,
        "overlaps": overlaps(passing),
        "clusters": clusters,
    }
    path = write_snapshot(home, data)
    dashboard = write_dashboard(home, data, rules)
    print(summary(data, rules, path, dashboard))
    if not args.no_open:
        webbrowser.open(dashboard.as_uri())
    return 0


def _terminate(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    faulthandler.register(signal.SIGUSR1, all_threads=True)  # `kill -USR1 <pid>` prints every thread's stack to stderr
    try:
        sys.exit(main())
    except KeyboardInterrupt:  # the temporary worktree is removed on the way out
        sys.exit(130)
