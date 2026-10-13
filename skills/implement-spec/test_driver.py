"""In-process driver tests: the same seam as test_cli.py without the launcher, so coverage sees it."""
import json
import subprocess

import pytest

import driver

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
ARGS = ["--gate", "make check", "--dry-run"]


def ticket(number, blocked="None (can start immediately)", touches="`demo/x.py: f`"):
    return f"# {number}: Ticket {number}\n\n**Blocked by:** {blocked}\n\n**Seam:** `f()`\n\n**Touches:** {touches}\n\n- [ ] It works\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clean git worktree with .agent ignored; returns a writer for the ticket set."""
    monkeypatch.chdir(tmp_path)
    subprocess.run([*GIT, "init", "-q"], check=True)
    (tmp_path / ".gitignore").write_text(".agent/\n")
    subprocess.run([*GIT, "add", "-A"], check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "init"], check=True)

    def write(**blocked):
        (tmp_path / ".agent/tickets").mkdir(parents=True, exist_ok=True)
        for number, text in blocked.items():
            (tmp_path / f".agent/tickets/{number[1:]}-t.md").write_text(text)
        return tmp_path
    return write


def run(capsys, *args):
    code = driver.main([*ARGS, *args])
    return code, capsys.readouterr().out


def test_valid_set_prints_the_widest_level(repo, capsys):
    repo(t01=ticket("01"), t02=ticket("02", "01"), t03=ticket("03"), t04=ticket("04", "Ticket 02, 3"))
    assert run(capsys) == (0, "max frontier width: 2\n")


def test_a_run_without_dry_run_says_what_is_missing(repo, capsys):
    repo(t01=ticket("01"))
    assert driver.main(["--gate", "make check"]) == 3
    assert "only --dry-run is implemented" in capsys.readouterr().out


def test_every_environment_and_ticket_error_is_listed_together(repo, capsys):
    root = repo(t01=ticket("01", "02"), t02=ticket("02", "01"), t03=ticket("03", "09, Nope", touches=""))
    (root / ".gitignore").write_text("")
    assert driver.main(["--dry-run"]) == 3
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("--gate")
    assert ".agent is not gitignored" in out
    assert "uncommitted change: .gitignore" in out
    assert "03-t.md: missing Touches (expected after Seam, line 5)" in out
    assert "03-t.md: Blocked by 09 names no ticket (line 3)" in out
    assert "03-t.md: Blocked by 'Nope' names no ticket (line 3)" in out
    assert "01-t.md: Blocked by cycle: 01-t.md -> 02-t.md -> 01-t.md" in out


def test_a_ticket_with_no_fields_lists_each_missing_one(repo, capsys):
    repo(t01="nothing here\n")
    code, out = run(capsys)
    assert code == 3
    assert out.splitlines() == ["01-t.md: missing title", "01-t.md: missing Blocked by", "01-t.md: missing Seam",
                                "01-t.md: missing criterion"]


def test_a_directory_without_tickets_is_an_error(repo, capsys):
    assert run(capsys, "--tickets", "nowhere") == (3, "nowhere: no ticket files (NN-name.md)\n")


def test_outside_a_git_worktree_git_errors_are_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    code, out = run(capsys)
    assert code == 3 and "git status failed" in out


def test_models_file_checks_the_first_entry_of_each_role(repo, capsys, tmp_path, monkeypatch):
    repo(t01=ticket("01"))
    monkeypatch.setattr(driver.shutil, "which", lambda cli: None if cli == "codex" else cli)
    models = tmp_path.parent / f"{tmp_path.name}-models.json"
    models.write_text(json.dumps({"implementer": ["openai/gpt-sol@medium"], "reviewer": ["claude-opus-5@medium"],
                                  "correctness": ["openai/gpt-sol@medium"], "fixer": ["other/x@low"]}))
    assert run(capsys, "--models", str(models)) == (
        3, "implementer: codex is not on PATH (first model entry openai/gpt-sol@medium)\n")


@pytest.mark.parametrize("content", ["{", "[]", '{"implementer": []}'])
def test_unreadable_models_file_is_an_error(repo, capsys, tmp_path, content):
    repo(t01=ticket("01"))
    models = tmp_path.parent / f"{tmp_path.name}-models.json"
    models.write_text(content)
    code, out = run(capsys, "--models", str(models))
    assert code == 3 and out.startswith(f"--models {models}: ")


def test_unknown_flag_exits_3_on_stdout(repo, capsys):
    with pytest.raises(SystemExit) as exit_info:
        driver.main(["--nope"])
    assert exit_info.value.code == 3
    assert "implement-spec: unrecognized arguments: --nope" in capsys.readouterr().out
