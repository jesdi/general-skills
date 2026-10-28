"""Ticket 06 acceptance tests: rebase, tidy, final review, fix batch, push.

All through `sh run.sh --runner <script> [--push] [--unattended]` on the fixture repository of test_review after the
tickets merged. The fixture adds a local bare `origin` (HEAD `main`, holding the PR branch's start commit, so the base
is the remote's default branch, found with `git ls-remote --symref origin HEAD`) and a fake `gh` on the restricted
PATH. No test reaches a real remote or a real `gh`.

Interface choices (ticket 06):
- end-of-run starts only when every ticket is merged and every slot is idle. A repository with no `origin` skips only
  the rebase and the push (ticket 08): test_amendments.py holds those tests.
- its agents report into `.agent/implement/final/`, so the fake runner keys them `final/final-reviewer`,
  `final/fixer` and `final/conflict`; gate records there are `gate-<first 7 hex>.log`.
- recovery point: before the rebase the driver sets `refs/implement-spec/feat%2Fcsv/recovery` to the PR tip and writes the ledger
  sentence "Recovery point: ... <sha>". An ended run leaves the ref in place.
- a tidied commit has the subject `feat(NN): <ticket title>` and a body line `Ticket: <ticket file name>`.
- the final fixer's `commits` hold `{"sha", "ticket"}` objects, and the driver renames each fixer commit to
  `fix(NN): <its subject>` before the gate and the scoped review run (no rewrite after a review).
- triage after the second final-review round: question `final-finding-<n>`, options `fix` and `accept`.
- local stop: stdout lines `Branch tip: <sha>`, `Reviewed tip: <sha>` and the rerun command, which has `--push`.
- the PR body lists, per ticket, a line with the ticket number and its commit's first 7 hex, and every ruling line.
- stage.json at a clean unattended end is `{"stage": "implement", "status": "done"}`.
- a second rebase conflict: the conflict agent finishes the commit and runs `git rebase --continue`; the next
  commit of the rebase that touches the same file conflicts again.
"""
import json
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_cli import SKILL, git, ticket
from test_review import FABLE, HOOKED, ONE, OPUS, Review, finding, gate_rule, review
from test_stops import UNATTENDED, decisions, stage
from test_walk import fix, impl, plan, tw

SCOPED = HOOKED.replace('("reviewer", "correctness")', '("reviewer", "correctness", "final-reviewer")')
RUNNER = SCOPED.replace(
    'for cmd in step.get("git", []):',
    '''if step.get("wait_pr_has"):
    wait(lambda: step["wait_pr_has"] in git("log", "--format=%s", "feat/csv"))
if step.get("hold"):
    before = git("rev-parse", "feat/csv")
    time.sleep(step["hold"])
    rec["held"] = dict(before=before, after=git("rev-parse", "feat/csv"),
                       recovery=git("rev-parse", "--verify", "-q", "refs/implement-spec/feat%2Fcsv/recovery"))
for cmd in step.get("git", []):''', 1).replace(
    'sha = ""\nif "commit" in step:',
    '''sha = ""
extras = []
for c in step.get("commits", []):
    for p, t in c["files"].items():
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(t)
    git("add", "-A")
    git("commit", "-q", "-m", c["message"])
    extras.append(git("rev-parse", "HEAD"))
def render(rep):
    text = json.dumps(rep).replace("$SHA", sha).replace("$HEAD", rec["head"])
    for i, c in enumerate(extras):
        text = text.replace(f"$C{i}", c)
    return text
if "commit" in step:''', 1).replace(
    'if step.get("report") is not None:',
    'for cmd in step.get("git_end", []):\n    git(*cmd)\n    sha = git("rev-parse", "HEAD")\nif step.get("report") is not None:', 1,
).replace('json.dumps(step["report"]).replace("$SHA", sha)', 'render(step["report"])', 1)
assert "render(step" in RUNNER and RUNNER.count("git_end") == 1 and RUNNER.count("wait_pr_has") == 2
assert '"final-reviewer")' in RUNNER and RUNNER.count("final-reviewer") == 1

GH = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
argv, body = sys.argv[1:], None
for i, a in enumerate(argv[:-1]):
    if a in ("--body", "-b"):
        body = argv[i + 1]
    if a in ("--body-file", "-F"):
        body = sys.stdin.read() if argv[i + 1] == "-" else Path(argv[i + 1]).read_text()
