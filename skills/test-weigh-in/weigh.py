"""Weigh a repo's test suite: `weigh.py <repo-path> [--pytest-args "..."]`."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from weigh_env import SetupError, build_overlay, check_versions, find_interpreter
from weigh_inventory import count_lines
from weigh_runs import aggregate, plain_run
from weigh_snapshot import commit_state, write_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(prog="weigh.py")
    parser.add_argument("repo_path")
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--python")
    args = parser.parse_args(argv)
    repo = Path(args.repo_path).resolve()
    try:
        python = find_interpreter(repo, args.python)
        check_versions(python)
        run = plain_run(python, repo, build_overlay(), args.pytest_args)
    except SetupError as e:
        print(f"weigh.py: {e}", file=sys.stderr)
        return 1
    tests = aggregate(run["cases"])
    commit, dirty = commit_state(repo)
    path = write_snapshot(repo, {
        "commit": commit,
        "dirty": dirty,
        "pytest_args": args.pytest_args,
        "pytest_version": run["pytest_version"],
        "python_version": run["python_version"],
        "inventory": {
            "tests": len(tests),
            "cases": len(run["cases"]),
            **count_lines(repo),
            "runtime_seconds": max(run["runtime_seconds"], sum(c["duration"] for c in run["cases"].values())),
        },
        "tests": tests,
    })
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
