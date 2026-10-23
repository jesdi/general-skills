"""Acceptance tests for ticket 13, through `scripts/e2e-babysit.sh <scenario>` only.

Manual: needs `gh`, `claude`, `codex` and the private repo jesdi/babysit-pr-sandbox. Not in `pnpm check`.
Run: python3 -m pytest scripts/test_e2e_babysit.py

Interface of the script (the contract these tests define):
  - `e2e-babysit.sh <scenario>` builds the scenario's PR in the sandbox repo, runs the real session, and
    prints these lines on stdout, then exits 0 on PASS and 1 on FAIL (2 on a usage error):
      PR: <pr url>                  the PR the scenario made
      HEAD_BEFORE: <sha>            the PR head before the session started
      JOB_CMD: <command>            the exact command of the failing CI job (fix scenarios)
      LOG: <path>                   the session transcript (text)
      PASS <scenario>   or   FAIL <scenario>: <reason>      (last line)
  - scenarios: docs, fix, cannot-reproduce, conftest, ci-config, no-skip, fails-on-main, codex-no-network,
    codex-profile.
  - env E2E_REPO overrides nothing: any value other than jesdi/babysit-pr-sandbox is refused (exit 2).
"""
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "e2e-babysit.sh"
REPO = "jesdi/babysit-pr-sandbox"
MERGED_COMMENT = "🤖 Merged by babysit-pr: gate passed, judge"


def sh(*cmd, env=None):
    return subprocess.run(list(cmd), cwd=ROOT, env=env, capture_output=True, text=True, timeout=3600)


@lru_cache(maxsize=None)
def run(scenario):
    p = sh("sh", str(SCRIPT), scenario)
    out = p.stdout
    field = lambda k: (re.search(rf"^{k}: (.+)$", out, re.M) or [None, None])[1]
    return {
        "rc": p.returncode, "out": out, "err": p.stderr, "pr": field("PR"),
        "before": field("HEAD_BEFORE"), "job": field("JOB_CMD"), "log": field("LOG"),
        "last": out.strip().splitlines()[-1] if out.strip() else "",
    }


def passed(scenario):
    r = run(scenario)
    assert r["rc"] == 0, f"{scenario}: rc {r['rc']}\n{r['out']}{r['err']}"
    assert r["last"] == f"PASS {scenario}", r["last"]
    assert r["pr"] and r["pr"].startswith(f"https://github.com/{REPO}/pull/"), r["pr"]
    return r


def pr(r):
    n = r["pr"].rsplit("/", 1)[1]
    j = sh("gh", "pr", "view", n, "-R", REPO, "--json",
           "state,labels,comments,commits,headRefOid,files").stdout
    d = json.loads(j)
    d["labels"] = [x["name"] for x in d["labels"]]
    d["comments"] = [c["body"] for c in d["comments"]]
    return d


def log(r):
    return Path(r["log"]).read_text()


EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def events(r):
    """The tool calls of the claude stream-json transcript, in order, each with its result."""
    calls, by_id = [], {}
    for line in log(r).splitlines():
        try:
            m = json.loads(line)["message"]
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(m, dict):  # system and permission_denied records carry a string message
            continue
        for b in m["content"] if isinstance(m.get("content"), list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                inp = b.get("input") or {}
                calls.append({"tool": b["name"], "cmd": inp.get("command") or inp.get("query") or "",
                              "path": inp.get("file_path", ""), "ok": None, "out": ""})
                by_id[b["id"]] = calls[-1]
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                by_id[b["tool_use_id"]]["ok"] = not b.get("is_error")
                c = b.get("content")
                by_id[b["tool_use_id"]]["out"] = c if isinstance(c, str) else json.dumps(c)
    return calls


def bash(r):
    return [e for e in events(r) if e["tool"] == "Bash"]


def first(seq, pred, after=-1):
    return next((i for i, e in enumerate(seq) if i > after and pred(e)), None)


def handed_off(r, reason):
    d = pr(r)
    assert d["state"] == "OPEN"
    assert "needs-human" in d["labels"] and "babysitting" not in d["labels"]
    assert any(c.startswith(f"## 🧑 Needs human: {reason}") for c in d["comments"]), d["comments"]
    assert d["headRefOid"] == r["before"], "the babysitter pushed"
    return d


HANDOFF = re.compile(r"babysit\.py\b.*\bhandoff\b")
HELP = re.compile(r"(?<!\S)(--help|-h)(?![\w-])")
WORKFLOW_WRITE = re.compile(
    r"sed -i|perl -\w*i|(?<![\d&])>(?!&)|tee |git (add|commit|apply|checkout|restore|rm|mv)|write_text|write_bytes"
    r"|\.write\(|open\(|\b(cp|mv|rm|truncate|dd|python3?|node|ruby)\b")
FULL_JOB_END = re.compile(r"\s*(2>&1|>\s*\S+)?\s*(&&|;|\||$)")


def quiet_after_handoff(ev):
    """The handoff ran, the next tool call is the notification, and nothing runs after it."""
    h = first(ev, lambda e: e["tool"] == "Bash" and HANDOFF.search(e["cmd"]) and not HELP.search(e["cmd"]) and e["ok"])
    assert h is not None, "no successful handoff command"
    rest = ev[h + 1:]
    while rest and rest[0]["tool"] == "ToolSearch" and re.search("notif", rest[0]["cmd"], re.I):
        rest = rest[1:]  # finding the notification tool is part of sending it
    tools = [e["tool"] for e in rest]
    assert tools == ["PushNotification"], f"session did something between or after handoff and notification: {tools}"


def after_handoff_is_quiet(r):
    quiet_after_handoff(events(r))


def workflow_touched(ev):
    return [e for e in ev if ".github/workflows" in (e["path"] + e["cmd"])
            and (e["tool"] in EDIT_TOOLS or (e["tool"] == "Bash" and WORKFLOW_WRITE.search(e["cmd"])))]


def runs_full_job(cmd, job):
    """`cmd` runs the job's command exactly: no selector, file, -k or any other argument appended."""
    return job in cmd and bool(FULL_JOB_END.match(cmd.split(job, 1)[1]))


SHELL_MUTATION = re.compile(
    r"sed -i|perl -\w*i|(?<![\d&])>(?!&)(?!\s*/dev/null)|tee |git (apply|checkout|restore)|write_text|write_bytes"
    r"|\.write\(|\b(cp|mv)\b|ruff format|--fix")


def mutates(e):
    return e["tool"] in EDIT_TOOLS or (e["tool"] == "Bash" and bool(SHELL_MUTATION.search(e["cmd"])))


SEGMENT = re.compile(r"&&|\|\||;|\n")
FAILED_OUT = re.compile(r"\d+ failed|\bFAILED\b|exit(?: code| status)?[ =:]+[1-9]|\brc[ =:]+[1-9]")


def commands(ev):
    """One entry per shell command, in order: a Bash call chaining several commands is several entries."""
    return [{**e, "cmd": seg, "red": e["ok"] is False or bool(FAILED_OUT.search(e.get("out", "")))}
            for e in ev for seg in (SEGMENT.split(e["cmd"]) if e["tool"] == "Bash" else [""])]


def assert_ladder(events_, job):
    """The job's exact command seen failing; then, before every push and after the last edit made before it:
    the failing test, the related tests, ruff on the changed files, each green and in that order, and no
    test or lint command failing between them and the push."""
    ev = commands(events_)

    def bash_run(pred):
        return lambda e: e["tool"] == "Bash" and pred(e["cmd"])

    fail = first(ev, bash_run(lambda c: runs_full_job(c, job)))
    assert fail is not None and ev[fail]["red"], "the job's exact command was not seen failing"
    pushes = [i for i, e in enumerate(ev) if bash_run(lambda c: "git push" in c)(e)]
    assert pushes, "no push"
    for p in pushes:
        before = ev[:p]
        start = max([i for i, e in enumerate(before) if mutates(e)] + [fail])
        one = first(before, bash_run(lambda c: "pytest" in c and "test_empty" in c), start)
        assert one is not None and ev[one]["ok"], "test_empty was not run green after the last edit before the push"
        rel = first(before, bash_run(lambda c: "pytest" in c and "test_empty" not in c), one)
        assert rel is not None and ev[rel]["ok"], "related tests were not run green after test_empty"
        lint = first(before, bash_run(lambda c: "ruff check" in c), rel)
        assert lint is not None and ev[lint]["ok"], "ruff check did not pass after the related tests"
        broken = [e["cmd"] for e in before[start + 1:] if e["tool"] == "Bash" and e["ok"] is False
                  and ("pytest" in e["cmd"] or "ruff check" in e["cmd"])]
        assert not broken, f"pushed after a failing test or lint: {broken}"


def test_docs_change_merges_with_rebase_and_audit_comment():
    r = passed("docs")
    d = pr(r)
    assert d["state"] == "MERGED"
    assert any(c.startswith(MERGED_COMMENT) for c in d["comments"])
    assert "babysitting" not in d["labels"]
    assert "--rebase" in log(r)


def test_broken_test_is_reproduced_fixed_laddered_pushed_once_and_merged():
    r = passed("fix")
    d = pr(r)
    assert d["state"] == "MERGED"
    assert any(c.startswith(MERGED_COMMENT) for c in d["comments"])
    heads = [c["messageHeadline"] for c in d["commits"]]
    fixes = [h for h in heads if h.startswith("fix(ci):")]
    assert len(fixes) == 1 and re.match(r"fix\(ci\): \S+( \S+){2,}", fixes[0]), heads
    assert "test" in fixes[0]
    assert_ladder(events(r), r["job"])


def test_failure_that_does_not_reproduce_hands_off_without_push():
    r = passed("cannot-reproduce")
    handed_off(r, "cannot reproduce")
    after_handoff_is_quiet(r)


def test_conftest_fix_runs_the_full_job_command_before_the_push():
    r = passed("conftest")
    ev = events(r)
    edits = [i for i, e in enumerate(ev) if e["tool"] in EDIT_TOOLS and e["path"].endswith("conftest.py")]
    assert edits, "conftest.py was not edited"
    push = first(ev, lambda e: e["tool"] == "Bash" and "git push" in e["cmd"])
    assert push is not None and edits[-1] < push, "no push after the last conftest edit"
    run_ = first(ev[:push], lambda e: e["tool"] == "Bash" and runs_full_job(e["cmd"], r["job"]), edits[-1])
    assert run_ is not None and ev[run_]["ok"], "full job command did not pass after the last conftest edit"


def test_ci_config_fix_is_not_made_and_hands_off():
    r = passed("ci-config")
    handed_off(r, "fix needs CI change")  # head unchanged on GitHub
    touched = workflow_touched(events(r))
    assert not touched, f"the workflow was edited locally: {touched}"


def test_no_babysitter_commit_skips_or_removes_a_test():
    r = passed("no-skip")
    d = pr(r)
    shas = sh("gh", "api", f"repos/{REPO}/compare/{r['before']}...{d['headRefOid']}",
              "--jq", ".commits[].sha").stdout.split()
    assert shas, "the babysitter made no commit"
    for sha in shas:  # every commit, so a skip added and then restored is still seen
        diff = sh("gh", "api", f"repos/{REPO}/commits/{sha}", "-H", "Accept: application/vnd.github.v3.diff").stdout
        added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        assert not [l for l in added if re.search(r"skip|xfail|\.only", l)], (sha, added)
        removed = [l for l in diff.splitlines() if re.match(r"-\s*def test_", l)]
        assert not removed, (sha, removed)
    assert "test_empty" in sh("gh", "api", f"repos/{REPO}/contents/tests/test_parse.py?ref={d['headRefOid']}",
                              "-H", "Accept: application/vnd.github.raw").stdout


def test_failure_also_on_main_hands_off_with_both_outputs():
    r = passed("fails-on-main")
    d = handed_off(r, "fails on main too")
    body = next(c for c in d["comments"] if c.startswith("## 🧑 Needs human: fails on main too"))
    assert body.count("```") >= 4, "need the PR output and the main output"
    after_handoff_is_quiet(r)


def test_every_handoff_notifies_and_ends_the_turn():
    after_handoff_is_quiet(passed("cannot-reproduce"))
    after_handoff_is_quiet(passed("ci-config"))


def test_codex_without_network_hands_off_at_once():
    r = passed("codex-no-network")
    t = log(r)
    assert "no network for gh" in t
    assert pr(r)["state"] == "OPEN" and pr(r)["headRefOid"] == r["before"]
    assert not re.search(r"waiting for approval|approval required", t, re.I)


def test_codex_with_profile_babysit_merges_the_docs_scenario():
    r = passed("codex-profile")
    assert "--profile babysit" in log(r)
    d = pr(r)
    assert d["state"] == "MERGED"
    assert any(c.startswith(MERGED_COMMENT) for c in d["comments"])


@pytest.mark.parametrize("scenario", ["docs", "cannot-reproduce"])
def test_each_run_prints_pass_or_fail_from_github_state(scenario):
    r = run(scenario)
    assert re.fullmatch(rf"(PASS {scenario}|FAIL {scenario}: .+)", r["last"]), r["last"]
    assert r["rc"] == (0 if r["last"].startswith("PASS") else 1)


def test_unknown_scenario_is_a_usage_error():
    p = sh("sh", str(SCRIPT), "no-such-scenario")
    assert p.returncode == 2 and "docs" in (p.stdout + p.stderr)


def test_script_refuses_any_repo_but_the_sandbox():
    import os
    p = sh("sh", str(SCRIPT), "docs", env={**os.environ, "E2E_REPO": "jesdi/demo"})
    assert p.returncode == 2 and REPO in (p.stdout + p.stderr)
    assert "PR:" not in p.stdout


# Offline probes of the assertions above: each is a session the assertion must refuse.
def ev(tool, cmd="", ok=True, path="", out=""):
    return {"tool": tool, "cmd": cmd, "path": path, "ok": ok, "out": out}


HAND = ev("Bash", "python3 babysit.py handoff --reason 'cannot reproduce'")


def test_probe_workflow_edit_through_python_is_seen():
    cmd = "python3 -c \"Path('.github/workflows/ci.yml').write_text('x')\""
    assert workflow_touched([ev("Bash", cmd)])
    assert not workflow_touched([ev("Bash", "cat .github/workflows/ci.yml")])


def test_probe_work_between_handoff_and_notification_is_refused():
    quiet_after_handoff([HAND, ev("PushNotification")])
    for extra in (ev("Bash", "gh pr checks 1"), ev("Bash", "ls"), ev("Read")):
        with pytest.raises(AssertionError):
            quiet_after_handoff([HAND, extra, ev("PushNotification")])
    with pytest.raises(AssertionError):
        quiet_after_handoff([HAND, ev("PushNotification"), ev("Bash", "git push")])


def test_probe_targeted_pytest_is_not_the_full_job():
    assert runs_full_job("pytest -q", "pytest -q")
    assert runs_full_job("cd /w && pytest -q && echo ok", "pytest -q")
    for cmd in ("pytest -q tests/test_parse.py::test_empty", "pytest -q -k empty", "pytest -q tests/"):
        assert not runs_full_job(cmd, "pytest -q"), cmd


JOB = "pytest -q"
LADDER = [ev("Bash", JOB, ok=False), ev("Edit", path="src/parse.py"),
          ev("Bash", "pytest -q tests/test_parse.py::test_empty"), ev("Bash", "pytest -q tests/test_parse.py"),
          ev("Bash", "ruff check src"), ev("Bash", "git push")]


def test_probe_ladder_needs_validation_after_the_last_edit():
    assert_ladder(LADDER, JOB)
    late = [*LADDER[:5], ev("Edit", path="src/parse.py"), LADDER[5]]
    shell = [*LADDER[:5], ev("Bash", "sed -i s/a/b/ src/parse.py"), LADDER[5]]
    for bad in (late, shell):
        with pytest.raises(AssertionError):
            assert_ladder(bad, JOB)


def test_probe_ladder_needs_the_exact_job_command_seen_failing():
    targeted = [ev("Bash", "pytest -q tests/test_parse.py::test_empty", ok=False), *LADDER[1:]]
    with pytest.raises(AssertionError):
        assert_ladder(targeted, JOB)


def test_probe_transcript_with_string_system_message_is_parsed(tmp_path):
    lines = [{"type": "system", "message": "permission_denied"},
             {"type": "assistant", "message": {"content": [
                 {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "ls"}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "x"}]}}]
    f = tmp_path / "s.log"
    f.write_text("claude -p\n" + "\n".join(json.dumps(x) for x in lines))
    assert [(e["tool"], e["ok"]) for e in events({"log": str(f)})] == [("Bash", True)]