with (Path(__file__).resolve().parent.parent / "gh.jsonl").open("a") as log:
    log.write(json.dumps(dict(argv=argv, body=body)) + "\\n")
print("https://github.example/pull/1")
"""
HOOK = '#!/bin/sh\nwhile read old new ref; do echo "$ref $new" >> "%s"; done\n'

TITLES = {"01": "Export rows", "02": "Quote fields", "03": "CLI flag"}
THREE = {
    "01-export-rows.md": ticket("01", "Export rows"),
    "02-quote-fields.md": ticket("02", "Quote fields", "01, 03"),
    "03-cli-flag.md": ticket("03", "CLI flag", seam="`demo export` CLI", touches="`demo/cli.py: main`"),
}
FILES = {nn: f"{nn}-{TITLES[nn].lower().replace(' ', '-')}.md" for nn in TITLES}
FINAL_MODELS = {"final-reviewer": [FABLE, OPUS]}


def ref(root, name):
    out = subprocess.run(["git", "rev-parse", "--verify", "-q", name], cwd=root, capture_output=True, text=True)
    return out.stdout.strip()


class Final(Review):
    """The rig of test_review plus a bare origin, a fake gh and the hooks of the final runner."""

    def __init__(self, tmp, files=ONE, models=None, remote=True, **kw):
        super().__init__(tmp, files, models, **kw)
        (self.rig / "run-agent").write_text(RUNNER)
        self.env["GIT_EDITOR"] = "true"
        gh = tmp / "bin/gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.origin, self.pushed, self.gh_log = tmp / "origin.git", tmp / "pushes.log", tmp / "gh.jsonl"
        if remote:
            git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
            git(self.repo, "remote", "add", "origin", str(self.origin))
            git(self.repo, "push", "-q", "origin", "feat/csv:refs/heads/main")
            git(self.repo, "fetch", "-q", "origin")
            git(self.repo, "remote", "set-head", "origin", "main")
            (self.origin / "hooks/pre-receive").write_text(HOOK % self.pushed)
            (self.origin / "hooks/pre-receive").chmod(0o755)
        self.start = ref(self.origin, "refs/heads/main") if remote else ""

    def move_base(self, files):
        clone = self.tmp / "base-clone"
        if not clone.exists():
            git(self.tmp, "clone", "-q", str(self.origin), str(clone))
        for path, text in files.items():
            (clone / path).parent.mkdir(parents=True, exist_ok=True)
            (clone / path).write_text(text)
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "base moves")
        git(clone, "push", "-q", "origin", "main")

    def pushes(self):
        lines = self.pushed.read_text().splitlines() if self.pushed.exists() else []
        return [l for l in lines if l.startswith("refs/heads/feat/csv ")]

    def prs(self):
        rows = [json.loads(l) for l in self.gh_log.read_text().splitlines()] if self.gh_log.exists() else []
        return [r for r in rows if r["argv"][:2] == ["pr", "create"]]

    def branch(self):
        return ref(self.repo, "feat/csv")

    def recovery(self):
        return ref(self.repo, "refs/implement-spec/feat%2Fcsv/recovery")

    def commits(self, base, tip="feat/csv"):
        raw = git(self.repo, "log", "--reverse", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", f"{base}..{tip}")
        rows = [r.strip("\n").split("\x1f") for r in raw.split("\x1e") if r.strip()]
        return [dict(sha=h, subject=s, body=b) for h, s, b in rows]

    def final_calls(self, role):
        return self.calls("final", role)

    def snap(self):
        return SimpleNamespace(code=self.code, out=self.out, calls=len(self.calls()), gates=len(self.gates()),
                               tip=self.branch(), bare=ref(self.origin, "refs/heads/feat/csv"), pushes=len(self.pushes()), prs=len(self.prs()),
                               ledger=len(self.ledger()))


def final(*findings, **extra):
    return {"report": dict(status="DONE", commits=[], files=[], concerns=[], findings=list(findings)), **extra}


def fixer(text, *messages, ticket_of="02", **extra):
    """A fixer step: one commit per message, each attributed to `ticket_of` in the report."""
    commits = [{"message": m, "files": {"demo/export.py": text + "".join(f"# {x}\n" for x in messages[:i + 1])}}
               for i, m in enumerate(messages)]
    report = dict(status="DONE", commits=[{"sha": f"$C{i}", "ticket": ticket_of} for i in range(len(messages))],
                  files=["demo/export.py"], concerns=[], green=["1 passed"])
    return {"commits": commits, "report": report, **extra}


def fn(nn):
    return f"def fn_{nn}():\n    return {nn!r}\n"


def stop_lines(out):
    pattern = r"(?im)^%s[^0-9a-f\n]*([0-9a-f]{7,40})"
    return (re.search(pattern % "branch tip", out), re.search(pattern % "reviewed tip", out),
            re.search(r"(?im)^.*--push.*$", out))


# ---------------------------------------------------------------- the story: stop, local stop, rerun, push

NEW_03 = "def test_x():\n    assert '03' == '03'\n"
CONTRA_03 = "locked test contradicts spec: tests/test_03.py::test_x"


@pytest.fixture(scope="module")
def story(tmp_path_factory):
    """01 merges, 03 stops on a contradicted locked test and is overruled, 02 is held in review, then end-of-run."""
    rig = Final(tmp_path_factory.mktemp("story"), THREE, FINAL_MODELS)
    steps = plan(**{
        "03/test-writer": [tw("03"), {**tw("03"), "files": {"tests/test_03.py": NEW_03}, "commit": "test: 03 overruled"}],
        "03/implementer": [{"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": [CONTRA_03],
                                       "green": []}}, impl("03", "demo/cli.py")],
        "02/reviewer": [review("02", [finding("M-MINOR", "minor")], hold=0.8)]})
    rig.run(steps)
    stopped = rig.snap()
    rig.run(steps, "--answer", "03-locked-test-1=overrule-test")
    local = rig.snap()
    rig.run(steps)
    again = rig.snap()
    rig.run(steps, "--push")
    return SimpleNamespace(rig=rig, stopped=stopped, local=local, again=again, pushed=rig.snap())


def test_end_of_run_work_does_not_start_while_02_is_in_review(story):
    rig = story.rig
    assert story.local.code == 0, rig.output()
    (held,) = [c["held"] for c in rig.calls("02", "reviewer")]
    assert held["before"] == held["after"] and not held["recovery"], "nothing rewrote the branch while 02 ran"
    finals = rig.final_calls("final-reviewer")
    assert finals and finals[0]["t0"] >= rig.calls("02", "reviewer")[0]["t1"], "no final review before 02 left review"


def test_seven_commits_in_merge_order_are_tidied_into_three_ticket_commits_with_the_same_tree(story):
    rig = story.rig
    assert story.local.code == 0, rig.output()
    assert rig.recovery(), "no recovery point"
    assert len(rig.commits(rig.start, rig.recovery())) == 7
    rows = rig.commits(rig.start)
    assert [r["subject"] for r in rows] == [f"feat({nn}): {TITLES[nn]}" for nn in ("01", "03", "02")]
    assert all(f"Ticket: {FILES[nn]}" in r["body"] for nn, r in zip(("01", "03", "02"), rows))
    assert ref(rig.repo, "feat/csv^{tree}") == ref(rig.repo, "refs/implement-spec/feat%2Fcsv/recovery^{tree}")


def test_one_gate_record_exists_for_the_tidied_tip_and_the_final_reviewer_is_pointed_at_it(story):
    rig, tip = story.rig, story.local.tip
    records = sorted((rig.repo / ".agent/implement/final").glob("gate-*.log"))
    assert [p.name for p in records] == [f"gate-{tip[:7]}.log"], records
    assert "GATE-OUTPUT-MARK" in records[0].read_text() and tip[:7] in records[0].read_text()
    (gate,) = [g for g in rig.gates() if g["commit"] == tip]
    first = rig.final_calls("final-reviewer")[0]
    assert gate["t0"] < first["t0"], "the gate runs before the final review"
    assert f"final/gate-{tip[:7]}.log" in first["prompt"]


def test_the_final_reviewer_runs_on_the_first_model_entry_pointed_at_review_diff_minors_and_rulings(story):
    rig = story.rig
    assert rig.led("final-reviewer", FABLE) and not rig.led("final-reviewer", OPUS)
    prompt = rig.final_calls("final-reviewer")[0]["prompt"]
    assert "review-diff" in prompt and "M-MINOR" in prompt and "locked test overruled" in prompt
    assert '"severity"' in prompt and '"findings"' in prompt, "the reviewer schema"


def test_the_local_run_pushes_nothing_and_prints_the_tip_the_reviewed_tip_and_the_push_command(story):
    now = story.local
    assert now.code == 0 and now.pushes == 0 and now.prs == 0
    assert not now.bare
    tip, reviewed, push = stop_lines(now.out)
    assert tip and reviewed and push, now.out
    assert now.tip.startswith(tip.group(1)) and now.tip.startswith(reviewed.group(1))


def test_a_rerun_after_the_tidy_runs_no_agent_no_gate_and_sends_no_ticket_back(story):
    before, now = story.local, story.again
    assert now.code == 0, story.rig.output()
    assert (now.calls, now.gates, now.tip, now.pushes) == (before.calls, before.gates, before.tip, 0)
    assert len(story.rig.calls("01", "implementer")) == 1 and not story.rig.led("returned to implement")
    assert {e["phase"] for e in story.rig.state()["tickets"].values()} == {"merged"}
    assert all(stop_lines(now.out))


def test_the_push_rerun_pushes_once_opens_the_pr_with_map_and_rulings_and_runs_only_the_pr_writer(story):
    rig, before, now = story.rig, story.again, story.pushed
    assert now.code == 0, rig.output()
    assert (now.calls, now.gates, now.tip) == (before.calls + 1, before.gates, before.tip), "only the pr-writer runs"
    assert rig.pushes() == [f"refs/heads/feat/csv {now.tip}"] and ref(rig.origin, "refs/heads/feat/csv") == now.tip
    (pr,) = rig.prs()
    assert "--draft" not in pr["argv"]
    lines = pr["body"].splitlines()
    for nn, row in zip(("01", "03", "02"), rig.commits(rig.start)):
        assert [l for l in lines if nn in l and row["sha"][:7] in l], (nn, pr["body"])
    assert "locked test overruled" in pr["body"]


# ---------------------------------------------------------------- the fix batch

@pytest.fixture(scope="module")
def fixed(tmp_path_factory):
    rig = Final(tmp_path_factory.mktemp("fixed"), THREE, FINAL_MODELS)
    rig.run(plan(**{"final/final-reviewer": [final(finding("Q-ONE"), finding("Q-TWO")), final()],
                    "final/fixer": [fixer(fn("02"), "quote embedded commas", "escape quotes")]}))
    return rig


def test_two_findings_make_one_fixer_batch_of_fix_commits_on_top_of_the_unchanged_tidied_commits(fixed):
    assert fixed.code == 0, fixed.output()
    (batch,) = fixed.final_calls("fixer")
    assert "Q-ONE" in batch["prompt"] and "Q-TWO" in batch["prompt"]
    reviewed = fixed.final_calls("final-reviewer")[0]["head"]
    assert [r["subject"] for r in fixed.commits(reviewed)] == ["fix(02): quote embedded commas", "fix(02): escape quotes"]
    assert len(fixed.commits(fixed.start, reviewed)) == 3
    assert ref(fixed.repo, "feat/csv~2") == reviewed


def test_green_ticket_carries_cap_finding_into_required_final_fix(tmp_path):
    rig = Final(tmp_path, THREE, {"implementer": [OPUS, FABLE]})
    recurring = review("02", [{**finding("MERGED-QUOTING-REGRESSION"), "basis": "regression"}])
    scoped = final()
    scoped["report"]["resolved_must_fix"] = ["MERGED-QUOTING-REGRESSION (demo/export.py:99)"]
    batch = fixer(fn("02"), "restore quoting", ticket_of="02")
    batch["report"]["addressed_must_fix"] = ["MERGED-QUOTING-REGRESSION (demo/export.py:99)"]
    steps = plan(**{"02/reviewer": [recurring] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-reviewer": [final(), scoped], "final/fixer": [batch]})

    rig.run(steps)

    assert rig.code == 0, rig.output()
    assert rig.state()["tickets"]["02"]["phase"] == "merged"
    assert not rig.state()["run"]["questions"]
    assert "MERGED-QUOTING-REGRESSION" in rig.final_calls("fixer")[0]["prompt"]
    assert len(rig.final_calls("fixer")) == 1


def test_unresolved_must_fix_cannot_be_accepted_or_published(tmp_path):
    rig = Final(tmp_path, THREE, {"implementer": [OPUS, FABLE]})
    recurring = review("02", [{**finding("MERGED-QUOTING-REGRESSION"), "basis": "regression"}])
    batch = fixer(fn("02"), "attempt quoting fix", ticket_of="02")
    batch["report"]["addressed_must_fix"] = ["MERGED-QUOTING-REGRESSION (demo/export.py:99)"]
    steps = plan(**{"02/reviewer": [recurring] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-reviewer": [final(), final(finding("MERGED-QUOTING-REGRESSION"))],
                    "final/fixer": [batch]})

    rig.run(steps)

    assert rig.code == 1, rig.output()
    assert not rig.state()["run"]["questions"]
    assert [item["title"] for item in rig.state()["run"]["final"]["open"]] == ["MERGED-QUOTING-REGRESSION"]
    assert not rig.pushes() and not rig.prs()
    rig.run(steps)
    assert rig.code == 1 and len(rig.final_calls("fixer")) == 1
    assert not rig.pushes() and not rig.prs()


def test_final_fix_batch_rejects_an_unaddressed_must_fix(tmp_path):
    rig = Final(tmp_path, THREE, {"implementer": [OPUS, FABLE]})
    recurring = review("02", [{**finding("MERGED-QUOTING-REGRESSION"), "basis": "regression"}])
    steps = plan(**{"02/reviewer": [recurring] * 3, "02/fixer": [fix("02", 1), fix("02", 2)],
                    "final/final-reviewer": [final()],
                    "final/fixer": [fixer(fn("02"), "attempt quoting fix", ticket_of="02")]})

    rig.run(steps)

    assert rig.code == 1, rig.output()
    assert not rig.pushes() and not rig.prs()
    assert any("addressed_must_fix: missing MERGED-QUOTING-REGRESSION (demo/export.py:99)" in line for line in rig.ledger())


def test_the_fixer_of_the_final_review_gets_the_gate_command_and_runs_it_before_it_reports(fixed):
    (batch,) = fixed.final_calls("fixer")
    gate_rule(batch["prompt"], str(fixed.rig / "gate"))


def test_a_scoped_review_covers_the_range_after_the_reviewed_tip_and_one_gate_runs_on_the_new_tip(fixed):
    reviewed, tip = fixed.final_calls("final-reviewer")[0]["head"], fixed.branch()
    first, scoped = fixed.final_calls("final-reviewer")
    assert reviewed[:7] in scoped["prompt"] and tip[:7] in scoped["prompt"]
    assert len([g for g in fixed.gates() if g["commit"] == tip]) == 1
    records = sorted(p.name for p in (fixed.repo / ".agent/implement/final").glob("gate-*.log"))
    assert records == sorted({f"gate-{reviewed[:7]}.log", f"gate-{tip[:7]}.log"})
    assert len(fixed.final_calls("final-reviewer")) == 2 and all(stop_lines(fixed.out))


# ---------------------------------------------------------------- schema violations

@pytest.fixture(scope="module")
def violation(tmp_path_factory):
    """One ticket. The final reviewer's first report lacks a finding's kind; the fixer's first lacks a ticket."""
    rig = Final(tmp_path_factory.mktemp("violation"), ONE)
    bad = dict(severity="important", file="demo/export.py", line=1, title="NOKIND", evidence="NOKIND")
    attempt = fixer(fn("01"), "tidy the header", ticket_of="01")
    del attempt["report"]["commits"][0]["ticket"]
    retry = {"report": dict(attempt["report"], commits=[{"sha": "$HEAD", "ticket": "01"}])}
    rig.run(plan(**{"final/final-reviewer": [final(bad), final(finding("HEADER")), final()],
                    "final/fixer": [attempt, retry]}))
    return rig


