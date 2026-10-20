"""CLI regressions for terminal label reconciliation, using the fake GitHub."""
import pytest

from test_babysit import SCRIPT
from test_handoff_takeover import takeover_before_label_write
from test_merge_and_handoff import world
from test_merge_session_safety import handoff


def test_sessionless_handoff_repairs_a_takeover(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    takeover_before_label_write(w)
    p = handoff(w)
    assert w.github().get("taken")
    assert set(w.github()["labels"]) == {"babysitting"}, p.output
    assert p.returncode != 0 and "taken over" in p.output.lower()


def test_losing_pending_claim_does_not_hide_holders_handoff(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w, finish_as_s2="paused")
    wrapper = tmp_path / "bin" / "gh"
    code = wrapper.read_text()
    # A delayed claimant publishes after s2's handoff, loses, and ends its
    # pending claim. It never owned the PR, so it cannot supersede that handoff.
    code = code.replace('rc = subprocess.run', '''st = json.loads(state.read_text())
if st.get("s2done") and not st.get("loser"):
    st["loser"] = True
    cid = st["next_id"]
    st["next_id"] += 1
    st["comments"].append({"id": cid, "user": {"login": "jesdi"},
        "created_at": st["now"], "updated_at": st["now"],
        "body": "<!-- babysit-pr lease: s3 third 2099-01-01T00:00:00Z -->\\nended\\n<!-- babysit-pr claim: pending -->\\nrounds: 0\\n"})
    state.write_text(json.dumps(st))
rc = subprocess.run''')
    wrapper.write_text(code)
    p = handoff(w, "--session", "s1")
    assert w.github().get("loser")
    assert set(w.github()["labels"]) == {"needs-human"}, p.output
    assert "taken over" in p.output.lower()


@pytest.mark.parametrize("failure", ["once", "persistent", "already-absent"])
def test_repair_handles_failed_label_removal(tmp_path, failure):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w)
    wrapper = tmp_path / "bin" / "gh"
    code = wrapper.read_text().replace('rc = subprocess.run', '''st = json.loads(state.read_text())
if st.get("taken") and (FAILURE == "persistent" or not st.get("delete_failed")) and argv[:3] == ["api", "-X", "DELETE"] and argv[3].endswith("/labels/needs-human"):
    st["delete_failed"] = True
    if FAILURE == "already-absent":
        st["labels"].remove("needs-human")
    state.write_text(json.dumps(st))
    print("HTTP 500", file=sys.stderr)
    sys.exit(1)
rc = subprocess.run'''.replace("FAILURE", repr(failure)))
    wrapper.write_text(code)
    p = handoff(w, "--session", "s1")
    assert w.github().get("delete_failed")
    if failure == "persistent":
        assert p.returncode != 0 and "reconciliation incomplete" in p.output
        assert "labels restored" not in p.output
        return
    assert set(w.github()["labels"]) == {"babysitting"}, p.output
    assert "taken over" in p.output.lower()


@pytest.mark.parametrize("finish_at", ["before-write", "after-holder-read", "during-repair"])
def test_completed_successor_merge_clears_stale_handoff(tmp_path, finish_at):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "s1").returncode == 0
    takeover_before_label_write(w)
    wrapper = tmp_path / "bin" / "gh"
    # Run the successor's real merge either before s1 writes its stale labels,
    # or while s1 is already restoring s2's active labels.
    trigger = ('st.get("taken") and argv[:3] == ["api", "-X", "DELETE"] '
               'and argv[3].endswith("/labels/needs-human")')
    if finish_at == "before-write":
        trigger = 'st.get("taking") and not st.get("taken")'
    elif finish_at == "after-holder-read":
        trigger = ('st.get("taken") and argv[:2] == ["api", "--paginate"] '
                   'and argv[2].endswith("/comments")')
    merge_s2 = '''
    if TRIGGER and not st.get("s2merged"):
        st["s2merged"] = True
        state.write_text(json.dumps(st))
        subprocess.run([sys.executable, SCRIPT, "merge", "--repo", "jesdi/demo", "--pr", "58",
                        "--sha", "a1b2c3d", "--body", BODY, "--judge", "agree", "--session", "s2"],
                       check=True, stdout=subprocess.DEVNULL)
        st = json.loads(state.read_text())
'''.replace("TRIGGER", trigger)
    merge_s2 = merge_s2.replace("SCRIPT", repr(str(SCRIPT))).replace("BODY", repr(str(tmp_path / "body.md")))
    code = wrapper.read_text()
    if finish_at == "before-write":
        code = code.replace('    st["taken"] = True', merge_s2 + '    st["taken"] = True')
    else:
        hook = 'sys.exit(rc)' if finish_at == "after-holder-read" else 'rc = subprocess.run'
        code = code.replace(hook, 'st = json.loads(state.read_text())\n'
                            + "\n".join(line[4:] for line in merge_s2.splitlines()) + '\n' + hook)
    wrapper.write_text(code)
    p = handoff(w, "--session", "s1")
    assert w.github().get("s2merged"), p.output
    assert len(w.github()["merges"]) == 1
    assert not w.active_leases()
    assert set(w.github()["labels"]) == set(), p.output
    assert p.returncode != 0 and "taken over" in p.output.lower()


def test_old_completed_holder_is_not_a_takeover_of_sessionless_handoff(tmp_path):
    w = world(tmp_path, repo_labels=["babysitting", "needs-human"])
    assert w.run("acquire", "previous").returncode == 0
    assert w.run("release", "previous").returncode == 0
    p = handoff(w)
    assert p.returncode == 0, p.output
    assert set(w.github()["labels"]) == {"needs-human"}
