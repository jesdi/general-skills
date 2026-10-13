"""Ticket 03 acceptance tests: walk the graph with slots, gate by the driver, merge, resume.

All through `sh run.sh --runner <script> --parallel N [--prepare <cmd>] --gate <cmd>` on the three-ticket fixture
repository. The fake runner plays test-writer and implementer per ticket; it logs, per call, its cwd, branch, HEAD,
start and end time into `<rig>/calls/`. The gate is a small script that logs its commit, status, branch and
environment into `<rig>/gates/`. Concurrency is steered with signal files (`touch` / `wait` plan keys), never sleeps.

Interface choices (ticket 03):
- gate record: `.agent/implement/<NN>/gate-<first 7 hex of the commit>.log`.
- `summary.md` sits in `.agent/implement/` and its table is repeated on stdout.
- `state.json` is `{"tickets": {"NN": {phase, round, tip, test_writer_sha, locked, green_gates, slot}}}`;
  `green_gates` holds full SHAs (a dict with a `commit` key is accepted too).
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_cli import RUN, TOOLS, git, make_repo, models_file, ticket, three

RUNNER = f"""#!{sys.executable}
import json, os, subprocess, sys, time
from pathlib import Path
rig = Path(__file__).resolve().parent
role, prompt, report = sys.argv[1:4]
nn = Path(report).parent.name
key = f"{{nn}}/{{role}}"
def git(*a):
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *a]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
def save(rec):
    tmp = rig / "calls" / (rec["id"] + ".tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, rig / "calls" / (rec["id"] + ".json"))
def where():
    return dict(branch=git("rev-parse", "--abbrev-ref", "HEAD"), head=git("rev-parse", "HEAD"),
                status=git("status", "--porcelain"))
def wait(done):
    end = time.time() + 6
    while not done():
        if time.time() > end:
            sys.exit(7)
        time.sleep(0.02)
(rig / "calls").mkdir(exist_ok=True)
(rig / "sig").mkdir(exist_ok=True)
n = sum(1 for f in (rig / "calls").glob("*.json") if json.loads(f.read_text())["key"] == key)
rec = dict(id=f"{{time.time_ns()}}-{{os.getpid()}}", key=key, nn=nn, role=role, t0=time.time(),
           cwd=os.path.realpath(os.getcwd()), prompt=Path(prompt).read_text(), **where(),
           log=git("log", "--format=%s", "HEAD"), pr_log=git("log", "--format=%s", "feat/csv"))
save(rec)
step = json.loads((rig / "plan.json").read_text()).get(key, [])
step = step[n] if n < len(step) else {{}}
for name in step.get("touch", []):
    (rig / "sig" / name).touch()
for name in step.get("wait", []):
    wait(lambda: (rig / "sig" / name).exists())
if step.get("wait_pr_moves"):
    wait(lambda: git("rev-parse", "feat/csv") != git("merge-base", "HEAD", "feat/csv"))
    rec["after_wait"] = where()
for path, text in step.get("files", {{}}).items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
sys.stdout.write(step.get("stdout", ""))
sys.stdout.flush()
time.sleep(step.get("sleep", 0))
sha = ""
if "commit" in step:
    git("add", "-A")
    git("commit", "-q", "-m", step["commit"])
    sha = git("rev-parse", "HEAD")
if step.get("report") is not None:
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).write_text(json.dumps(step["report"]).replace("$SHA", sha))
rec.update(t1=time.time(), sha=sha)
save(rec)
"""

GATE = f"""#!{sys.executable}
import json, os, subprocess, sys, time
from pathlib import Path
rig = Path(__file__).resolve().parent
def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
conf = json.loads((rig / "gate.json").read_text())
(rig / "gates").mkdir(exist_ok=True)
branch = git("rev-parse", "--abbrev-ref", "HEAD")
code = 1 if conf.get("all") or branch in conf.get("red", []) else 0
rec = dict(t0=time.time(), cwd=os.path.realpath(os.getcwd()), commit=git("rev-parse", "HEAD"), branch=branch,
           status=git("status", "--short"), base=os.environ.get("IMPLEMENT_SPEC_BASE"),
           pycache=os.environ.get("PYTHONPYCACHEPREFIX"), exit=code)