def test_a_final_review_report_follows_the_reviewer_schema_or_is_rerun_once(violation):
    assert violation.code == 0, violation.output()
    calls = violation.final_calls("final-reviewer")
    assert len(calls) == 3 and "HEADER" in violation.final_calls("fixer")[0]["prompt"]


def test_a_fixer_commit_without_its_ticket_is_a_schema_violation(violation):
    assert len(violation.final_calls("fixer")) == 2
    reviewed = violation.final_calls("final-reviewer")[0]["head"]
    assert [r["subject"] for r in violation.commits(reviewed)] == ["fix(01): tidy the header"]


# ---------------------------------------------------------------- the third round

@pytest.fixture(scope="module")
def third(tmp_path_factory):
    rig = Final(tmp_path_factory.mktemp("third"), ONE)
    steps = plan(**{"final/final-reviewer": [final(finding("STILL-OPEN")), final(finding("STILL-OPEN"))],
                    "final/fixer": [fixer(fn("01"), "first try", ticket_of="01")]})
    rig.run(steps)
    parked = rig.snap()
    questions = decisions(rig)["questions"] if parked.code == 2 else []
    rig.run(steps, "--answer", "final-finding-1=accept")
    return SimpleNamespace(rig=rig, parked=parked, questions=questions, answered=rig.snap())


