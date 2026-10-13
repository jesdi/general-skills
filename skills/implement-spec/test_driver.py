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

from conftest import GIT, ticket

ARGS = ["--gate", "make check", "--dry-run"]


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


def branches():
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], capture_output=True, text=True).stdout.split()


def test_accepted_reports_end_the_run_with_exit_0(repo, tmp_path, capsys):
    repo()
    body = f'{COMMIT}case $role in test-writer) {WRITER};; *) {IMPLEMENTER}grep -q "Locked files: work.txt" $prompt || exit 9;; esac\n{REPORT}'
    code, ledger = agents(tmp_path, capsys, body, seam="`f()`")
    assert code == 0
    tip = subprocess.run(["git", "rev-parse", "--short=7", "feat/x"], capture_output=True, text=True).stdout.strip()
    assert [line.partition(" for ")[0] for line in ledger] == [
        "Ticket 01: test-writer ran on sonnet@medium", "Ticket 01: implementer ran on opus@medium",
        f"Ticket 01: the gate is green on {tip}, record .agent/implement/01/gate-{tip}.log.",
        f"Ticket 01: merged into feat/x as a fast-forward at {tip}."]
    assert ledger[1].endswith(", cost 0.25 USD: report accepted.")
    assert branches() == ["feat/x"] and "nothing to commit" in subprocess.run(
        ["git", "status"], capture_output=True, text=True).stdout
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
    assert code == 0 and len(ledger) == 3 and ledger[0].endswith("report accepted.")


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
    assert subprocess.run(["git", "rev-parse", "feat/x~1"], capture_output=True, text=True).stdout.strip() == tip


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


# In-process walks: several tickets, slots, the gate, the merge and the resume.

WORK = """nn=$(basename "$(dirname "$report")"); fields='"green": ["1 passed"]'
echo "$role $nn" >> "${FILE:-work-$nn.txt}"; git add -A; git commit -q -m "$role $nn"
echo "$nn $PWD" >> "$0.calls"
"""
# Ticket 01 waits until ticket 02 has its branch, so both start from the same PR tip.
TOGETHER = """[ "$nn" != 01 ] || until git rev-parse -q --verify refs/heads/ticket/02 > /dev/null; do sleep 0.02; done\n"""
REFACTOR = {"seam": "none — refactor"}


def walk(tmp_path, capsys, body, *args, gate="true"):
    """Run the driver on the ticket set of the repository; returns the exit code, the ledger lines and stdout."""
    runner = tmp_path.parent / f"{tmp_path.name}-runner"
    runner.write_text(f"#!/bin/sh\nrole=$1 prompt=$2 report=$3\n{body}")
    runner.chmod(0o755)
    code = driver.main(["--gate", gate, "--spec", "spec.md", "--runner", str(runner), *args])
    ledger = tmp_path / ".agent/implement/ledger.md"
    return code, ledger.read_text().splitlines() if ledger.exists() else [], capsys.readouterr().out


def out_of(*command):
    return subprocess.run(command, capture_output=True, text=True).stdout.strip()


def state_of(tmp_path):
    return json.loads((tmp_path / ".agent/implement/state.json").read_text())


def two(repo, second="None", touches="`demo/y.py: g`"):
    return repo(t01=ticket("01", **REFACTOR), t02=ticket("02", second, touches=touches, **REFACTOR))


def test_two_tickets_run_in_two_slots_and_the_second_merge_is_a_gated_merge_commit(repo, tmp_path, capsys):
    two(repo)
    log = tmp_path.parent / f"{tmp_path.name}-prepare.log"
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, "--prepare", f"pwd >> {log}")
    assert code == 0, out
    calls = dict(line.split() for line in Path(f"{tmp_path}-runner.calls").read_text().splitlines())
    assert calls == {"01": str(tmp_path.resolve()), "02": str((tmp_path / ".agent/worktrees/slot-2").resolve())}
    assert len(out_of("git", "rev-list", "--parents", "-n1", "feat/x").split()) == 3  # a merge commit
    assert len([line for line in ledger if "the gate is green" in line]) == 3 and "| gates run | 3 |" in out
    assert [line for line in ledger if "merged into feat/x as a merge commit" in line]
    assert {entry["phase"] for entry in state_of(tmp_path)["tickets"].values()} == {"merged"}
    assert branches() == ["feat/x"] and out_of("git", "status", "--porcelain") == ""
    assert (tmp_path / "work-01.txt").exists() and (tmp_path / "work-02.txt").exists()
    assert out == "max frontier width: 2\n" + (tmp_path / ".agent/implement/summary.md").read_text()
    # A rerun finds every ticket merged: no agent, no gate, no second prepare.
    code, again, _ = walk(tmp_path, capsys, "exit 9\n", "--prepare", f"pwd >> {log}")
    assert (code, again) == (0, ledger) and len(log.read_text().splitlines()) == 1


