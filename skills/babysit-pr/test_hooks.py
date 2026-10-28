import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / "hooks" / "post-pr-create.sh"
STOP_HOOK = Path(__file__).parent / "hooks" / "stop-babysit-pending.sh"
URL = "https://github.com/jesdi/demo/pull/58"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".merge-risk.json").write_text("{}")
    return tmp_path


def run(cwd, command, response, codex=False, **fields):
    """Feed one PostToolUse payload to the hook; return its stdout."""
    if codex:
        response = response["stdout"] if isinstance(response, dict) else response
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": response,
        "cwd": str(cwd),
        **fields,
    }
    p = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=_env(cwd))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _env(tmp):
    """Each test gets its own pending queue: TMPDIR points into its tmp_path."""
    return {**os.environ, "TMPDIR": str(tmp)}


def stop(tmp, session="s1"):
    """Feed one Stop payload to the stop hook; return its stdout."""
    payload = {"hook_event_name": "Stop", "session_id": session, "cwd": str(tmp)}
    p = subprocess.run(["bash", str(STOP_HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=_env(tmp))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


OK = {"stdout": f"Creating pull request\n{URL}\n", "stderr": "", "interrupted": False}


def context(out):
    out = json.loads(out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    return out["additionalContext"]


@pytest.mark.parametrize("codex", [False, True])
def test_created_pr_tells_agent_to_babysit(repo, codex):
    out = run(repo, "gh pr create --title x --body y", OK, codex)
    assert f"/babysit-pr {URL}" in context(out)


@pytest.mark.parametrize("codex", [False, True])
def test_draft_adds_nothing(repo, codex):
    assert run(repo, "gh pr create --draft --title x --body y", OK, codex) == ""


@pytest.mark.parametrize("codex", [False, True])
def test_failed_create_adds_nothing(repo, codex):
    failed = {"stdout": "", "stderr": "GraphQL: error\n", "interrupted": False}
    assert run(repo, "gh pr create --title x --body y", failed, codex) == ""


@pytest.mark.parametrize("codex", [False, True])
def test_repo_without_policy_adds_nothing(tmp_path, codex):
    assert run(tmp_path, "gh pr create --title x --body y", OK, codex) == ""


SUB = {"session_id": "s1", "agent_id": "a1", "agent_type": "general-purpose"}


def test_subagent_pr_is_left_to_the_main_session(repo):
    out = context(run(repo, "gh pr create --title x --body y", OK, **SUB))
    assert URL in out
    assert f"/babysit-pr {URL}" not in out


def test_main_session_babysits_a_subagent_pr_when_it_stops(repo):
    run(repo, "gh pr create --title x --body y", OK, **SUB)
    out = json.loads(stop(repo))
    assert out["decision"] == "block"
    assert f"/babysit-pr {URL}" in out["reason"]


def test_subagent_pr_is_handed_over_once(repo):
    run(repo, "gh pr create --title x --body y", OK, **SUB)
    stop(repo)
    assert stop(repo) == ""


def test_two_subagent_prs_are_both_handed_over(repo):
    other = "https://github.com/jesdi/demo/pull/59"
    run(repo, "gh pr create --title x --body y", OK, **SUB)
    run(repo, "gh pr create --title x --body y",
        {**OK, "stdout": f"{other}\n"}, **{**SUB, "agent_id": "a2"})
    reason = json.loads(stop(repo))["reason"]
    assert f"/babysit-pr {URL}" in reason and f"/babysit-pr {other}" in reason


def test_other_session_does_not_pick_up_the_pr(repo):
    run(repo, "gh pr create --title x --body y", OK, **SUB)
    assert stop(repo, session="s2") == ""
    assert f"/babysit-pr {URL}" in json.loads(stop(repo))["reason"]


def test_stop_with_nothing_pending_adds_nothing(tmp_path):
    assert stop(tmp_path) == ""


def test_main_session_pr_is_not_queued(repo):
    run(repo, "gh pr create --title x --body y", OK, session_id="s1")
    assert stop(repo) == ""


def test_unsafe_session_id_stays_inside_the_queue(repo):
    run(repo, "gh pr create --title x --body y", OK, **{**SUB, "session_id": "../../x"})
    assert not (repo.parent / "x").exists()
    assert f"/babysit-pr {URL}" in json.loads(stop(repo, session="../../x"))["reason"]
