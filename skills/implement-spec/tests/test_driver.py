"""In-process driver tests: the same seam as test_cli.py without the launcher, so coverage sees it."""
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import jsonschema
import pytest

import driver
import driver_agents
import driver_report
import driver_state

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
    assert "ticket/feat%2Fx/01" not in subprocess.run(["git", "branch"], capture_output=True, text=True).stdout


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
    # The default reviewer and final-reviewer start on codex, so this file gives both a claude entry.
    claude = {"reviewer": ["claude-opus-5-5@medium"], "final-reviewer": ["claude-opus-5-5@medium"]}
    models.write_text(json.dumps({"implementer": ["openai/gpt-sol@medium"], **claude,
                                  "correctness": ["openai/gpt-sol@medium"], "fixer": ["other/x@low"]}))
    assert run(capsys, "--models", str(models)) == (
        3, "implementer: codex is not on PATH (model entry openai/gpt-sol@medium)\n"
           "fixer: model entry other/x@low names an unknown provider (known: anthropic, openai)\n"
           "correctness: no entry of its list (openai/gpt-sol@medium) is on another provider than the implementer's "
           "first entry openai/gpt-sol@medium; the correctness pass never runs on the implementer's provider\n")
    # Requirement 2 names the first entry only: a later implementer entry whose CLI is missing is no error. For the
    # correctness role it names the entry requirement 13 selects, which here is the second of its list.
    models.write_text(json.dumps({"implementer": ["opus@medium", "openai/gpt-sol@medium"], **claude,
                                  "correctness": ["opus@high", "openai/gpt-sol@low", "openai/gpt-astra@low"]}))
    assert run(capsys, "--models", str(models)) == (
        3, "correctness: codex is not on PATH (model entry openai/gpt-sol@low)\n")


# Each entry is invalid on purpose: `turbo` is no effort of the anthropic provider, the second entry has no
# effort, and the third has no model.
@pytest.mark.parametrize("entry", ["claude-opus-5-5@turbo", "openai/gpt-sol", "openai/@low"],
                         ids=["unknown-effort", "no-effort", "no-model"])
def test_an_entry_needs_a_model_and_an_effort_of_its_provider(repo, capsys, tmp_path, entry):
    repo(t01=ticket("01"))
    models = tmp_path.parent / f"{tmp_path.name}-models.json"
    models.write_text(json.dumps({"reviewer": ["claude-opus-5-5@low", entry]}))
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
# The reviewer of a plain run: it finds nothing and proves the locked file of WRITER.
CLEAN = """case $role in reviewer|correctness|final-reviewer) echo FAILED t
    printf '{"status": "DONE", "commits": [], "files": [], "concerns": [], "findings": [], "mutations": [{"test": "work.txt::t", "file": "work.txt", "line": 1, "red": "FAILED t"}]}' > "$report"; exit 0;; esac
"""
ONCE = '[ -e "$0.again" ] || { touch "$0.again"; %s; }\n'  # the first attempt only


def agents(tmp_path, capsys, body, *args, seam="none — refactor"):
    """Run the driver on one ticket with a runner made of `body`; returns the exit code and the ledger lines."""
    (tmp_path / ".agent/tickets").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent/tickets/01-t.md").write_text(ticket("01", seam=seam))
    runner = tmp_path.parent / f"{tmp_path.name}-runner"
    runner.write_text(f"#!/bin/sh\nrole=$1 prompt=$2 report=$3\n{'' if '--only' in args else CLEAN}{body}")
    runner.chmod(0o755)
    code = driver.main(["--gate", "true", "--spec", "spec.md", "--runner", str(runner), *args])
    capsys.readouterr()
    ledger = tmp_path / ".agent/implement/ledger.md"
    return code, ledger_of(tmp_path)


# The end of run follows the last merge and has its own tests (test_tidy.py): its sentences start with one of these.
END = ("End-of-run work skipped", "End of run:", "Recovery point:", "Tidy:", "Final review ")


def ledger_of(tmp_path):
    """The ledger sentences of the tickets."""
    ledger = tmp_path / ".agent/implement/ledger.md"
    return [line for line in ledger.read_text().splitlines() if not line.startswith(END)] if ledger.exists() else []


def walked(*short):
    """The PR tip as the tickets left it: the recovery point of the tidy when there is one, else the tip of feat/x."""
    made = subprocess.run(["git", "rev-parse", *short, "-q", "--verify", "refs/implement-spec/feat%2Fx/recovery"], capture_output=True, text=True)
    return made.stdout.strip() or subprocess.run(["git", "rev-parse", *short, "feat/x"], capture_output=True, text=True).stdout.strip()


def branches():
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], capture_output=True, text=True).stdout.split()


