"""Acceptance tests: `pr_exam.py config [<path>]`, run as a subprocess.

Spec specs/pr-exam: requirement 9, scenario "an invalid config stops the run".
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "pr_exam.py"


def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), "config", *map(str, args)],
                          capture_output=True, text=True, cwd=cwd)


def cfg(tmp_path, content):
    f = tmp_path / ".pr-exam.json"
    f.write_text(content if isinstance(content, str) else json.dumps(content))
    return run(f)


def refused(r):
    assert r.returncode != 0
    assert r.stdout.strip() == ""
    return r.stdout + r.stderr


def test_no_file_prints_defaults(tmp_path):
    r = run(tmp_path / "missing.json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"enabled": True, "bypassLabels": {}}


def test_no_path_prints_defaults(tmp_path):
    r = run(cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"enabled": True, "bypassLabels": {}}


def test_enabled_false(tmp_path):
    r = cfg(tmp_path, {"enabled": False})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"enabled": False, "bypassLabels": {}}


def test_bypass_labels(tmp_path):
    labels = {"exam:skip": "skip", "exam:defer": "defer"}
    r = cfg(tmp_path, {"bypassLabels": labels})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"enabled": True, "bypassLabels": labels}


def test_invalid_bypass_mode(tmp_path):
    out = refused(cfg(tmp_path, {"bypassLabels": {"exam:skip": "maybe"}}))
    for word in ("bypassLabels", "exam:skip", "skip", "defer"):
        assert word in out


def test_non_boolean_enabled(tmp_path):
    assert "enabled" in refused(cfg(tmp_path, {"enabled": "yes"}))


def test_unknown_key(tmp_path):
    assert "colour" in refused(cfg(tmp_path, {"colour": 1}))


def test_invalid_json(tmp_path):
    assert "json" in refused(cfg(tmp_path, "{not json")).lower()


def test_two_errors_both_named(tmp_path):
    out = refused(cfg(tmp_path, {"enabled": "yes", "colour": 1}))
    assert "enabled" in out and "colour" in out


def test_config_that_is_not_an_object(tmp_path):
    assert "config: must be a JSON object" in refused(cfg(tmp_path, ["enabled"]))


def test_bypass_labels_that_are_not_an_object(tmp_path):
    assert "bypassLabels" in refused(cfg(tmp_path, {"bypassLabels": ["exam:skip"]}))
