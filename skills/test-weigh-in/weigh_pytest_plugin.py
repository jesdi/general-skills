"""Pytest plugin loaded into the target's interpreter: writes each case's outcome and duration to TEST_WEIGH_IN_OUT,
and sends the files pytest would write into the repo to TEST_WEIGH_IN_ARTIFACTS."""
import json
import os
import sys
import time

import pytest

_cases = {}
_start = time.perf_counter()
# Option dests of core and common-plugin file writers (junitxml, logging, basetemp, pytest-reportlog, pytest-html,
# pytest-json-report). They are read from the effective config, so addopts, PYTEST_ADDOPTS and ini values are covered.
# ponytail: a writer of an unknown plugin still gets through; weigh_runs refuses the run when the repo changed.
WRITERS = ("xmlpath", "log_file", "basetemp", "report_log", "htmlpath", "json_report_file")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    artifacts = os.environ["TEST_WEIGH_IN_ARTIFACTS"]
    for dest in WRITERS:
        set_by_ini = dest == "log_file" and config.getini("log_file")
        if getattr(config.option, dest, None) or set_by_ini:
            setattr(config.option, dest, os.path.join(artifacts, dest))


def pytest_runtest_logreport(report):
    case = _cases.setdefault(report.nodeid, {"outcome": "passed", "duration": 0.0})
    if report.when == "call":
        case["outcome"] = "xfail" if hasattr(report, "wasxfail") else report.outcome
        case["duration"] = report.duration
    elif report.failed and case["outcome"] != "failed":
        case["outcome"] = "error"
    elif report.skipped and report.when == "setup":
        case["outcome"] = "xfail" if hasattr(report, "wasxfail") else "skipped"
    # a teardown skip leaves the call's outcome as it was


def pytest_sessionfinish(session):
    data = {
        "pytest_version": pytest.__version__,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "runtime_seconds": time.perf_counter() - _start,
        "cases": _cases,
    }
    with open(os.environ["TEST_WEIGH_IN_OUT"], "w") as out:
        json.dump(data, out)