def test_accepted_reports_end_the_run_with_exit_0(repo, tmp_path, capsys):
    repo()
    # The implementer leaves the locked work.txt as the test-writer committed it.
    commit = COMMIT.replace("work.txt", "$file")
    body = (f'file=code.txt; [ $role != test-writer ] || file=work.txt\n{commit}case $role in test-writer) {WRITER};; '
            f'*) {IMPLEMENTER}grep -q "Locked files: work.txt" $prompt || exit 9;; esac\n{REPORT}')
    code, ledger = agents(tmp_path, capsys, body, seam="`f()`")
    assert code == 0
    tip = walked("--short=7")
    assert [line.partition(" for ")[0] for line in ledger] == [
        "Ticket 01: test-writer ran on claude-sonnet-5-5@medium", "Ticket 01: implementer ran on claude-sonnet-5-5@medium",
        f"Ticket 01: the gate is green on {tip}, record .agent/implement/01/gate-{tip}.log.",
        "Ticket 01: reviewer ran on openai/gpt-6-astra@medium",
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
    code, ledger = agents(tmp_path, capsys, "exit 0\n", "--only", "reviewer")
    assert code == 1 and len(ledger) == 3
    assert ledger[2].startswith("Ticket 01: BLOCKED at the reviewer: report .agent/implement/01/reviewer-0.json is not")


@pytest.mark.parametrize("extra", ['"locked": 5', '"locked": ["work.txt", "never-committed.txt"]', '"red": 5',
                                   '"mutations": "x"'])
def test_a_field_of_another_role_is_ignored(repo, tmp_path, capsys, extra):
    repo()
    code, ledger = agents(tmp_path, capsys, f"{COMMIT}fields='\"green\": [], {extra}'\n{REPORT}")
    assert code == 0 and len(ledger) == 4 and ledger[0].endswith("report accepted.")


@pytest.mark.parametrize("status", ["BLOCKED", "NEEDS_CONTEXT"])
def test_a_report_that_says_blocked_blocks_the_ticket_without_a_rerun(repo, tmp_path, capsys, status):
    repo()
    body = f"""printf '{{"status": "{status}", "commits": [], "files": [], "concerns": ["test_x contradicts the spec"], "green": []}}' > "$report"\n"""
    code, ledger = agents(tmp_path, capsys, body)
    assert code == 2 and len(ledger) == 3 and "question of kind implementer-stopped" in ledger[2]
    assert ledger[1] == f"Ticket 01: BLOCKED at the implementer: the implementer reported {status}: test_x contradicts the spec."


def test_a_rerun_prompt_holds_only_template_lines_and_keeps_its_ending(repo, tmp_path, capsys):
    repo()
    agents(tmp_path, capsys, "exec sleep 30\n", "--timeout", "implementer=0.005")
    prompt = prompt_of(tmp_path)
    first = driver_agents.render("implementer", {
        "spec": "spec.md", "ticket": ".agent/tickets/01-t.md", "seam": "none — refactor", "locked": "",
        "gate": "true", "pr_branch": "feat/x",
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
    line = (driver_report.PROMPTS / "moved.md").read_text().replace("{branch}", "ticket/feat%2Fx/01").replace("{tip}", tip)
    assert line in prompt_of(tmp_path) and prompt_of(tmp_path).rstrip().endswith("one ref store.")


def test_a_process_the_agent_leaves_behind_is_stopped(repo, tmp_path, capsys):
    repo()
    code, _ = agents(tmp_path, capsys, f"( sleep 0.5; echo late > late.txt; echo LATE ) &\n{COMMIT}{IMPLEMENTER}{REPORT}")
    assert code == 0
    time.sleep(1)
    assert not (tmp_path / "late.txt").exists()
    assert "LATE" not in (tmp_path / ".agent/implement/01/implementer-0.log").read_text()


def test_the_process_group_of_an_agent_is_signalled_only_while_its_leader_is_not_reaped(tmp_path, monkeypatch):
    """Final review, correctness 4: when the leader is reaped, its id can be the group of another process."""
    seen, real = [], os.killpg

    def killpg(group, sig):
        # WNOWAIT: look and do not reap. A leader that is reaped already is a ChildProcessError here.
        ended = os.waitid(os.P_PID, group, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None
        seen.append((ended, group in driver_agents.LIVE))
        with contextlib.suppress(ProcessLookupError, PermissionError):
            real(group, sig)

    monkeypatch.setattr(driver_agents, "STOPPED", False)
    monkeypatch.setattr(driver_agents.os, "killpg", killpg)
    prompt, log = tmp_path / "prompt.md", tmp_path / "agent.log"
    prompt.write_text("")
    assert driver_agents.launch(["sh", "-c", "echo out; exit 7"], prompt, log, 30, str(tmp_path)) == 7
    assert seen == [(True, True)] and log.read_text() == "out\n"  # ended, not reaped, and stop() still knows it
    begin = time.monotonic()
    assert driver_agents.launch(["sh", "-c", "sleep 30"], prompt, log, 0.2, str(tmp_path)) is None
    assert seen[1:] == [(False, True)] and 0.2 <= time.monotonic() - begin < 10
    # After the launch the leader is reaped, so stop() has no group to signal.
    assert not driver_agents.LIVE
    driver_agents.stop()
    assert len(seen) == 2 and driver_agents.STOPPED


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
    ("git checkout -q feat/x", "HEAD is on feat/x instead of ticket/feat%2Fx/01"),
    ("git checkout -q --detach", "HEAD is on a detached commit instead of ticket/feat%2Fx/01"),
    ("git checkout -q --detach; git branch -q -D ticket/feat%2Fx/01", "branch ticket/feat%2Fx/01 is missing"),
    ("git checkout -q -B ticket/feat%2Fx/01 HEAD~1", "ticket/feat%2Fx/01 no longer holds"),
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
    subprocess.run(["git", "branch", "ticket/feat%2Fx/01"], check=True)
    assert agents(tmp_path, capsys, "exit 0\n") == (3, [])


def test_a_timeout_for_an_unknown_role_is_a_usage_error(repo):
    with pytest.raises(SystemExit) as exit_info:
        driver.main(["--timeout", "nobody=5"])
    assert exit_info.value.code == 3


def test_command_lines_per_provider():
    assert driver_agents.command("implementer", "openai/gpt-sol@high", "r.json") == [
        "codex", "exec", "--model", "gpt-sol", "-c", 'model_reasoning_effort="high"', "--sandbox", "workspace-write"]
    assert driver_agents.command("reviewer", "openai/gpt-sol@high", "r.json")[-4:] == [
        "--sandbox", "read-only", "-o", "r.json.last"]
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
TOGETHER = """[ "$nn" != 01 ] || until git rev-parse -q --verify refs/heads/ticket/feat%2Fx/02 > /dev/null; do sleep 0.02; done\n"""
REFACTOR = {"seam": "none — refactor"}
# A fixer that cannot make the red gate green: the ticket blocks at fix round 1.
GIVE_UP = """[ $role != fixer ] || { printf '{"status": "BLOCKED", "commits": [], "files": [], "concerns": ["the gate stays red"], "green": [], "ticket": "01"}' > "$report"; exit 0; }\n"""
GAVE_UP = "Ticket 01: BLOCKED at fix round 1: the fixer reported BLOCKED: the gate stays red."
ASKED = "Ticket 01: the operator decides on 1 question of kind fixer-stopped in .agent/implement/decisions.json."


def walk(tmp_path, capsys, body, *args, gate="true"):
    """Run the driver on the ticket set of the repository; returns the exit code, the ledger lines and stdout."""
    runner = tmp_path.parent / f"{tmp_path.name}-runner"
    runner.write_text(f"#!/bin/sh\nrole=$1 prompt=$2 report=$3\n{CLEAN}{body}")
    runner.chmod(0o755)
    code = driver.main(["--gate", gate, "--spec", "spec.md", "--runner", str(runner), *args])
    return code, ledger_of(tmp_path), capsys.readouterr().out


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
    assert len(out_of("git", "rev-list", "--parents", "-n1", walked()).split()) == 3  # a merge commit
    # Each ticket tip and the merge commit; the fourth gate is that of the end of run, on the tidied tip.
    assert len([line for line in ledger if "the gate is green" in line]) == 3 and "| gates run | 4 |" in out
    assert [line for line in ledger if "merged into feat/x as a merge commit" in line]
    assert {entry["phase"] for entry in state_of(tmp_path)["tickets"].values()} == {"merged"}
    assert branches() == ["feat/x"] and out_of("git", "status", "--porcelain") == ""
    assert (tmp_path / "work-01.txt").exists() and (tmp_path / "work-02.txt").exists()
    assert out.startswith("max frontier width: 2\n" + (tmp_path / ".agent/implement/summary.md").read_text())
    # A rerun finds every ticket merged: no agent, no gate, no second prepare.
    code, again, _ = walk(tmp_path, capsys, "exit 9\n", "--prepare", f"pwd >> {log}")
    assert (code, again) == (0, ledger) and len(log.read_text().splitlines()) == 1


# The conflict agent of a merge that conflicts: it commits the open merge as it is, or it does nothing.
RESOLVE = """if [ $role = conflict ]; then git add -A; git commit -q -m resolved
    printf '{"status": "DONE", "commits": ["%s"], "files": ["work.txt"], "concerns": []}' "$(git rev-parse HEAD)" > "$report"
    exit 0; fi
"""


def test_a_merge_conflict_goes_to_the_conflict_agent_once_and_its_commit_is_gated(repo, tmp_path, capsys):
    two(repo)
    code, ledger, out = walk(tmp_path, capsys, RESOLVE + "FILE=work.txt\n" + WORK + TOGETHER + REPORT)
    assert code == 0, out
    (line,) = [line for line in ledger if "the conflict agent resolved the merge conflict in work.txt at " in line]
    number, tip = line[7:9], walked()
    assert line.endswith(f" at {tip[:7]}.") and len(out_of("git", "rev-list", "--parents", "-n1", tip).split()) == 3
    assert f"Ticket {number}: the gate is green on {tip[:7]}, record .agent/implement/{number}/gate-{tip[:7]}.log." in ledger
    assert "| gates run | 4 |" in out and "| agents, conflict on claude-opus-5-5@medium | 1 |" in out
    assert dict(state_of(tmp_path)["run"]["landed"])[number] == tip
    prompt = (tmp_path / f".agent/implement/{number}/conflict-0.prompt.md").read_text()
    assert "Files in conflict: work.txt\n" in prompt and prompt.rstrip().endswith("one ref store.")
    assert branches() == ["feat/x"] and out_of("git", "status", "--porcelain") == ""


def test_a_conflict_agent_that_does_not_resolve_blocks_the_ticket_and_keeps_its_branch(repo, tmp_path, capsys):
    two(repo)
    stuck = '[ $role != conflict ] || exit 0\n'
    code, ledger, out = walk(tmp_path, capsys, stuck + "FILE=work.txt\n" + WORK + TOGETHER + REPORT)
    assert code == 1, out
    (line,) = [line for line in ledger if "BLOCKED" in line]
    number = line[7:9]
    assert f"BLOCKED at the merge: the conflict agent: report .agent/implement/{number}/conflict-0.json is not" in line
    assert len([line for line in ledger if "conflict ran on" in line]) == 2  # the one rerun of a violation
    assert state_of(tmp_path)["tickets"][number]["phase"] == "blocked"
    assert f"ticket/feat%2Fx/{number}" in branches() and f"Ticket {number} is not merged: its phase is blocked." in out
    for slot in (".", ".agent/worktrees/slot-2"):  # the driver's open merge is gone
        assert out_of("git", "-C", slot, "status", "--porcelain") == ""
        assert out_of("git", "-C", slot, "rev-parse", "-q", "--verify", "MERGE_HEAD") == ""


def test_a_conflict_cli_that_fails_stops_the_run_and_leaves_no_open_merge(repo, tmp_path, capsys):
    two(repo)
    failing = '[ $role != conflict ] || { echo "ERROR: not signed in"; exit 7; }\n'
    code, ledger, out = walk(tmp_path, capsys, failing + "FILE=work.txt\n" + WORK + TOGETHER + REPORT)
    assert code == 4 and "with no report: ERROR: not signed in" in out, out
    assert not [line for line in ledger if "BLOCKED" in line]
    (number,) = [n for n, entry in state_of(tmp_path)["tickets"].items() if entry["phase"] == "merge"]
    assert f"ticket/feat%2Fx/{number}" in branches()
    for slot in (".", ".agent/worktrees/slot-2"):
        assert out_of("git", "-C", slot, "status", "--porcelain") == ""
        assert out_of("git", "-C", slot, "rev-parse", "-q", "--verify", "MERGE_HEAD") == ""


def test_a_red_gate_is_a_finding_and_the_ticket_it_blocks_never_starts(repo, tmp_path, capsys):
    two(repo, second="01")
    code, ledger, out = walk(tmp_path, capsys, GIVE_UP + WORK + REPORT, gate="echo CHECK-OUTPUT; exit 1")
    tip = out_of("git", "rev-parse", "--short=7", "ticket/feat%2Fx/01")
    assert code == 2 and ledger[-2:] == [GAVE_UP, ASKED], out
    assert ledger[1] == (f"Ticket 01: critical finding of kind code from the gate at "
                         f".agent/implement/01/gate-{tip}.log:0: the gate is red on {tip}.")
    record = (tmp_path / f".agent/implement/01/gate-{tip}.log").read_text()
    assert "status: clean\n" in record and "CHECK-OUTPUT\n\nexit: 1\nafter: clean\nend: " in record
    assert "Ticket 02 is not merged: its phase is pending." in out and "| gates run | 1 |" in out
    assert out_of("git", "symbolic-ref", "--short", "HEAD") == "feat/x"
    assert walk(tmp_path, capsys, "exit 9\n")[0] == 2  # a BLOCKED ticket stays BLOCKED on a rerun


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
    code, ledger, out = walk(tmp_path, capsys, GIVE_UP + WORK + REPORT + "echo left > junk.txt\n", gate="echo NEVER")
    assert code == 2 and "from the gate at .agent/implement/01/gate-" in ledger[-4] and ledger[-2:] == [GAVE_UP, ASKED], out
    record = next((tmp_path / ".agent/implement/01").glob("gate-*.log")).read_text()
    assert "status: not clean\n    ?? junk.txt\n" in record and "exit: not run" in record and "\nNEVER\n" not in record
    assert out_of("git", "symbolic-ref", "--short", "HEAD") == "ticket/feat%2Fx/01" and (tmp_path / "junk.txt").exists()
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 2 and (tmp_path / "junk.txt").exists(), "the stop repeats: the work in the slot is no error"


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
    assert "Ticket 01: returned to implement from the PR tip: the recorded tip 1111111 is not on ticket/feat%2Fx/01." in ledger
    assert out_of("git", "rev-parse", "feat/x~1") == base  # the earlier, untrusted commit is not in the PR branch


def test_a_resumed_ticket_with_a_settled_gate_merges_without_a_gate_or_an_agent(repo, tmp_path, capsys):
    tip = None
    entry = blocked_then_edited(repo, tmp_path, capsys, phase="merge")
    tip = entry["tip"]
    state = state_of(tmp_path)
    state["tickets"]["01"]["green_gates"] = [tip]
    (tmp_path / ".agent/implement/state.json").write_text(json.dumps(state))
    # A gate on the ticket branch is still red, so a second gate for the settled commit would block the ticket. The
    # one new gate is that of the end of run, on the PR branch.
    code, ledger, out = walk(tmp_path, capsys, "exit 9\n", gate='[ "$(git rev-parse --abbrev-ref HEAD)" != ticket/feat%2Fx/01 ]')
    assert code == 0 and walked() == tip and "| gates run | 2 |" in out


def as_older_driver(tmp_path, *numbers):
    """Make the run in tmp_path one that an older driver started: no `schema`, and the old names of its ticket
    branches and of its recovery ref."""
    state = state_of(tmp_path)
    del state["run"]["schema"]
    (tmp_path / ".agent/implement/state.json").write_text(json.dumps(state))
    for nn in numbers:
        subprocess.run(["git", "branch", "-m", f"ticket/feat%2Fx/{nn}", f"ticket/{nn}"], check=True)
    if recovery := out_of("git", "rev-parse", "-q", "--verify", "refs/implement-spec/feat%2Fx/recovery"):
        subprocess.run(["git", "update-ref", "refs/implement/recovery", recovery], check=True)
        subprocess.run(["git", "update-ref", "-d", "refs/implement-spec/feat%2Fx/recovery"], check=True)
    return recovery


def test_a_run_of_an_older_driver_resumes_on_its_renamed_ticket_branch(repo, tmp_path, capsys):
    """An older driver named the ticket branch `ticket/NN`. The resume renames it and trusts the recorded tip."""
    entry = blocked_then_edited(repo, tmp_path, capsys, phase="merge")
    state = state_of(tmp_path)
    state["tickets"]["01"]["green_gates"] = [entry["tip"]]
    (tmp_path / ".agent/implement/state.json").write_text(json.dumps(state))
    as_older_driver(tmp_path, "01")
    code, ledger, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 0, out
    assert "Ticket 01: renamed the branch ticket/01 of an older driver to ticket/feat%2Fx/01." in ledger
    assert not any("returned to implement" in line for line in ledger) and walked() == entry["tip"]
    assert branches() == ["feat/x"]


def test_a_run_of_an_older_driver_on_a_contradicted_ticket_branch_returns_the_ticket_to_implement(repo, tmp_path,
                                                                                               capsys):
    """Spec review: slot 1 is on `ticket/NN` of an older driver, and git contradicts the recorded tip, so the branch
    keeps its old name. The resume accepts slot 1 there, as the older driver did, and starts the ticket again."""
    blocked_then_edited(repo, tmp_path, capsys, phase="review", tip="1" * 40)
    as_older_driver(tmp_path, "01")
    subprocess.run(["git", "checkout", "-q", "ticket/01"], check=True)
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT)
    assert code == 0, out
    assert "Ticket 01: returned to implement from the PR tip: the recorded tip 1111111 is not on ticket/feat%2Fx/01." in ledger
    assert not [line for line in ledger if "renamed" in line]


def test_a_ticket_branch_of_an_older_driver_that_git_cannot_rename_stops_the_run(repo, tmp_path, capsys):
    blocked_then_edited(repo, tmp_path, capsys)
    as_older_driver(tmp_path, "01")
    subprocess.run(["git", "branch", "ticket/feat%2Fx"], check=True)  # a branch where the folder of the new name goes
    code, ledger, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and out.startswith("implement-spec: cannot rename the branch ticket/01 of an older driver to "
                                        "ticket/feat%2Fx/01: "), out
    assert "ticket/01" in branches() and not [line for line in ledger if "renamed" in line]


def test_a_finished_run_of_an_older_driver_gets_the_new_name_of_its_recovery_ref(repo, tmp_path, capsys):
    """Spec review: the recovery ref moves through the driver, before the end of run reads it."""
    repo(t01=ticket("01", **REFACTOR))
    assert walk(tmp_path, capsys, WORK + REPORT)[0] == 0
    recovery = as_older_driver(tmp_path)
    assert recovery
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 0, out
    assert ("Recovery point: moved refs/implement/recovery of an older driver to refs/implement-spec/feat%2Fx/recovery."
            in (tmp_path / ".agent/implement/ledger.md").read_text().splitlines())
    assert out_of("git", "rev-parse", "-q", "--verify", "refs/implement/recovery") == "" and walked() == recovery


def test_no_older_driver_starts_while_a_newer_driver_runs(repo, tmp_path, capsys):
    """Spec review: the newer driver holds the lock of an older driver, shared, for its whole run. The agent tries to
    take that lock as an older driver does, alone, and cannot."""
    repo(t01=ticket("01", **REFACTOR))
    old_lock = Path(out_of("git", "rev-parse", "--git-common-dir")).resolve() / "implement-spec.lock"
    probe = tmp_path.parent / f"{tmp_path.name}-probe"
    older = (f'"{sys.executable}" -c "import fcntl; fcntl.flock(open(\'{old_lock}\', \'a\'), '
             f'fcntl.LOCK_EX | fcntl.LOCK_NB)" 2>/dev/null; echo $? > {probe}\n')
    code, _, out = walk(tmp_path, capsys, older + WORK + REPORT)
    assert code == 0 and probe.read_text() == "1\n", out


@pytest.mark.parametrize("older_run", [False, True])
def test_a_newer_driver_waits_while_an_older_driver_runs(repo, tmp_path, capsys, older_run):
    """Correctness review: an older driver holds only `implement-spec.lock`, so a newer driver waits for that lock,
    on a run of its own and on a run that an older driver started."""
    blocked_then_edited(repo, tmp_path, capsys)
    if older_run:
        as_older_driver(tmp_path, "01")
    old_lock = Path(out_of("git", "rev-parse", "--git-common-dir")).resolve() / "implement-spec.lock"
    with hold(old_lock):
        code, ledger, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and out == ("implement-spec: a driver of an older version runs in this repository: it holds the "
                                 f"lock {old_lock}. Wait until it ends, then run the driver again.\n")
    assert ("ticket/01" if older_run else "ticket/feat%2Fx/01") in branches()
    assert not [line for line in ledger if "renamed" in line] and state_of(tmp_path)["tickets"]["01"]["phase"] == "blocked"


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


def test_a_first_run_in_a_slot_of_another_run_is_refused(repo, tmp_path, capsys):
    """Correctness review: a slot has no state.json of its own, so its ticket branch would name a lock of its own,
    and two drivers would control one worktree."""
    repo(t01=ticket("01", **REFACTOR))
    subprocess.run(["git", "checkout", "-q", "-b", "ticket/feat%2Fx/01"], check=True)
    code, ledger, out = walk(tmp_path, capsys, "exit 9\n")
    assert (code, ledger) == (3, []) and out == (
        "the PR worktree is on the ticket branch ticket/feat%2Fx/01 of a run: check out the PR branch\n")
    assert not (tmp_path / ".agent/implement").exists()


def test_a_ticket_branch_that_cannot_be_checked_out_blocks_the_ticket(repo, tmp_path, capsys):
    """A slot of an earlier run with more slots still holds the branch."""
    blocked_then_edited(repo, tmp_path, capsys, phase="implement")
    subprocess.run(["git", "worktree", "add", "-q", str(tmp_path.parent / f"{tmp_path.name}-other"), "ticket/feat%2Fx/01"], check=True)
    code, ledger, _ = walk(tmp_path, capsys, "exit 9\n")
    assert code == 1 and "BLOCKED at the start: cannot check out ticket/feat%2Fx/01 in slot 1" in ledger[-1]


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


def hold(lock: Path):
    """The lock of another driver: it holds the kernel lock while it runs."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    held = lock.open("w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return held


def test_a_driver_on_another_pr_branch_does_not_stop_the_run(repo, tmp_path, capsys):
    """Runs on different PR branches share no state.json, no slot and no ref, so the lock of one stops no other."""
    repo(t01=ticket("01", **REFACTOR))
    locks = Path(out_of("git", "rev-parse", "--git-common-dir")).resolve() / "implement-spec"
    with hold(locks / "feat%2Fother.lock"), hold(locks / "feat.lock"):
        code, _, out = walk(tmp_path, capsys, WORK + REPORT)
    assert code == 0 and "1 of 1 tickets are merged into feat/x." in out
    assert branches() == ["feat/x"]  # the merge deleted ticket/feat%2Fx/01


def test_a_driver_that_starts_while_the_first_starts_its_run_locks_the_branch_of_that_run(repo, monkeypatch):
    """Correctness review: right after the second driver reads HEAD, the first one writes state.json and puts slot 1
    on a ticket branch. The PR branch that the second driver locks is still the one of that run."""
    repo(t01=ticket("01", **REFACTOR))
    real = driver.driver_slots.on_branch

    def first_driver_moves(slot):
        here = real(slot)
        driver.STATE.parent.mkdir(parents=True, exist_ok=True)
        driver.STATE.write_text(json.dumps({"run": {"pr_branch": "feat/x"}}))
        subprocess.run(["git", "checkout", "-q", "-b", "ticket/feat%2Fx/01"], check=True)
        return here
    monkeypatch.setattr(driver.driver_slots, "on_branch", first_driver_moves)
    assert driver._run_branch() == "feat/x"


def test_a_run_that_leaves_its_branch_while_the_driver_takes_the_lock_exits_3(repo, tmp_path, capsys, monkeypatch):
    repo(t01=ticket("01", **REFACTOR))
    real = driver_state.lock

    def lock_while_another_run_starts(path, shared=False):
        driver.STATE.parent.mkdir(parents=True, exist_ok=True)
        driver.STATE.write_text(json.dumps({"run": {"pr_branch": "feat/other", "schema": driver_state.SCHEMA}}))
        return real(path, shared)
    monkeypatch.setattr(driver_state, "lock", lock_while_another_run_starts)
    code, ledger, out = walk(tmp_path, capsys, WORK + REPORT)
    assert (code, ledger) == (3, []) and out == (
        "implement-spec: the run in this worktree left feat/x while the driver took its lock: run the driver again\n")


def test_a_second_driver_on_the_same_pr_branch_exits_3_and_changes_nothing(repo, tmp_path, capsys):
    """Final review, correctness 1: two drivers on one state.json lose updates and move the same refs. Ticket 09:
    the lock is in the git common directory, so it is the same file in every worktree of the repository."""
    repo(t01=ticket("01", **REFACTOR))
    lock = Path(out_of("git", "rev-parse", "--git-common-dir")).resolve() / "implement-spec/feat%2Fx.lock"
    with hold(lock):
        code, ledger, out = walk(tmp_path, capsys, WORK + REPORT)
        assert (code, out) == (3, "implement-spec: another driver runs on feat/x: it holds the lock "
                                  f"{lock}. Wait until it ends, then run the driver again.\n")
        assert ledger == [] and not (tmp_path / ".agent/implement").exists() and branches() == ["feat/x"]
        assert not Path(f"{tmp_path}-runner.calls").exists()
        assert run(capsys) == (0, "max frontier width: 1\n")  # --dry-run changes nothing, so it needs no lock
    # The lock ends with its holder, also one that was killed: no file is left to remove by hand.
    code, _, out = walk(tmp_path, capsys, WORK + REPORT)
    assert code == 0 and "1 of 1 tickets are merged into feat/x." in out
    assert walk(tmp_path, capsys, "exit 9\n")[0] == 0  # the run let its lock go when it ended
    assert out_of("git", "status", "--porcelain") == ""


def test_a_wrong_call_takes_no_lock_and_makes_no_file(repo, tmp_path, capsys):
    repo(t01="nothing here\n")
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and out.startswith("01-t.md: missing title\n") and not (tmp_path / ".agent/implement").exists()
    assert not (tmp_path / ".git/implement-spec.lock").exists()


def test_a_run_outside_a_repository_takes_no_lock_and_makes_no_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    (tmp_path / ".agent/implement").mkdir(parents=True)  # what an earlier run left does not make the call valid
    code, _, out = walk(tmp_path, capsys, "exit 9\n")
    assert code == 3 and "git status failed" in out and [path.name for path in tmp_path.iterdir()] == [".agent"]


def test_a_pr_branch_that_another_hand_moves_during_the_merge_gate_starts_the_landing_again(repo, tmp_path, capsys):
    """Ticket 09: the locked-file check and the ref move are one step. The ref moves only from the tip the check
    read; from a tip that moved, the merge, the check and the gate are made again."""
    two(repo)
    mark = tmp_path.parent / f"{tmp_path.name}-moved"
    # The gate of the first merge commit puts one more commit on the PR branch, as another hand would.
    gate = (f'[ -e {mark} ] || [ -z "$(git rev-parse -q --verify HEAD^2)" ] || {{ touch {mark}; git update-ref '
            'refs/heads/feat/x "$(git commit-tree "feat/x^{tree}" -p feat/x -m other)"; }')
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, gate=gate)
    assert code == 0 and mark.exists(), out
    subjects = out_of("git", "log", "--format=%s", walked()).splitlines()
    assert "other" in subjects and {"implementer 01", "implementer 02"} <= set(subjects)
    assert len([line for line in ledger if "merged into feat/x as a merge commit" in line]) == 1
    assert "| gates run | 5 |" in out  # two ticket tips, the stale merge commit, the new one, the end of run


# Ticket 09, review round 1 (m1, Codex 6): the restart has a bound, and only a stale ref starts it.
MERGE_GATE = '[ -z "$(git rev-parse -q --verify HEAD^2)" ] || '  # the rest of the line runs for a merge commit only
MOVE = 'git update-ref refs/heads/feat/x "$(git commit-tree "feat/x^{tree}" -p feat/x -m other)"'


def merge_blocked(ledger):
    (line,) = [line for line in ledger if "BLOCKED" in line]
    return line


def test_a_pr_branch_that_moves_at_each_merge_gate_blocks_the_ticket_after_the_second_try(repo, tmp_path, capsys):
    two(repo)
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, gate=MERGE_GATE + MOVE)
    assert code == 1, out
    assert "BLOCKED at the merge: " in merge_blocked(ledger) and "feat/x" in merge_blocked(ledger)
    assert "| gates run | 4 |" in out  # two ticket tips and two merge commits: the landing is tried two times
    assert out_of("git", "log", "--format=%s", "feat/x").splitlines().count("other") == 2


def test_a_red_merge_gate_does_not_start_the_landing_again_when_the_pr_branch_moved(repo, tmp_path, capsys):
    two(repo)
    mark = tmp_path.parent / f"{tmp_path.name}-red"
    gate = MERGE_GATE + f'[ -e {mark} ] || {{ touch {mark}; {MOVE}; exit 1; }}'
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, gate=gate)
    assert code == 1 and "BLOCKED at the merge: the gate is red on " in merge_blocked(ledger), out
    assert "| gates run | 3 |" in out


def test_a_conflict_on_the_second_try_blocks_the_ticket_with_no_conflict_agent(repo, tmp_path, capsys):
    """Requirement 11: the conflict agent runs once for each merge. The second try runs no agent."""
    two(repo)
    mark = tmp_path.parent / f"{tmp_path.name}-foreign"
    # Another hand commits both work files to the PR branch: the one of the ticket in flight is an add/add conflict.
    foreign = ('( export GIT_INDEX_FILE="$(git rev-parse --git-dir)/foreign-index"; git read-tree feat/x; '
               'blob=$(echo foreign | git hash-object -w --stdin); for nn in 01 02; do '
               'git update-index --add --cacheinfo 100644,$blob,work-$nn.txt; done; git update-ref refs/heads/feat/x '
               '"$(git commit-tree "$(git write-tree)" -p feat/x -m other)"; rm "$GIT_INDEX_FILE" )')
    gate = MERGE_GATE + f'[ -e {mark} ] || {{ touch {mark}; {foreign}; }}'
    code, ledger, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT, gate=gate)
    assert code == 1 and mark.exists(), out
    assert "BLOCKED at the merge: the merge into the PR branch conflicts in work-0" in merge_blocked(ledger)
    assert not [line for line in ledger if "conflict ran on" in line] and "| gates run | 3 |" in out


def test_the_landing_check_runs_while_the_ticket_holds_the_merge_lock(repo, tmp_path, capsys, monkeypatch):
    """Review round 1, m2: the check and the ref move are one step only under the lock that makes merges serial."""
    two(repo)
    runs, held, check = [], [], driver.driver_merge.refused

    class Seen(driver.Run):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            runs.append(self)

    def refused(*args):
        held.append(runs[0].merging.locked())
        return check(*args)

    monkeypatch.setattr(driver, "Run", Seen)
    monkeypatch.setattr(driver.driver_merge, "refused", refused)
    code, _, out = walk(tmp_path, capsys, WORK + TOGETHER + REPORT)
    assert code == 0 and held == [True, True], out


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
    code, ledger, out = walk(tmp_path, capsys, GIVE_UP + WORK + REPORT, gate=gate)
    assert code == 2 and "from the gate at .agent/implement/01/gate-" in ledger[-4] and ledger[-2:] == [GAVE_UP, ASKED], out
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
    assert f"Ticket 01: returned to implement from the PR tip: the recorded tip {tip} is not in feat/x." in ledger
    assert ledger[-1].startswith("Ticket 01: merged into feat/x as a fast-forward at ")
    assert (tmp_path / "work-01.txt").exists() and out_of("git", "rev-parse", "feat/x~1") == base
    # The end of run tidied the branch in the first run, and that tip left the branch too: it stops (test_tidy.py).
    assert code == 1 and f"End of run: BLOCKED at feat/x does not hold the tidied tip {tip}" in out, out


@pytest.mark.parametrize("older_driver", [False, True])
def test_a_rerun_after_an_interrupt_keeps_the_work_in_each_slot_and_reruns_the_role(repo, tmp_path, capsys, monkeypatch,
                                                                                      older_driver):
    """With `older_driver`, the interrupted run is one whose driver named the ticket branches `ticket/NN` (spec
    review): the resume renames each one, also in the slot that has it, and the slot keeps its work."""
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
    if older_driver:
        as_older_driver(tmp_path, "01", "02")
    resume = ('grep -q "was interrupted" "$prompt" || exit 9\nnn=$(basename "$(dirname "$report")")\n'
              '[ "$(cat wip-$nn.txt)" = half ] || exit 8\n')
    code, ledger, out = walk(tmp_path, capsys, resume + WORK + REPORT)
    assert code == 0, out + "\n".join(ledger)
    calls = dict(line.split() for line in Path(f"{tmp_path}-runner.calls").read_text().splitlines())
    assert calls == {"01": str(tmp_path.resolve()), "02": str(slot2.resolve())}
    assert out_of("git", "show", "feat/x:wip-01.txt") == "half" and out_of("git", "show", "feat/x:wip-02.txt") == "half"
    assert [line for line in ledger if "Ticket 02: resumed in slot 2 with the work the interrupted implementer left" in line]
    assert out_of("git", "status", "--porcelain") == "" and branches() == ["feat/x"]
    renamed = [f"Ticket {nn}: renamed the branch ticket/{nn} of an older driver to ticket/feat%2Fx/{nn}." for nn in ("01", "02")]
    assert [line for line in ledger if "renamed the branch" in line] == (renamed if older_driver else [])


def test_the_preflight_asks_each_used_entry_once_and_names_each_cli_that_fails(tmp_path, monkeypatch):
    """claude answers; codex refuses gpt-astra, and the gpt-6.1-sol call never ends."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text('#!/bin/sh\ncat > /dev/null\necho "$*" >> "$(dirname "$0")/asked"\n')
    (bin_dir / "codex").write_text('#!/bin/sh\ncat > /dev/null\necho "$*" >> "$(dirname "$0")/asked"\n'
                                   'case "$*" in *gpt-astra*) echo "ERROR: not supported"; exit 1;; '
                                   '*gpt-6.1-sol*) sleep 30;; esac\n')
    for cli in ("claude", "codex"):
        (bin_dir / cli).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(driver_agents, "PREFLIGHT_MINUTES", 0.02)
    models, _ = driver_agents.load_models(None)
    models["reviewer"] = ["openai/gpt-astra@medium"]
    assert driver_agents.used_entries(models) == ["claude-opus-5-5@medium", "claude-sonnet-5-5@medium",
                                                  "openai/gpt-6-astra@medium", "openai/gpt-6.1-sol@medium",
                                                  "openai/gpt-astra@medium"]
    assert driver_agents.preflight(models) == [
        "model entry openai/gpt-6.1-sol@medium: the codex CLI failed the preflight (no answer in 0.02 minutes): "
        "no output",
        "model entry openai/gpt-astra@medium: the codex CLI failed the preflight (exit 1): ERROR: not supported"]
    assert len((bin_dir / "asked").read_text().splitlines()) == 5


def test_last_words_takes_the_result_of_a_stream_json_line(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text('{"type": "system"}\n{"type": "result", "is_error": true, "result": "There is an issue"}\n\n')
    assert driver_agents.last_words(log) == "There is an issue"
    log.write_text("ERROR: plain\n")
    assert driver_agents.last_words(log) == "ERROR: plain"
    assert driver_agents.last_words(tmp_path / "none.log") == "no output"
