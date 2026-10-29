"""Agent launch acceptance tests, through `sh run.sh` with a fake runner script or fake claude/codex on PATH.

Interface choices (ticket 02): `--runner <script>` is called as `<script> <role> <prompt> <report>` in the slot
directory. A plain run (no --dry-run) branches ticket/feat%2Fcsv/NN from the PR tip in slot 1, runs the test-writer (unless the
Seam is `none — refactor`) then the implementer; from ticket 03 on an accepted ticket goes on to the driver's gate (`true` here) and a
merge into the PR branch, which deletes `ticket/feat%2Fcsv/NN`. Exit 0 when the ticket merges, 1 on BLOCKED.
`--only <role>` runs that one role on the first ticket and stops. `--spec <path>` names the spec for the prompts.
`--timeout <role>=<minutes>` (repeatable, fractions allowed) overrides a role timeout.
Ticket 08: the fixture has no `origin`, and a run that merges its ticket ends with the tidy and the final review, so
`ticket_commits()` reads the ticket's commits from `refs/implement-spec/feat%2Fcsv/recovery`, the PR tip from before the tidy.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_cli import GIT, ROLES, RUN, SKILL, TOOLS, drive, git, make_repo, models_file, ticket

RED = "FAILED tests/test_export.py::test_rows - AttributeError"
REFUSED = "ERROR: The 'gpt-astra' model is not supported when using Codex with a ChatGPT account."
RUNNER = f"""#!{sys.executable}
import json, os, subprocess, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
role, prompt, report = sys.argv[1:4]
def git(*a):
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *a]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
calls = here / "calls.jsonl"
past = [json.loads(l) for l in calls.read_text().splitlines()] if calls.exists() else []
n = sum(1 for c in past if c["role"] == role)
rec = dict(role=role, argv=sys.argv[1:], cwd=os.path.realpath(os.getcwd()), prompt=Path(prompt).read_text(),
           branch=git("rev-parse", "--abbrev-ref", "HEAD"), head=git("rev-parse", "HEAD"),
           status=git("status", "--porcelain"))
with calls.open("a") as f:
    f.write(json.dumps(rec) + "\\n")
plan = json.loads((here / "plan.json").read_text()).get(role, [])
step = plan[n] if n < len(plan) else {{}}
if role in ("reviewer", "final-reviewer") and "report" not in step:  # a role the test does not script reports no findings
    reds = [f"FAILED {{p}}::test_x - AttributeError" for p in git("diff", "--name-only", "feat/csv...HEAD", "--", "tests").split()]
    proofs = [dict(test=r.split()[1], file="demo/export.py", line=1, red=r) for r in reds]
    step = dict(step, stdout="".join(r + "\\n" for r in reds), report=dict(
        status="DONE", commits=[], files=[], concerns=[], findings=[], mutations=proofs))
for cmd in step.get("git", []):
    git(*cmd)
for path, text in step.get("files", {{}}).items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
sys.stdout.write(step.get("stdout", ""))
sys.stderr.write(step.get("stderr", ""))
sys.stdout.flush()
sys.stderr.flush()
time.sleep(step.get("sleep", 0))
sha = ""
if "commit" in step:
    git("add", "-A")
    git("commit", "-q", "-m", step["commit"])
    sha = git("rev-parse", "HEAD")
if step.get("report") is not None:
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).write_text(json.dumps(step["report"]).replace("$SHA", sha))
sys.exit(step.get("exit", 0))
"""


@pytest.fixture
def env(tmp_path):
    """A restricted PATH whose fake claude and codex append their argv (NUL-separated) and exit 1. They answer the
    preflight prompt with exit 0 and log nothing, except for the model gpt-astra, which the account cannot use."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in TOOLS:
        if shutil.which(name):
            (bin_dir / name).symlink_to(shutil.which(name))
    for cli in ("claude", "codex"):
        log = f'"{tmp_path}/argv-{cli}"'
        (bin_dir / cli).write_text(f"""#!/bin/sh
case "$(cat)" in *'implement-spec preflight'*)
    case " $* " in *' gpt-astra '*) echo "{REFUSED}"; exit 1;; esac
    exit 0;;
esac
printf '%s\\0' "$@" >> {log}
printf '\\036' >> {log}
exit 1
""")
        (bin_dir / cli).chmod(0o755)
    return dict(os.environ, PATH=str(bin_dir)), tmp_path


