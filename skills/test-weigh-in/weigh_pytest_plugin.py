"""Pytest plugin loaded into the target's interpreter: writes each case's outcome and duration to TEST_WEIGH_IN_OUT."""
import json
import os
import sys
import time

import pytest

_cases = {}
_start = time.perf_counter()


def _context(item, when):
    """Under `coverage run`, record each phase's lines under "<nodeid>|<when>"; a plain run never imports coverage."""
    cov = sys.modules.get("coverage")
    current = cov and cov.Coverage.current()
    if current:
        current.switch_context(f"{item.nodeid}|{when}")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _context(item, "setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    _context(item, "call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item):
    _context(item, "teardown")


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
