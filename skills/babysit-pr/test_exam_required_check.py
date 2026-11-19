"""Acceptance tests for the missing-required-check warning of `babysit.py lease acquire`
(spec specs/pr-exam, requirement 15, scenario "babysit-pr warns when the check isn't required").

Interface under test: `lease acquire` prints one warning naming `pr-exam setup <owner/repo>` when the
repo is public, exams are enabled (`.pr-exam.json` on origin/<base>) and the base branch's required
status checks do not include `exam`. `lease renew` and `lease check` print none. No warning when
`exam` is required, when the repo is private, or when exams are disabled.
`gh` is the fake in fixtures/gh (required_checks, protection, private, protection_error).
"""
import pytest

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