def launches(tmp_path, cli):
    path = tmp_path / f"argv-{cli}"
    records = path.read_bytes().decode().split("\x1e")[:-1] if path.exists() else []
    return [record.split("\0")[:-1] for record in records]


def value_after(argv, *flags):
    return next((argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg in flags), None)


class World:
    def __init__(self, env, *, refactor=False, **models):
        self.env, self.tmp = env, env[1]
        seam = {"seam": "none — refactor", "criteria": ()} if refactor else {}
        self.repo = make_repo(self.tmp / "demo", {"01-export-rows.md": ticket("01", "Export rows", **seam)})
        (self.repo / "specs/csv").mkdir(parents=True)
        (self.repo / "specs/csv/spec.md").write_text("# csv spec\n")
        git(self.repo, "add", "specs")
        git(self.repo, "commit", "-q", "-m", "spec")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        self.models = models_file(self.tmp, **models)
        self.runner = self.tmp / "runner/run-agent"
        self.runner.parent.mkdir()
        self.runner.write_text(RUNNER)
        self.runner.chmod(0o755)

    def run(self, plan=None, *extra, runner=True):
        (self.runner.parent / "plan.json").write_text(json.dumps(plan or {}))
        args = ["--tickets", ".agent/tickets", "--gate", "true", "--fast-gate", "true", "--spec", "specs/csv/spec.md",
                "--models", str(self.models), *(["--runner", str(self.runner)] if runner else []), *extra]
        return drive(self.env, self.repo, *args)

    def calls(self, role):
        path = self.runner.parent / "calls.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row["role"] == role]

    def ledger(self):
        path = self.repo / ".agent/implement/ledger.md"
        return path.read_text() if path.exists() else ""

    def ticket_commits(self):
        made = subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/implement-spec/feat%2Fcsv/recovery"], cwd=self.repo,
                              capture_output=True, text=True).stdout.strip() or "feat/csv"
        return git(self.repo, "rev-list", "--reverse", "--no-merges", f"{self.base}..{made}").split()


def ok(proc):
    assert proc.returncode == 0, proc.stdout + proc.stderr


def blocked(proc):
    assert proc.returncode == 1, proc.stdout + proc.stderr


def tw(n=0, stdout=RED + "\n", **changes):
    step = {"files": {"tests/test_export.py": f"def test_rows():\n    assert {n} == 'rows'\n"},
            "stdout": stdout, "commit": "test: red",
            "report": {"status": "DONE", "commits": ["$SHA"], "files": ["tests/test_export.py"], "concerns": [],
                       "locked": ["tests/test_export.py"],
                       "red": [{"test": "tests/test_export.py::test_rows", "line": RED}]}}
    return {**step, **changes}


def impl(n=0, **changes):
    step = {"files": {"demo/export.py": f"def export_csv():\n    return 'rows{n}'\n"}, "commit": "feat: rows",
            "report": {"status": "DONE", "commits": ["$SHA"], "files": ["demo/export.py"], "concerns": [],
                       "green": ["1 passed"]}}
    return {**step, **changes}


def with_report(step, **changes):
    return {**step, "report": {**step["report"], **changes}}


