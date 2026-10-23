"""Acceptance tests for `hooks/pre-pr-create.sh`: agent payload JSON on stdin, agent output JSON on stdout.

Deny is `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": ...}}`. Allow is empty stdout (or any output that is not a deny).
"""
import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).with_name("hooks") / "pre-pr-create.sh"

VALID_BODY = '''## Summary

Text.

## Merge Danger

```merge-risk v1
door: two-way
blast_radius: isolated
touches: [docs, tests]
rollback: revert
verdict: auto
reasons:
  - "Only skill prose and its tests change"
```
'''
NO_BLOCK_BODY = "## Summary\n\nText, no block.\n"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path, policy=True):
    """A clone of an origin whose base has no policy; the policy, when asked, is only in the working tree."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    seed.mkdir()
    git(seed, "init", "-b", "main")
    (seed / "README.md").write_text("hi\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "init")
    git(seed, "push", str(origin), "main")
    git(tmp_path, "clone", str(origin), str(repo))
    if policy:
        (repo / ".merge-risk.json").write_text('{"agentMerge": true}\n')
    return repo


def run_hook(payload):
    return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                          text=True, capture_output=True, timeout=30)


def claude(command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "cwd": str(cwd)}


def decision(proc):
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None, ""
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out["permissionDecision"], out.get("permissionDecisionReason", "")


def assert_deny(proc, *needles):
    verdict, reason = decision(proc)
    assert verdict == "deny", proc.stdout
    for n in needles:
        assert n in reason, reason


def assert_allow(proc):
    assert decision(proc)[0] != "deny", proc.stdout


def test_body_file_without_block_is_denied_with_the_fix(tmp_path):
    repo = make_repo(tmp_path)
    body = tmp_path / "body.md"
    body.write_text(NO_BLOCK_BODY)
    proc = run_hook(claude(f"gh pr create --title x --body-file {body}", repo))
    assert_deny(proc, "/pr, then /pr-risk")


def test_valid_block_in_body_substitution_is_allowed(tmp_path):
    repo = make_repo(tmp_path)
    body = tmp_path / "body.md"
    body.write_text(VALID_BODY)
    proc = run_hook(claude(f'gh pr create --title x --body "$(cat {body})"', repo))
    assert_allow(proc)


def test_fill_variants_and_no_body_flag_are_denied(tmp_path):
    repo = make_repo(tmp_path)
    for cmd in ("gh pr create --fill", "gh pr create --fill-first", "gh pr create --web",
                "gh pr create --title x"):
        assert_deny(run_hook(claude(cmd, repo)), "the body must come from --body or --body-file")


def test_repo_without_policy_allows_with_no_output(tmp_path):
    repo = make_repo(tmp_path, policy=False)
    proc = run_hook(claude("gh pr create --fill", repo))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_other_command_allows_with_no_output(tmp_path):
    repo = make_repo(tmp_path)
    proc = run_hook(claude("gh pr view 12", repo))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_policy_only_in_working_tree_still_enforces(tmp_path):
    repo = make_repo(tmp_path)
    assert (repo / ".merge-risk.json").exists()
    assert_deny(run_hook(claude("gh pr create --fill", repo)))


def test_codex_payload_is_denied(tmp_path):
    repo = make_repo(tmp_path)
    proc = run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr create --fill"},
                     "cwd": str(repo)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"


def test_gh_pr_create_inside_a_longer_command_is_denied(tmp_path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert_deny(run_hook(claude(f"cd {repo} && gh pr create --fill", outside)))
    (repo / "body.md").write_text(VALID_BODY)
    assert_allow(run_hook(claude(f"cd {repo} && gh pr create --title x --body-file body.md", outside)))
    (repo / "bad.md").write_text(NO_BLOCK_BODY)
    assert_deny(run_hook(claude(f"cd {repo} && gh pr create --title x --body-file bad.md", outside)),
                "/pr, then /pr-risk")


def test_relative_body_file_resolves_against_cwd(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "body.md").write_text(VALID_BODY)
    assert_allow(run_hook(claude("gh pr create --title x --body-file body.md", repo)))


def heredoc_command(body):
    return f"gh pr create --title x --body \"$(cat <<'EOF'\n{body}EOF\n)\""


def test_valid_block_in_quoted_heredoc_is_allowed(tmp_path):
    repo = make_repo(tmp_path)
    assert_allow(run_hook(claude(heredoc_command(VALID_BODY), repo)))


def test_heredoc_body_without_block_is_denied(tmp_path):
    repo = make_repo(tmp_path)
    assert_deny(run_hook(claude(heredoc_command(NO_BLOCK_BODY), repo)), "/pr, then /pr-risk")


def test_malformed_existing_blocks_are_denied(tmp_path):
    repo = make_repo(tmp_path)
    bad_value = VALID_BODY.replace("door: two-way", "door: sideways")
    missing_field = VALID_BODY.replace("rollback: revert\n", "")
    duplicate = VALID_BODY + "\n" + VALID_BODY
    for name, body in (("value", bad_value), ("missing", missing_field), ("dup", duplicate)):
        (repo / f"{name}.md").write_text(body)
        proc = run_hook(claude(f"gh pr create --title x --body-file {name}.md", repo))
        assert_deny(proc, "/pr, then /pr-risk")
