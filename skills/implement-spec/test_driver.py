"""In-process driver tests: the same seam as test_cli.py without the launcher, so coverage sees it."""
import json
import os
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest

import driver
import driver_agents
import driver_report

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
ARGS = ["--gate", "make check", "--dry-run"]


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


def run(capsys, *args):
    code = driver.main([*ARGS, *args])
    return code, capsys.readouterr().out


def test_valid_set_prints_the_widest_level(repo, capsys):
    repo(t01=ticket("01"), t02=ticket("02", "01"), t03=ticket("03"), t04=ticket("04", "Ticket 02, 3"))
    assert run(capsys) == (0, "max frontier width: 2\n")


def test_a_run_without_dry_run_needs_the_spec(repo, capsys):
    repo(t01=ticket("01"))
    assert driver.main(["--gate", "make check"]) == 3
    assert capsys.readouterr().out == "--spec is required: the spec file the prompts point at\n"
    assert "ticket/01" not in subprocess.run(["git", "branch"], capture_output=True, text=True).stdout


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
    monkeypatch.setattr(driver_agents.shutil, "which", lambda cli: None if cli == "codex" else cli)
    models = tmp_path.parent / f"{tmp_path.name}-models.json"
    models.write_text(json.dumps({"implementer": ["openai/gpt-sol@medium"], "reviewer": ["claude-opus-5@medium"],
                                  "correctness": ["openai/gpt-sol@medium"], "fixer": ["other/x@low"]}))
    assert run(capsys, "--models", str(models)) == (
        3, "implementer: codex is not on PATH (first model entry openai/gpt-sol@medium)\n"
           "fixer: model entry other/x@low names an unknown provider (known: anthropic, openai)\n")


@pytest.mark.parametrize("entry", ["claude-opus-5@turbo", "openai/gpt-sol", "openai/@low"])
def test_an_entry_needs_a_model_and_an_effort_of_its_provider(repo, capsys, tmp_path, entry):
    repo(t01=ticket("01"))
    models = tmp_path.parent / f"{tmp_path.name}-models.json"
    models.write_text(json.dumps({"reviewer": ["claude-opus-5@low", entry]}))
    code, out = run(capsys, "--models", str(models))
    assert code == 3 and out.startswith(f"reviewer: model entry {entry} needs a model and an effort from ")


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


# In-process agent runs: a fake runner script, as in test_agents.py.

REPORT = """printf '{"status": "%s", "commits": ["%s"], "files": [], "concerns": [], %s}' \\
    "${STATUS:-DONE}" "$(git rev-parse HEAD)" "$fields" > "$report"
"""
COMMIT = "echo $role >> work.txt; git add -A; git commit -q -m $role\n"
WRITER = """fields='"locked": ["work.txt"], "red": [{"test": "t", "line": "FAILED t"}]'; echo FAILED t\n"""
IMPLEMENTER = """fields='"green": ["1 passed"]'; echo '{"total_cost_usd": 0.25}'\n"""
ONCE = '[ -e "$0.again" ] || { touch "$0.again"; %s; }\n'  # the first attempt only


def agents(tmp_path, capsys, body, *args, seam="none — refactor"):
    """Run the driver on one ticket with a runner made of `body`; returns the exit code and the ledger lines."""
    (tmp_path / ".agent/tickets").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent/tickets/01-t.md").write_text(ticket("01", seam=seam))
    runner = tmp_path.parent / f"{tmp_path.name}-runner"
    runner.write_text(f"#!/bin/sh\nrole=$1 prompt=$2 report=$3\n{body}")
    runner.chmod(0o755)
    code = driver.main(["--gate", "true", "--spec", "spec.md", "--runner", str(runner), *args])
    capsys.readouterr()
    ledger = tmp_path / ".agent/implement/ledger.md"
    return code, ledger.read_text().splitlines() if ledger.exists() else []