def test_runner_is_called_with_role_prompt_report_in_the_slot_directory(env):
    w = World(env, refactor=True)
    ok(w.run({"implementer": [impl()]}))
    (call,) = w.calls("implementer")
    role, prompt, report = call["argv"]
    assert role == "implementer" and call["prompt"].strip()
    assert Path(call["cwd"], report).resolve().as_posix().endswith(".agent/implement/01/implementer-0.json")
    assert Path(call["cwd"], report).exists()
    assert call["cwd"] == str(w.repo.resolve()) and call["branch"] == "ticket/feat%2Fcsv/01"


def test_default_models_file_names_every_role():
    models = json.loads((SKILL / "models.json").read_text())
    assert set(models) == set(ROLES) and all(models[role] for role in ROLES)


def test_help_lists_the_timeout_flag_and_default_minutes():
    proc = subprocess.run(["sh", str(RUN), "--help"], capture_output=True, text=True)
    assert "--timeout" in proc.stdout and re.search(r"implementer\W+90", proc.stdout), proc.stdout


@pytest.mark.parametrize("entry", ["openai/gpt-sol@medium"])
def test_openai_implementer_launches_codex_exec_in_workspace_write(env, entry):
    w = World(env, refactor=True, implementer=[entry])
    w.run(runner=False)
    argv = launches(w.tmp, "codex")[0]
    assert argv[0] == "exec" and "--ephemeral" not in argv
    assert value_after(argv, "--model", "-m") == "gpt-sol"
    assert any("model_reasoning_effort" in a and "medium" in a for a in argv)
    assert value_after(argv, "--sandbox", "-s") == "workspace-write"
    assert not launches(w.tmp, "claude")


def test_the_reviewer_launches_codex_exec_read_only(env):
    w = World(env, refactor=True, reviewer=["openai/gpt-sol@medium"])
    w.run(None, "--only", "reviewer", runner=False)
    argv = launches(w.tmp, "codex")[0]
    assert argv[0] == "exec" and "--ephemeral" not in argv
    assert value_after(argv, "--sandbox", "-s") == "read-only"
    assert value_after(argv, "--model", "-m") == "gpt-sol"


@pytest.mark.parametrize("writes", ["file", "message"])
def test_a_read_only_codex_report_is_the_file_it_wrote_else_its_last_message(env, writes):
    # A read-only sandbox can still let the agent write its report; codex then writes the last message to the -o file.
    w = World(env, refactor=True, reviewer=["openai/gpt-sol@medium"])
    report = {"status": "DONE", "commits": [], "files": [], "concerns": [], "findings": []}
    (w.tmp / "bin/codex").write_text(f"""#!{sys.executable}
import re, sys
from pathlib import Path
prompt, argv = sys.stdin.read(), sys.argv[1:]
last = Path(argv[argv.index("-o") + 1])
if "implement-spec preflight" in prompt:
    last.write_text("OK")
elif {writes!r} == "file":
    Path(re.search(r"JSON report at (\\S+?)\\.? ", prompt).group(1)).write_text({json.dumps(report)!r})
    last.write_text("Wrote and validated the report.\\n")
else:
    last.write_text({json.dumps(report)!r})
""")
    ok(w.run(None, "--only", "reviewer", runner=False))
    assert json.loads((w.repo / ".agent/implement/01/reviewer-0.json").read_text()) == report
    assert "reviewer ran on openai/gpt-sol@medium" in w.ledger() and "report accepted" in w.ledger()


@pytest.mark.parametrize("entry", ["claude-opus-5@medium", "anthropic/claude-opus-5@medium"])
def test_claude_entry_launches_claude_p_and_bare_means_anthropic(env, entry):
    w = World(env, refactor=True, implementer=[entry])
    w.run(runner=False)
    argv = launches(w.tmp, "claude")[0]
    assert ("-p" in argv or "--print" in argv)
    assert value_after(argv, "--model") == "claude-opus-5"
    assert value_after(argv, "--effort") == "medium"
    assert not launches(w.tmp, "codex")