def test_findings_open_after_the_second_round_become_fix_or_accept_questions_and_no_third_round_starts(third):
    rig = third.rig
    assert third.parked.code == 2, third.parked.out
    (q,) = third.questions
    assert q["id"] == "final-finding-1" and {o["id"] for o in q["options"]} == {"fix", "accept"}
    assert "STILL-OPEN" in q["title"] + q["why"] and "final-finding-1" in third.parked.out
    assert len(rig.final_calls("final-reviewer")) == 2 and len(rig.final_calls("fixer")) == 1
    assert third.parked.pushes == 0


def test_accepting_the_finding_ends_the_run_with_a_ruling_and_starts_no_more_agents(third):
    rig = third.rig
    assert third.answered.code == 0, rig.output()
    assert rig.led("Ruling", "STILL-OPEN", "accept")
    assert (third.answered.calls, third.answered.pushes) == (third.parked.calls, 0) and all(stop_lines(third.answered.out))


# ---------------------------------------------------------------- the rebase

CLI_RESOLVED = "def main():\n    return 'resolved'\n"
RESOLVE = {"commit": "resolve conflict", "git_end": [["rebase", "--continue"]],
           "report": {"status": "DONE", "commits": ["$SHA"], "files": ["demo/cli.py"], "concerns": []}}