def test_accepted_reports_end_the_run_with_exit_0(repo, tmp_path, capsys):
    repo()
    body = f'{COMMIT}case $role in test-writer) {WRITER};; *) {IMPLEMENTER}grep -q "Locked files: work.txt" $prompt || exit 9;; esac\n{REPORT}'
    code, ledger = agents(tmp_path, capsys, body, seam="`f()`")
    assert code == 0
    assert [line.partition(" for ")[0] for line in ledger] == [
        "Ticket 01: test-writer ran on sonnet@medium", "Ticket 01: implementer ran on opus@medium"]
    assert ledger[1].endswith(", cost 0.25 USD: report accepted.")
    assert (tmp_path / ".agent/implement/01/implementer-0.log").read_text() == '{"total_cost_usd": 0.25}\n'


def test_a_violation_reruns_once_with_the_error_then_blocks(repo, tmp_path, capsys):
    repo()
    code, ledger = agents(tmp_path, capsys, f"STATUS=FINISHED\n{COMMIT}{IMPLEMENTER}{REPORT}")
    assert code == 1 and len(ledger) == 3
    assert ledger[2].startswith("Ticket 01: BLOCKED at the implementer: status: 'FINISHED' is not one of")
    assert "The driver rejected the earlier attempt: status: 'FINISHED'" in (
        tmp_path / ".agent/implement/01/implementer-0.prompt.md").read_text()


def prompt_of(tmp_path, role="implementer"):
    return (tmp_path / f".agent/implement/01/{role}-0.prompt.md").read_text()


def test_an_agent_that_writes_no_report_is_rerun_once_then_blocks(repo, tmp_path, capsys):
    repo()
    code, ledger = agents(tmp_path, capsys, "exit 7\n", "--only", "reviewer")
    assert code == 1 and len(ledger) == 3
    assert ledger[2].startswith("Ticket 01: BLOCKED at the reviewer: report .agent/implement/01/reviewer-0.json is not")


@pytest.mark.parametrize("extra", ['"locked": 5', '"locked": ["work.txt", "never-committed.txt"]', '"red": 5',
                                   '"mutations": "x"'])
def test_a_field_of_another_role_is_ignored(repo, tmp_path, capsys, extra):
    repo()
    code, ledger = agents(tmp_path, capsys, f"{COMMIT}fields='\"green\": [], {extra}'\n{REPORT}")
    assert code == 0 and len(ledger) == 1 and ledger[0].endswith("report accepted.")


@pytest.mark.parametrize("status", ["BLOCKED", "NEEDS_CONTEXT"])
def test_a_report_that_says_blocked_blocks_the_ticket_without_a_rerun(repo, tmp_path, capsys, status):
    repo()
    body = f"""printf '{{"status": "{status}", "commits": [], "files": [], "concerns": ["test_x contradicts the spec"], "green": []}}' > "$report"\n"""
    code, ledger = agents(tmp_path, capsys, body)
    assert code == 1 and len(ledger) == 2
    assert ledger[1] == f"Ticket 01: BLOCKED at the implementer: the implementer reported {status}: test_x contradicts the spec."