print("GATE-OUTPUT-MARK", rec["commit"])
(rig / "gates" / f"{{time.time_ns()}}-{{os.getpid()}}.json").write_text(json.dumps(rec))
sys.exit(code)
"""

COST = '{"total_cost_usd": 0.25}\n'


def tw(nn):
    path, red = f"tests/test_{nn}.py", f"FAILED tests/test_{nn}.py::test_x - AttributeError"
    return {"files": {path: f"def test_x():\n    assert {nn} == 'x'\n"}, "stdout": red + "\n" + COST,
            "commit": f"test: {nn} red",
            "report": {"status": "DONE", "commits": ["$SHA"], "files": [path], "concerns": [], "locked": [path],
                       "red": [{"test": f"{path}::test_x", "line": red}]}}


def impl(nn, path="demo/export.py", text=None, **changes):
    step = {"files": {path: text or f"def fn_{nn}():\n    return {nn!r}\n"}, "stdout": COST,
            "commit": f"feat: {nn} rows",
            "report": {"status": "DONE", "commits": ["$SHA"], "files": [path], "concerns": [], "green": ["1 passed"]}}
    return {**step, **changes}


PATHS = {"01": "demo/export.py", "02": "demo/export.py", "03": "demo/cli.py"}


def plan(**changes):
    """Every ticket gets a test-writer and an implementer step; `changes` replace or extend single steps."""
    steps = {}
    for nn, path in PATHS.items():
        steps[f"{nn}/test-writer"] = [tw(nn)]
        steps[f"{nn}/implementer"] = [impl(nn, path)]
    steps.update({key.replace("_", "/", 1).replace("_", "-"): value for key, value in changes.items()})
    return steps


def make_env(tmp):
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    for name in TOOLS:
        if shutil.which(name):
            (bin_dir / name).symlink_to(shutil.which(name))
    for cli in ("claude", "codex"):
        (bin_dir / cli).write_text("#!/bin/sh\nexit 1\n")
        (bin_dir / cli).chmod(0o755)
    return dict(os.environ, PATH=str(bin_dir))


class Rig:
    def __init__(self, tmp, files=None, gate=None, ignore=None):
        self.tmp, self.env = tmp, make_env(tmp)
        self.repo = make_repo(tmp / "demo", files or three())
        (self.repo / "specs/csv").mkdir(parents=True)
        (self.repo / "specs/csv/spec.md").write_text("# csv spec\n")
        if ignore:
            with (self.repo / ".gitignore").open("a") as f:
                f.write(ignore + "\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "spec")
        self.rig = tmp / "rig"
        self.rig.mkdir()
        for name, text in (("run-agent", RUNNER), ("gate", GATE)):
            (self.rig / name).write_text(text)
            (self.rig / name).chmod(0o755)
        self.models = models_file(tmp)
        self.gate_conf(**(gate or {}))
        self.slot2 = (self.repo / ".agent/worktrees/slot-2").resolve()

    def gate_conf(self, **conf):
        (self.rig / "gate.json").write_text(json.dumps(conf))

    def run(self, steps, *extra, parallel="2", watch=False):
        """Run the driver once; with `watch`, read state.json in a tight loop meanwhile (torn reads, snapshots)."""
        (self.rig / "plan.json").write_text(json.dumps(steps))
        args = ["--tickets", ".agent/tickets", "--gate", str(self.rig / "gate"), "--spec", "specs/csv/spec.md",
                "--models", str(self.models), "--runner", str(self.rig / "run-agent"), "--parallel", parallel, *extra]
        out, err = self.tmp / "out.txt", self.tmp / "err.txt"
        with out.open("w") as o, err.open("w") as e:
            proc = subprocess.Popen(["sh", str(RUN), *args], cwd=self.repo, env=self.env, text=True, stdout=o,
                                    stderr=e, start_new_session=True)
            self.snaps, self.torn = [], []
            state, end = self.repo / ".agent/implement/state.json", time.time() + 120
            while proc.poll() is None and time.time() < end:
                if watch:
                    try:
                        self.snaps.append(json.loads(state.read_text()))
                    except FileNotFoundError:
                        pass
                    except json.JSONDecodeError as exc:
                        self.torn.append(str(exc))
                time.sleep(0.002)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        self.code, self.out, self.err = proc.returncode, out.read_text(), err.read_text()
        return self

    def calls(self, nn=None, role=None):
        folder = self.rig / "calls"
        rows = [json.loads(p.read_text()) for p in folder.glob("*.json")] if folder.exists() else []
        rows = [r for r in rows if nn in (None, r["nn"]) and role in (None, r["role"])]
        return sorted(rows, key=lambda r: r["t0"])

    def sha(self, nn, role):
        return self.calls(nn, role)[0]["sha"]

    def gates(self):
        folder = self.rig / "gates"
        rows = [json.loads(p.read_text()) for p in folder.glob("*.json")] if folder.exists() else []
        return sorted(rows, key=lambda r: r["t0"])

    def ledger(self):
        path = self.repo / ".agent/implement/ledger.md"
        return path.read_text().splitlines() if path.exists() else []

    def state(self):
        return json.loads((self.repo / ".agent/implement/state.json").read_text())

    def pr_subjects(self):
        return git(self.repo, "log", "--format=%s", "feat/csv").splitlines()

    def branches(self):
        return git(self.repo, "branch", "--list", "ticket/*").split()

    def output(self):
        return f"exit {self.code}\n{self.out}{self.err}"


def commit_of(entry):
    return entry if isinstance(entry, str) else entry["commit"]


def green(state, nn):
    return {commit_of(e) for e in state["tickets"][nn]["green_gates"]}


@pytest.fixture(scope="module")
def std(tmp_path_factory):
    """The default scenario: three tickets, --parallel 2, 01's implementer holds until 03 is in flight."""
    rig = Rig(tmp_path_factory.mktemp("std"))
    steps = plan(**{"03_test-writer": [{**tw("03"), "touch": ["s3"]}], "01_implementer": [{**impl("01"), "wait": ["s3"]}]})
    return rig.run(steps, watch=True)