@pytest.fixture(scope="module")
def conflicted(tmp_path_factory):
    """The base moves: docs.md is new and demo/cli.py is added there too, so only 03's commit conflicts."""
    rig = Final(tmp_path_factory.mktemp("conflict"), THREE)
    rig.move_base({"docs.md": "base docs\n", "demo/cli.py": "def main():\n    return 'base'\n"})
    rig.run(plan(**{"final/conflict": [{**RESOLVE, "files": {"demo/cli.py": CLI_RESOLVED}}]}))
    return rig


def test_a_conflict_with_the_fetched_base_runs_the_conflict_agent_once_then_the_gate(conflicted):
    rig = conflicted
    assert rig.code == 0, rig.output()
    (call,) = rig.final_calls("conflict")
    (gate,) = [g for g in rig.gates() if g["commit"] == call["sha"]]
    assert gate["t0"] >= call["t1"], "the gate follows the resolution"
    base = ref(rig.origin, "refs/heads/main")
    git(rig.repo, "merge-base", "--is-ancestor", base, "feat/csv")
    assert git(rig.repo, "show", "feat/csv:docs.md") == "base docs\n"
    assert git(rig.repo, "show", "feat/csv:demo/cli.py") == CLI_RESOLVED
    assert len(rig.commits(base)) == 3 and ref(rig.repo, "feat/csv^{tree}") == ref(rig.repo, f"{call['sha']}^{{tree}}")


