"""Report validation acceptance tests, through driver_report.validate only.

context is a dict: repo (slot path), branch, start (HEAD sha when the role started),
log (the agent's log file), full (a full review of the reviewer).
"""
import json
import subprocess

import pytest

from driver_report import validate

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
RED = "FAILED tests/test_export.py::test_rows - AttributeError"
MUTATION = "FAILED tests/test_export.py::test_rows after breaking demo/export.py:2"


def git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def commit(root, path, text):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"edit {path}")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    """main: base. ticket/01: base -> tests (test-writer) -> impl. other: base -> stray."""
    root = tmp_path / "slot"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    base = commit(root, "demo/export.py", "def export_csv():\n    return ''\n")
    git(root, "checkout", "-q", "-b", "other")
    stray = commit(root, "stray.txt", "x\n")
    git(root, "checkout", "-q", "-b", "ticket/01", "main")
    tests = commit(root, "tests/test_export.py", "def test_rows():\n    assert False\n")
    impl = commit(root, "demo/export.py", "def export_csv():\n    return 'rows'\n")
    (tmp_path / "agent.log").write_text(f"collected 1 item\n{RED}\n{MUTATION}\n")
    shas = dict(base=base, stray=stray, tests=tests, impl=impl)

    def check(role, report, **context):
        path = tmp_path / f"{role}-0.json"
        path.write_text(report if isinstance(report, str) else json.dumps(report))
        ctx = {"repo": str(root), "branch": "ticket/01", "log": str(tmp_path / "agent.log")}
        return validate(role, str(path), {**ctx, **context})
    return shas, check, tmp_path


def common(**changes):
    return {"status": "DONE", "commits": [], "files": [], "concerns": [], **changes}


def writer(shas, **changes):
    return common(**{"commits": [shas["tests"]], "files": ["tests/test_export.py"], "locked": ["tests/test_export.py"],
                     "red": [{"test": "tests/test_export.py::test_rows", "line": RED}], **changes})


def implementer(shas, **changes):
    return common(**{"commits": [shas["impl"]], "files": ["demo/export.py"], "green": ["1 passed"], **changes})


def finding(**changes):
    return {"severity": "minor", "file": "demo/export.py", "line": 2, "title": "t", "evidence": "e", "kind": "code",
            **changes}


def reviewer(**changes):
    return common(**{"findings": [finding()], "mutations": [
        {"test": "tests/test_export.py::test_rows", "file": "demo/export.py", "line": 2, "red": MUTATION}], **changes})


def test_valid_reports_pass_and_unknown_status_is_named(world):
    shas, check, _ = world
    assert check("test-writer", writer(shas), start=shas["base"]) == []
    assert check("implementer", implementer(shas), start=shas["tests"]) == []
    assert check("reviewer", reviewer(), start=shas["impl"]) == []
    errors = check("implementer", implementer(shas, status="FINISHED"), start=shas["tests"])
    assert errors and any("status" in e and "FINISHED" in e for e in errors)


@pytest.mark.parametrize("field", ["status", "commits", "files", "concerns"])
def test_missing_common_field_is_named(world, field):
    shas, check, _ = world
    assert check("implementer", implementer(shas), start=shas["tests"]) == []
    report = implementer(shas)
    del report[field]
    assert any(field in e for e in check("implementer", report, start=shas["tests"]))


@pytest.mark.parametrize("role, field", [("test-writer", "locked"), ("test-writer", "red"), ("implementer", "green")])
def test_missing_role_field_is_named(world, role, field):
    shas, check, _ = world
    report = writer(shas) if role == "test-writer" else implementer(shas)
    start = shas["base"] if role == "test-writer" else shas["tests"]
    assert check(role, report, start=start) == []
    del report[field]
    assert any(field in e for e in check(role, report, start=start))


@pytest.mark.parametrize("path", ["missing", "empty", "not json"])
def test_unusable_report_is_an_error_not_a_crash(world, path):
    shas, check, tmp = world
    assert check("implementer", implementer(shas), start=shas["tests"]) == []
    if path == "missing":
        assert validate("implementer", str(tmp / "nope.json"), {"repo": str(tmp / "slot"), "branch": "ticket/01",
                                                                 "start": shas["tests"], "log": str(tmp / "agent.log")})
        return
    assert check("implementer", "" if path == "empty" else "{oops", start=shas["tests"])


def test_commit_off_the_ticket_branch_or_unknown_is_a_violation(world):
    shas, check, _ = world
    assert check("implementer", implementer(shas), start=shas["tests"]) == []
    for sha in (shas["stray"], "0123456789abcdef0123456789abcdef01234567"):
        errors = check("implementer", implementer(shas, commits=[sha]), start=shas["tests"])
        assert any(sha[:7] in e for e in errors), errors


