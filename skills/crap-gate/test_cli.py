import json
import subprocess
import textwrap
from pathlib import Path

import pytest

import crap

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]

# A stand-in for `pytest --cov`: writes a coverage.py-shaped report where
# every function is 100% covered except those whose name starts with `un`.
FAKE_COVERAGE = textwrap.dedent('''
    import ast, json, sys
    from pathlib import Path
    files = {}
    for p in Path("pkg").glob("*.py"):
        fns = {}
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.FunctionDef):
                pct = 0.0 if node.name.startswith("un") else 100.0
                fns[node.name] = {"start_line": node.lineno, "summary": {"percent_covered": pct}}
        files[p.as_posix()] = {"functions": fns}
    Path(".crap").mkdir(exist_ok=True)
    Path(".crap/coverage.json").write_text(json.dumps({"files": files}))
''')

CONFIG = {
    "thresholds": {"existing": 15, "new": 9},
    "base": "main",
    "targets": [
        {
            "name": "backend",
            "paths": ["pkg/**/*.py"],
            "exclude": ["pkg/tests/**"],
            "complexity": {"tool": "python"},
            "coverage": {
                "format": "coverage.py",
                "root": ".",
                "report": ".crap/coverage.json",
                "command": "python3 fake_cov.py",
            },
        }
    ],
}

BASE_SRC = "def f(x):\n    return x\n\n\ndef unloved(a, b):\n    if a:\n        return b\n    return a or b\n"


def git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".crap-gate.json").write_text(json.dumps(CONFIG))
    (tmp_path / "fake_cov.py").write_text(FAKE_COVERAGE)
    (tmp_path / ".gitignore").write_text(".crap/\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(BASE_SRC)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "switch", "-q", "-c", "feature")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_clean_branch_exits_zero(repo, capsys):
    assert crap.main([]) == 0
    out = capsys.readouterr().out
    assert "no changed functions" in out
    assert not (repo / ".crap" / "coverage.json").exists()  # nothing to measure → no run


def test_new_untested_function_fails_with_new_limit_and_hint(repo, capsys):
    (repo / "pkg" / "m.py").write_text(BASE_SRC + "\n\ndef unnew(p, q):\n    return p if q else (q if p else 0)\n")
    assert crap.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  pkg/m.py:11  unnew  CC 3  cov 0%  CRAP 12.0  limit 9 (new)  → cover ≥ 13% or split to CC ≤ 2" in out
    assert "Summary: 1 FAIL, 0 ok, 0 not measured" in out


def test_touching_existing_function_uses_existing_limit(repo, capsys):
    src = BASE_SRC.replace("    return a or b", "    return a or b or 0")  # unloved: CC 4 → CRAP 20 > 15
    (repo / "pkg" / "m.py").write_text(src)
    assert crap.main(["--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    fn = data["functions"][0]
    assert (fn["name"], fn["kind"], fn["limit"], fn["status"], fn["cc"], fn["crap"]) == ("unloved", "existing", 15, "FAIL", 4, 20.0)
    (run,) = data["targets"]
    assert (run["name"], run["report"], run["ran"]) == ("backend", ".crap/coverage.json", True)
    assert run["seconds"] >= 0
    assert data["summary"] == {"fail": 1, "ok": 0, "not_measured": 0}


def test_untouched_functions_are_not_reported(repo, capsys):
    (repo / "pkg" / "m.py").write_text(BASE_SRC.replace("    return x", "    return x + 1"))
    assert crap.main([]) == 0
    out = capsys.readouterr().out
    assert "ok    pkg/m.py:1  f  CC 1  cov 100%  CRAP 1.0  limit 15 (existing)" in out
    assert "unloved" not in out


def test_out_of_scope_code_files_are_listed_not_measured(repo, capsys):
    (repo / "pkg" / "tests").mkdir()
    (repo / "pkg" / "tests" / "test_m.py").write_text("def test_it():\n    assert True\n")
    (repo / "README.md").write_text("hi\n")
    assert crap.main([]) == 0
    out = capsys.readouterr().out
    assert "Not measured: pkg/tests/test_m.py" in out
    assert "README.md" not in out


def test_all_scores_everything_as_existing_and_flags_override(repo, capsys):
    assert crap.main(["--all", "--threshold-existing", "2"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  pkg/m.py:5  unloved  CC 3  cov 0%  CRAP 12.0  limit 2 (existing)" in out
    assert "ok    pkg/m.py:1  f  CC 1" in out


def test_no_run_without_report_is_a_tool_error(repo, capsys):
    (repo / "pkg" / "m.py").write_text(BASE_SRC + "\n\ndef k():\n    return 1\n")
    assert crap.main(["--no-run"]) == 2
    assert "coverage.json" in capsys.readouterr().err


def test_failing_coverage_command_that_still_writes_a_report_warns(repo, capsys):
    cfg = json.loads((repo / ".crap-gate.json").read_text())
    cfg["targets"][0]["coverage"]["command"] = "python3 fake_cov.py && exit 3"
    (repo / ".crap-gate.json").write_text(json.dumps(cfg))
    (repo / "pkg" / "m.py").write_text(BASE_SRC + "\n\ndef k():\n    return 1\n")
    assert crap.main([]) == 0
    assert "exited 3" in capsys.readouterr().err


def test_unknown_base_is_a_tool_error(repo, capsys):
    assert crap.main(["--base", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_config_not_at_git_toplevel_is_a_tool_error(tmp_path, monkeypatch, capsys):
    git(tmp_path, "init", "-q", "-b", "main")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".crap-gate.json").write_text(json.dumps(CONFIG))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(sub)
    assert crap.main([]) == 2
    assert "git repository root" in capsys.readouterr().err
