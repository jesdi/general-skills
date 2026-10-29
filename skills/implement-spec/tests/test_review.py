"""Ticket 04 acceptance tests: review, fix loop, fixture corrections, conflict agent.

All through `sh run.sh --runner <script>` on the fixture repository (the rig of test_walk). The fake runner also plays
reviewer, fixer, conflict and a second test-writer call; a role it has no script for reports nothing wrong.

Interface choices (ticket 04):
- the runner replaces the CLI, so a model entry is observed in the ledger: the line of a call names its role and entry.
- a fix round runs the role `fixer` (report `fixer-<round>.json`) on the implementer's model list: entry 0 in round
  1, entry 1 in round 2. `state.json` `round` counts fix rounds only. The review that follows round K writes
  `reviewer-K.json` (the first review `reviewer-0.json`).
- a second conflict on one merge: the PR branch moved again while the conflict agent worked.
"""

import pytest

from test_cli import ticket, three, git, models_file
from test_walk import RUNNER, Rig, fix, impl, plan, tw

# The fake runner of test_walk plus three hooks: `git` runs before the step's files, `git_after` right after them,
# and `only` names the paths the commit takes, as an agent that commits its own files only.
HOOKED = RUNNER.replace(
    'for path, text in step.get("files", {}).items():',
    'for cmd in step.get("git", []):\n    git(*cmd)\nfor path, text in step.get("files", {}).items():', 1).replace(
    'sys.stdout.write(step.get("stdout", ""))',
    'for cmd in step.get("git_after", []):\n    git(*cmd)\nsys.stdout.write(step.get("stdout", ""))', 1).replace(
    'git("commit", "-q", "-m", step["commit"])',
    'git("commit", "-q", "-m", step["commit"], *(["--", *step["only"]] if "only" in step else []))', 1)
assert HOOKED.count("git_after") == 1 and HOOKED.count('step.get("git", [])') == 1 and HOOKED.count('step["only"]') == 1

ONE = {"01-export-rows.md": ticket("01", "Export rows")}
RISKY = {"02-quote-fields.md": ticket("02", "Quote fields", risk="persistence")}
OPUS, FABLE, ASTRA = "claude-opus-5@medium", "claude-fable-5-1@high", "openai/gpt-6-astra@medium"


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


def review(nn="01", findings=(), **extra):
    """A reviewer step with `findings`."""
    report = dict(status="DONE", commits=[], files=[], concerns=[], findings=list(findings))
    return {"report": report, **extra}


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


# ---------------------------------------------------------------- the reviewer alone

@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    """A ticket without a Risk line whose reviewer reports two minor findings."""
    rig = Review(tmp_path_factory.mktemp("plain"), ONE)
    return rig.run(plan(**{"01/reviewer": [review("01", [finding("M-ONE", "minor"), finding("M-TWO", "minor")])]}))


def test_a_ticket_without_a_risk_line_is_reviewed_by_one_agent_told_it_names_no_risk(plain):
    assert plain.code == 0, plain.output()
    (call,) = plain.calls("01", "reviewer")
    assert "The ticket names no risk." in call["prompt"]
    assert [c["role"] for c in plain.calls("01")] == ["test-writer", "implementer", "reviewer"]


def test_a_review_with_only_minor_findings_passes_and_writes_them_to_the_ledger(plain):
    assert not plain.calls("01", "fixer") and plain.state()["tickets"]["01"]["phase"] == "merged"
    assert plain.state()["tickets"]["01"]["round"] == 0
    assert plain.led("M-ONE", "minor") and plain.led("M-TWO", "minor")


def test_unrelated_important_observation_waits_for_final_review(tmp_path):
    rig = Review(tmp_path)
    step = review("01")
    step["report"]["observations"] = [finding("DUPLICATED-HELPER")]

    rig.run(plan(**{"01/reviewer": [step]}))

    assert rig.code == 0, rig.output()
    assert not rig.calls("01", "fixer")
    assert rig.state()["tickets"]["01"]["phase"] == "merged"
    assert "DUPLICATED-HELPER" in rig.calls("final", "final-spec")[0]["prompt"]


def test_rereview_defers_newly_noticed_issue_outside_its_open_findings(tmp_path):
    rig = Review(tmp_path)
    steps = plan(**{"01/reviewer": [review("01", [finding("HEADER-MISSING")]),
                                   review("01", [finding("PREEXISTING-NAME")])],
                    "01/fixer": [fix("01", 1)]})

    rig.run(steps)

    assert rig.code == 0, rig.output()
    assert len(rig.calls("01", "fixer")) == 1
    assert "PREEXISTING-NAME" in rig.calls("final", "final-spec")[0]["prompt"]


def test_rereview_can_open_a_regression_introduced_by_the_fix(tmp_path):
    rig = Review(tmp_path)
    after_first = review("01")
    after_first["report"]["regressions"] = [finding("QUOTING-BROKEN")]
    steps = plan(**{"01/reviewer": [review("01", [finding("HEADER-MISSING")]), after_first, review("01")],
                    "01/fixer": [fix("01", 1), fix("01", 2)]})

    rig.run(steps)

    assert rig.code == 0, rig.output()
    assert len(rig.calls("01", "fixer")) == 2
    assert "findings-1.json" in rig.calls("01", "fixer")[1]["prompt"]
    assert "QUOTING-BROKEN" in (rig.folder() / "findings-1.json").read_text()


