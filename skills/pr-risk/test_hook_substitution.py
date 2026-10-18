"""Acceptance tests: `gh pr create` inside a command substitution, and shell quoting of the body."""
from test_hooks import NO_BLOCK_BODY, VALID_BODY, assert_allow, assert_deny, claude, make_repo, run_hook

BODY_FLAG_REASON = "the body must come from --body or --body-file"


def test_substituted_fill_is_denied(tmp_path):
    repo = make_repo(tmp_path)
    assert_deny(run_hook(claude("url=$(gh pr create --fill)", repo)), BODY_FLAG_REASON)


def test_substituted_body_file_without_block_is_denied(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "body.md").write_text(NO_BLOCK_BODY)
    cmd = "url=$(gh pr create --title x --body-file /tmp/body.md)".replace("/tmp/body.md", str(repo / "body.md"))
    assert_deny(run_hook(claude(cmd, repo)), "/pr, then /pr-risk")


def test_backtick_substitution_is_denied(tmp_path):
    repo = make_repo(tmp_path)
    assert_deny(run_hook(claude("url=`gh pr create --fill`", repo)), BODY_FLAG_REASON)


def test_single_quoted_substitution_body_is_literal_and_denied(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "body.md").write_text(VALID_BODY)
    cmd = "gh pr create --title x --body '$(cat body.md)'"
    assert_deny(run_hook(claude(cmd, repo)), "/pr, then /pr-risk")


def test_double_quoted_substitution_body_is_expanded_and_allowed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "body.md").write_text(VALID_BODY)
    assert_allow(run_hook(claude('gh pr create --title x --body "$(cat body.md)"', repo)))


def test_substitution_without_pr_create_allows_with_no_output(tmp_path):
    repo = make_repo(tmp_path)
    proc = run_hook(claude("x=$(git rev-parse HEAD) && echo $x", repo))
    assert proc.returncode == 0 and proc.stdout.strip() == ""
