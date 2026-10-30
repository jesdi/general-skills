"""The plain and coverage runs: pytest through the target's interpreter, cases aggregated into tests."""
import json
import os
import shlex
import subprocess
import tempfile

from weigh_env import SetupError
from weigh_snapshot import git

PYTEST = ["-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "-p", "weigh_pytest_plugin"]
# pytest exit codes of a run that executed its tests: all passed, some failed.
RAN = (0, 1)


def _pytest(python, repo, overlay, pytest_args, tmp, runner=(), env=None):
    """One pytest run; the plugin sends pytest's file writers to tmp, and a run that still changed the repo is refused."""
    args = shlex.split(pytest_args)
    # --debug opens its log before any plugin is configured, so it cannot be redirected.
    if any(a.split("=", 1)[0] == "--debug" for a in args + shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))):
        raise SetupError("--pytest-args: --debug writes files and would change the target repo")
    out = os.path.join(tmp, "cases.json")
    env = {**os.environ, **(env or {}), "PYTHONPATH": str(overlay), "PYTHONDONTWRITEBYTECODE": "1",
           "TEST_WEIGH_IN_OUT": out, "TEST_WEIGH_IN_ARTIFACTS": tmp}
    before = git(repo, "status", "--porcelain")
    proc = subprocess.run([str(python), *runner, *PYTEST, *args], cwd=repo, env=env, capture_output=True, text=True)
    if git(repo, "status", "--porcelain") != before:
        raise SetupError("pytest changed the target repo; drop the --pytest-args or configuration that writes files:\n"
                         + git(repo, "status", "--porcelain"))
    return proc, out


def plain_run(python, repo, overlay, pytest_args):
    with tempfile.TemporaryDirectory() as tmp:
        proc, out = _pytest(python, repo, overlay, pytest_args, tmp)
        if proc.returncode not in RAN or not os.path.exists(out):
            raise SetupError(f"pytest exited {proc.returncode} without running the tests:\n" + proc.stdout + proc.stderr)
        with open(out) as f:
            return json.load(f)


def aggregate(cases):
    """Group cases by node id without [params]; durations sum, any failure wins, all-skipped is skipped."""
    tests = {}
    for node, case in cases.items():
        tests.setdefault(node.split("[", 1)[0], []).append(case)
    result = {}
    for node, group in tests.items():
        outcomes = {c["outcome"] for c in group}
        if outcomes & {"failed", "error"}:
            outcome = "failed"
        elif outcomes == {"skipped"}:
            outcome = "skipped"
        else:
            outcome = "passed"
        result[node] = {"outcome": outcome, "duration": sum(c["duration"] for c in group), "cases": len(group)}
    return result


def coverage_run(python, repo, overlay, pytest_args):
    """Branch coverage percent of the repo's own files over one pytest run under coverage; None when the run yields no data."""
    with tempfile.TemporaryDirectory() as tmp:
        rc = os.path.join(tmp, "rc")
        open(rc, "w").close()
        data, report = os.path.join(tmp, "data"), os.path.join(tmp, "report.json")
        cov = [str(python), "-m", "coverage"]
        _pytest(python, repo, overlay, pytest_args, tmp, env={"COVERAGE_RCFILE": rc},
                runner=["-m", "coverage", "run", "--branch", f"--data-file={data}", f"--source={repo}", "--omit=*/.venv/*,*/venv/*"])
        env = {**os.environ, "PYTHONPATH": str(overlay), "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_RCFILE": rc}
        proc = subprocess.run([*cov, "json", f"--data-file={data}", "-o", report, "-q"],
                              cwd=repo, env=env, capture_output=True, text=True)
        if proc.returncode or not os.path.exists(report):
            return None
        with open(report) as f:
            totals = json.load(f)["totals"]
        # Branch arcs only: percent_covered mixes statements in. No branches: the metric is not measured.
        return 100 * totals["covered_branches"] / totals["num_branches"] if totals["num_branches"] else None
