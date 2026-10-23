"""Completed handoffs identify themselves even when merge exits with an error."""
import json

from test_merge_and_handoff import cli, merge, world


def test_direct_handoff_returns_terminal_notification_instruction(tmp_path):
    w = world(tmp_path)
    fix_log = tmp_path / "fix-log.md"
    fix_log.write_text("No changes pushed.")
    p = cli(w, "handoff", "--repo", "jesdi/demo", "--pr", "58",
            "--reason", "cannot reproduce", "--ci", "red", "--rounds", "1",
            "--gate", "not run", "--judge", "not run", "--fix-log", str(fix_log))
    assert p.returncode == 0, p.output
    result = json.loads(p.stdout)
    assert result["terminal"] is True and result["status"] == "handoff"
    assert result["reason"] == "cannot reproduce"
    assert "PushNotification" in result["next"]


def test_merge_handoff_is_terminal_despite_error_exit(tmp_path):
    w = world(tmp_path)
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "disagree")
    assert p.returncode != 0
    result = json.loads(p.stdout)
    assert result["terminal"] is True and result["status"] == "handoff"
    assert result["reason"] == "judge disagree"
    assert "end the turn" in result["next"]
    assert not w.github().get("merges")