def test_a_merge_conflict_blocks_the_second_ticket_and_keeps_its_branch(repo, tmp_path, capsys):
    two(repo)
    code, ledger, out = walk(tmp_path, capsys, "FILE=work.txt\n" + WORK + TOGETHER + REPORT)
    assert code == 1, out
    (line,) = [line for line in ledger if "BLOCKED" in line]
    assert "BLOCKED at the merge: the merge into the PR branch conflicts in work.txt." in line
    number = line[7:9]
    assert state_of(tmp_path)["tickets"][number]["phase"] == "blocked"
    assert branches() == ["feat/x", f"ticket/{number}"] and f"Ticket {number} is not merged: its phase is blocked." in out


def test_a_red_gate_blocks_the_ticket_and_the_ticket_it_blocks_never_starts(repo, tmp_path, capsys):
    two(repo, second="01")
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT, gate="echo CHECK-OUTPUT; exit 1")
    tip = out_of("git", "rev-parse", "--short=7", "ticket/01")
    assert code == 1 and len(ledger) == 2, out
    assert ledger[1] == (f"Ticket 01: BLOCKED at the gate: the gate is red on {tip}, "
                         f"record .agent/implement/01/gate-{tip}.log.")
    record = (tmp_path / f".agent/implement/01/gate-{tip}.log").read_text()
    assert "status: clean\n" in record and "CHECK-OUTPUT\n\nexit: 1\nafter: clean\nend: " in record
    assert "Ticket 02 is not merged: its phase is pending." in out and "| gates run | 1 |" in out
    assert out_of("git", "symbolic-ref", "--short", "HEAD") == "feat/x"
    assert walk(tmp_path, capsys, "exit 9\n")[0] == 1  # a BLOCKED ticket stays BLOCKED on a rerun


def test_a_red_merge_gate_blocks_the_ticket_and_the_pr_branch_does_not_move(repo, tmp_path, capsys):
    two(repo)
    gate = 'test "$(git rev-parse --abbrev-ref HEAD)" != HEAD'  # red on a detached commit: the merge commit
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, gate=gate)
    assert code == 1, out
    (line,) = [line for line in ledger if "BLOCKED" in line]
    assert "BLOCKED at the merge: the gate is red on " in line
    assert len(out_of("git", "rev-list", "--parents", "-n1", "feat/x").split()) == 2  # no merge commit landed
    assert len(branches()) == 2 and out_of("git", "status", "--porcelain") == ""


def test_a_tree_that_is_not_clean_is_a_red_gate_and_the_slot_keeps_the_work(repo, tmp_path, capsys):
    repo(t01=ticket("01", **REFACTOR))
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT + "echo left > junk.txt\n", gate="echo NEVER")
    assert code == 1 and "BLOCKED at the gate" in ledger[-1], out
    record = next((tmp_path / ".agent/implement/01").glob("gate-*.log")).read_text()
    assert "status: not clean\n    ?? junk.txt\n" in record and "exit: not run" in record and "\nNEVER\n" not in record
    assert out_of("git", "symbolic-ref", "--short", "HEAD") == "ticket/01" and (tmp_path / "junk.txt").exists()
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and "uncommitted change: junk.txt" in out


def test_a_failing_prepare_stops_the_run_and_removes_the_slot(repo, tmp_path, capsys):
    two(repo)
    code, ledger, out = walk(tmp_path, capsys, "exit 9\n", "--prepare", "echo no-deps >&2; false")
    assert (code, ledger) == (3, [])
    assert "slot 2: --prepare `echo no-deps >&2; false` exited 1 in .agent/worktrees/slot-2: no-deps" in out
    assert not (tmp_path / ".agent/worktrees/slot-2").exists() and branches() == ["feat/x"]


def test_a_touches_overlap_holds_the_second_ticket_and_the_ledger_says_why(repo, tmp_path, capsys):
    two(repo, touches="`demo/x.py`")
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT)
    assert code == 0, out
    assert ledger[0] == "Ticket 02: held while 01 runs: both touch demo/x.py: f."
    assert ledger.count(ledger[0]) == 1 and len(out_of("git", "rev-list", "--merges", "feat/x").split()) == 0
    assert not (tmp_path / ".agent/worktrees/slot-2/work-02.txt").exists()  # 02 ran in slot 1, after 01


