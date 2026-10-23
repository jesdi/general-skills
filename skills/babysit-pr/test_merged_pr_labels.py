"""Acceptance tests: a merged PR never keeps a stale handoff label.

Seam: `babysit.py handoff --session S` and `babysit.py merge --session S`, with the fake `gh`.

The fake always serves the PR as open, so a wrapper around it serves the PR as merged once the
fake has recorded a `gh pr merge` (REST `merged: true, state: closed`, `gh pr view` `state: MERGED`).
The wrapper also injects the race: right before the stale session s1's terminal write, the clock
moves past the lease expiry and s2 takes over, merges and releases.
  TRIGGER=comment: the injection fires just before s1 posts its handoff comment, so s1's comment
                   lands after s2's merge comment.
  TRIGGER=labels:  it fires just before s1's first label write, so s1's comment is older.
Expected: the merged PR ends without `needs-human` and without `babysitting`.
KIND picks what happens at the injection point: "successor" (s2 takes over, merges, releases), "hold"
(s2 takes over and keeps the open PR), "hold+owner" (s2 takes over, then the owner merges the PR with
no merge comment), "owner" (no takeover; the owner merges the PR with no merge comment).
"""
import pytest

from test_babysit import SCRIPT
from test_merge_and_handoff import cli, handoffs, merge, world
from test_merge_session_safety import handoff

WRAPPER = '''#!/usr/bin/env python3
import json, os, re, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
argv = sys.argv[1:]
state = Path(os.environ["GH_FAKE_STATE"])
st = json.loads(state.read_text())
text = " ".join(argv)
is_comment = (argv[:2] == ["pr", "comment"] or (argv[:1] == ["api"] and re.search(r"issues/\\d+/comments\\b", text)
              and any(a in argv for a in ("-X", "--method", "-f", "-F", "--input")))) and "Needs human" in text
is_label = (argv[:2] == ["pr", "edit"] and "label" in text) or (
    argv[:1] == ["api"] and re.search(r"/issues/\\d+/labels(/|\\s|$)", text)
    and any(a in argv for a in ("-X", "--method", "-f", "-F", "--input")))
terminal = any(c["body"].startswith("## 🧑 Needs human") for c in st["comments"])
fire = {inject!r} and not st.get("taking") and (is_comment if {trigger!r} == "comment" else is_label and terminal)
if fire:
    st["taking"] = True
    kind = {kind!r}
    if kind != "owner":
        st["now"] = (datetime.fromisoformat(st["now"].replace("Z", "+00:00"))
                     + timedelta(minutes=91)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind.endswith("owner"):
        st.setdefault("merges", []).append({{"number": 58, "method": "merge"}})
    state.write_text(json.dumps(st))
    base = [sys.executable, {script!r}]
    where = ["--repo", "jesdi/demo", "--pr", "58"]
    if kind != "owner":
        subprocess.run([*base, "lease", "acquire", *where, "--session", "s2", "--host", "other"],
                       check=True, stdout=subprocess.DEVNULL)
    if kind == "successor":
        subprocess.run([*base, "merge", *where, "--sha", "a1b2c3d", "--body", {body!r},
                        "--judge", "agree", "--session", "s2"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([*base, "lease", "release", *where, "--session", "s2"], stdout=subprocess.DEVNULL)
    st = json.loads(state.read_text())
    st["taken"] = True
    state.write_text(json.dumps(st))

# serve the PR as merged once the fake recorded a merge
st = json.loads(state.read_text())
pr_read = (argv[:2] == ["pr", "view"]) or (argv[:1] == ["api"] and re.search(r"(issues|pulls)/\\d+$", text.split("--jq")[0].strip())
           and not any(a in argv for a in ("-X", "--method", "-f", "-F", "--input")))
if st.get("merges") and pr_read:
    jq, rest, i = None, [], 0
    while i < len(argv):
        if argv[i] in ("--jq", "-q"):
            jq = argv[i + 1]; i += 2
        else:
            rest.append(argv[i]); i += 1
    p = subprocess.run([{real!r}, *rest], capture_output=True, text=True)
    if p.returncode:
        sys.stderr.write(p.stderr); sys.exit(p.returncode)
    obj = json.loads(p.stdout)
    if argv[0] == "pr":
        obj.update(state="MERGED", mergedAt=st["now"], closed=True)
    else:
        obj.update(state="closed", merged=True, merged_at=st["now"])
    if jq:
        q = subprocess.run(["jq", "-r", jq], input=json.dumps(obj), text=True, capture_output=True)
        sys.stdout.write(q.stdout); sys.stderr.write(q.stderr); sys.exit(q.returncode)
    print(json.dumps(obj)); sys.exit(0)
sys.exit(subprocess.run([{real!r}, *argv]).returncode)
'''


def install(w, tmp_path, trigger="labels", inject=True, kind="successor"):
    fake = w.project.parent / "bin" / "gh"
    real = fake.with_name("gh-real")
    fake.rename(real)
    fake.write_text(WRAPPER.format(script=str(SCRIPT), real=str(real), trigger=trigger, inject=inject, kind=kind,
                                   body=str(tmp_path / "body.md")))
    fake.chmod(0o755)


@pytest.mark.parametrize("trigger", ["comment", "labels"])
def test_stale_handoff_after_a_successor_merge_leaves_the_merged_pr_without_labels(tmp_path, trigger):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    install(w, tmp_path, trigger)
    handoff(w, "--session", "s1")
    st = w.github()
    assert st.get("taken"), "the takeover never fired"
    assert len(st["merges"]) == 1, "the successor never merged"
    assert any("Merged by babysit-pr" in c["body"] for c in st["comments"])
    assert "needs-human" not in st["labels"], st["labels"]
    assert "babysitting" not in st["labels"], st["labels"]


def test_handoff_on_an_open_pr_with_no_takeover_still_ends_with_needs_human(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    install(w, tmp_path, inject=False)
    p = handoff(w, "--session", "s1")
    assert p.returncode == 0, p.output
    labels = w.github()["labels"]
    assert "needs-human" in labels and "babysitting" not in labels
    assert len(handoffs(w)) == 1


def test_takeover_of_an_open_pr_keeps_the_new_holders_labels(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    install(w, tmp_path, "labels", kind="hold")
    handoff(w, "--session", "s1")
    st = w.github()
    assert st.get("taken"), "the takeover never fired"
    assert not st.get("merges")
    assert "babysitting" in st["labels"], st["labels"]
    assert "needs-human" not in st["labels"], st["labels"]


@pytest.mark.parametrize("kind", ["hold+owner", "owner"])
def test_pr_merged_during_the_stale_write_ends_without_labels(tmp_path, kind):
    """The merge lands between the handoff comment and the label write, with no merge comment to order by."""
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    install(w, tmp_path, "labels", kind=kind)
    handoff(w, "--session", "s1")
    st = w.github()
    assert st.get("taken"), "the injection never fired"
    assert len(st["merges"]) == 1
    assert "needs-human" not in st["labels"], st["labels"]
    assert "babysitting" not in st["labels"], st["labels"]