def test_commit_predating_the_role_start_is_a_violation(world):
    shas, check, _ = world
    assert check("implementer", implementer(shas), start=shas["tests"]) == []
    errors = check("implementer", implementer(shas, commits=[shas["tests"], shas["impl"]]), start=shas["tests"])
    assert any(shas["tests"][:7] in e for e in errors), errors


def test_implementer_without_a_new_commit_is_a_violation(world):
    shas, check, _ = world
    assert check("implementer", implementer(shas), start=shas["tests"]) == []
    assert any("commit" in e for e in check("implementer", implementer(shas, commits=[]), start=shas["tests"]))
    assert check("implementer", implementer(shas), start=shas["impl"])  # its only commit is not new


def test_an_implementer_with_nothing_to_change_reports_no_commit_and_why(world):
    """Issue 123: an implementer whose start already meets the ticket has a report the driver accepts."""
    shas, check, _ = world
    why = ["the correction made the locked tests green: the start needs no change"]
    assert check("implementer", implementer(shas, commits=[], concerns=why), start=shas["impl"]) == []
    assert check("implementer", implementer(shas, commits=[]), start=shas["impl"]) == [
        "no commit, and no concern: say in `concerns` why the start needs no change"]
    assert check("implementer", implementer(shas, commits=[], concerns=why), start=shas["tests"]) == [
        f"commit {shas['impl'][:7]} is on ticket/01 and not in `commits`: list each commit you made"]


def test_a_fixer_with_nothing_to_change_reports_no_commit_and_a_reason_per_finding(world):
    """Issue 123: a fixer whose findings need no change has a report the driver accepts: no commit, a concern each."""
    shas, check, tmp = world
    fixer = lambda **changes: implementer(shas, **{"commits": [], "ticket": "01", **changes})
    fix = {"start": shas["impl"], "fix": ["STUCK", "LOOSE"]}
    reasons = ["STUCK: the correction at abc1234 resolved it", "LOOSE: the code already handles it"]
    assert check("fixer", fixer(status="DONE_WITH_CONCERNS", concerns=reasons), **fix) == []
    assert check("fixer", fixer(concerns=reasons[:1]), **fix) == [
        "no commit, and no concern names the finding LOOSE: give each finding a concern that says why it needs no change"]
    assert check("fixer", fixer(concerns=reasons), **fix, red=True) == [
        f"no commit, and the gate is red on {shas['impl'][:7]}: a fix of a red gate is a commit"]
    # A commit that predates the fixer is still refused, and a commit it made is in `commits`.
    assert check("fixer", fixer(commits=[shas["impl"]], concerns=reasons), **fix)
    assert check("fixer", fixer(concerns=reasons), **{**fix, "start": shas["tests"]}) == [
        f"commit {shas['impl'][:7]} is on ticket/01 and not in `commits`: list each commit you made"]
    (tmp / "slot" / "demo/export.py").write_text("changed\n")
    assert check("fixer", fixer(concerns=reasons), **fix) == [
        "no commit, and the tree has uncommitted changes: commit the fix and list the commit"]


def test_locked_path_absent_from_the_commit_is_a_violation(world):
    shas, check, _ = world
    assert check("test-writer", writer(shas), start=shas["base"]) == []
    errors = check("test-writer", writer(shas, locked=["tests/test_export.py", "tests/test_other.py"]),
                   start=shas["base"])
    assert any("tests/test_other.py" in e for e in errors), errors


def test_red_line_absent_from_the_log_is_a_violation(world):
    shas, check, _ = world
    assert check("test-writer", writer(shas), start=shas["base"]) == []
    report = writer(shas, red=[{"test": "t", "line": "FAILED tests/test_export.py::test_rows - KeyError"}])
    assert check("test-writer", report, start=shas["base"]) == [
        "red line not found in log: t: FAILED tests/test_export.py::test_rows - KeyError"]


def test_reviewer_fields_and_mutation_proofs(world):
    shas, check, _ = world
    assert check("reviewer", reviewer(), start=shas["impl"], full=True) == []
    assert any("severity" in e for e in check("reviewer", reviewer(findings=[finding(severity="huge")]),
                                              start=shas["impl"]))
    assert any("kind" in e for e in check("reviewer", reviewer(findings=[finding(kind="style")]), start=shas["impl"]))
    no_proof = reviewer()
    del no_proof["mutations"]
    assert any("mutations" in e for e in check("reviewer", no_proof, start=shas["impl"], full=True))
    fake = reviewer(mutations=[{"test": "t", "file": "demo/export.py", "line": 2, "red": "never ran"}])
    assert any("not found in log" in e for e in check("reviewer", fake, start=shas["impl"], full=True))
