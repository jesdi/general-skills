"""Ticket 04 acceptance tests: review, correctness pass, fix loop, fixture corrections, conflict agent.

All through `sh run.sh --runner <script>` on the fixture repository (the rig of test_walk). The fake runner also plays
reviewer, correctness, fixer, conflict and a second test-writer call; a role it has no script for reports nothing wrong.

Interface choices (ticket 04):
- the runner replaces the CLI, so a model entry is observed in the ledger: the line of a call names its role and entry.
- a fix round runs the role `fixer` (report `fixer-<round>.json`) on the implementer's model list: entry 0 in rounds
  1-2, entry 1 in rounds 3-4. `state.json` `round` counts fix rounds only. The review that follows round K writes
  `reviewer-K.json` (the first review `reviewer-0.json`); a correctness pass writes `correctness-K.json`.
- `mutations[].test` is `<locked file>::test_x`; a re-review prompt names each mutation proof to repeat as
  `<file>:<line>`, with the line number it has at the new tip.
- an installed skill without a sibling `review-diff/correctness.md` makes the driver exit 3 before any agent, and the
  message names `review-diff/correctness.md`.
- a second conflict on one merge: the PR branch moved again while the conflict agent worked.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_cli import SKILL, ticket, three, git, models_file
from test_walk import RUNNER, Rig, fix, impl, plan, tw

# The fake runner of test_walk plus two hooks: `git` runs before the step's files, `git_after` right after them.
HOOKED = RUNNER.replace(
    'for path, text in step.get("files", {}).items():',
    'for cmd in step.get("git", []):\n    git(*cmd)\nfor path, text in step.get("files", {}).items():', 1).replace(
    'sys.stdout.write(step.get("stdout", ""))',
    'for cmd in step.get("git_after", []):\n    git(*cmd)\nsys.stdout.write(step.get("stdout", ""))', 1)
assert HOOKED.count("git_after") == 1 and HOOKED.count('step.get("git", [])') == 1

ONE = {"01-export-rows.md": ticket("01", "Export rows")}
RISKY = {"02-quote-fields.md": ticket("02", "Quote fields", risk="persistence")}
OPUS, FABLE, SOL = "claude-opus-5@medium", "claude-fable-5-1@high", "openai/gpt-sol@medium"
LONG = "".join(f"v{i} = {i}\n" for i in range(1, 41))


class Review(Rig):
    """The rig of test_walk with the hooked runner and per-test model lists."""

    def __init__(self, tmp, files=ONE, models=None, **kw):
        super().__init__(tmp, files, **kw)
        (self.rig / "run-agent").write_text(HOOKED)
        self.models = models_file(tmp, **(models or {}))

    def led(self, *words):
        return [l for l in self.ledger() if all(w in l for w in words)]

    def tip(self):
        return self.walked()  # the tip the walk made: the end of run tidies it away (ticket 08)

    def folder(self, nn="01"):
        return self.repo / ".agent/implement" / nn


def finding(title, severity="important", kind="code", file="demo/export.py", line=99):
    return dict(severity=severity, file=file, line=line, title=title, evidence=title, kind=kind)


def red(nn, line):
    return f"FAILED tests/test_{nn}.py::test_x - AssertionError at demo/export.py:{line}"


def review(nn="01", findings=(), lines=(1,), **extra):
    """A reviewer step: findings plus a mutation proof per production line, its red line printed to the log."""
    proofs = [dict(test=f"tests/test_{nn}.py::test_x", file="demo/export.py", line=n, red=red(nn, n)) for n in lines]
    report = dict(status="DONE", commits=[], files=[], concerns=[], findings=list(findings), mutations=proofs)
    return {"stdout": "".join(p["red"] + "\n" for p in proofs), "report": report, **extra}


def correct(findings=(), **extra):
    report = dict(status="DONE", commits=[], files=[], concerns=[], findings=list(findings))
    return {"report": report, **extra}


def edit(text, **new):
    """`text` with line N replaced (keyword `l<N>`)."""
    lines = text.splitlines(keepends=True)
    for key, value in new.items():
        lines[int(key[1:]) - 1] = value + "\n"
    return "".join(lines)


# ---------------------------------------------------------------- locked-file check

def test_a_changed_locked_file_is_a_critical_finding_that_starts_a_fix_round(tmp_path):
    rig = Review(tmp_path)
    locked = tw("01")["files"]
    cheat = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "tests/test_01.py": "# edited\n"})
    steps = plan(**{"01/implementer": [cheat], "01/fixer": [fix("01", 1, files={**locked, "demo/export.py": "x = 1\n"})]})
    rig.run(steps)
    assert rig.code == 0, rig.output()
    (line,) = rig.led("locked file changed: tests/test_01.py")
    assert "critical" in line.lower()
    assert len(rig.calls("01", "fixer")) == 1 and rig.state()["tickets"]["01"]["round"] == 1
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == locked["tests/test_01.py"]


# ---------------------------------------------------------------- correctness pass

@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    """Ticket 02 (Risk: persistence), codex on PATH: reviewer and correctness each wait for the other to start."""
    rig = Review(tmp_path_factory.mktemp("pair"), RISKY, {"correctness": [SOL, OPUS], "implementer": [OPUS]})
    steps = plan(**{
        "02/reviewer": [review("02", [finding("R-MINOR", "minor")], touch=["r"], wait=["c"])],
        "02/correctness": [correct([finding("C-IMPORTANT")], touch=["c"], wait=["r"])],
        "02/fixer": [fix("02", 1)]})
    return rig.run(steps)


def test_reviewer_and_correctness_run_at_the_same_time_on_their_models(pair):
    assert pair.code == 0, pair.output()
    (r,), (c,) = pair.calls("02", "reviewer")[:1], pair.calls("02", "correctness")[:1]
    assert r["t0"] < c["t1"] and c["t0"] < r["t1"], "each started before the other ended"
    assert pair.led("reviewer", OPUS) and pair.led("correctness", "gpt-sol@medium")


def test_findings_of_both_reports_are_merged_with_their_severities(pair):
    assert len(pair.calls("02", "fixer")) == 1, "the important finding of the correctness pass starts a round"
    assert pair.led("R-MINOR", "minor") and pair.led("C-IMPORTANT", "important")
    assert pair.state()["tickets"]["02"]["phase"] == "merged"


# Ticket 08: the pass is never folded into the reviewer. A correctness entry with no CLI on PATH stops the run at the
# start: test_amendments.py test_a_correctness_entry_whose_cli_is_not_on_path_stops_the_run_before_any_agent.

def test_an_installed_skill_without_review_diff_fails_clearly_before_any_agent(tmp_path):
    rig = Review(tmp_path, RISKY)
    copy = tmp_path / "installed/skills/implement-spec"
    shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "test_*"))
    (rig.rig / "plan.json").write_text(json.dumps(plan()))
    args = ["--tickets", ".agent/tickets", "--gate", str(rig.rig / "gate"), "--spec", "specs/csv/spec.md",
            "--models", str(rig.models), "--runner", str(rig.rig / "run-agent")]
    proc = subprocess.run(["sh", str(copy / "run.sh"), *args], cwd=rig.repo, env=rig.env, text=True,
                          capture_output=True, timeout=120)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "review-diff/correctness.md" in proc.stdout + proc.stderr
    assert not rig.calls() and not (rig.repo / ".agent/implement/summary.md").exists()


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    """A ticket without a Risk line whose reviewer reports two minor findings."""
    rig = Review(tmp_path_factory.mktemp("plain"), ONE, {"correctness": [SOL, OPUS]})
    return rig.run(plan(**{"01/reviewer": [review("01", [finding("M-ONE", "minor"), finding("M-TWO", "minor")])]}))


def test_a_ticket_without_a_risk_line_runs_no_correctness_pass(plain):
    assert plain.code == 0, plain.output()
    assert len(plain.calls("01", "reviewer")) == 1 and not plain.calls("01", "correctness")
    assert not plain.led("folded")


def test_a_review_with_only_minor_findings_passes_and_writes_them_to_the_ledger(plain):
    assert not plain.calls("01", "fixer") and plain.state()["tickets"]["01"]["phase"] == "merged"
    assert plain.state()["tickets"]["01"]["round"] == 0
    assert plain.led("M-ONE", "minor") and plain.led("M-TWO", "minor")


# ---------------------------------------------------------------- fix loop

STUCK = finding("STUCK")


@pytest.fixture(scope="module")
def escalated(tmp_path_factory):
    """One important finding survives the re-reviews after rounds 1 and 2; round 3 is clean."""
    rig = Review(tmp_path_factory.mktemp("esc"), ONE, {"implementer": [OPUS, FABLE]})
    bad = review("01", [STUCK])
    return rig.run(plan(**{"01/reviewer": [bad, bad, bad, review("01")],
                           "01/fixer": [fix("01", n) for n in (1, 2, 3)]}))


def test_round_3_starts_a_fresh_implementer_on_the_next_model(escalated):
    assert escalated.code == 0, escalated.output()
    assert escalated.led("01 round 3: escalated to claude-fable-5-1@high")
    assert not escalated.led("round 1: escalated") and not escalated.led("round 2: escalated")
    assert len(escalated.led("fixer", OPUS)) == 2 and len(escalated.led("fixer", FABLE)) == 1
    assert escalated.state()["tickets"]["01"]["round"] == 3


def test_each_round_ends_with_one_gate_record_and_a_re_review_scoped_to_the_fix(escalated):
    tips = [escalated.sha("01", "implementer")] + [c["sha"] for c in escalated.calls("01", "fixer")]
    commits = [g["commit"] for g in escalated.gates()]
    assert len(tips) == 4 and all(commits.count(t) == 1 for t in tips), commits
    assert all((escalated.folder() / f"gate-{t[:7]}.log").exists() for t in tips)
    reviews = escalated.calls("01", "reviewer")
    assert len(reviews) == 4
    for k in (1, 2, 3):
        assert tips[k - 1][:7] in reviews[k]["prompt"] and tips[k][:7] in reviews[k]["prompt"], k


def gate_rule(prompt, gate):
    """The line of `prompt` that gives the gate command; it must say to run it before the report."""
    (line,) = [l for l in prompt.splitlines() if f"`{gate}`" in l]
    assert "IMPLEMENT_SPEC_BASE=feat/csv" in line and "Before you report DONE" in line, line
    return line


def test_the_implementer_and_each_fixer_get_the_gate_command_and_run_it_before_they_report(escalated):
    gate = str(escalated.rig / "gate")
    agents = escalated.calls("01", "implementer") + escalated.calls("01", "fixer")
    assert len(agents) == 4, "the implementer and the fixers of rounds 1, 2 and 3 (the next model)"
    for call in agents:
        gate_rule(call["prompt"], gate)


def test_reports_are_named_by_role_and_round(escalated):
    for name in ("reviewer-0", "reviewer-1", "reviewer-2", "reviewer-3", "fixer-1", "fixer-2", "fixer-3"):
        assert (escalated.folder() / f"{name}.json").exists(), name


@pytest.fixture(scope="module")
def capped(tmp_path_factory):
    """The three-ticket default; 01's reviewer keeps the same finding through round 4."""
    rig = Review(tmp_path_factory.mktemp("cap"), three(), {"implementer": [OPUS, FABLE]})
    return rig.run(plan(**{"01/reviewer": [review("01", [STUCK])] * 5,
                           "01/fixer": [fix("01", n) for n in (1, 2, 3, 4)]}))


