"""Acceptance tests for ticket 14, through `scripts/e2e-babysit.sh <scenario>` only.

Manual: needs `gh`, `claude`, `codex` and the private repo jesdi/babysit-pr-sandbox. Not in `pnpm check`.
Run: python3 -m pytest scripts/test_e2e_babysit_base_owner.py

Scenarios this ticket adds to the script (same stdout contract as test_e2e_babysit.py):
  base-moved        base +3 commits, no conflict: rebase, lease push, CI again, merge
  conflict-code     2 branch commits touch src/demo/parse.py, which conflicts: rebase, resolving-merge-conflicts,
                    hand off "conflict resolved in code" (head pushed, PR open)
  conflict-merge    3 branch commits touch tests/test_parse.py, which conflicts: merge origin/<base>, no force, merged
  conflict-tests    2 branch commits touch tests/test_parse.py, which conflicts: rebase, resolved, merged
  stacked           PR on a parent that holds a logic change and moved 3 commits: every step uses origin/<parent>
  stacked-base-fails  PR on a parent whose own test fails: hand off "fails on main too" from a clean worktree of origin/<parent>
  owner-instruction `@babysitter rename parse_x to parse_cells` from the owner: renamed, validated, pushed, merged
  owner-resume      handed off, then `@babysitter add a test for the empty case`: lease, instruction, merge
  owner-resume-held same, but another session holds the lease: no edit, no push, no merge
  owner-merge       `door: one-way` handed off, then `@babysitter merge <head sha>`: merged without the judge
"""
import json
import re

from test_e2e_babysit import (HANDOFF, HELP, REPO, after_handoff_is_quiet, bash, events, first, handed_off, log, mutates, passed, pr,
                              quiet_after_handoff, sh)

PUSH = re.compile(r"\bgit push\b")
JUDGE = re.compile(r"\bcodex exec\b|\bclaude (-p|--print)\b|\bpython3?\s+\S+\s+judge(?![-\w])(?![^;&\n]*--help)")
MERGE = re.compile(r"\bpython3?\s+\S+\s+merge(?![-\w])(?![^;&\n]*--help)")
UPDATE_PLAN = re.compile(r"\bpython3?\s+\S+\s+update-plan(?![-\w])(?![^;&\n]*--help)")


def meta(r):
    n = r["pr"].rsplit("/", 1)[1]
    return json.loads(sh("gh", "pr", "view", n, "-R", REPO, "--json", "headRefName,baseRefName").stdout)


def pushes(r):
    return [e for e in bash(r) if PUSH.search(e["cmd"])]


def only_lease_pushes(r):
    """Every `git push` of every command, compound ones included, is `git push --force-with-lease=<branch>:<old sha> origin <branch>`."""
    m = meta(r)
    pushed = pushes(r)
    assert pushed, "no push"
    lease = re.compile(rf"git push --force-with-lease={re.escape(m['headRefName'])}:[0-9a-f]{{7,40}} "
                       rf"origin {re.escape(m['headRefName'])}(\s+\d*>\s*\S+)?\s*")
    segments = [seg.strip() for e in pushed for seg in re.findall(r"\bgit push\b[^;&|\n]*", e["cmd"].replace("2>&1", ""))]
    assert [seg for seg in segments if not lease.fullmatch(seg)] == []
    assert any(f"--force-with-lease={m['headRefName']}:{r['before']}" in e["cmd"] for e in pushed), \
        "the lease does not name the old head"


def no_force(r):
    forced = [e["cmd"] for e in pushes(r) if re.search(r"--force|(?<!\S)-f(?!\S)|origin \+", e["cmd"])]
    assert not forced, forced


