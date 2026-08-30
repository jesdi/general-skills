import subprocess
from pathlib import Path

import pytest

from crap_git import changed_files, merge_base, show, toplevel, touched_lines, tracked_files
from crap_model import ToolError

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def f(x):\n    return x\n\n\ndef g(y):\n    return y\n")
    (tmp_path / "pkg" / "old.py").write_text("def h():\n    return 1\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "switch", "-q", "-c", "feature")
    return tmp_path


def test_toplevel_and_merge_base(repo):
    assert toplevel(repo / "pkg") == repo
    assert merge_base(repo, "main") == git(repo, "rev-parse", "main").strip()
    with pytest.raises(ToolError, match="nope"):
        merge_base(repo, "nope")


def test_changed_files_covers_modified_added_renamed_untracked_and_skips_deleted(repo):
    base = merge_base(repo, "main")
    (repo / "pkg" / "a.py").write_text("def f(x):\n    return x + 1\n\n\ndef g(y):\n    return y\n")
    git(repo, "mv", "pkg/old.py", "pkg/new.py")
    (repo / "pkg" / "added.py").write_text("def k():\n    return 2\n")
    git(repo, "add", "pkg/added.py")
    git(repo, "commit", "-q", "-m", "work")
    (repo / "pkg" / "untracked.py").write_text("def u():\n    return 3\n")
    (repo / "pkg" / "gone.py").write_text("x = 1\n")
    git(repo, "add", "pkg/gone.py")
    git(repo, "commit", "-q", "-m", "add gone")
    (repo / "pkg" / "gone.py").unlink()
    assert changed_files(repo, base) == [
        ("pkg/a.py", "pkg/a.py"),
        ("pkg/added.py", None),
        ("pkg/new.py", "pkg/old.py"),
        ("pkg/untracked.py", None),
    ]


def test_touched_lines_include_pure_deletions(repo):
    base = merge_base(repo, "main")
    # change line 2, delete lines 5-6 (function g)
    (repo / "pkg" / "a.py").write_text("def f(x):\n    return x * 2\n\n\n")
    assert touched_lines(repo, base, "pkg/a.py") == [(2, 2), (4, 5)]


def test_show_returns_base_content_or_none(repo):
    base = merge_base(repo, "main")
    assert show(repo, base, "pkg/old.py") == "def h():\n    return 1\n"
    assert show(repo, base, "pkg/missing.py") is None


def test_tracked_files_includes_untracked_and_excludes_deleted(repo):
    (repo / "pkg" / "u.py").write_text("")
    (repo / "pkg" / "old.py").unlink()
    assert tracked_files(repo) == ["pkg/a.py", "pkg/u.py"]