def test_a_finding_open_after_round_4_blocks_the_ticket_and_the_run_goes_on(capped):
    assert capped.code == 1, capped.output()
    assert capped.led("01", "BLOCKED", "STUCK")
    assert len(capped.calls("01", "fixer")) == 4 and len(capped.calls("01", "reviewer")) == 5
    assert not capped.calls("02"), "02 is blocked by 01"
    assert capped.state()["tickets"]["01"]["phase"] == "blocked"
    assert capped.state()["tickets"]["03"]["phase"] == "merged" and "feat: 03 rows" in capped.pr_subjects()


# ---------------------------------------------------------------- fixture correction

def test_a_fixture_finding_runs_one_test_writer_correction_outside_the_rounds(tmp_path):
    rig = Review(tmp_path)
    new = "def test_x():\n    assert '01' == 'x'\n"
    correction = {**tw("01"), "files": {"tests/test_01.py": new}, "commit": "test: 01 fixture fix"}
    stale = finding("STALEMARK stale path", file="tests/test_01.py", kind="fixture", line=1)
    rig.run(plan(**{"01/test-writer": [tw("01"), correction], "01/reviewer": [review("01", [stale])]}))
    assert rig.code == 0, rig.output()
    assert len(rig.calls("01", "test-writer")) == 2 and not rig.calls("01", "fixer")
    assert rig.led("tests/test_01.py", "STALEMARK")
    assert rig.state()["tickets"]["01"]["round"] == 0 and rig.state()["tickets"]["01"]["phase"] == "merged"
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == new


