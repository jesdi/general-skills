"""The fake gh records comment edits like GitHub, so tests never inject edit metadata by hand.

Seam: `babysit.py owner-comments` and `babysit.py lease acquire|renew`, with fixtures/gh.
"""
import json
import subprocess

from test_babysit import MIN, T, World, iso
from test_owner_comments import read

EDITS = ('query { repository(owner: "jesdi", name: "demo") { pullRequest(number: 58) {'
         ' comments(first: 100) { nodes { databaseId editor { login } lastEditedAt } } } } }')


def gh_as(w, login, *args):
    st = w.github()
    st["user"] = login
    w.state.write_text(json.dumps(st))
    p = subprocess.run(["gh", *args], env=w.env, text=True, capture_output=True)
    assert p.returncode == 0, p.stdout + p.stderr
    st = w.github()
    st["user"] = "jesdi"
    w.state.write_text(json.dumps(st))
    return p.stdout


def edit_as(w, login, cid, body):
    gh_as(w, login, "api", "-X", "PATCH", f"repos/jesdi/demo/issues/comments/{cid}", "-f", f"body={body}")


def graphql_comments(w):
    out = gh_as(w, "jesdi", "api", "graphql", "-f", f"query={EDITS}")
    return {n["databaseId"]: n for n in
            json.loads(out)["data"]["repository"]["pullRequest"]["comments"]["nodes"]}


def test_owner_comment_patched_by_alice_through_the_fake_hands_off(tmp_path):
    w = World(tmp_path)
    gh_as(w, "jesdi", "api", "-X", "POST", "repos/jesdi/demo/issues/58/comments",
          "-f", "body=@babysitter merge a1b2c3d")
    cid = w.github()["comments"][-1]["id"]
    st = w.github()
    st["commits"] = [{"sha": "a1b2c3d", "date": iso(T - 5 * MIN)}]
    w.state.write_text(json.dumps(st))
    w.set_now(T + 5 * MIN)
    edit_as(w, "alice", cid, "@babysitter merge a1b2c3d")
    w.log.write_text("")
    out = read(w)
    assert out["handoff"] == "owner comment edited by alice"
    assert out["instructions"] == []


def test_lease_renewed_through_the_cli_records_editor_and_last_edited_at(tmp_path):
    w = World(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    lease_id = w.github()["comments"][-1]["id"]
    w.set_now(T + 10 * MIN)
    assert w.run("renew", "s1").returncode == 0
    node = graphql_comments(w)[lease_id]
    assert node["editor"] == {"login": "jesdi"}
    assert node["lastEditedAt"] == iso(T + 10 * MIN)


def test_renewed_lease_is_no_instruction_and_does_not_hide_a_later_one(tmp_path):
    w = World(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    w.set_now(T + 10 * MIN)
    assert w.run("renew", "s1").returncode == 0
    gh_as(w, "jesdi", "api", "-X", "POST", "repos/jesdi/demo/issues/58/comments",
          "-f", "body=@babysitter add a test")
    new_id = w.github()["comments"][-1]["id"]
    w.log.write_text("")
    out = read(w)
    assert out["handoff"] is None
    assert [i["id"] for i in out["instructions"]] == [new_id]


def test_lease_comment_edited_into_an_instruction_by_the_owner_is_not_one(tmp_path):
    w = World(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    lease_id = w.github()["comments"][-1]["id"]
    w.set_now(T + 10 * MIN)
    assert w.run("renew", "s1").returncode == 0
    edit_as(w, "jesdi", lease_id, "@babysitter merge a1b2c3d")
    w.log.write_text("")
    out = read(w)
    assert out["handoff"] is None
    assert out["instructions"] == []