def skills(r):
    out = []
    for line in log(r).splitlines():
        try:
            blocks = json.loads(line)["message"]["content"]
        except (ValueError, KeyError, TypeError):
            continue
        for b in blocks if isinstance(blocks, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                out.append((b["input"] or {}).get("skill", ""))
    return out


def no_abort(r):
    assert not [e["cmd"] for e in bash(r) if re.search(r"git (rebase|merge|cherry-pick) .*--abort", e["cmd"])]


def merge_cmd(r):
    """The successful `babysit.py merge` call: `gh pr merge` runs inside it, so its flags are not in the transcript."""
    ms = [e for e in bash(r) if MERGE.search(e["cmd"]) and e["ok"]]
    assert ms, "no successful babysit.py merge"
    return ms[-1]["cmd"]


def merged_sha(cmd):
    m = re.search(r"--(?:sha|approved-sha)[ =]([0-9a-f]{7,40})", cmd)
    assert m, cmd
    return m.group(1)


def merge_method(r):
    """How GitHub merged the PR: two parents is a merge; one parent that keeps the last commit's headline is a rebase."""
    n = r["pr"].rsplit("/", 1)[1]
    oid = json.loads(sh("gh", "pr", "view", n, "-R", REPO, "--json", "mergeCommit").stdout)["mergeCommit"]["oid"]
    c = json.loads(sh("gh", "api", f"repos/{REPO}/commits/{oid}").stdout)
    if len(c["parents"]) == 2:
        return "merge"
    last = pr(r)["commits"][-1]["messageHeadline"]
    return "rebase" if c["commit"]["message"].splitlines()[0] == last else "squash"


def plans(r, base):
    return [e for e in bash(r) if UPDATE_PLAN.search(e["cmd"]) and re.search(rf"--base[ =]{re.escape(base)}\b", e["cmd"])]


def validated_before_push_and_ci_green_before_handoff(ev):
    """After the resolution (the last edit before the first push): green pytest and ruff, then the push, then a green
    `checks` result for the pushed head, and only then the handoff."""
    ok = lambda e, pat: e["tool"] == "Bash" and e["ok"] and re.search(pat, e["cmd"])
    push = first(ev, lambda e: e["tool"] == "Bash" and PUSH.search(e["cmd"]) and e["ok"])
    assert push is not None, "no successful push"
    edit = max((i for i in range(push) if mutates(ev[i])), default=None)
    assert edit is not None, "no resolution before the push"
    for tool in ("pytest", "ruff"):
        assert first(ev[:push], lambda e: ok(e, rf"\b{tool}\b"), edit) is not None, f"no green {tool} after the resolution, before the push"
    wait = first(ev, lambda e: ok(e, r"babysit\.py\b.*\bchecks\b") and re.search(r"\bgreen\b", e["out"]), push)
    handoff = first(ev, lambda e: e["tool"] == "Bash" and HANDOFF.search(e["cmd"]) and not HELP.search(e["cmd"]) and e["ok"])
    assert wait is not None, "no green CI observation after the push"
    assert handoff is not None and wait < handoff, "handed off before CI was green on the pushed head"


def test_base_moved_without_conflict_rebases_pushes_with_lease_and_merges():
    r = passed("base-moved")
    only_lease_pushes(r)
    m = meta(r)
    ev = bash(r)
    plan = first(ev, lambda e: UPDATE_PLAN.search(e["cmd"]) and f"--base {m['baseRefName']}" in e["cmd"])
    assert plan is not None and plan < first(ev, lambda e: PUSH.search(e["cmd"])), "no update-plan before the push"
    assert first(ev, lambda e: re.search(r"git rebase\b", e["cmd"])) is not None
    assert merge_method(r) == "rebase"


def test_conflict_in_two_commits_is_rebased_resolved_with_the_skill_and_handed_off():
    r = passed("conflict-code")
    ev = events(r)
    skill = first(ev, lambda e: e["tool"] == "Skill")
    assert "resolving-merge-conflicts" in skills(r) and skill is not None
    assert first(ev, lambda e: e["tool"] == "Bash" and re.search(r"git rebase\b", e["cmd"])) is not None
    only_lease_pushes(r)
    no_abort(r)
    validated_before_push_and_ci_green_before_handoff(ev)
    d = pr(r)
    assert any(c.startswith("## 🧑 Needs human: conflict resolved in code") for c in d["comments"])
    assert not any("Merged by babysit-pr" in c for c in d["comments"])
    quiet_after_handoff(ev)


def test_conflict_in_three_commits_merges_the_base_without_force_and_merges_with_the_merge_method():
    r = passed("conflict-merge")
    m = meta(r)
    assert first(bash(r), lambda e: re.search(rf"git merge\b.*origin/{re.escape(m['baseRefName'])}", e["cmd"])) is not None
    assert pushes(r)
    no_force(r)
    no_abort(r)
    assert pr(r)["headRefOid"].startswith(merged_sha(merge_cmd(r)))
    assert merge_method(r) == "merge"


def test_conflict_only_in_tests_is_resolved_with_the_skill_and_merged():
    r = passed("conflict-tests")
    assert "resolving-merge-conflicts" in skills(r)
    only_lease_pushes(r)
    no_abort(r)
    assert pr(r)["headRefOid"].startswith(merged_sha(merge_cmd(r)))
    assert merge_method(r) == "rebase"
    d = pr(r)
    assert not any("conflict resolved in code" in c for c in d["comments"])


def test_stacked_pr_rebases_onto_its_parent_and_never_looks_at_main():
    r = passed("stacked")
    m = meta(r)
    assert m["baseRefName"].startswith("e2e/parent-")
    cmds = [e["cmd"] for e in bash(r)]
    on_main = [c for c in cmds if re.search(r"origin/main\b|\.\.\.main\b|\bmain\.\.\.", c)]
    assert not on_main, on_main
    assert plans(r, m["baseRefName"]), "update-plan did not use the parent"
    assert any(re.search(rf"git rebase\b.*origin/{re.escape(m['baseRefName'])}\b", c) for c in cmds), "no rebase onto origin/<parent>"
    only_lease_pushes(r)


def test_stacked_pr_checks_whether_the_failure_also_happens_on_its_parent():
    r = passed("stacked-base-fails")
    m = meta(r)
    handed_off(r, "fails on main too")
    cmds = [e["cmd"] for e in bash(r)]
    assert not [c for c in cmds if re.search(r"origin/main\b|\.\.\.main\b|\bmain\.\.\.", c)]
    assert any("worktree" in c and f"origin/{m['baseRefName']}" in c for c in cmds), "no clean worktree of origin/<parent>"
    body = next(c for c in pr(r)["comments"] if c.startswith("## 🧑 Needs human: fails on main too"))
    assert body.count("```") >= 4, "need the PR output and the parent output"
    after_handoff_is_quiet(r)


def test_owner_rename_is_validated_pushed_and_gated_on_the_new_diff():
    r = passed("owner-instruction")
    ev = events(r)
    is_push = lambda e: e["tool"] == "Bash" and PUSH.search(e["cmd"]) and e["ok"]
    pushed = [i for i, e in enumerate(ev) if is_push(e)]
    assert pushed, "no push"
    prev = -1
    for push in pushed:
        edits = [i for i in range(prev + 1, push) if mutates(ev[i])]
        prev = push
        if not edits and push != pushed[0]:
            continue
        assert edits, "nothing was edited before the push"
        run = [(i, e) for i, e in enumerate(ev[:push]) if i > edits[-1] and e["tool"] == "Bash"
               and re.search(r"\bpytest\b|\bruff\b", e["cmd"])]
        for tool in ("pytest", "ruff"):
            last = [e for _, e in run if tool in e["cmd"]]
            assert last and last[-1]["ok"], f"the last {tool} run before the push is missing or red"
        green = lambda pred, after=-1: first(ev[:push], lambda e: e["tool"] == "Bash" and e["ok"] and pred(e["cmd"]), max(after, edits[-1]))
        targeted = green(lambda c: "pytest" in c and re.search(r"test_parse_x|::|-k\b", c))
        related = green(lambda c: "pytest" in c and ("test_parse.py" in c or not re.search(r"test_\w+\.py|::|-k\b", c)),
                        targeted if targeted is not None else 10**9)
        lint = green(lambda c: "ruff check" in c, related if related is not None else 10**9)
        assert targeted is not None, "no green targeted pytest after the last edit"
        assert related is not None and related >= targeted, "no green related pytest after the targeted one"
        assert lint is not None and lint >= related, "no green ruff check after the related tests"
    d = pr(r)
    assert d["headRefOid"] != r["before"]
    judge = first(ev, lambda e: e["tool"] == "Bash" and JUDGE.search(e["cmd"]), pushed[-1])
    handoff = first(ev, lambda e: e["tool"] == "Bash" and HANDOFF.search(e["cmd"]) and not HELP.search(e["cmd"]) and e["ok"],
                    pushed[-1])
    assert judge is not None or handoff is not None, "neither the judge nor a handoff saw the new head"
    merge = first(ev, lambda e: e["tool"] == "Bash" and MERGE.search(e["cmd"]) and e["ok"])
    if d["state"] == "MERGED":  # the judge may refuse the rename's interface change: then it is a handoff, not a merge
        assert merge is not None and merge > pushed[-1], "merged before the last push"
        assert judge is not None and judge < merge, "the judge did not see the new head"
        assert d["headRefOid"].startswith(merged_sha(merge_cmd(r)))


def test_resume_after_handoff_takes_the_lease_does_the_instruction_first_then_merges():
    r = passed("owner-resume")
    ev = events(r)
    edit = first(ev, mutates)
    assert edit is not None, "the instruction was not done"
    lease = first(ev, lambda e: e["tool"] == "Bash" and e["ok"] and re.search(r"babysit\.py\b.*\blease acquire\b", e["cmd"]))
    assert lease is not None and lease < edit, "no successful lease acquire before the first edit"
    assert first(ev, lambda e: e["tool"] == "Bash" and e["ok"] and "owner-comments" in e["cmd"]) is not None, "the owner's comment was not read"
    push = first(ev, lambda e: e["tool"] == "Bash" and PUSH.search(e["cmd"]), edit)
    merge = first(ev, lambda e: e["tool"] == "Bash" and MERGE.search(e["cmd"]) and e["ok"])
    assert push is not None and merge is not None and edit < push < merge
    d = pr(r)
    assert d["state"] == "MERGED" and "babysitting" not in d["labels"]


def test_owner_approval_of_the_head_merges_a_one_way_pr_without_the_judge():
    r = passed("owner-merge")
    d = pr(r)
    assert d["state"] == "MERGED" and d["headRefOid"] == r["before"]
    assert not pushes(r)
    assert not [e["cmd"] for e in bash(r) if JUDGE.search(e["cmd"])], "the judge ran"
    cmd = merge_cmd(r)
    assert "--approved-sha" in cmd and r["before"].startswith(merged_sha(cmd))


def test_resume_while_another_session_holds_the_lease_edits_and_pushes_nothing():
    r = passed("owner-resume-held")
    d = pr(r)
    assert d["state"] == "OPEN" and d["headRefOid"] == r["before"]
    assert not pushes(r)
    assert not [e for e in events(r) if mutates(e)], "edited while another session held the lease"
    assert not [e["cmd"] for e in bash(r) if MERGE.search(e["cmd"])]
    assert not any("Merged by babysit-pr" in c for c in d["comments"])
