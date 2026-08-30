import json

import pytest

from crap_config import CODE_SUFFIXES, Target, find_config, load, matches
from crap_model import ToolError

MINIMAL = {
    "targets": [
        {
            "name": "backend",
            "paths": ["backend/**/*.py"],
            "exclude": ["backend/tests/**", "backend/seed_*.py"],
            "complexity": {"tool": "python"},
            "coverage": {
                "format": "coverage.py",
                "root": "backend",
                "report": "backend/.crap/coverage.json",
                "command": "true",
            },
        }
    ]
}


def write(tmp_path, data):
    p = tmp_path / ".crap-gate.json"
    p.write_text(json.dumps(data))
    return p


def test_find_config_walks_up(tmp_path):
    p = write(tmp_path, MINIMAL)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == p


def test_find_config_raises_when_absent(tmp_path):
    with pytest.raises(ToolError, match=r"\.crap-gate\.json"):
        find_config(tmp_path)


def test_defaults_and_overrides(tmp_path):
    cfg = load(write(tmp_path, MINIMAL), {})
    assert cfg.thresholds == {"existing": 15, "new": 9}
    assert cfg.base == "origin/main"
    assert cfg.root == tmp_path
    cfg = load(write(tmp_path, MINIMAL), {"base": "main", "threshold_new": 5})
    assert (cfg.base, cfg.thresholds["new"], cfg.thresholds["existing"]) == ("main", 5, 15)


def test_target_fields(tmp_path):
    t = load(write(tmp_path, MINIMAL), {}).targets[0]
    assert t == Target(
        name="backend",
        paths=["backend/**/*.py"],
        exclude=["backend/tests/**", "backend/seed_*.py"],
        complexity_tool="python",
        complexity_cwd=".",
        coverage_format="coverage.py",
        coverage_report="backend/.crap/coverage.json",
        coverage_command="true",
        coverage_root="backend",
    )


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"targets": []}, "at least one target"),
        ({"targets": [{**MINIMAL["targets"][0], "complexity": {"tool": "lizard"}}]}, "complexity.tool"),
        ({"targets": [{**MINIMAL["targets"][0], "coverage": {**MINIMAL["targets"][0]["coverage"], "format": "lcov"}}]}, "coverage.format"),
        ({"targets": [MINIMAL["targets"][0], MINIMAL["targets"][0]]}, "duplicate target name"),
        ({"thresholds": {"existing": "x"}, **MINIMAL}, "thresholds"),
    ],
)
def test_validation(tmp_path, bad, message):
    with pytest.raises(ToolError, match=message):
        load(write(tmp_path, bad), {})


def test_matches_globs_with_double_star_and_exclude(tmp_path):
    t = load(write(tmp_path, MINIMAL), {}).targets[0]
    assert matches(t, "backend/main.py")
    assert matches(t, "backend/services/deep/x.py")
    assert not matches(t, "backend/tests/test_x.py")
    assert not matches(t, "backend/seed_securities.py")
    assert not matches(t, "frontend/src/a.ts")
    assert not matches(t, "backend/notes.md")


def test_code_suffixes():
    assert CODE_SUFFIXES == {".py", ".ts", ".tsx"}
