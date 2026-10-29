import pytest

from fixture_repo import git, snapshots


@pytest.mark.parametrize("args", ["--junitxml=report.xml", "-q --junit-xml report.xml", "--debug"])
def test_file_writing_pytest_args_are_rejected_and_leave_the_repo_untouched(run, home, args):
    repo, result = run(f"--pytest-args={args}")
    assert result.returncode != 0
    assert "writes files" in result.stderr
    assert git(repo, "status", "--porcelain") == ""
    assert snapshots(home, "github.com/jesdi/demo") == []
