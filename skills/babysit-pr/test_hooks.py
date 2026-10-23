import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / "hooks" / "post-pr-create.sh"
URL = "https://github.com/jesdi/demo/pull/58"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".merge-risk.json").write_text("{}")
    return tmp_path


def run(cwd, command, response, codex=False):
    """Feed one PostToolUse payload to the hook; return its stdout."""
    if codex:
        response = response["stdout"] if isinstance(response, dict) else response
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": response,
        "cwd": str(cwd),
    }
    p = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True)
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