@pytest.mark.parametrize("entry", ["mystery/model@medium", "anthropic/claude-opus-5@turbo", "openai/gpt-sol@turbo"])
def test_unknown_provider_or_effort_exits_3_naming_role_and_entry_before_any_agent(env, entry):
    w = World(env, reviewer=[entry])
    proc = drive(w.env, w.repo, "--tickets", ".agent/tickets", "--gate", "true", "--fast-gate", "true", "--models", str(w.models), "--dry-run")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert any("reviewer" in l and entry in l for l in proc.stdout.splitlines()), proc.stdout
    proc = w.run({"test-writer": [tw()]})
    assert proc.returncode == 3 and not w.calls("test-writer")
    assert not launches(w.tmp, "claude") and not launches(w.tmp, "codex")
    assert "ticket/feat%2Fcsv/01" not in git(w.repo, "branch", "--list")


def test_implementer_prompt_carries_pointers_and_only_template_sentences(env):
    w = World(env)
    ok(w.run({"test-writer": [tw()], "implementer": [impl()]}))
    (call,) = w.calls("implementer")
    prompt, report = call["prompt"], call["argv"][2]
    sha = w.ticket_commits()[0]
    for pointer in ("specs/csv/spec.md", ".agent/tickets/01-export-rows.md", "`export_csv()`", sha,
                    "tests/test_export.py", report):
        assert pointer in prompt, pointer
    template = (SKILL / "prompts/implementer.md").read_text()
    for prefix in (str(w.repo.resolve()) + "/", str(w.repo) + "/"):
        prompt = prompt.replace(prefix, "")
    for value in (report, "specs/csv/spec.md", ".agent/tickets/01-export-rows.md", "`export_csv()`", sha,
                  "tests/test_export.py", "feat/csv", "true"):
        prompt = prompt.replace(value, "")
    lines = lambda text: {" ".join(l.split()) for l in text.splitlines() if l.strip()}
    assert lines(prompt) <= lines(re.sub(r"\{[a-z_]+\}", "", template))


@pytest.mark.parametrize("role, extra, refactor", [
    ("test-writer", (), False), ("implementer", (), True),
    ("reviewer", ("--only", "reviewer"), True)])
def test_every_prompt_ends_with_report_path_schema_and_the_never_rule(env, role, extra, refactor):
    w = World(env, refactor=refactor)
    w.run(None, *extra)
    call = w.calls(role)[0]
    prompt, report = call["prompt"].rstrip(), call["argv"][2]
    last = prompt.splitlines()[-1].lower()
    assert "never" in last and "stash" in last and "reset" in last and "check out" in last, last
    assert prompt.rfind(report) < prompt.rfind("DONE_WITH_CONCERNS") < prompt.rfind(prompt.splitlines()[-1])
    assert report in prompt


@pytest.mark.parametrize("role", ["test-writer", "implementer", "fixer", "conflict", "reviewer", "final-reviewer",
                                  "final-fixer", "rebase-conflict"])
def test_each_role_that_runs_commands_stops_only_its_own_processes(role):
    """Issue 101: a `pkill -f` of one slot killed the gate of another."""
    text = (SKILL / f"prompts/{role}.md").read_text()
    assert "Stop only processes you started and their child processes, by PID" in text
    assert "Never kill a process you did not start, and never use `pkill`, `killall` or a `kill` by name" in text
    assert "If a process you did not start blocks you, report it in your concerns and do not kill it." in text


def test_no_report_is_a_violation_with_one_rerun_then_blocked(env):
    w = World(env, refactor=True)
    blocked(w.run({"implementer": [{}, {}, impl()]}))
    first, second = w.calls("implementer")
    assert first["prompt"].strip() in second["prompt"] and len(second["prompt"]) > len(first["prompt"])
    assert "report" in second["prompt"][len(first["prompt"].rstrip()):].lower()
    assert len(w.calls("implementer")) == 2
    assert any("01" in l and "BLOCKED" in l for l in w.ledger().splitlines()), w.ledger()


