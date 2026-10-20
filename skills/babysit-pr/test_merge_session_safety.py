"""CLI regressions for lease ownership at merge and handoff."""
import json

from test_merge_and_handoff import cli, handoffs, merge, world


def handoff(w, *extra):
    return cli(w, "handoff", "--repo", "jesdi/demo", "--pr", "58",
               "--reason", "door one-way", "--ci", "green", "--rounds", "0",
               "--gate", "gate: human", "--judge", "judge: not run",
               "--fix-log", str(w.project.parent / "body.md"), *extra)


def test_sessionless_handoff_cannot_adopt_a_lease(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    p = handoff(w)
    assert p.returncode != 0, p.output
    assert "--session" in p.output
    assert handoffs(w) == []
    assert w.active_leases()[0][1] == "s1"


def test_handoff_retry_cannot_end_resumed_session(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    assert handoff(w, "--session", "s1").returncode == 0
    st = w.github()
    st["comments"].append({"id": st["next_id"], "user": {"login": "jesdi"},
                           "body": "@babysitter add a test"})
    st["next_id"] += 1
    w.state.write_text(json.dumps(st))
    assert w.run("acquire", "s2").returncode == 0
    for args in [(), ("--session", "s1")]:
        p = handoff(w, *args)
        assert p.returncode != 0, p.output
        assert len(handoffs(w)) == 1
        assert w.active_leases()[0][1] == "s2"


def test_stale_session_cannot_merge_or_release_new_holder(tmp_path):
    w = world(tmp_path)
    assert w.run("acquire", "s2").returncode == 0
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree", "--session", "s1")
    assert p.returncode != 0, p.output
    assert w.github().get("merges", []) == []
    assert handoffs(w) == []
    assert w.active_leases()[0][1] == "s2"


def test_sessionless_merge_cannot_adopt_a_lease(tmp_path):
    w = world(tmp_path)
    assert w.run("acquire", "s2").returncode == 0
    before = w.github()
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0, p.output
    assert "--session" in p.output
    assert w.github() == before


def resume_during_handoff(w, after_end):
    """Resume via the real CLI at a terminal-write boundary of the fake gh."""
    from test_babysit import SCRIPT

    fake = w.project.parent / "bin" / "gh"
    real = fake.with_name("gh-real")
    fake.rename(real)
    condition = ('any("\\nended\\n" in c["body"] for c in st["comments"])' if after_end else
                 'any(c["body"].startswith("## 🧑 Needs human") for c in st["comments"])')
    fake.write_text(f'''#!/usr/bin/env python3
import json, os, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
p = subprocess.run([{str(real)!r}, *sys.argv[1:]])
state = Path(os.environ["GH_FAKE_STATE"])
st = json.loads(state.read_text())
if not st.get("resumed") and {condition}:
    st["resumed"] = True
    if not {after_end!r}:
        st["now"] = (datetime.fromisoformat(st["now"].replace("Z", "+00:00")) + timedelta(minutes=91)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.write_text(json.dumps(st))
    subprocess.run([sys.executable, {str(SCRIPT)!r}, "lease", "acquire", "--repo", "jesdi/demo", "--pr", "58", "--session", "s2", "--host", "other"], check=True, stdout=subprocess.DEVNULL)
sys.exit(p.returncode)
''')
    fake.chmod(0o755)


def test_handoff_finishes_labels_before_releasing_to_resumed_session(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    resume_during_handoff(w, after_end=True)
    p = handoff(w, "--session", "s1")
    assert p.returncode == 0, p.output
    assert w.active_leases()[0][1] == "s2"
    assert "babysitting" in w.github()["labels"]
    assert "needs-human" not in w.github()["labels"]


def test_handoff_rechecks_ownership_after_posting_comment(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    resume_during_handoff(w, after_end=False)
    p = handoff(w, "--session", "s1")
    assert p.returncode != 0, p.output
    assert w.active_leases()[0][1] == "s2"
    assert "babysitting" in w.github()["labels"]
    assert "needs-human" not in w.github()["labels"]


def test_merge_with_own_session_ends_only_its_lease(tmp_path):
    w = world(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree", "--session", "s1")
    assert p.returncode == 0, p.output
    assert len(w.github()["merges"]) == 1
    assert w.active_leases() == []
    assert "babysitting" not in w.github()["labels"]
