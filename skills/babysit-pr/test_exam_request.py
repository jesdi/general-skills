"""Acceptance tests for `babysit.py exam --repo R --pr N` and `babysit.py checks` with exams.

Spec specs/pr-exam, requirements 9-11; scenarios "babysit-pr waits for the exam once the PR is
otherwise ready", "the exam isn't requested before CI is green", "exams turned off for a repo",
"an invalid config stops the run", "an agent's skip label is ignored", the owner's skip/defer
labels (request half).

Interface under test:
  babysit.py exam --repo R --pr N
      exit 0 when the PR is ready: asks the host once (POST $PR_EXAM_HOST/api/exam with exactly
      {"repo", "pr"}) and prints one JSON object holding the host's `url` and `state`, then a line
      with "bypassed" (state skipped) or "deferred" (a deferred open exam: debt true).
      Not ready (a check pending, or owner-comments would hand off): prints "not ready", asks
      nothing. Exams off in `.pr-exam.json`: prints "disabled", asks nothing. Invalid config,
      PR_EXAM_HOST or PR_EXAM_APP unset, pr-exam not installed or an unreachable host: non-zero, with the reason.
  babysit.py checks ...: the `exam` commit status never counts as pending, stalled or red.
`.pr-exam.json` is read from `origin/<base>` of the checkout in the current directory. `gh` is the
fake in fixtures/gh, the host is fixtures/exam_host.py, reached through PR_EXAM_HOST.
"""
import json
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from exam_host import FakeHost  # noqa: E402
from test_babysit import BOT, STAGED, SCRIPT, T, MIN, World, iso
from test_babysit_judge import git, commit

PR_EXAM_INSTALL = "npx @jesdi/skills-cli install pr-exam"
GREEN = [{"name": "test", "status": "completed", "conclusion": "success", "started_at": iso(T)}]


class Exam(World):
    def __init__(self, tmp_path, base_config=None, head_config=None, checks=GREEN, statuses=None,
                 **kw):
        super().__init__(tmp_path, **kw)
        repo = self.project
        git(repo, "init", "-q", "-b", "work")
        base = commit(repo, {"README.md": "hi\n", **(
            {".pr-exam.json": base_config} if base_config is not None else {})}, "base")
        git(repo, "update-ref", "refs/remotes/origin/main", base)
        if head_config is not None:
            commit(repo, {".pr-exam.json": head_config}, "pr config")
        st = self.github()
        st.update(head_sha="head1", base_ref="main", check_runs={"head1": list(checks)},
                  statuses={"head1": list(statuses or [])})
        self.state.write_text(json.dumps(st))

    def seed(self, **extra):
        st = self.github()
        st.update(extra)
        self.state.write_text(json.dumps(st))

    def exam(self, host_url, script=None):
        env = {**self.env}
        if host_url:
            env["PR_EXAM_HOST"] = host_url
        else:
            env.pop("PR_EXAM_HOST", None)
        p = subprocess.run([sys.executable, str(script or self.script), "exam", "--repo",
                            "jesdi/demo", "--pr", "58"], cwd=self.project, env=env, text=True,
                           capture_output=True)
        p.output = p.stdout + p.stderr
        return p

    def checks(self):
        p = subprocess.run([sys.executable, str(self.script), "checks", "--repo", "jesdi/demo",
                            "--pr", "58", "--wait-minutes", "60"], cwd=self.project,
                           env=self.env, text=True, capture_output=True)
        p.output = p.stdout + p.stderr
        return p


def reported(p):
    """The JSON object `exam` printed (first line that parses as one)."""
    for line in p.stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise AssertionError("no JSON object on stdout: " + p.output)


def test_a_ready_pr_in_a_repo_without_config_asks_the_host_once(tmp_path):
    w = Exam(tmp_path)
    with FakeHost({"state": "open"}) as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert len(host.requests) == 1
    assert host.requests[0]["path"] == "/api/exam"
    out = reported(p)
    assert out["url"] == "https://box.example.ts.net/exam/jesdi/demo/58"
    assert out["state"] == "open"


def test_a_check_still_running_is_not_ready_and_asks_nothing(tmp_path):
    w = Exam(tmp_path, checks=GREEN + [{"name": "e2e", "status": "in_progress",
                                        "conclusion": None, "started_at": iso(T)}])
    with FakeHost() as host:
        p = w.exam(host.url)
    assert "not ready" in p.output.lower(), p.output
    assert host.requests == []


def test_an_owner_comments_handoff_is_not_ready_and_asks_nothing(tmp_path):
    w = Exam(tmp_path)
    w.seed(comments=[{"id": 1001, "user": {"login": "alice"}, "body": "why this change?",
                      "created_at": iso(T), "updated_at": iso(T)}], next_id=1002)
    with FakeHost() as host:
        p = w.exam(host.url)
    assert "not ready" in p.output.lower(), p.output
    assert host.requests == []


