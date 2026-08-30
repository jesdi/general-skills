import json
from pathlib import Path

import pytest

from crap_config import Target
from crap_coverage import CoveragePyReport, IstanbulReport, load_report
from crap_model import Func, ToolError

COVERAGE_PY = {
    "files": {
        "services/x.py": {
            "functions": {
                "f": {"start_line": 3, "summary": {"percent_covered": 75.0}},
                "K.m": {"start_line": 10, "summary": {"percent_covered": 0.0}},
                "": {"start_line": 1, "summary": {"percent_covered": 100.0}},
            }
        }
    }
}


def test_coverage_py_matches_by_start_line_and_prefixes_root():
    r = CoveragePyReport(COVERAGE_PY, "backend")
    assert r.has_file("backend/services/x.py")
    assert not r.has_file("services/x.py")
    assert r.function_coverage(Func("backend/services/x.py", "f", 3, 8, 2), []) == 0.75
    assert r.function_coverage(Func("backend/services/x.py", "K.m", 10, 12, 2), []) == 0.0


def test_coverage_py_falls_back_to_first_entry_inside_range_else_zero():
    r = CoveragePyReport(COVERAGE_PY, "backend")
    # decorated function: analyzer says def at 2, coverage.py says 3
    assert r.function_coverage(Func("backend/services/x.py", "f", 2, 8, 2), []) == 0.75
    assert r.function_coverage(Func("backend/services/x.py", "ghost", 20, 25, 2), []) == 0.0


def istanbul(repo_root):
    path = str(repo_root / "frontend/src/a.tsx")
    return {
        path: {
            "path": path,
            "statementMap": {
                "0": {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 10}},
                "1": {"start": {"line": 3, "column": 0}, "end": {"line": 3, "column": 10}},
                "2": {"start": {"line": 5, "column": 0}, "end": {"line": 5, "column": 10}},  # inside child
                "3": {"start": {"line": 9, "column": 0}, "end": {"line": 9, "column": 10}},  # outside
            },
            "s": {"0": 1, "1": 0, "2": 0, "3": 1},
            "branchMap": {
                "0": {"loc": {"start": {"line": 3, "column": 4}, "end": {"line": 3, "column": 9}},
                      "type": "cond-expr", "locations": [], "line": 3},
            },
            "b": {"0": [1, 0]},
            "fnMap": {},
            "f": {},
        }
    }


def test_istanbul_counts_statements_and_branches_in_range_excluding_children(tmp_path):
    r = IstanbulReport(istanbul(tmp_path), tmp_path)
    assert r.has_file("frontend/src/a.tsx")
    parent = Func("frontend/src/a.tsx", "P", 1, 7, 3)
    child = Func("frontend/src/a.tsx", "P.c", 4, 6, 1)
    # statements 0 (hit), 1 (miss) + branches [1, 0] → 2 of 4
    assert r.function_coverage(parent, [child]) == 0.5
    # child has one statement, missed
    assert r.function_coverage(child, []) == 0.0


def test_istanbul_function_without_statements_is_fully_covered(tmp_path):
    r = IstanbulReport(istanbul(tmp_path), tmp_path)
    assert r.function_coverage(Func("frontend/src/a.tsx", "T", 20, 21, 1), []) == 1.0


def target(fmt, report, root="."):
    return Target("t", ["**"], [], "python", ".", fmt, report, "", root)


def test_load_report_dispatches_and_errors(tmp_path):
    (tmp_path / "cov.json").write_text(json.dumps(COVERAGE_PY))
    r = load_report(target("coverage.py", "cov.json", "backend"), tmp_path)
    assert isinstance(r, CoveragePyReport)
    (tmp_path / "ist.json").write_text(json.dumps(istanbul(tmp_path)))
    assert isinstance(load_report(target("istanbul", "ist.json"), tmp_path), IstanbulReport)
    with pytest.raises(ToolError, match="missing.json"):
        load_report(target("istanbul", "missing.json"), tmp_path)
