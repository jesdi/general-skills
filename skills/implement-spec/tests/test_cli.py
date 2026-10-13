"""Driver acceptance tests, through `sh run.sh` in a throwaway fixture repository."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
RUN = SKILL / "run.sh"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
TOOLS = ("sh", "uname", "dirname", "readlink", "git", "env", "mktemp", "curl", "tar", "gzip", "mv", "rm", "mkdir",
         "sha256sum", "shasum", "cat", "chmod", "ln", "date", "basename")
ROLES = ("explorer", "test-writer", "implementer", "reviewer", "correctness", "fixer", "final-reviewer", "conflict")
NONE = "None (can start immediately)"


def ticket(number, title, blocked=NONE, *, seam="`export_csv()`", touches="`demo/export.py: export_csv`",
           criteria=("It works",), risk=None):
    lines = [f"# {number}: {title}" if title else "", "", "**What to build:** Something.", "",
             f"**Blocked by:** {blocked}", "", f"**Seam:** {seam}", ""]
    if touches:
        lines += [f"**Touches:** {touches}", ""]
    if risk:
        lines += [f"**Risk:** {risk}", ""]
    return "\n".join(lines + [f"- [ ] {c}" for c in criteria] + [""])


def three(**changes):
    files = {
        "01-export-rows.md": ticket("01", "Export rows", criteria=("Rows export", "Header exports")),
        "02-quote-fields.md": ticket("02", "Quote fields", "01", risk="persistence"),
        "03-cli-flag.md": ticket("03", "CLI flag", seam="`demo export` CLI", touches="`demo/cli.py: main`"),
    }
    files.update(changes)
    return files


def git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout


def snapshot(root):
    """Every file under root (but .git) with its bytes, plus the branch list."""
    files = {p.relative_to(root).as_posix(): p.read_bytes()
             for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}
    return files, git(root, "branch", "--list")


def make_repo(root, files, *, ignore_agent=True, commit_agent=False):
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "demo").mkdir()
    (root / "demo/export.py").write_text("def export_csv():\n    return ''\n")
    (root / ".gitignore").write_text(".agent/\n" if ignore_agent else ".crap/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "checkout", "-q", "-b", "feat/csv")
    tickets = root / ".agent/tickets"
    tickets.mkdir(parents=True)
    for name, text in files.items():
        (tickets / name).write_text(text)
    if commit_agent:
        git(root, "add", "-f", ".agent")
        git(root, "commit", "-q", "-m", "tickets")
    return root


@pytest.fixture
def env(tmp_path):
    """A PATH holding only basic tools plus fake claude and codex that log any launch."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in TOOLS:
        if shutil.which(name):
            (bin_dir / name).symlink_to(shutil.which(name))
    for cli in ("claude", "codex"):
        (bin_dir / cli).write_text(f'#!/bin/sh\necho {cli} >> "{tmp_path}/launched"\nexit 1\n')
        (bin_dir / cli).chmod(0o755)
    return dict(os.environ, PATH=str(bin_dir)), tmp_path


def models_file(tmp_path, **overrides):
    entries = {role: ["anthropic/claude-opus-5@medium"] for role in ROLES}
    entries["correctness"] = ["openai/gpt-sol@medium"]
    entries.update(overrides)
    path = tmp_path / "models.json"
    path.write_text(json.dumps(entries))
    return path


def drive(env, repo, *args, script=RUN):
    env_vars, _ = env
    args = args or ("--tickets", ".agent/tickets", "--gate", "make check", "--dry-run")
    return subprocess.run(["sh", str(script), *args], cwd=repo, env=env_vars,
                          text=True, capture_output=True, timeout=300)


def assert_untouched(proc, repo, before, tmp_path):
    assert snapshot(repo) == before
    assert not (tmp_path / "launched").exists()


def test_missing_touches_names_file_and_seam_line_and_creates_nothing(env, tmp_path):
    files = three(**{"02-quote-fields.md": ticket("02", "Quote fields", "01", touches=None)})
    repo = make_repo(tmp_path / "demo", files)
    before = snapshot(repo)
    seam_line = files["02-quote-fields.md"].splitlines().index("**Seam:** `export_csv()`") + 1
    proc = drive(env, repo)
    assert proc.returncode == 3, proc.stderr
    (line,) = [l for l in proc.stdout.splitlines() if "missing Touches" in l]
    assert line.startswith("02-quote-fields.md") and re.search(rf"\b{seam_line}\b", line)
    assert_untouched(proc, repo, before, tmp_path)


def test_every_error_is_listed_in_one_run(env, tmp_path):
    files = three(**{"01-export-rows.md": ticket("01", None), "03-cli-flag.md": ticket("03", "CLI flag", criteria=())})
    repo = make_repo(tmp_path / "demo", files)
    proc = drive(env, repo)
    assert proc.returncode == 3, proc.stderr
    lines = proc.stdout.splitlines()
    assert any("01-export-rows.md" in l and "missing title" in l for l in lines)
    assert any("03-cli-flag.md" in l and "missing criterion" in l for l in lines)