def test_exams_turned_off_say_disabled_and_ask_nothing(tmp_path):
    w = Exam(tmp_path, base_config='{"enabled": false}')
    with FakeHost() as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert "disabled" in p.output.lower(), p.output
    assert host.requests == []


INVALID_AND_OFF = '{"enabled": false, "bypassLabels": {"exam:skip": "maybe"}}'


@pytest.mark.parametrize("config", ['{"bypassLabels": {"exam:skip": "maybe"}}', INVALID_AND_OFF])
def test_an_invalid_bypass_mode_stops_naming_the_field_and_the_modes(tmp_path, config):
    w = Exam(tmp_path, base_config=config)  # `"enabled": false` never makes an invalid file "exams off"
    with FakeHost() as host:
        p = w.exam(host.url)
    assert p.returncode != 0, p.output
    for word in ("bypassLabels", "skip", "defer"):
        assert word in p.output
    assert host.requests == []


def test_a_config_changed_only_on_the_pr_branch_has_no_effect(tmp_path):
    w = Exam(tmp_path, base_config=None, head_config='{"enabled": false}')
    with FakeHost() as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert len(host.requests) == 1


def test_an_invalid_config_only_on_the_pr_branch_does_not_stop_the_run(tmp_path):
    w = Exam(tmp_path, base_config='{"enabled": true}',
             head_config='{"bypassLabels": {"exam:skip": "maybe"}}')
    with FakeHost() as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert len(host.requests) == 1


def test_the_request_is_only_repo_and_pr_and_a_non_owner_skip_label_changes_nothing(tmp_path):
    w = Exam(tmp_path, base_config='{"bypassLabels": {"exam:skip": "skip"}}')
    w.seed(labels=["exam:skip"], head_sha="head1")
    with FakeHost({"state": "open"}) as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert host.requests[0]["body"] == {"repo": "jesdi/demo", "pr": 58}
    assert reported(p)["state"] == "open"
    assert "bypassed" not in p.output.lower()


def test_a_skipped_exam_is_reported_as_bypassed(tmp_path):
    w = Exam(tmp_path, base_config='{"bypassLabels": {"exam:skip": "skip"}}')
    with FakeHost({"state": "skipped", "pending": False}) as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert "bypassed" in p.output.lower()


def test_a_deferred_exam_is_reported_as_deferred(tmp_path):
    w = Exam(tmp_path, base_config='{"bypassLabels": {"exam:defer": "defer"}}')
    with FakeHost({"state": "open", "debt": True, "pending": False}) as host:
        p = w.exam(host.url)
    assert p.returncode == 0, p.output
    assert "deferred" in p.output.lower()


@pytest.mark.parametrize("state", ["pending", "error", "failure"])
def test_checks_ignore_the_exam_status_whatever_its_state_and_age(tmp_path, state):
    w = Exam(tmp_path, statuses=[{"context": "exam", "state": state, "creator": BOT}])
    first = w.checks()
    assert "green" in first.output.lower(), first.output
    w.set_now(T + 3 * 24 * 60 * MIN)  # three days later, far beyond --wait-minutes
    later = w.checks().output
    assert "green" in later.lower(), later
    assert "stalled" not in later.lower()
    assert "red" not in later.lower()


def test_exams_enabled_without_the_host_variable_names_it(tmp_path):
    w = Exam(tmp_path)
    p = w.exam(None)
    assert p.returncode != 0, p.output
    assert "PR_EXAM_HOST" in p.output


def test_exams_enabled_without_the_app_variable_names_it(tmp_path):
    w = Exam(tmp_path)
    w.env.pop("PR_EXAM_APP")
    with FakeHost() as host:
        p = w.exam(host.url)
    assert p.returncode != 0, p.output
    assert "PR_EXAM_APP" in p.output
    assert host.requests == []


def test_exams_enabled_without_pr_exam_installed_names_the_install_command(tmp_path):
    w = Exam(tmp_path)
    stage = tmp_path / "stage" / "skills" / "babysit-pr"  # no sibling pr-exam, none under HOME
    stage.mkdir(parents=True)
    for name in STAGED:
        shutil.copy(SCRIPT.parent / name, stage / name)
    with FakeHost() as host:
        p = w.exam(host.url, script=stage / "babysit.py")
    assert p.returncode != 0, p.output
    assert PR_EXAM_INSTALL in p.output
    assert host.requests == []


def test_an_unreachable_host_exits_non_zero_and_says_so(tmp_path):
    w = Exam(tmp_path)
    with FakeHost() as host:
        dead = host.url
    p = w.exam(dead)  # the server is shut down: nothing listens there
    assert p.returncode != 0, p.output
    assert "unreachable" in p.output.lower()


