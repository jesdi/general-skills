"""Acceptance tests: a terminal label write never leaves its labels on a taken-over PR.

Seam: `babysit.py handoff|merge --session S` and `babysit.py lease acquire`, with the fake `gh`.

The takeover is injected through a wrapper around the fake `gh`: right before the first PR label
write (issues/N/labels, not the repo-level label creation) that follows the terminal comment (handoff comment, or a recorded merge), the clock moves
past the lease expiry and session s2 runs the real `lease acquire`. That is after any ownership
check that precedes the write, and before the write lands over s2's labels.
Expected: s2 keeps `babysitting`, `needs-human` is off, the handoff comment stays, and the
command reports "taken over".
"""
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
paths = [a for a in argv if re.search(r"/issues/\\d+/labels(/|$)", a)]
write = (argv[:2] == ["pr", "edit"] and any("label" in a for a in argv)) or (
    argv[:1] == ["api"] and paths and any(a in argv for a in ("-X", "--method", "-f", "-F", "--input")))
terminal = any(c["body"].startswith("## 🧑 Needs human") or "Merged by babysit-pr" in c["body"]
               for c in st["comments"]) or st.get("merges")
if write and terminal and not st.get("taking"):
    st["taking"] = True
    st["now"] = (datetime.fromisoformat(st["now"].replace("Z", "+00:00"))
                 + timedelta(minutes=91)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state.write_text(json.dumps(st))
    subprocess.run([sys.executable, {script!r}, "lease", "acquire", "--repo", "jesdi/demo",
                    "--pr", "58", "--session", "s2", "--host", "other"],
                   check=True, stdout=subprocess.DEVNULL)
    st = json.loads(state.read_text())
    st["taken"] = True
    state.write_text(json.dumps(st))
st = json.loads(state.read_text())
if {finish!r} and st.get("taken") and argv[:3] == ["api", "-X", "DELETE"] and argv[3].endswith("/labels/needs-human") \
        and not st.get("s2done"):
    # s2 finishes its own handoff after s1's repair put babysitting back, right before s1's
    # repair removes needs-human: that removal lands over s2's completed handoff.
    st["s2done"] = True
    state.write_text(json.dumps(st))
    if {finish!r} == "paused":
        # s2 has posted its handoff and written its labels but has not released its lease yet.
        base = ["api", "-X"]
        issue = "repos/jesdi/demo/issues/58"
        for call in (["POST", issue + "/comments", "-f", "body=## 🧑 Needs human: done\\n"],
                     ["POST", issue + "/labels", "-f", "labels[]=needs-human"]):
            subprocess.run([{real!r}, *base, *call], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([{real!r}, *base, "DELETE", issue + "/labels/babysitting"], check=True,
                       stdout=subprocess.DEVNULL)
    else:
        subprocess.run([sys.executable, {script!r}, "handoff", "--repo", "jesdi/demo", "--pr", "58",
                        "--reason", "done", "--ci", "green", "--rounds", "0", "--gate", "gate: human",
                        "--judge", "judge: not run", "--fix-log", {body!r}, "--session", "s2"],
                       check=True, stdout=subprocess.DEVNULL)
rc = subprocess.run([{real!r}, *argv]).returncode
sys.exit(rc)
'''


def takeover_before_label_write(w, finish_as_s2=False):
    """finish_as_s2: True runs s2's whole handoff; "paused" stops it just before the lease release."""
    fake = w.project.parent / "bin" / "gh"
    real = fake.with_name("gh-real")
    fake.rename(real)
    fake.write_text(WRAPPER.format(script=str(SCRIPT), real=str(real), finish=finish_as_s2,
                                     body=str(w.project.parent / "body.md")))
    fake.chmod(0o755)


def holder(w):
    return [lease[1] for lease in w.active_leases()]


def test_handoff_after_takeover_leaves_the_new_holders_labels_and_reports_it(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w)
    p = handoff(w, "--session", "s1")
    assert w.github().get("taken"), "the takeover never fired"
    assert holder(w) == ["s2"]
    labels = w.github()["labels"]
    assert "babysitting" in labels and "needs-human" not in labels
    assert len(handoffs(w)) == 1
    assert p.returncode != 0, p.output
    assert "taken over" in p.output.lower()


def test_merge_after_takeover_leaves_the_new_holders_labels_and_reports_it(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w)
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree", "--session", "s1")
    assert w.github().get("taken"), "the takeover never fired"
    assert len(w.github()["merges"]) == 1
    assert holder(w) == ["s2"]
    labels = w.github()["labels"]
    assert "babysitting" in labels and "needs-human" not in labels
    assert "taken over" in p.output.lower()


def test_handoff_without_takeover_ends_with_needs_human_and_no_babysitting(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    p = handoff(w, "--session", "s1")
    assert p.returncode == 0, p.output
    assert "taken over" not in p.output.lower()
    labels = w.github()["labels"]
    assert "needs-human" in labels and "babysitting" not in labels
    assert len(handoffs(w)) == 1
    assert holder(w) == []


def test_new_holders_own_handoff_survives_the_repair_of_a_stale_handoff(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w, finish_as_s2=True)
    p = handoff(w, "--session", "s1")
    assert w.github().get("s2done"), "s2 never finished its handoff"
    labels = w.github()["labels"]
    assert "needs-human" in labels and "babysitting" not in labels, labels
    assert holder(w) == []
    assert len(handoffs(w)) == 2
    assert p.returncode != 0, p.output
    assert "taken over" in p.output.lower()


def test_new_holders_handoff_survives_the_repair_while_its_lease_is_still_active(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w, finish_as_s2="paused")
    p = handoff(w, "--session", "s1")
    assert w.github().get("s2done"), "s2 never posted its handoff"
    labels = w.github()["labels"]
    assert "needs-human" in labels and "babysitting" not in labels, labels
    assert holder(w) == ["s2"]
    assert "taken over" in p.output.lower()