@pytest.fixture(scope="module")
def conflicted_twice(tmp_path_factory):
    """The base rewrites demo/export.py: 01's commit conflicts, and 02's commit conflicts again."""
    rig = Final(tmp_path_factory.mktemp("twice"), THREE)
    rig.move_base({"demo/export.py": "def export_csv():\n    return 'base'\n"})
    rig.run(plan(**{"final/conflict": [{**RESOLVE, "files": {"demo/export.py": fn("01").replace("'01'", "'resolved'")},
                                        "report": {**RESOLVE["report"], "files": ["demo/export.py"]}}]}))
    return rig


def test_a_second_conflict_ends_the_run_with_exit_1_and_the_branch_unrewritten_at_its_recovery_point(conflicted_twice):
    rig = conflicted_twice
    assert rig.code == 1, rig.output()
    assert len(rig.final_calls("conflict")) == 1 and not rig.final_calls("final-reviewer")
    recovery = rig.recovery()
    assert recovery and rig.branch() == recovery and len(rig.commits(rig.start, recovery)) == 6
    assert rig.led("ecovery", recovery[:7])
    assert git(rig.repo, "symbolic-ref", "--short", "HEAD").strip() == "feat/csv" and git(rig.repo, "status", "--short") == ""
    for name in ("rebase-merge", "rebase-apply"):
        assert not (rig.repo / git(rig.repo, "rev-parse", "--git-path", name).strip()).exists()
    assert rig.pushes() == [] and not rig.prs()


