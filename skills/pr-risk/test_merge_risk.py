"""Acceptance tests for `merge_risk.py parse|validate`, through the command line only.

`parse` prints one JSON object: door, blast_radius, touches, rollback, verdict,
reasons and errors (a list of messages, empty when the block is valid).
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("merge_risk.py").resolve()

VALID = '''door: two-way            # one-way | two-way
blast_radius: isolated
touches: [docs, tests]
rollback: revert
verdict: auto
reasons:
  - "Only skill prose and its tests change"
'''


def body(block=VALID):
    return f"## Summary\n\nText.\n\n## Merge Danger\n\n```merge-risk v1\n{block}```\n"


def run(cmd, tmp_path, text):
    f = tmp_path / "body.md"
    f.write_text(text)
    return subprocess.run([sys.executable, str(SCRIPT), cmd, str(f)],
                          text=True, capture_output=True, timeout=30)


def parsed(tmp_path, text):
    proc = run("parse", tmp_path, text)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def errors_of(tmp_path, text):
    return parsed(tmp_path, text)["errors"]


def test_valid_block_parses(tmp_path):
    out = parsed(tmp_path, body())
    assert out["door"] == "two-way"
    assert out["touches"] == ["docs", "tests"]
    assert out["reasons"] == ["Only skill prose and its tests change"]
    assert out["errors"] == []


def test_unknown_category_is_invalid(tmp_path):
    errs = errors_of(tmp_path, body(VALID.replace("[docs, tests]", "[docs, styles]")))
    assert "unknown category: styles" in errs


def test_touches_as_item_list_is_invalid(tmp_path):
    block = VALID.replace("touches: [docs, tests]", "touches:\n  - docs\n  - tests")
    assert errors_of(tmp_path, body(block))


def test_two_blocks_are_invalid(tmp_path):
    assert "more than one block" in errors_of(tmp_path, body() + "\n" + body())


def test_no_block_is_invalid(tmp_path):
    assert "no merge-risk block" in errors_of(tmp_path, "## Summary\n\nNothing here.\n")


def test_missing_key_is_named(tmp_path):
    errs = errors_of(tmp_path, body(VALID.replace("rollback: revert\n", "")))
    assert any("rollback" in e for e in errs)


def test_unknown_key_is_named(tmp_path):
    errs = errors_of(tmp_path, body(VALID + "colour: red\n"))
    assert any("colour" in e for e in errs)


def test_enum_outside_list_is_named(tmp_path):
    errs = errors_of(tmp_path, body(VALID.replace("door: two-way", "door: sideways")))
    assert any("door" in e and "sideways" in e for e in errs)


def test_empty_reasons_is_invalid(tmp_path):
    block = VALID.split("reasons:")[0] + "reasons:\n"
    assert any("reasons" in e for e in errors_of(tmp_path, body(block)))


def test_validate_exits_zero_for_valid(tmp_path):
    assert run("validate", tmp_path, body()).returncode == 0


def test_validate_exits_nonzero_and_prints_errors(tmp_path):
    proc = run("validate", tmp_path, body(VALID.replace("[docs, tests]", "[docs, styles]")))
    assert proc.returncode != 0
    assert "unknown category: styles" in proc.stdout + proc.stderr
