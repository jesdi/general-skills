"""Heredoc substitutions run as the shell runs them: continuations folded, before the line's `cd`."""
from test_hooks import assert_deny, claude, make_repo, run_hook

REASON = "the body must come from --body or --body-file"


def test_continuation_inside_heredoc_does_not_hide_substitution(tmp_path):
    repo = make_repo(tmp_path)
    assert_deny(run_hook(claude("cat <<EOF\n$\\\n(gh pr create --fill)\nEOF", repo)), REASON)


def test_heredoc_substitution_runs_before_the_commands_cd(tmp_path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert_deny(run_hook(claude(f"cd {outside} <<EOF\n$(gh pr create --fill)\nEOF", repo)), REASON)