# ---------------------------------------------------------------- unattended

@pytest.fixture(scope="module")
def unattended(tmp_path_factory):
    rig = Final(tmp_path_factory.mktemp("unattended"), ONE)
    return rig.run(plan(), *UNATTENDED)


def test_unattended_with_everything_validated_pushes_once_opens_the_pr_and_writes_stage_done(unattended):
    rig = unattended
    assert rig.code == 0, rig.output()
    tip = rig.branch()
    assert rig.pushes() == [f"refs/heads/feat/csv {tip}"] and ref(rig.origin, "refs/heads/feat/csv") == tip
    (pr,) = rig.prs()
    assert [l for l in pr["body"].splitlines() if "01" in l and tip[:7] in l], pr["body"]
    assert stage(rig) == {"stage": "implement", "status": "done"}


def test_unattended_with_a_blocked_ticket_pushes_nothing_and_exits_1(tmp_path):
    rig = Final(tmp_path, THREE, gate={"red": ["ticket/feat%2Fcsv/01"]})
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1, rig.output()
    assert rig.pushes() == [] and not rig.prs() and not ref(rig.origin, "refs/heads/feat/csv")
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["01"]}


# ---------------------------------------------------------------- the merge-risk block of the PR body (ticket 15)

PR_RISK = SKILL.parent / "pr-risk"
BLOCK = ("door: two-way\nblast_radius: isolated\ntouches: [%s]\nrollback: revert\nverdict: auto\n"
         "reasons:\n  - \"Only prose changes\"\n")


def risk_body(touches="docs"):
    return ("## Summary\n\nCSV export.\n\n## Merge Danger\n\n```merge-risk v1\n" + BLOCK % touches + "```\n")


def writer(body):
    return {"report": dict(status="DONE", commits=[], files=[], concerns=[], body=body)}


def risk_valid(tmp_path, body):
    f = tmp_path / "check-body.md"
    f.write_text(body)
    return subprocess.run([sys.executable, str(PR_RISK / "merge_risk.py"), "validate", str(f)],
                          capture_output=True, text=True).returncode == 0


