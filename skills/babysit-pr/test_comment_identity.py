"""CLI regressions for lease identity and hidden handoff boundaries."""
import json

from test_babysit import MIN, T, World
from test_owner_comments import HANDOFF_BODY, comment, read


def test_resume_instruction_before_new_confirmed_lease_is_returned(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", HANDOFF_BODY),
                                  comment(1002, "jesdi", "@babysitter add a test", at=T + MIN)])
    w.set_now(T + 2 * MIN)
    assert w.run("acquire", "s1").returncode == 0
    st = w.github()
    c = st["comments"][-1]
    c.update(editor={"login": "jesdi"}, last_edited_at=c["updated_at"])
    w.state.write_text(json.dumps(st))
    w.log.write_text("")
    assert [i["id"] for i in read(w)["instructions"]] == [1002]


def test_hidden_handoff_disguised_as_lease_cannot_replay_an_instruction(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter add a test"),
                                  comment(1002, "jesdi", HANDOFF_BODY, at=T + MIN)])
    st = w.github()
    c = st["comments"][-1]
    c.update(body="<!-- babysit-pr lease: s1 mac 2020-01-01T11:00:00Z -->\nrounds: 0",
             editor={"login": "jesdi"}, last_edited_at=c["updated_at"])
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert out["instructions"] == []


def test_created_lease_cannot_become_an_instruction_after_an_edit(tmp_path):
    w = World(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    st = w.github()
    c = st["comments"][-1]
    c.update(body="@babysitter add a test", editor={"login": "jesdi"},
             last_edited_at=c["updated_at"])
    w.state.write_text(json.dumps(st))
    w.log.write_text("")
    assert read(w)["instructions"] == []


def test_known_lease_disguised_as_handoff_does_not_hide_a_new_instruction(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter add a test")])
    assert w.run("acquire", "s1").returncode == 0
    st = w.github()
    c = st["comments"][-1]
    c.update(body=HANDOFF_BODY, editor={"login": "jesdi"}, last_edited_at=c["updated_at"])
    w.state.write_text(json.dumps(st))
    w.log.write_text("")
    out = read(w)
    assert out["handoff"] is None
    assert [i["id"] for i in out["instructions"]] == [1001]


def test_observed_handoff_disguised_as_lease_keeps_its_identity(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", HANDOFF_BODY)])
    assert read(w)["handoff"] is None
    st = w.github()
    c = st["comments"][-1]
    c.update(body="<!-- babysit-pr lease: s1 mac 2020-01-01T11:00:00Z -->\nrounds: 0",
             editor={"login": "jesdi"}, last_edited_at=c["updated_at"])
    st["comments"].append(comment(1002, "jesdi", "@babysitter add a test", at=T + MIN))
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert out["instructions"] == []