@pytest.fixture(scope="module")
def blocked(tmp_path_factory):
    """The default scenario with a red gate on ticket/01."""
    return Rig(tmp_path_factory.mktemp("blocked"), gate={"red": ["ticket/01"]}).run(plan())


def test_01_starts_in_slot_1_03_in_slot_2_and_02_after_the_merge_of_01(std):
    assert std.code == 0, std.output()
    first = lambda nn: std.calls(nn)[0]
    assert first("01")["cwd"] == str(std.repo.resolve()) and first("03")["cwd"] == str(std.slot2)
    assert "feat: 01 rows" in first("02")["pr_log"] and "feat: 01 rows" in first("02")["log"]
    assert first("02")["t0"] > std.calls("01")[-1]["t1"] and first("02")["branch"] == "ticket/02"


def test_touches_overlap_holds_03_until_01_is_merged(tmp_path):
    files = three(**{"03-cli-flag.md": ticket("03", "CLI flag", seam="`demo export` CLI",
                                              touches="`demo/export.py: export_csv`")})
    rig = Rig(tmp_path, files).run(plan())
    assert rig.code == 0, rig.output()
    assert rig.calls("03"), "03 never started"
    third = rig.calls("03")[0]
    assert third["t0"] >= rig.calls("01")[-1]["t1"]
    assert "feat: 01 rows" in third["pr_log"] and "feat: 01 rows" in third["log"]


def test_prepare_runs_once_in_slot_2_and_the_slot_is_reused(tmp_path):
    files = three(**{"02-quote-fields.md": ticket("02", "Quote fields", "03", touches="`demo/other.py: g`")})
    rig = Rig(tmp_path, files, ignore="node_modules")
    (rig.repo / "node_modules").mkdir()
    prepare = f"ln -s ../../../node_modules node_modules && pwd -P >> {rig.tmp}/prepare.log"
    steps = plan(**{"02_test-writer": [{**tw("02"), "touch": ["s2"]}],
                    "01_implementer": [{**impl("01"), "wait": ["s2"]}],
                    "02_implementer": [impl("02", "demo/other.py")]})
    rig.run(steps, "--prepare", prepare)
    assert rig.code == 0, rig.output()
    assert (rig.tmp / "prepare.log").read_text().split() == [str(rig.slot2)]
    assert rig.calls("03")[0]["cwd"] == rig.calls("02")[0]["cwd"] == str(rig.slot2)
    assert (rig.slot2 / "node_modules").is_symlink() and all(g["status"] == "" for g in rig.gates())


def test_failing_prepare_exits_3_before_any_agent_naming_slot_and_command(tmp_path):
    rig = Rig(tmp_path).run(plan(), "--prepare", "false")
    assert rig.code == 3, rig.output()
    assert re.search(r"slot[- ]?2", rig.out + rig.err) and "false" in rig.out + rig.err
    assert not rig.calls() and not rig.gates()