def pushed_run(tmp_path, monkeypatch, steps, risk_in, gh_fails=False):
    """`implement-spec` as installed (a copy next to review-diff) with `pr` installed in `risk_in`'s home, and
    `pr-risk` there too unless `risk_in` is None; the run is `--push`."""
    rig = Final(tmp_path, ONE)
    if gh_fails:
        (tmp_path / "bin/gh").write_text("#!/bin/sh\necho gh down >&2\nexit 1\n")
    skills = tmp_path / "installed/skills"
    shutil.copytree(SKILL, skills / "implement-spec", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "test_*"))
    shutil.copytree(SKILL.parent / "review-diff", skills / "review-diff", ignore=shutil.ignore_patterns("__pycache__"))
    home = tmp_path / "home"
    claude = tmp_path / "claude-home"
    claude.mkdir(), home.mkdir()
    where = {"claude": claude / "skills", "agents": home / ".agents/skills", None: claude / "skills"}[risk_in]
    names = ["pr", "pr-risk"] if risk_in else ["pr"]
    for name in names:
        (where / name).mkdir(parents=True)
        (where / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    if risk_in:
        for module in ("merge_risk.py", "merge_policy.py"):
            shutil.copy(PR_RISK / module, where / "pr-risk" / module)
    env = {**rig.env, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(claude)}
    (rig.rig / "plan.json").write_text(json.dumps(steps))
    args = ["--tickets", ".agent/tickets", "--gate", str(rig.rig / "gate"), "--spec", "specs/csv/spec.md",
            "--models", str(rig.models), "--runner", str(rig.rig / "run-agent"), "--push"]
    rig.rerun = lambda: _rerun(rig, ["sh", str(skills / "implement-spec/run.sh"), *args], env)
    rig.rerun()
    return rig, where / "pr-risk/SKILL.md"


def _rerun(rig, cmd, env):
    proc = subprocess.run(cmd, cwd=rig.repo, env=env, text=True, capture_output=True, timeout=180)
    rig.code, rig.out = proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize("home", ["claude", "agents"])
def test_the_pr_writer_gets_the_pr_risk_path_and_the_pr_opens_with_a_valid_block_above_the_facts(tmp_path, monkeypatch, home):
    rig, skill = pushed_run(tmp_path, monkeypatch, plan(**{"final/pr-writer": [writer(risk_body())]}), home)
    assert rig.code == 0, rig.out
    (call,) = rig.final_calls("pr-writer")
    assert str(skill) in call["prompt"]
    (pr,) = rig.prs()
    body = pr["body"]
    assert risk_valid(tmp_path, body), body
    assert 0 <= body.index("## Merge Danger") < body.index("## Tickets"), "the driver's facts come after the block"


def test_without_pr_risk_installed_the_run_is_blocked_with_the_install_command_and_pushes_nothing(tmp_path, monkeypatch):
    rig, _ = pushed_run(tmp_path, monkeypatch, plan(), None)
    assert rig.code != 0 and "pr-risk" in rig.out and "install" in rig.out.lower(), rig.out
    assert re.search(r"npx [^\n]*pr-risk", rig.out), f"no executable install command: {rig.out}"
    assert rig.pushes() == [] and not rig.prs() and not rig.final_calls("pr-writer")


def test_an_invalid_first_body_runs_the_pr_writer_once_more_and_a_valid_second_body_opens_the_pr(tmp_path, monkeypatch):
    steps = plan(**{"final/pr-writer": [writer(risk_body("styles")), writer(risk_body())]})
    rig, _ = pushed_run(tmp_path, monkeypatch, steps, "claude")
    assert rig.code == 0, rig.out
    assert len(rig.final_calls("pr-writer")) == 2
    (pr,) = rig.prs()
    assert risk_valid(tmp_path, pr["body"]) and "styles" not in pr["body"].split("## Tickets")[0]


def test_an_invalid_second_body_blocks_the_run_and_opens_no_pr(tmp_path, monkeypatch):
    steps = plan(**{"final/pr-writer": [writer(risk_body("styles")), writer(risk_body("styles")), writer(risk_body())]})
    rig, _ = pushed_run(tmp_path, monkeypatch, steps, "claude")
    assert rig.code != 0, rig.out
    assert len(rig.final_calls("pr-writer")) == 2 and not rig.prs()


def test_skill_md_step_2_tells_the_operator_to_start_the_babysitter_on_the_pr_url():
    step = next(l for l in (SKILL / "SKILL.md").read_text().splitlines() if l.startswith("2. "))
    assert "/babysit-pr https://github.com/jesdi/demo/pull/61" in step


def test_a_cached_body_that_no_longer_validates_is_rewritten_before_gh_pr_create_on_resume(tmp_path, monkeypatch):
    steps = plan(**{"final/pr-writer": [writer(risk_body()), writer(risk_body())]})
    rig, _ = pushed_run(tmp_path, monkeypatch, steps, "claude", gh_fails=True)
    assert rig.code != 0 and "gh pr create failed" in rig.out and len(rig.final_calls("pr-writer")) == 1, rig.out
    cached = rig.repo / ".agent/implement/final/pr-body.md"
    cached.write_text(cached.read_text().replace("touches: [docs]", "touches: [styles]"))
    (tmp_path / "bin/gh").write_text(GH)
    rig.rerun()
    assert rig.code == 0, rig.out
    assert len(rig.final_calls("pr-writer")) == 2, "the invalid cached body was not rewritten"
    (pr,) = rig.prs()
    assert risk_valid(tmp_path, pr["body"].split("## Tickets")[0]) and "styles" not in pr["body"].split("## Tickets")[0]
