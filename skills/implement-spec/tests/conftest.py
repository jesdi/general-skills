"""The fixture repository of the in-process tests: a clean git worktree on feat/x with .agent ignored."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the driver modules are one folder up

import driver_agents  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def ticket(number, blocked="None (can start immediately)", touches="`demo/x.py: f`", seam="`f()`"):
    return f"# {number}: Ticket {number}\n\n**Blocked by:** {blocked}\n\n**Seam:** {seam}\n\n**Touches:** {touches}\n\n- [ ] It works\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clean git worktree with .agent ignored; returns a writer for the ticket set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(driver_agents.shutil, "which", lambda cli: cli)  # CI has no claude or codex
    for index, setting in enumerate(GIT[2::2]):  # the fake agents commit too
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", setting.partition("=")[0])
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", setting.partition("=")[2])
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    subprocess.run([*GIT, "init", "-q", "-b", "feat/x"], check=True)
    (tmp_path / ".gitignore").write_text(".agent/\n")
    subprocess.run([*GIT, "add", "-A"], check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "init"], check=True)

    def write(**blocked):
        (tmp_path / ".agent/tickets").mkdir(parents=True, exist_ok=True)
        for number, text in blocked.items():
            (tmp_path / f".agent/tickets/{number[1:]}-t.md").write_text(text)
        return tmp_path
    return write