def test_fast_forward_merge_has_exactly_one_gate_record_and_no_merge_gate(tmp_path):
    rig = Rig(tmp_path, {"01-export-rows.md": ticket("01", "Export rows")}).run(plan())
    assert rig.code == 0, rig.output()
    a1 = rig.sha("01", "implementer")
    assert git(rig.repo, "rev-parse", "feat/csv").strip() == a1
    records = sorted((rig.repo / ".agent/implement/01").glob("gate-*.log"))
    assert [p.name for p in records] == [f"gate-{a1[:7]}.log"]
    text = records[0].read_text()
    assert a1[:7] in text and "GATE-OUTPUT-MARK" in text and len(re.findall(r"\d\d:\d\d:\d\d", text)) >= 2
    assert re.search(r"exit\D*0", text) and not re.search(r"^(\?\?| M|M |A )", text, re.M)
    (gate,) = rig.gates()
    assert (gate["commit"], gate["status"], gate["cwd"]) == (a1, "", str(rig.repo.resolve()))


def test_gate_environment_names_the_pr_branch_and_a_pycache_prefix_under_agent_implement(tmp_path):
    rig = Rig(tmp_path, {"01-export-rows.md": ticket("01", "Export rows")}).run(plan())
    assert rig.code == 0, rig.output()
    (gate,) = rig.gates()
    assert gate["base"] == "feat/csv" and gate["pycache"]
    assert os.path.realpath(gate["pycache"]).startswith(str((rig.repo / ".agent/implement").resolve()) + os.sep)


def test_red_gate_blocks_the_ticket_names_the_record_and_launches_no_later_role(blocked):
    assert blocked.code == 1, blocked.output()
    a1 = blocked.sha("01", "implementer")
    assert [l for l in blocked.ledger() if "01" in l and "BLOCKED" in l and f"gate-{a1[:7]}.log" in l], blocked.ledger()
    assert re.search(r"exit\D*1", (blocked.repo / f".agent/implement/01/gate-{a1[:7]}.log").read_text())
    assert {c["role"] for c in blocked.calls("01")} == {"test-writer", "implementer"}
    assert blocked.state()["tickets"]["01"]["phase"] == "blocked"
    assert "ticket/01" in blocked.branches() and "feat: 01 rows" not in blocked.pr_subjects()


def test_blocked_ticket_stops_only_the_tickets_it_blocks(blocked):
    assert not blocked.calls("02"), "02 is blocked by 01 and must not start"
    assert "feat: 03 rows" in blocked.pr_subjects() and blocked.state()["tickets"]["03"]["phase"] == "merged"
    assert blocked.code == 1


