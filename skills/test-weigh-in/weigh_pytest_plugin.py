"""Pytest plugin loaded into the target's interpreter: writes each case's outcome and duration to TEST_WEIGH_IN_OUT."""
import json
import os
import sys
import time

import pytest

_cases = {}
_start = time.perf_counter()


def pytest_runtest_logreport(report):
    if report.when == "call":
        outcome = "xfail" if hasattr(report, "wasxfail") else report.outcome
        _cases[report.nodeid] = {"outcome": outcome, "duration": report.duration}
    elif report.outcome != "passed":
        outcome = "skipped" if report.outcome == "skipped" else "error"
        _cases[report.nodeid] = {"outcome": outcome, "duration": 0.0}


def pytest_sessionfinish(session):
    data = {
        "pytest_version": pytest.__version__,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "runtime_seconds": time.perf_counter() - _start,
        "cases": _cases,
    }
    with open(os.environ["TEST_WEIGH_IN_OUT"], "w") as out:
        json.dump(data, out)
