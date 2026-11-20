"""Acceptance tests for the missing-required-check warning of `babysit.py lease acquire`
(spec specs/pr-exam, requirement 15, scenario "babysit-pr warns when the check isn't required").

Interface under test: `lease acquire` prints one warning naming `pr-exam setup <owner/repo>` when the
repo is public, exams are enabled (`.pr-exam.json` on origin/<base>) and the default branch's required
status checks do not include `exam` (as `pr_exam.py setup --check` reads them: the branch `setup`
protects). `lease renew` and `lease check` print none. No warning when `exam` is required, when the
repo is private, when exams are disabled, or when the answer is unknown. The warning is advisory: it
is worked out after the lease lock is released, and never fails or delays the lease.
`gh` is the fake in fixtures/gh (required_checks, protection, private, protection_error).
"""
import pytest

from test_babysit_judge import git
from test_exam_request import Exam

REPO = "jesdi/general-skills"
WARNING = f"pr-exam setup {REPO}"


def acquire(tmp_path, name, base_config=None, **github):
    w = Exam(tmp_path / name, base_config=base_config)
    w.env.pop("PR_EXAM_HOST", None)
    w.seed(**github)
    p = w.run("acquire", "s1", repo=REPO)
    assert p.returncode == 0, p.output
    return w, p


@pytest.fixture(autouse=True)
def dirs(tmp_path):
    for n in ("warn", "quiet", "other"):
        (tmp_path / n).mkdir()


@pytest.mark.parametrize("github", [{"required_checks": ["test"]}, {}],
                         ids=["other-checks-required", "unprotected"])
def test_acquire_warns_once_and_renew_and_check_stay_silent(tmp_path, github):
    w, p = acquire(tmp_path, "warn", private=False, **github)
    assert p.output.count(WARNING) == 1, p.output
    for verb in ("renew", "check"):
        q = w.run(verb, "s1", repo=REPO)
        assert q.returncode == 0, q.output
        assert "pr-exam setup" not in q.output, q.output


def test_no_warning_when_exam_is_required(tmp_path):
    _, control = acquire(tmp_path, "warn", private=False, required_checks=["test"])
    assert WARNING in control.output
    _, p = acquire(tmp_path, "quiet", private=False, required_checks=["test", "exam"])
    assert "pr-exam setup" not in p.output, p.output


def test_no_warning_when_exam_is_required_through_branch_protection(tmp_path):
    _, control = acquire(tmp_path, "warn", private=False)
    assert WARNING in control.output
    protection = {"required_status_checks": {"strict": False, "contexts": ["exam"],
                                             "checks": [{"context": "exam", "app_id": 424242}]},
                  "enforce_admins": {"enabled": True}}
    _, p = acquire(tmp_path, "quiet", private=False, protection=protection)
    assert "pr-exam setup" not in p.output, p.output


def test_no_warning_for_a_private_repo(tmp_path):
    _, control = acquire(tmp_path, "warn", private=False, required_checks=[])
    assert WARNING in control.output
    _, p = acquire(tmp_path, "quiet", private=True, required_checks=[])
    assert "pr-exam setup" not in p.output, p.output
    _, p = acquire(tmp_path, "other", private=True,
                   protection_error={"status": 403, "message": "Upgrade to GitHub Pro or make this "
                                                             "repository public."})
    assert "pr-exam setup" not in p.output, p.output


def test_no_warning_when_exams_are_disabled(tmp_path):
    _, control = acquire(tmp_path, "warn", private=False, required_checks=[])
    assert WARNING in control.output
    _, p = acquire(tmp_path, "quiet", base_config='{"enabled": false}', private=False,
                   required_checks=[])
    assert "pr-exam setup" not in p.output, p.output


@pytest.mark.parametrize("broken", ["not-fetched", "invalid-config"])
def test_an_unreadable_base_config_leaves_acquire_silent(tmp_path, broken):
    # the advisory never speaks for `exam`/`merge`, which stop on the same cause with the full message
    w = Exam(tmp_path / "quiet", base_config='{"enabled": "yes"}' if broken == "invalid-config" else None)
    w.env.pop("PR_EXAM_HOST", None)
    w.seed(private=False, required_checks=[])
    if broken == "not-fetched":
        git(w.project, "update-ref", "-d", "refs/remotes/origin/main")
    p = w.run("acquire", "s1", repo=REPO)
    assert p.returncode == 0, p.output
    assert p.stderr == ""


def test_the_default_branch_is_the_one_checked_not_the_prs_base(tmp_path):
    # `setup` protects the default branch: a PR into another branch is told about that one
    _, control = acquire(tmp_path, "warn", private=False, default_branch="trunk", base_ref="main")
    assert WARNING in control.output
    protection = {"required_status_checks": {"strict": False, "contexts": ["exam"],
                                             "checks": [{"context": "exam", "app_id": 424242}]}}
    _, p = acquire(tmp_path, "quiet", private=False, default_branch="trunk", base_ref="main", protection=protection)
    assert "pr-exam setup" not in p.output, p.output


def test_github_refusing_to_say_leaves_acquire_silent(tmp_path):
    _, p = acquire(tmp_path, "quiet", private=False,
                   protection_error={"status": 500, "message": "Server Error"})
    assert p.stderr == "", p.stderr


def test_the_warning_is_worked_out_after_the_lease_lock_is_released(tmp_path):
    """While `setup --check` runs, another session's lease command is not held up."""
    import os, subprocess, sys, time
    w = Exam(tmp_path / "warn")
    w.env.pop("PR_EXAM_HOST", None)
    w.seed(private=False)
    real = w.env["PATH"].split(os.pathsep)[0] + "/gh"
    slow = tmp_path / "slow"
    slow.mkdir()
    gate = tmp_path / "gate"
    (slow / "gh").write_text(  # the protection read blocks until the gate file exists
        f"#!{sys.executable}\nimport os, sys, time\n"
        f"if any('protection' in a for a in sys.argv):\n"
        f"    open({str(tmp_path / 'reached')!r}, 'w').close()\n"
        f"    while not os.path.exists({str(gate)!r}):\n        time.sleep(0.05)\n"
        f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n")
    (slow / "gh").chmod(0o755)
    env = {**w.env, "PATH": f"{slow}{os.pathsep}{w.env['PATH']}"}
    cmd = [sys.executable, str(w.script), "lease", "{verb}", "--repo", REPO, "--pr", "58", "--session", "s1", "--host", "mac2"]
    first = subprocess.Popen([c.format(verb="acquire") for c in cmd], cwd=w.project, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 60
        while not (tmp_path / "reached").exists():
            assert time.time() < deadline and first.poll() is None, "acquire never asked for the protection"
            time.sleep(0.05)
        other = subprocess.run([c.format(verb="check") for c in cmd], cwd=w.project, env=w.env, text=True,
                               capture_output=True, timeout=60)  # would time out if acquire still held the lock
        assert other.returncode == 0, other.stdout + other.stderr
    finally:
        gate.write_text("")
        out, err = first.communicate(timeout=60)
    assert first.returncode == 0, out + err
    assert err.count(WARNING) == 1, err