# ---------------------------------------------------------------- mutation proofs

def scoped(tmp, lines, fixes, last_lines):
    """01's implementer writes 40 lines; the reviewer proves `lines`; each fix replaces the file."""
    rig = Review(tmp)
    bad = review("01", [STUCK], lines=lines)
    reviews = [bad] * len(fixes) + [review("01", lines=last_lines)]
    rig.run(plan(**{"01/implementer": [impl("01", text=LONG)], "01/reviewer": reviews,
                    "01/fixer": [fix("01", n, text=t) for n, t in enumerate(fixes, 1)]}))
    return rig


def test_a_re_review_asks_the_mutation_proof_only_for_the_lines_the_fix_changed(tmp_path):
    first = edit(LONG, l12="v12 = 'a'", l30="v30 = 'a'")
    rig = scoped(tmp_path, (12, 30), [first, edit(first, l30="v30 = 'b'")], (30,))
    assert rig.code == 0, rig.output()
    after1, after2 = (c["prompt"] for c in rig.calls("01", "reviewer")[1:])
    assert "demo/export.py:12" in after1 and "demo/export.py:30" in after1
    assert "demo/export.py:30" in after2 and "demo/export.py:12" not in after2


def test_a_mutation_proof_follows_its_production_line_when_a_fix_moves_it(tmp_path):
    lines = LONG.splitlines(keepends=True)
    moved = "".join(lines[:19] + ["i1 = 1\n", "i2 = 2\n"] + lines[19:29] + ["v30 = 'changed'\n"] + lines[30:])
    rig = scoped(tmp_path, (12, 30, 35), [moved], (32,))
    assert rig.code == 0, rig.output()
    prompt = rig.calls("01", "reviewer")[1]["prompt"]
    assert "demo/export.py:32" in prompt, "line 30 is line 32 after two inserted lines"
    for gone in (":30", ":12", ":35", ":37"):  # the old number, and the lines that did not change
        assert f"demo/export.py{gone}" not in prompt, gone


