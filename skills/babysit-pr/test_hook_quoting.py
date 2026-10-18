import pytest

from test_hooks import OK, URL, context, repo, run


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("body", [
    '"text --draft text"',
    '"hello ; world"',
    "'hello ; world'",
    '";"',
    r'hello\;world',
    'hello" ; "world',
    r'"hello \" ; world"',
])
def test_attached_body_preserves_shell_quoting(repo, codex, body):
    command = f"gh pr create --title x --body={body}"
    assert f"/babysit-pr {URL}" in context(run(repo, command, OK, codex))
    assert run(repo, command + " --draft", OK, codex) == ""


@pytest.mark.parametrize("separator", [";", "&&", "||", "\n", "|"])
def test_unquoted_operator_still_separates_commands(repo, separator):
    command = f'echo x{separator}gh pr create --body="hello ; world"'
    assert f"/babysit-pr {URL}" in context(run(repo, command, OK))