def _stage(tmp_path):
    """babysit.py with no sibling pr-exam, so only the checkout and HOME can supply one."""
    stage = tmp_path / "stage" / "skills" / "babysit-pr"
    stage.mkdir(parents=True)
    for name in STAGED:
        shutil.copy(SCRIPT.parent / name, stage / name)
    return stage / "babysit.py"


def _evil(marker):
    return f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\nraise SystemExit(0)\n"


def _run_with_install(tmp_path, place):
    w = Exam(tmp_path, base_config='{"enabled": true}')
    marker = tmp_path / "marker"
    script = place(w, _evil(marker)) or _stage(tmp_path)
    with FakeHost() as host:
        p = w.exam(host.url, script=script)
    return p, marker, host


def _in_checkout(w, src):
    for d in ("skills/pr-exam", ".claude/skills/pr-exam", ".agents/skills/pr-exam"):
        (w.project / d).mkdir(parents=True, exist_ok=True)
        (w.project / d / "pr_exam.py").write_text(src)
    # Exercise the sibling-install candidate while babysit itself lives in the reviewed repo.
    stage = w.project / "skills/babysit-pr"
    stage.mkdir()
    for name in STAGED:
        shutil.copy(SCRIPT.parent / name, stage / name)
    return stage / "babysit.py"


def _home_is_checkout(w, src):
    w.env["HOME"] = str(w.project)
    (w.project / ".claude/skills/pr-exam").mkdir(parents=True)
    (w.project / ".claude/skills/pr-exam/pr_exam.py").write_text(src)


def _home_symlink(w, src, skills=".claude/skills"):
    (w.project / "vendored").mkdir()
    (w.project / "vendored/pr_exam.py").write_text(src)
    d = w.home / skills / "pr-exam"
    d.mkdir(parents=True)
    (d / "pr_exam.py").symlink_to(w.project / "vendored/pr_exam.py")


def _codex_home_symlink(w, src):
    _home_symlink(w, src, ".codex/skills")


@pytest.mark.parametrize("place", [_in_checkout, _home_is_checkout, _home_symlink, _codex_home_symlink])
def test_a_pr_exam_inside_the_reviewed_checkout_never_runs(tmp_path, place):
    p, marker, host = _run_with_install(tmp_path, place)
    assert not marker.exists(), "the PR's own pr_exam.py was executed"
    assert p.returncode != 0, p.output
    assert PR_EXAM_INSTALL in p.output
    assert host.requests == []


@pytest.mark.parametrize("skills", [".claude/skills", ".codex/skills"])  # .codex: the box's Codex home
def test_a_trusted_install_under_home_still_works(tmp_path, skills):
    w = Exam(tmp_path, base_config='{"enabled": true}')
    d = w.home / skills / "pr-exam"
    d.mkdir(parents=True)
    shutil.copy(SCRIPT.parent.parent / "pr-exam" / "pr_exam.py", d / "pr_exam.py")
    with FakeHost({"state": "open"}) as host:
        p = w.exam(host.url, script=_stage(tmp_path))
    assert p.returncode == 0, p.output
    assert len(host.requests) == 1


def test_an_install_committed_on_the_base_branch_is_used_not_the_working_tree_one(tmp_path):
    w = Exam(tmp_path, base_config='{"enabled": true}')
    real = (SCRIPT.parent.parent / "pr-exam" / "pr_exam.py").read_text()
    base = commit(w.project, {".claude/skills/pr-exam/pr_exam.py": real}, "install pr-exam")
    git(w.project, "update-ref", "refs/remotes/origin/main", base)
    marker = tmp_path / "marker"
    (w.project / ".claude/skills/pr-exam/pr_exam.py").write_text(_evil(marker))  # the PR's edit of it
    with FakeHost({"state": "open"}) as host:
        p = w.exam(host.url, script=_stage(tmp_path))
    assert p.returncode == 0, p.output
    assert not marker.exists(), "the working tree's pr_exam.py was executed"
    assert len(host.requests) == 1


@pytest.mark.parametrize("config", ['{"enabled": false}', INVALID_AND_OFF, '{"enabled": true}',
                                    '{"enabled": "false"}', '{"bypassLabels": {}}', "[]", "{"])
def test_every_config_needs_the_install_to_decide_exams_turned_off_included(tmp_path, config):
    # only pr_exam.py reads the config: without it nothing, not even `"enabled": false`, is trusted
    w = Exam(tmp_path, base_config=config)
    with FakeHost() as host:
        p = w.exam(host.url, script=_stage(tmp_path))
    assert p.returncode != 0, p.output
    assert PR_EXAM_INSTALL in p.output
    assert "disabled" not in p.output.lower(), p.output
    assert host.requests == []