def test_a_first_review_without_a_mutation_for_a_locked_test_is_a_violation(tmp_path):
    rig = Review(tmp_path)
    rig.run(plan(**{"01/reviewer": [review("01", lines=()), review("01")]}))
    assert rig.code == 0, rig.output()
    first, second = rig.calls("01", "reviewer")
    appended = second["prompt"].replace(first["prompt"].strip(), "")
    assert first["prompt"].strip() in second["prompt"] and "tests/test_01.py" in appended
    assert "mutation" in appended.lower()


@pytest.mark.parametrize("second_matches", [True, False])
def test_a_red_line_missing_from_the_reviewer_log_reruns_once_then_blocks(tmp_path, second_matches):
    rig = Review(tmp_path)
    bad = review("01")
    bad["report"]["mutations"][0]["red"] = "NEVER PRINTED"
    rig.run(plan(**{"01/reviewer": [bad, review("01") if second_matches else bad]}))
    first, second = rig.calls("01", "reviewer")
    assert "red line not found in log" in second["prompt"] and "red line not found" not in first["prompt"]
    assert rig.code == (0 if second_matches else 1), rig.output()
    assert len(rig.calls("01", "reviewer")) == 2
    if not second_matches:
        assert rig.led("01", "BLOCKED") and "feat: 01 rows" not in rig.pr_subjects()


# ---------------------------------------------------------------- conflict agent

def cli(text):
    return f"def main():\n    return {text!r}\n"


def conflicted(tmp, moves_again=False):
    """01 and 03 both add demo/cli.py; 03 merges first, so 01's merge conflicts."""
    rig = Review(tmp, {k: v for k, v in three().items() if not k.startswith("02")})
    resolve = {"files": {"demo/cli.py": cli("resolved")}, "commit": "merge: resolve cli",
               "report": {"status": "DONE", "commits": ["$SHA"], "files": ["demo/cli.py"], "concerns": []}}
    if moves_again:  # while the agent works, another commit lands on the PR branch and changes the same file
        third = str(tmp / "third")
        resolve["git"] = [["worktree", "add", "--detach", third, "feat/csv"]]
        resolve["files"][third + "/demo/cli.py"] = cli("third")
        resolve["git_after"] = [["-C", third, "add", "-A"], ["-C", third, "commit", "-q", "-m", "feat: third"],
                                ["-C", third, "branch", "-f", "feat/csv", "HEAD"],
                                ["worktree", "remove", "--force", third]]
    one = impl("01", files={"demo/export.py": "def fn_01():\n    return '01'\n", "demo/cli.py": cli("one")})
    return rig.run(plan(**{
        "01/test-writer": [{**tw("01"), "touch": ["s1"]}],
        "03/implementer": [{**impl("03", "demo/cli.py", cli("three")), "wait": ["s1"]}],
        "01/implementer": [{**one, "wait_pr_moves": True}],
        "01/conflict": [resolve]}))


def test_a_conflicting_merge_runs_the_conflict_agent_once_then_the_gate_on_the_merge_commit(tmp_path):
    rig = conflicted(tmp_path)
    assert rig.code == 0, rig.output()
    (call,) = rig.calls("01", "conflict")
    assert call["cwd"] == rig.calls("01", "implementer")[0]["cwd"], "in the ticket's slot"
    assert rig.tip() in [g["commit"] for g in rig.gates()], "the merge commit is gated"
    assert git(rig.repo, "show", "feat/csv:demo/cli.py") == cli("resolved")
    assert rig.led("demo/cli.py", "onflict")


def test_a_second_conflict_on_the_same_merge_blocks_the_ticket(tmp_path):
    rig = conflicted(tmp_path, moves_again=True)
    assert rig.code == 1, rig.output()
    assert len(rig.calls("01", "conflict")) == 1
    assert rig.led("01", "BLOCKED") and rig.state()["tickets"]["01"]["phase"] == "blocked"
    assert rig.state()["tickets"]["03"]["phase"] == "merged"