def test_a_rerun_prompt_holds_only_template_lines_and_keeps_its_ending(repo, tmp_path, capsys):
    repo()
    agents(tmp_path, capsys, "exec sleep 30\n", "--timeout", "implementer=0.005")
    prompt = prompt_of(tmp_path)
    first = driver_agents.render("implementer", {
        "spec": "spec.md", "ticket": ".agent/tickets/01-t.md", "seam": "none — refactor", "locked": "",
        "start_sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "report": ".agent/implement/01/implementer-0.json"})
    ending = first[first.index("\nWrite one JSON report"):]
    line = (driver_report.PROMPTS / "timeout.md").read_text().replace("{minutes}", "0.005")
    assert "timed out" in line and "in the tree" in line
    assert prompt == first + line + ending


def test_a_moved_head_prompt_line_comes_from_its_template(repo, tmp_path, capsys):
    repo()
    tip = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    agents(tmp_path, capsys, "git checkout -q --detach\n")
    line = (driver_report.PROMPTS / "moved.md").read_text().replace("{branch}", "ticket/01").replace("{tip}", tip)
    assert line in prompt_of(tmp_path) and prompt_of(tmp_path).rstrip().endswith("one ref store.")


def test_a_process_the_agent_leaves_behind_is_stopped(repo, tmp_path, capsys):
    repo()
    code, _ = agents(tmp_path, capsys, f"( sleep 0.5; echo late > late.txt; echo LATE ) &\n{COMMIT}{IMPLEMENTER}{REPORT}")
    assert code == 0
    time.sleep(1)
    assert not (tmp_path / "late.txt").exists()
    assert "LATE" not in (tmp_path / ".agent/implement/01/implementer-0.log").read_text()


def test_concurrent_first_runs_of_the_launcher_all_succeed(tmp_path):
    """The loser of the race to publish the venv uses the winner's venv."""
    run_sh = Path(driver.__file__).with_name("run.sh")
    assert subprocess.run(["sh", str(run_sh), "--help"], capture_output=True).returncode == 0  # provision the cache
    real = Path(os.environ.get("IMPLEMENT_SPEC_CACHE_DIR")
                or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "implement-spec")
    cache = tmp_path / "cache"
    cache.mkdir()
    for part in ("uv", "python", "uv-cache"):  # all but the venv, so each run builds it
        (cache / part).symlink_to(real / part)
    env = dict(os.environ, IMPLEMENT_SPEC_CACHE_DIR=str(cache))
    procs = [subprocess.Popen(["sh", str(run_sh), "--help"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True) for _ in range(6)]
    assert [(p.wait(), p.stderr.read()) for p in procs] == [(0, "")] * 6
    assert [p.name for p in (cache / "venv").iterdir()] == ["3.13.7-jsonschema-4.26.0"]


def test_a_timeout_doubles_the_limit_and_a_second_one_blocks(repo, tmp_path, capsys):
    repo()
    code, ledger = agents(tmp_path, capsys, "exec sleep 30\n", "--timeout", "implementer=0.005")
    assert code == 1
    assert ledger[0].endswith(": timed out after 0.005 minutes.") and ledger[1].endswith(": timed out after 0.01 minutes.")
    assert ledger[2].startswith("Ticket 01: BLOCKED at the implementer: timed out")


@pytest.mark.parametrize("move, problem", [
    ("git checkout -q feat/x", "HEAD is on feat/x instead of ticket/01"),
    ("git checkout -q --detach", "HEAD is on a detached commit instead of ticket/01"),
    ("git checkout -q --detach; git branch -q -D ticket/01", "branch ticket/01 is missing"),
    ("git checkout -q -B ticket/01 HEAD~1", "ticket/01 no longer holds"),
])
def test_a_moved_head_resets_the_slot_and_reruns(repo, tmp_path, capsys, move, problem):
    repo()
    (tmp_path / "second.txt").write_text("a second commit, so the tip has a parent\n")
    subprocess.run([*GIT, "add", "-A"], check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "second"], check=True)
    tip = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    code, ledger = agents(tmp_path, capsys, ONCE % f"{move}; exit 0" + COMMIT + IMPLEMENTER + REPORT)
    assert code == 0
    assert problem in ledger[0] and ledger[0].endswith(f"the driver reset the slot to {tip[:7]}.")
    assert subprocess.run(["git", "rev-parse", "ticket/01~1"], capture_output=True, text=True).stdout.strip() == tip


def test_an_existing_ticket_branch_stops_the_run(repo, tmp_path, capsys):
    repo()
    subprocess.run(["git", "branch", "ticket/01"], check=True)
    assert agents(tmp_path, capsys, "exit 0\n") == (3, [])


def test_a_timeout_for_an_unknown_role_is_a_usage_error(repo):
    with pytest.raises(SystemExit) as exit_info:
        driver.main(["--timeout", "nobody=5"])
    assert exit_info.value.code == 3


def test_command_lines_per_provider():
    assert driver_agents.command("implementer", "openai/gpt-sol@high", "r.json") == [
        "codex", "exec", "--model", "gpt-sol", "-c", 'model_reasoning_effort="high"', "--sandbox", "workspace-write"]
    assert driver_agents.command("reviewer", "openai/gpt-sol@high", "r.json")[-4:] == [
        "--sandbox", "read-only", "-o", "r.json"]
    assert driver_agents.command("reviewer", "opus@low", "r.json")[:6] == [
        "claude", "-p", "--model", "opus", "--effort", "low"]


@pytest.mark.parametrize("role", driver.ONLY)
def test_every_role_template_holds_a_valid_schema(role):
    jsonschema.Draft202012Validator.check_schema(driver_report.schema(role))