def test_single_ticket_without_touches_is_accepted(env, tmp_path):
    repo = make_repo(tmp_path / "demo", {"01-export-rows.md": ticket("01", "Export rows", touches=None)})
    proc = drive(env, repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "max frontier width: 1" in proc.stdout
    assert "missing" not in proc.stdout


def test_refactor_seam_is_accepted_with_no_criterion(env, tmp_path):
    files = {"01-export-rows.md": ticket("01", "Export rows", seam="none — refactor", criteria=())}
    repo = make_repo(tmp_path / "demo", files)
    proc = drive(env, repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "max frontier width: 1" in proc.stdout


def test_blocked_by_cycle_prints_one_error_naming_both_files(env, tmp_path):
    files = three(**{"01-export-rows.md": ticket("01", "Export rows", "03"),
                     "03-cli-flag.md": ticket("03", "CLI flag", "01", touches="`demo/cli.py: main`")})
    repo = make_repo(tmp_path / "demo", files)
    proc = drive(env, repo)
    assert proc.returncode == 3, proc.stderr
    both = [l for l in proc.stdout.splitlines() if "01-export-rows.md" in l and "03-cli-flag.md" in l]
    assert len(both) == 1


def test_blocked_by_unknown_number_names_file_and_number(env, tmp_path):
    files = three(**{"02-quote-fields.md": ticket("02", "Quote fields", "07")})
    repo = make_repo(tmp_path / "demo", files)
    proc = drive(env, repo)
    assert proc.returncode == 3, proc.stderr
    assert any("02-quote-fields.md" in l and "07" in l for l in proc.stdout.splitlines())


@pytest.mark.parametrize("files, code", [
    (three(**{"02-quote-fields.md": ticket("02", "Quote fields", "07")}), 3),
    (three(), 0),
])
def test_driver_never_modifies_a_ticket_file(env, tmp_path, files, code):
    repo = make_repo(tmp_path / "demo", files)
    before = {p.name: p.read_bytes() for p in (repo / ".agent/tickets").iterdir()}
    proc = drive(env, repo)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert {p.name: p.read_bytes() for p in (repo / ".agent/tickets").iterdir()} == before


def test_run_without_gate_exits_3_naming_the_flag(env, tmp_path):
    repo = make_repo(tmp_path / "demo", three())
    proc = drive(env, repo, "--tickets", ".agent/tickets", "--dry-run")
    assert proc.returncode == 3, proc.stderr
    assert "--gate" in proc.stdout


def test_agent_dir_not_ignored_exits_3(env, tmp_path):
    repo = make_repo(tmp_path / "demo", three(), ignore_agent=False, commit_agent=True)
    proc = drive(env, repo)
    assert proc.returncode == 3, proc.stderr
    assert ".agent is not gitignored" in proc.stdout


def test_dirty_worktree_exits_3_naming_the_paths(env, tmp_path):
    repo = make_repo(tmp_path / "demo", three())
    (repo / "demo/export.py").write_text("def export_csv():\n    return 'edited'\n")
    (repo / "notes.txt").write_text("untracked\n")
    proc = drive(env, repo)
    assert proc.returncode == 3, proc.stderr
    assert "demo/export.py" in proc.stdout and "notes.txt" in proc.stdout


def test_models_file_naming_a_provider_without_its_cli_exits_3(env, tmp_path):
    (tmp_path / "bin/codex").unlink()
    repo = make_repo(tmp_path / "demo", three())
    models = models_file(tmp_path, implementer=["openai/gpt-sol@medium"])
    proc = drive(env, repo, "--tickets", ".agent/tickets", "--gate", "make check", "--models", str(models), "--dry-run")
    assert proc.returncode == 3, proc.stderr
    assert any("implementer" in l and "codex" in l for l in proc.stdout.splitlines())


@pytest.mark.parametrize("link", ["directory", "file"])
def test_valid_set_prints_frontier_width_and_dry_run_creates_nothing(env, tmp_path, link):
    repo = make_repo(tmp_path / "demo", three())
    script = RUN
    if link == "directory":  # an install through ~/.my-skills links the whole skill directory
        (tmp_path / "skills link").symlink_to(SKILL)
        script = tmp_path / "skills link/run.sh"
    elif link == "file":
        (tmp_path / "run link.sh").symlink_to(RUN)
        script = tmp_path / "run link.sh"
    before = snapshot(repo)
    proc = drive(env, repo, "--tickets", ".agent/tickets", "--gate", "make check", "--dry-run", script=script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "max frontier width: 2" in proc.stdout
    assert_untouched(proc, repo, before, tmp_path)