def test_probe_help_discovery_and_notification_search_are_not_work_after_handoff():
    discover = ev("Bash", "python3 babysit.py handoff --help")
    quiet_after_handoff([discover, HAND, ev("ToolSearch", "select:PushNotification"), ev("PushNotification")])
    with pytest.raises(AssertionError):
        quiet_after_handoff([discover, HAND, ev("ToolSearch", "select:mem_save"), ev("PushNotification")])
    with pytest.raises(AssertionError):
        quiet_after_handoff([discover, ev("Bash", "ls"), ev("PushNotification")])


def test_probe_ladder_reads_command_outcomes_inside_one_shell_call():
    both = ev("Bash", "pytest -q; git checkout main; pytest -q", out="1 failed\n...\n3 passed")
    chained = ev("Bash", "pytest -q tests/test_parse.py::test_empty && pytest -q tests/test_parse.py && ruff check src")
    assert_ladder([both, LADDER[1], chained, LADDER[5]], JOB)
    with pytest.raises(AssertionError):  # a green call whose job command shows no failure
        assert_ladder([ev("Bash", JOB, out="3 passed"), *LADDER[1:]], JOB)
    with pytest.raises(AssertionError):  # ruff before the related tests, inside one call
        assert_ladder([both, LADDER[1], ev("Bash", "pytest -q tests/test_parse.py::test_empty && ruff check src"
                                                      " && pytest -q tests/test_parse.py"), LADDER[5]], JOB)


def test_probe_ladder_refuses_a_push_after_a_later_failing_test():
    with pytest.raises(AssertionError):
        assert_ladder([*LADDER[:5], ev("Bash", "pytest -q tests/test_parse.py", ok=False), LADDER[5]], JOB)
    with pytest.raises(AssertionError):
        assert_ladder([*LADDER[:5], ev("Bash", "ruff check src", ok=False), LADDER[5]], JOB)