def blocked_then_edited(repo, tmp_path, capsys, **fields):
    """A one-ticket run that a red gate blocks, then state.json edited as the resume tests need."""
    repo(t01=ticket("01", **REFACTOR))
    assert walk(tmp_path, capsys, WORK + REPORT, gate="false")[0] == 1
    state = state_of(tmp_path)
    state["tickets"]["01"].update(fields)
    (tmp_path / ".agent/implement/state.json").write_text(json.dumps(state))
    return state["tickets"]["01"]


def test_a_resumed_tip_that_is_not_on_the_branch_returns_the_ticket_to_implement(repo, tmp_path, capsys):
    blocked_then_edited(repo, tmp_path, capsys, phase="review", tip="1" * 40)
    base = out_of("git", "rev-parse", "feat/x")
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT)
    assert code == 0, out
    assert "Ticket 01: returned to implement from the PR tip: the recorded tip 1111111 is not on ticket/01." in ledger
    assert out_of("git", "rev-parse", "feat/x~1") == base  # the earlier, untrusted commit is not in the PR branch


def test_a_resumed_ticket_with_a_settled_gate_merges_without_a_gate_or_an_agent(repo, tmp_path, capsys):
    tip = None
    entry = blocked_then_edited(repo, tmp_path, capsys, phase="merge")
    tip = entry["tip"]
    state = state_of(tmp_path)
    state["tickets"]["01"]["green_gates"] = [tip]
    (tmp_path / ".agent/implement/state.json").write_text(json.dumps(state))
    code, ledger, out = walk(tmp_path, capsys, "exit 9\n", gate="false")
    assert code == 0 and out_of("git", "rev-parse", "feat/x") == tip and "| gates run | 1 |" in out


def test_an_unknown_phase_fails_the_resume(repo, tmp_path, capsys):
    blocked_then_edited(repo, tmp_path, capsys, phase="polish")
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and ".agent/implement/state.json: ticket 01 has the unknown phase 'polish'" in out


def test_a_rerun_on_another_branch_is_refused_and_a_detached_rerun_uses_the_recorded_branch(repo, tmp_path, capsys):
    repo(t01=ticket("01", **REFACTOR))
    assert walk(tmp_path, capsys, WORK + REPORT)[0] == 0
    subprocess.run(["git", "checkout", "-q", "-b", "other"], check=True)
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and "records a run on feat/x, not on other" in out
    subprocess.run(["git", "checkout", "-q", "--detach"], check=True)  # as an interrupted run leaves slot 1
    assert walk(tmp_path, capsys, "exit 9\n")[0] == 0
    assert out_of("git", "symbolic-ref", "--short", "HEAD") == "feat/x"


def test_a_first_run_on_a_detached_head_or_with_no_slot_is_refused(repo, tmp_path, capsys):
    repo(t01=ticket("01", **REFACTOR))
    code, _, out = walk(tmp_path, capsys, "exit 9\n", "--parallel", "0")
    assert code == 3 and "--parallel must be 1 or more" in out
    subprocess.run(["git", "checkout", "-q", "--detach"], check=True)
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and "the PR worktree is on a detached HEAD" in out


def test_a_ticket_branch_that_cannot_be_checked_out_blocks_the_ticket(repo, tmp_path, capsys):
    """A slot of an earlier run with more slots still holds the branch."""
    blocked_then_edited(repo, tmp_path, capsys, phase="implement")
    subprocess.run(["git", "worktree", "add", "-q", str(tmp_path.parent / f"{tmp_path.name}-other"), "ticket/01"], check=True)
    code, ledger, _ = walk(tmp_path, capsys, "exit 9\n")
    assert code == 1 and "BLOCKED at the start: cannot check out ticket/01 in slot 1" in ledger[-1]


def test_an_interrupt_kills_the_running_agents_and_stops_the_driver_at_once(repo, tmp_path, capsys, monkeypatch):
    repo(t01=ticket("01", **REFACTOR))
    started = tmp_path.parent / f"{tmp_path.name}-started"

    def interrupt(*_, **__):
        while not (started.exists() and driver_agents.LIVE):
            time.sleep(0.01)
        raise KeyboardInterrupt

    def leave(code):
        raise SystemExit(code)

    monkeypatch.setattr(driver_agents, "STOPPED", False)  # restored after the test: stop() sets it for the process
    monkeypatch.setattr(driver, "wait", interrupt)
    monkeypatch.setattr(driver.os, "_exit", leave)
    begin = time.monotonic()
    with pytest.raises(SystemExit) as exit_info:
        walk(tmp_path, capsys, f"touch {started}; sleep 30; echo late > late.txt\n")
    assert exit_info.value.code == 130 and time.monotonic() - begin < 20
    assert driver_agents.STOPPED and not driver_agents.LIVE and not (tmp_path / "late.txt").exists()
    assert state_of(tmp_path)["tickets"]["01"]["phase"] == "implement"  # not BLOCKED: a rerun starts it again
    assert "interrupted; run it again to resume" in capsys.readouterr().out