def test_a_model_the_account_cannot_use_fails_the_preflight_with_exit_3_before_any_agent(env):
    w = World(env, reviewer=["openai/gpt-astra@medium"])
    proc = w.run({"test-writer": [tw()]}, runner=False)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert f"model entry openai/gpt-astra@medium: the codex CLI failed the preflight (exit 1): {REFUSED}" \
        in proc.stdout.splitlines(), proc.stdout
    assert not launches(w.tmp, "claude") and not launches(w.tmp, "codex")
    assert "ticket/feat%2Fcsv/01" not in git(w.repo, "branch", "--list") and not w.ledger()


def test_a_cli_that_fails_with_no_report_stops_the_run_with_exit_4_and_the_same_command_resumes(env):
    w = World(env, refactor=True)
    plan = {"implementer": [{"stderr": REFUSED + "\n", "exit": 1}] * 2 + [impl()]}
    proc = w.run(plan)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert f"implement-spec: the run stopped: the implementer on anthropic/claude-opus-5@medium exited 1 with no " \
        f"report: {REFUSED}" in proc.stdout.splitlines(), proc.stdout
    assert "BLOCKED" not in w.ledger() and "Ticket 01: the run stops and the phase stays for the next run: the implementer" in w.ledger()
    assert json.loads((w.repo / ".agent/implement/state.json").read_text())["tickets"]["01"]["phase"] == "implement"
    ok(w.run(plan))
    assert len(w.calls("implementer")) == 3


def test_finished_status_reruns_the_implementer_once_with_the_error_appended(env):
    w = World(env)
    ok(w.run({"test-writer": [tw()], "implementer": [with_report(impl(0), status="FINISHED"), impl(1)]}))
    first, second = w.calls("implementer")
    assert first["prompt"].strip() in second["prompt"] and "FINISHED" in second["prompt"]
    assert "FINISHED" not in first["prompt"]


def test_second_schema_violation_blocks_the_ticket_with_the_error_in_the_ledger(env):
    w = World(env)
    bad = lambda n: with_report(impl(n), status="FINISHED")
    blocked(w.run({"test-writer": [tw()], "implementer": [bad(0), bad(1), impl(2)]}))
    assert len(w.calls("implementer")) == 2
    assert any("01" in l and "BLOCKED" in l and "FINISHED" in l for l in w.ledger().splitlines()), w.ledger()


@pytest.mark.parametrize("second_matches", [True, False])
def test_red_line_missing_from_the_log_reruns_once_then_blocks(env, second_matches):
    w = World(env)
    plan = {"test-writer": [tw(0, "no such line\n"), tw(1, RED + "\n" if second_matches else "still none\n")],
            "implementer": [impl()]}
    proc = w.run(plan)
    second = w.calls("test-writer")[1]["prompt"]
    assert "red line not found in log" in second and "red line not found" not in w.calls("test-writer")[0]["prompt"]
    assert f"red line not found in log: tests/test_export.py::test_rows: {RED}" in second
    if second_matches:
        ok(proc)
        assert len(w.calls("implementer")) == 1
    else:
        blocked(proc)
        assert len(w.calls("test-writer")) == 2 and not w.calls("implementer")
        assert any("01" in l and "BLOCKED" in l for l in w.ledger().splitlines()), w.ledger()


@pytest.mark.parametrize("claimed, printed", [
    ("E       assert 0 == 'rows'", "E           assert 0 == 'rows'"),  # pytest pads a parametrized test's lines
    ("AssertionError: '12 UNP\u2212 EL' !~ /Resilience\\s*4\\.3/",  # a stream-json log keeps non-ASCII raw
     '"text": "AssertionError: \'12 UNP\u2212 EL\' !~ /Resilience\\\\s*4\\\\.3/"')], ids=["spacing", "non-ascii"])
