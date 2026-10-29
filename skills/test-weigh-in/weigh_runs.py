"""The plain run: pytest once through the target's interpreter, cases aggregated into tests."""
import json
import os
import shlex
import subprocess
import tempfile

from weigh_env import SetupError

# pytest options that write files relative to the repo; the run must leave the repo untouched.
# ponytail: denylist of core and common-plugin writers, an unknown plugin's writer still gets through.
WRITERS = {"--junitxml", "--junit-xml", "--resultlog", "--result-log", "--report-log", "--debug",
           "--basetemp", "--html", "--cov-report", "--json-report-file"}


def plain_run(python, repo, overlay, pytest_args):
    for arg in shlex.split(pytest_args):
        if arg.split("=", 1)[0] in WRITERS:
            raise SetupError(f"--pytest-args: {arg} writes files and would change the target repo")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "cases.json")
        env = {**os.environ, "PYTHONPATH": str(overlay), "TEST_WEIGH_IN_OUT": out, "PYTHONDONTWRITEBYTECODE": "1"}
        cmd = [str(python), "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider",
               "-p", "weigh_pytest_plugin", *shlex.split(pytest_args)]
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True)
        if not os.path.exists(out):
            raise SetupError("pytest produced no results:\n" + proc.stdout + proc.stderr)
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