# Fix round 1 of ticket 03.

def test_a_merge_commit_the_slot_cannot_check_out_is_not_gated_and_does_not_land(repo, tmp_path, capsys):
    two(repo)
    gate = 'touch "$(git rev-parse --git-dir)/index.lock"'  # after its gate, the slot can check out nothing
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, gate=gate)
    assert code == 1, out
    (line,) = [line for line in ledger if "BLOCKED" in line]
    assert "BLOCKED at the merge: the slot is at " in line and "the gate did not run" in line
    assert len(out_of("git", "rev-list", "--parents", "-n1", "feat/x").split()) == 2  # no merge commit landed
    assert "| gates run | 2 |" in out


@pytest.mark.parametrize("gate, after", [
    ("echo patched >> work-01.txt", "after: not clean\n     M work-01.txt\n"),
    ("git commit -q --allow-empty -m by-the-gate", "after: HEAD moved to "),
])
def test_a_gate_that_changes_the_tree_or_moves_head_is_red(repo, tmp_path, capsys, gate, after):
    repo(t01=ticket("01", **REFACTOR))
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT, gate=gate)
    assert code == 1 and "BLOCKED at the gate: the gate is red on " in ledger[-1], out
    record = next((tmp_path / ".agent/implement/01").glob("gate-*.log")).read_text()
    assert "\nexit: 0\n" in record and after in record
    assert state_of(tmp_path)["tickets"]["01"]["green_gates"] == []
    assert "by-the-gate" not in out_of("git", "log", "--format=%s", "feat/x")


def test_a_merged_ticket_whose_commits_left_the_pr_branch_is_not_merged_on_a_rerun(repo, tmp_path, capsys):
    repo(t01=ticket("01", **REFACTOR))
    base = out_of("git", "rev-parse", "HEAD")
    assert walk(tmp_path, capsys, WORK + REPORT)[0] == 0
    tip = out_of("git", "rev-parse", "--short=7", "HEAD")
    subprocess.run(["git", "reset", "-q", "--hard", base], check=True)  # the operator dropped the ticket's commits
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT)
    assert code == 0, out
    assert f"Ticket 01: returned to implement from the PR tip: the recorded tip {tip} is not in feat/x." in ledger
    assert (tmp_path / "work-01.txt").exists() and out_of("git", "rev-parse", "feat/x~1") == base


def test_a_rerun_after_an_interrupt_keeps_the_work_in_each_slot_and_reruns_the_role(repo, tmp_path, capsys, monkeypatch):
    two(repo)
    mark = f"{tmp_path.parent}/{tmp_path.name}-started"

    def interrupt(*_, **__):
        while not (Path(mark + "-01").exists() and Path(mark + "-02").exists() and len(driver_agents.LIVE) == 2):
            time.sleep(0.01)
        raise KeyboardInterrupt

    def leave(code):
        raise SystemExit(code)

    hang = f'nn=$(basename "$(dirname "$report")"); echo half > wip-$nn.txt; touch {mark}-$nn; sleep 30\n'
    with monkeypatch.context() as patch:
        patch.setattr(driver_agents, "STOPPED", False)
        patch.setattr(driver, "wait", interrupt)
        patch.setattr(driver.os, "_exit", leave)
        with pytest.raises(SystemExit):
            walk(tmp_path, capsys, hang)
    slot2 = tmp_path / ".agent/worktrees/slot-2"
    assert (tmp_path / "wip-01.txt").exists() and (slot2 / "wip-02.txt").exists()
    resume = ('grep -q "was interrupted" "$prompt" || exit 9\nnn=$(basename "$(dirname "$report")")\n'
              '[ "$(cat wip-$nn.txt)" = half ] || exit 8\n')
    code, ledger, out = walk(tmp_path, capsys, resume + WORK + REPORT)
    assert code == 0, out + "\n".join(ledger)
    calls = dict(line.split() for line in Path(f"{tmp_path}-runner.calls").read_text().splitlines())
    assert calls == {"01": str(tmp_path.resolve()), "02": str(slot2.resolve())}
    assert out_of("git", "show", "feat/x:wip-01.txt") == "half" and out_of("git", "show", "feat/x:wip-02.txt") == "half"
    assert [line for line in ledger if "Ticket 02: resumed in slot 2 with the work the interrupted implementer left" in line]
    assert out_of("git", "status", "--porcelain") == "" and branches() == ["feat/x"]