def test_red_line_that_differs_from_the_log_only_in_spacing_or_escaping_is_seen(env, claimed, printed):
    w = World(env)
    writer = with_report(tw(0, printed + "\n"), red=[{"test": "tests/test_export.py::test_rows", "line": claimed}])
    ok(w.run({"test-writer": [writer], "implementer": [impl()]}))
    assert len(w.calls("test-writer")) == 1


def test_implementer_report_with_no_new_commit_is_a_violation(env):
    w = World(env)
    nothing = with_report(impl(), commits=[])
    nothing.pop("commit"), nothing.pop("files")
    blocked(w.run({"test-writer": [tw()], "implementer": [nothing, nothing]}))
    assert len(w.calls("implementer")) == 2
    assert any("01" in l and "BLOCKED" in l and "commit" in l for l in w.ledger().splitlines()), w.ledger()


def test_timeout_keeps_the_tree_and_reruns_once_with_double_the_limit(env):
    w = World(env, refactor=True)
    hang = {"files": {"wip.txt": "half done\n"}, "sleep": 60}
    # 0.04 min = 2.4 s: the rerun sleeps 3.6 s, which passes only under the doubled 4.8 s limit.
    ok(w.run({"implementer": [hang, impl(sleep=3.6)]}, "--timeout", "implementer=0.04"))
    first, second = w.calls("implementer")
    assert "wip.txt" in second["status"]
    prompt = second["prompt"].lower()
    assert "timed out" in prompt and "in the tree" in prompt and "timed out" not in first["prompt"].lower()
    assert git(w.repo, "show", "feat/csv:wip.txt") == "half done\n"


def test_second_timeout_blocks_the_ticket(env):
    w = World(env, refactor=True)
    hang = {"files": {"wip.txt": "half done\n"}, "sleep": 60}
    blocked(w.run({"implementer": [hang, hang, impl()]}, "--timeout", "implementer=0.04"))
    assert len(w.calls("implementer")) == 2
    assert any("BLOCKED" in l and "timed out" in l for l in w.ledger().splitlines()), w.ledger()
    assert (w.repo / "wip.txt").exists()


def test_head_moved_to_the_pr_branch_resets_the_slot_and_reruns_once(env):
    w = World(env)
    ok(w.run({"test-writer": [tw()], "implementer": [{"git": [["checkout", "feat/csv"]]}, impl()]}))
    first, second = w.calls("implementer")
    tests_sha = w.ticket_commits()[0]
    assert (second["branch"], second["head"]) == ("ticket/feat%2Fcsv/01", tests_sha)
    assert any("implementer" in l and "feat/csv" in l for l in w.ledger().splitlines()), w.ledger()


def test_a_missing_ticket_branch_is_a_violation_named_in_the_ledger(env):
    w = World(env)
    gone = {"git": [["checkout", "--detach"], ["branch", "-D", "ticket/feat%2Fcsv/01"]]}
    proc = w.run({"test-writer": [tw()], "implementer": [gone, impl()]})
    assert "Traceback" not in proc.stderr
    assert any("ticket/feat%2Fcsv/01" in l and "branch" in l for l in w.ledger().splitlines()), w.ledger()


def test_log_next_to_report_and_ledger_records_role_entry_duration_and_cost(env):
    w = World(env, refactor=True, implementer=["claude-opus-5@medium"])
    step = impl(stdout='OUT-MARK\n{"total_cost_usd": 0.4242}\n', stderr="ERR-MARK\n")
    ok(w.run({"implementer": [step]}))
    folder = w.repo / ".agent/implement/01"
    log = (folder / "implementer-0.log").read_text()
    assert "OUT-MARK" in log and "ERR-MARK" in log and (folder / "implementer-0.json").exists()
    (line,) = [l for l in w.ledger().splitlines() if "implementer" in l and "claude-opus-5@medium" in l]
    assert re.search(r"\d+(\.\d+)?s\b", line) and "0.4242" in line, line