def test_non_fast_forward_merge_is_a_plumbing_merge_commit_gated_once(tmp_path):
    files = {k: v for k, v in three().items() if not k.startswith("02")}
    rig = Rig(tmp_path, files)
    steps = plan(**{"01_test-writer": [{**tw("01"), "touch": ["s1"]}],
                    "03_implementer": [{**impl("03", "demo/cli.py"), "wait": ["s1"]}],
                    "01_implementer": [{**impl("01"), "wait_pr_moves": True}]})
    rig.run(steps)
    assert rig.code == 0, rig.output()
    a01, a03 = rig.sha("01", "implementer"), rig.sha("03", "implementer")
    tip = git(rig.repo, "rev-parse", "feat/csv").strip()
    assert len(git(rig.repo, "rev-list", "--parents", "-n1", tip).split()) == 3, "tip is a merge commit"
    commits = [g["commit"] for g in rig.gates()]
    assert sorted(commits) == sorted([a01, a03, tip]), commits
    waited = rig.calls("01", "implementer")[0]
    assert waited["after_wait"]["branch"] == "ticket/01" and waited["after_wait"]["head"] == waited["head"]
    assert waited["after_wait"]["status"] == ""
    assert git(rig.repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feat/csv"
    assert git(rig.repo, "status", "--short") == "" and (rig.repo / "demo/cli.py").exists()
    assert "fn_01" in (rig.repo / "demo/export.py").read_text()


def test_merged_ticket_branches_are_deleted_and_their_commits_are_ancestors(std):
    assert std.code == 0, std.output()
    assert std.branches() == []
    for nn in ("01", "02", "03"):
        for role in ("test-writer", "implementer"):
            git(std.repo, "merge-base", "--is-ancestor", std.sha(nn, role), "feat/csv")
    assert git(std.repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feat/csv"
    assert git(std.repo, "status", "--short") == ""


def test_state_json_is_atomic_and_no_transition_is_lost_with_two_tickets_in_flight(std):
    assert std.code == 0, std.output()
    assert not std.torn and std.snaps, std.torn
    seen_phase, seen_green = {}, {}
    for snap in std.snaps:
        for nn, entry in snap["tickets"].items():
            if seen_phase.get(nn) == "merged":
                assert entry["phase"] == "merged", f"{nn} regressed to {entry['phase']}"
            seen_phase[nn] = entry["phase"]
            assert seen_green.get(nn, set()) <= green(snap, nn), f"{nn} lost a recorded gate"
            seen_green[nn] = green(snap, nn)
    final = std.state()
    for nn in ("01", "02", "03"):
        entry = final["tickets"][nn]
        assert entry["phase"] == "merged" and entry["round"] == 0
        assert std.sha(nn, "implementer") in green(final, nn)
        assert entry["test_writer_sha"] == std.sha(nn, "test-writer") and entry["locked"] == [f"tests/test_{nn}.py"]


def test_ledger_is_plain_sentences_written_by_the_driver_and_named_in_no_prompt(std):
    ledger = std.ledger()
    assert ledger and all(l.strip() == l and l and not l.startswith(("#", "|", "-", "*", "{")) for l in ledger), ledger
    for nn in ("01", "02", "03"):
        assert [l for l in ledger if nn in l and "merged" in l.lower()], (nn, ledger)
    assert not [c for c in std.calls() if "ledger" in c["prompt"].lower()]


def single_blocked(tmp_path):
    """Run 1 of a resume test: one ticket, red gate, so state.json holds a test-writer commit and a tip."""
    rig = Rig(tmp_path, {"01-export-rows.md": ticket("01", "Export rows")}, gate={"all": True})
    rig.run({**plan(), "01/implementer": [impl("01"), impl("01", text="def fn_01():\n    return 'again'\n",
                                                           commit="feat: 01 again")]})
    assert rig.code == 1, rig.output()
    return rig


def test_resume_returns_a_ticket_with_an_unverifiable_tip_to_implement_from_the_test_writer_commit(tmp_path):
    rig = single_blocked(tmp_path)
    state, fake = rig.state(), "1" * 40
    tw_sha = rig.sha("01", "test-writer")
    assert state["tickets"]["01"]["test_writer_sha"] == tw_sha
    state["tickets"]["01"].update(phase="review", tip=fake, green_gates=[])
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    before = rig.ledger()
    rig.gate_conf()
    rig.run({**plan(), "01/implementer": [impl("01"), impl("01", text="def fn_01():\n    return 'again'\n",
                                                           commit="feat: 01 again")]})
    assert rig.code == 0, rig.output()
    again = rig.calls("01", "implementer")[1]
    assert (again["branch"], again["head"]) == ("ticket/01", tw_sha) and len(rig.calls("01", "test-writer")) == 1
    assert [l for l in rig.ledger()[len(before):] if "01" in l and fake[:7] in l], rig.ledger()
    assert "feat: 01 again" in rig.pr_subjects()


def test_resume_keeps_a_settled_gate_and_runs_no_gate_again(tmp_path):
    rig = single_blocked(tmp_path)
    a1, state = rig.sha("01", "implementer"), rig.state()
    state["tickets"]["01"].update(phase="merge", tip=a1, green_gates=[a1])
    (rig.repo / ".agent/implement/state.json").write_text(json.dumps(state))
    gates_before = len(rig.gates())
    rig.run(plan())  # the gate still exits 1 on everything: any gate run would block the ticket
    assert rig.code == 0, rig.output()
    assert len(rig.gates()) == gates_before and git(rig.repo, "rev-parse", "feat/csv").strip() == a1


def test_summary_md_and_stdout_carry_the_measurement_table(std):
    summary = (std.repo / ".agent/implement/summary.md").read_text()
    rows = [l for l in summary.splitlines() if l.startswith("|")]
    assert rows and all(row in std.out for row in rows), summary
    lines = summary.splitlines()
    line = lambda *words: [l for l in lines if all(w in l.lower() for w in words)]
    assert [l for l in line("gate") if re.search(rf"\b{len(std.gates())}\b", l)], summary
    for role in ("test-writer", "implementer"):
        assert [l for l in line(role) if "anthropic/claude-opus-5@medium" in l and re.search(r"\b3\b", l)], summary
        assert [l for l in line(role) if re.search(r"\d+(\.\d+)?\s*s\b", l)], summary
    assert all(line(f"{nn}: 0") for nn in ("01", "02", "03")) and [l for l in line("stop") if "0" in l], summary
    assert re.search(r"1\.5", summary), "six agents at 0.25 each"