def test_unmet_criterion_blocks_after_two_fixes_while_independent_ticket_continues(tmp_path):
    rig = Review(tmp_path, three(), {"implementer": [OPUS, FABLE]})
    steps = plan(**{"01/reviewer": [review("01", [finding("HEADER-MISSING")])] * 3,
                    "01/fixer": [fix("01", 1), fix("01", 2)]})

    rig.run(steps)

    assert rig.code == 1, rig.output()
    assert rig.state()["tickets"]["01"]["phase"] == "blocked"
    assert rig.state()["tickets"]["03"]["phase"] == "merged"
    assert len(rig.calls("01", "fixer")) == 2
    assert rig.led("fixer", FABLE)
    assert not rig.state()["run"]["questions"]


def test_new_criterion_failure_caused_by_first_fix_blocks_at_cap(tmp_path):
    rig = Review(tmp_path, models={"implementer": [OPUS, FABLE]})
    after_first = review("01")
    after_first["report"]["regressions"] = [{**finding("FOOTER-MISSING"), "basis": "criterion"}]
    steps = plan(**{"01/reviewer": [review("01", [finding("HEADER-MISSING")]), after_first,
                                     review("01", [finding("FOOTER-MISSING")])],
                    "01/fixer": [fix("01", 1), fix("01", 2)]})

    rig.run(steps)

    assert rig.code == 1, rig.output()
    assert rig.state()["tickets"]["01"]["phase"] == "blocked"
    assert not rig.state()["run"]["final"]["must_fix"]


def test_red_gate_after_second_fix_blocks_the_ticket(tmp_path):
    rig = Review(tmp_path, models={"implementer": [OPUS, FABLE]}, gate={"subjects": ["fix: 01 round 2"]})
    steps = plan(**{"01/reviewer": [review("01", [finding("HEADER-MISSING")])] * 2,
                    "01/fixer": [fix("01", 1), fix("01", 2)]})

    rig.run(steps)

    assert rig.code == 1, rig.output()
    assert rig.state()["tickets"]["01"]["phase"] == "blocked"
    assert len(rig.calls("01", "fixer")) == 2
    assert any(record["exit"] == 1 for record in rig.gates())


# ---------------------------------------------------------------- fix loop

STUCK = finding("STUCK")


@pytest.fixture(scope="module")
def escalated(tmp_path_factory):
    """One important finding survives round 1; round 2 on the next model closes it."""
    rig = Review(tmp_path_factory.mktemp("esc"), ONE, {"implementer": [OPUS, FABLE]})
    bad = review("01", [STUCK])
    return rig.run(plan(**{"01/reviewer": [bad, bad, review("01")],
                           "01/fixer": [fix("01", n) for n in (1, 2)]}))


def test_round_2_starts_a_fresh_implementer_on_the_next_model(escalated):
    assert escalated.code == 0, escalated.output()
    assert escalated.led("01 round 2: escalated to claude-fable-5-1@high")
    assert not escalated.led("round 1: escalated")
    assert len(escalated.led("fixer", OPUS)) == 1 and len(escalated.led("fixer", FABLE)) == 1
    assert escalated.state()["tickets"]["01"]["round"] == 2


def test_each_round_ends_with_one_gate_record_and_a_re_review_scoped_to_the_fix(escalated):
    tips = [escalated.sha("01", "implementer")] + [c["sha"] for c in escalated.calls("01", "fixer")]
    commits = [g["commit"] for g in escalated.gates("fast")]
    assert len(tips) == 3 and all(commits.count(t) == 1 for t in tips), commits
    assert all((escalated.folder() / f"gate-fast-{t[:7]}.log").exists() for t in tips)
    reviews = escalated.calls("01", "reviewer")
    assert len(reviews) == 3
    for k in (1, 2):
        assert tips[k - 1][:7] in reviews[k]["prompt"] and tips[k][:7] in reviews[k]["prompt"], k


def gate_rule(prompt, gate, locked):
    """The line of `prompt` that gives the fast gate command; it must say to run it, with the locked files, before
    the report."""
    (line,) = [l for l in prompt.splitlines() if f"`{gate}`" in l]
    assert "IMPLEMENT_SPEC_BASE=feat/csv" in line and "Before you report DONE" in line, line
    assert f"IMPLEMENT_SPEC_LOCKED='{locked}'" in line, line
    return line


def no_full_gate(rig):
    """No prompt of any agent of the run names the full gate command: only that command sets TIER=full."""
    assert rig.calls() and not [call["role"] for call in rig.calls() if "TIER=full" in call["prompt"]]


def test_the_implementer_and_each_fixer_get_the_fast_gate_command_and_run_it_before_they_report(escalated):
    gate = f"TIER=fast {escalated.rig / 'gate'}"
    agents = escalated.calls("01", "implementer") + escalated.calls("01", "fixer")
    assert len(agents) == 3, "the implementer and the fixers of rounds 1 and 2 (the next model)"
    for call in agents:
        gate_rule(call["prompt"], gate, "tests/test_01.py")
    no_full_gate(escalated)


def test_reports_are_named_by_role_and_round(escalated):
    for name in ("reviewer-0", "reviewer-1", "reviewer-2", "fixer-1", "fixer-2"):
        assert (escalated.folder() / f"{name}.json").exists(), name


@pytest.fixture(scope="module")
def capped(tmp_path_factory):
    """The three-ticket default; 01's reviewer keeps the same criterion finding through round 2."""
    rig = Review(tmp_path_factory.mktemp("cap"), three(), {"implementer": [OPUS, FABLE]})
    return rig.run(plan(**{"01/reviewer": [review("01", [STUCK])] * 3,
                           "01/fixer": [fix("01", n) for n in (1, 2)]}))


def test_a_criterion_open_after_round_2_blocks_the_ticket_and_the_run_goes_on(capped):
    assert capped.code == 1, capped.output()
    assert capped.led("01", "BLOCKED", "STUCK")
    assert len(capped.calls("01", "fixer")) == 2 and len(capped.calls("01", "reviewer")) == 3
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
