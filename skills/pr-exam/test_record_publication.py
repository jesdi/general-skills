"""Regression tests: `record` refuses a concept that is not a slug, publishes a successor before it
supersedes its predecessor, and `learned` counts the newest of two active records.
Spec specs/pr-exam: requirement 8; design.md "Learning record".
"""
import json
import os

from test_learning_records import cli, exam_with, field, learned_ok, write_record, TODAY, WEEK


def run_record(tmp_path, records, concept, points):
    (tmp_path / "rexam.json").write_text(json.dumps(exam_with([(concept, "text", "concept")])))
    (tmp_path / "result.json").write_text(json.dumps(
        {"passed": False, "score": points, "max": 1, "missed": [],
         "questions": [{"id": "q1", "concept": concept, "points": points}]}))
    return cli("record", "--records", records, "--exam", tmp_path / "rexam.json",
               "--result", tmp_path / "result.json", "--today", TODAY)


def test_record_refuses_a_concept_that_is_not_a_slug(tmp_path):
    records = tmp_path / "records"
    r = run_record(tmp_path, records, "../escape", 1)
    assert r.returncode == 1 and "slug" in r.stderr, r.stderr
    assert not (tmp_path / "escape.md").exists()
    assert not any(records.glob("*.md"))


def test_failed_successor_write_keeps_the_previous_record_active(tmp_path):
    records = tmp_path / "records"
    write_record(records, 1, "merge-lease", "demonstrated", WEEK)
    (records / ".lock").touch()
    os.chmod(records, 0o555)  # existing files stay writable; no new file can be created
    try:
        assert run_record(tmp_path, records, "merge-lease", 0).returncode != 0
    finally:
        os.chmod(records, 0o755)
    assert field(records / "0001-merge-lease.md", "status") == "active"
    assert learned_ok(records) == {"merge-lease": "skip"}


def test_newest_of_two_active_records_counts(tmp_path):
    records = tmp_path / "records"
    write_record(records, 9, "merge-lease", "demonstrated", WEEK)
    write_record(records, 10, "merge-lease", "misconception", TODAY)
    assert learned_ok(records) == {"merge-lease": "misconception"}
