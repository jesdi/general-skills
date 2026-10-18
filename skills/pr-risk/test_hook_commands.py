"""Regression coverage for PR command recognition and forbidden creation modes."""
import pytest

from test_hooks import VALID_BODY, assert_allow, assert_deny, make_repo, run_hook


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("command", [
    "if true; then gh pr create --fill; fi",
    "if gh pr create --fill; then true; fi",
    "if false; then true; else gh pr create --fill; fi",
    "while false; do gh pr create --fill; done",
    "gh --repo owner/repo pr create --fill",
    "gh --repo=owner/repo pr create --fill",
    "gh -R owner/repo pr create --fill",
    "gh -Rowner/repo pr create --fill",
    "gh pr --repo owner/repo create --fill",
    "gh pr create --web --body-file body.md",
    "gh pr create --fill --body-file body.md",
    "gh pr create --fill-first --body-file body.md",
])
def test_creation_bypasses_are_denied_only_with_policy(tmp_path, agent, command):
    repo = make_repo(tmp_path)
    (repo / "body.md").write_text(VALID_BODY)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)}
    if agent == "claude":
        payload["hook_event_name"] = "PreToolUse"
    assert_deny(run_hook(payload), "the body must come from --body or --body-file",
                "/pr, then /pr-risk")
    (repo / ".merge-risk.json").unlink()
    proc = run_hook(payload)
    assert_allow(proc)
    assert proc.stdout == ""


@pytest.mark.parametrize("command", [
    "if true; then gh pr create --body-file body.md; fi",
    "gh --repo owner/repo pr create --body-file body.md",
    "gh pr create --title --web --body-file body.md",
    "echo gh --repo owner/repo pr create --fill",
    "gh --repo owner/repo pr view 12",
])
def test_valid_creations_and_other_commands_are_allowed(tmp_path, command):
    repo = make_repo(tmp_path)
    (repo / "body.md").write_text(VALID_BODY)
    assert_allow(run_hook({"tool_name": "Bash", "tool_input": {"command": command},
                           "cwd": str(repo)}))
