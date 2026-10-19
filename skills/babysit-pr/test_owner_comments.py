"""Acceptance tests for `babysit.py owner-comments --repo R --pr N`.

Interface under test:
  babysit.py owner-comments --repo <owner/name> --pr <number>
Exit 0; stdout is one JSON object:
  {"handoff": <reason string or null>,
   "instructions": [{"id": <comment id>, "text": <comment body>, "approved_sha": <sha or null>}]}
`handoff` is set when a review, or a comment that is not a trusted owner comment, needs a human.
An instruction is a comment that starts with `@babysitter`, whose author login is the owner (the
login of `gh api user`), that no other account edited, and that was created after the last
handoff comment (`## 🧑 Needs human: ...`). `approved_sha` is set for `@babysitter merge`: the
commit SHA the comment names, given as a hex prefix that must resolve to exactly one of the PR's
commits (otherwise null, with a handoff); it is never inferred from commit dates.

`gh` is the fake in fixtures/gh (REST and GraphQL `editor` / `lastEditedAt`).
"""
import json
import subprocess
import sys
from datetime import timedelta

from test_babysit import SCRIPT, MIN, T, World, iso


def comment(cid, login, body, at=T, editor=None):
    c = {"id": cid, "user": {"login": login}, "body": body,
         "created_at": iso(at), "updated_at": iso(at)}
    if editor:
        c.update(editor={"login": editor}, last_edited_at=iso(at + MIN))
    return c


def read(w):
    p = subprocess.run([sys.executable, str(SCRIPT), "owner-comments", "--repo", "jesdi/demo",
                        "--pr", "58"], cwd=w.project, env=w.env, text=True, capture_output=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert w.unsupported() == []
    assert w.writes() == []
    return json.loads(p.stdout)


def test_review_comment_from_another_login_hands_off(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "alice", "why this change?")])
    out = read(w)
    assert out["handoff"] == "review comment from alice"
    assert out["instructions"] == []


def test_changes_requested_without_a_comment_hands_off(tmp_path):
    w = World(tmp_path)
    st = w.github()
    st["reviews"] = [{"id": 5, "user": {"login": "alice"}, "state": "CHANGES_REQUESTED",
                      "body": "", "submitted_at": iso(T)}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert out["instructions"] == []


def test_owner_instruction_is_returned_with_its_text(tmp_path):
    text = "@babysitter rename `parse_x` to `parse_row`"
    w = World(tmp_path, comments=[comment(1001, "jesdi", text)])
    out = read(w)
    assert out["handoff"] is None
    assert len(out["instructions"]) == 1
    assert out["instructions"][0]["text"] == text
    assert out["instructions"][0]["approved_sha"] is None


def test_owner_comment_edited_by_another_account_hands_off(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge", editor="alice")])
    out = read(w)
    assert out["handoff"] == "owner comment edited by alice"
    assert out["instructions"] == []


def test_owner_comment_last_edited_by_the_owner_still_hands_off(tmp_path):
    # an earlier edit by another account is hidden when the owner edits last
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge", editor="jesdi")])
    out = read(w)
    assert out["handoff"] == "owner comment edited, edit history unverifiable"
    assert out["instructions"] == []


def test_comment_from_another_login_naming_the_owner_hands_off(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "alice", "@babysitter merge (approved by jesdi)")])
    out = read(w)
    assert out["handoff"] == "review comment from alice"
    assert out["instructions"] == []


def test_merge_approval_names_the_sha_in_the_comment(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge a1b2c3d", at=T)])
    st = w.github()
    st["commits"] = [{"sha": "a1b2c3d", "date": iso(T - 5 * MIN)}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"] is None
    assert [i["approved_sha"] for i in out["instructions"]] == ["a1b2c3d"]


def test_merge_approval_is_not_inferred_from_commit_dates_once_the_head_moved_on(tmp_path):
    # Commit dates are not push times: with a later commit on the PR, the head at the time of
    # the comment is unknowable, so no commit may be named as approved (fail closed).
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge", at=T)])
    st = w.github()
    st["commits"] = [{"sha": "9f8e7d6", "date": iso(T - 30 * MIN)},
                     {"sha": "a1b2c3d", "date": iso(T - 5 * MIN)},
                     {"sha": "e4f5a6b", "date": iso(T + 5 * MIN)}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert [i for i in out["instructions"] if i["approved_sha"]] == []


def test_merge_approval_is_not_transferred_to_a_later_pushed_commit_dated_before_the_comment(tmp_path):
    # Alice builds descendant B locally before the comment but pushes it after: B's commit date
    # precedes the approval, yet the owner approved A. Dates cannot show which commit was the head
    # at comment time, so with more than one commit no commit may be named as approved.
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge", at=T)])
    st = w.github()
    st["commits"] = [{"sha": "a1b2c3d", "date": iso(T - 10 * MIN)},
                     {"sha": "e4f5a6b", "date": iso(T - 2 * MIN)}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert [i for i in out["instructions"] if i["approved_sha"]] == []


def test_only_owner_comments_after_the_last_handoff_are_instructions(tmp_path):
    lease = "<!-- babysit-pr lease: s1 mac 2020-01-01T11:00:00Z -->\nrounds: 0\n@babysitter merge"
    old_handoff = "## 🧑 Needs human: door one-way\n\nnext: reply `@babysitter merge`\n"
    last_handoff = "## 🧑 Needs human: 5 fix rounds\n\nnext: reply\n@babysitter merge\n"
    w = World(tmp_path, comments=[
        comment(1001, "jesdi", "@babysitter old one", at=T - 60 * MIN),
        comment(1002, "jesdi", old_handoff, at=T - 50 * MIN),
        comment(1003, "jesdi", "@babysitter before the last handoff", at=T - 40 * MIN),
        comment(1004, "jesdi", last_handoff, at=T - 30 * MIN),
        comment(1005, "jesdi", lease, at=T - 20 * MIN, editor="jesdi"),  # acquisition PATCHes the lease to confirm its claim
        comment(1006, "jesdi", "@babysitter add a test for the empty case", at=T - 10 * MIN)])
    out = read(w)
    assert out["handoff"] is None
    assert [i["id"] for i in out["instructions"]] == [1006]


def test_unresolved_review_thread_hands_off(tmp_path):
    w = World(tmp_path)
    st = w.github()
    st["threads"] = [{"user": {"login": "alice"}, "resolved": False}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"] == "review comment from alice"
    assert out["instructions"] == []


def test_merge_approval_is_not_given_to_a_sole_commit_dated_after_the_comment(tmp_path):
    # A sole commit created after the approval cannot have been the head the owner approved.
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge", at=T)])
    st = w.github()
    st["commits"] = [{"sha": "e4f5a6b", "date": iso(T + 5 * MIN)}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert [i for i in out["instructions"] if i["approved_sha"]] == []


def test_resolved_review_thread_older_than_the_last_handoff_does_not_hand_off(tmp_path):
    handoff = "## 🧑 Needs human: 5 fix rounds\n\nnext: reply\n"
    w = World(tmp_path, comments=[comment(1001, "jesdi", handoff, at=T)])
    st = w.github()
    st["threads"] = [{"user": {"login": "alice"}, "resolved": True, "created_at": iso(T - 10 * MIN)}]
    w.state.write_text(json.dumps(st))
    assert read(w)["handoff"] is None


def test_resolved_review_thread_newer_than_the_last_handoff_hands_off(tmp_path):
    handoff = "## 🧑 Needs human: 5 fix rounds\n\nnext: reply\n"
    w = World(tmp_path, comments=[comment(1001, "jesdi", handoff, at=T)])
    st = w.github()
    st["threads"] = [{"user": {"login": "alice"}, "resolved": True, "created_at": iso(T + 10 * MIN)}]
    w.state.write_text(json.dumps(st))
    assert read(w)["handoff"] == "review comment from alice"


def test_unread_comments_of_a_review_thread_hand_off(tmp_path):
    # a resolved thread whose comments run past the page read: the unread ones may be alice's
    w = World(tmp_path)
    st = w.github()
    st["threads"] = [{"user": {"login": "jesdi"}, "resolved": True, "more_comments": True}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"]
    assert out["instructions"] == []


def test_a_review_comment_on_a_later_page_suppresses_instructions(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter rename it"),
                                  comment(1002, "alice", "why this change?")])
    st = w.github()
    st["page_size"] = 1
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"] == "review comment from alice"
    assert out["instructions"] == []


def test_merge_approval_does_not_transfer_to_a_replacement_sole_commit(tmp_path):
    # The owner approved a1b2c3d. A force-push then leaves b7c8d9e, dated before the comment, as
    # the sole commit: a1b2c3d is no commit of the PR any more, so the comment approves nothing.
    w = World(tmp_path, comments=[comment(1001, "jesdi", "@babysitter merge a1b2c3d", at=T)])
    st = w.github()
    st["commits"] = [{"sha": "b7c8d9e", "date": iso(T - 3 * MIN)}]
    w.state.write_text(json.dumps(st))
    out = read(w)
    assert out["handoff"] == "merge approval must name a commit: @babysitter merge <sha>"
    assert [i for i in out["instructions"] if i["approved_sha"]] == []


MERGE_HANDOFF = "merge approval must name a commit: @babysitter merge <sha>"
A = "a1b2c3d" + "0" * 33
B = "a1b2c3d" + "1" * 33


def merge_read(tmp_path, body, shas):
    w = World(tmp_path, comments=[comment(1001, "jesdi", body, at=T)])
    st = w.github()
    st["commits"] = [{"sha": s, "date": iso(T - (10 - i) * MIN)} for i, s in enumerate(shas)]
    w.state.write_text(json.dumps(st))
    return read(w)


def test_merge_approval_by_a_unique_prefix_names_the_full_sha(tmp_path):
    out = merge_read(tmp_path, "@babysitter merge a1b2c3d", [A, "b7c8d9e" + "2" * 33])
    assert out["handoff"] is None
    assert [i["approved_sha"] for i in out["instructions"]] == [A]


def test_merge_approval_by_the_full_sha_names_it(tmp_path):
    out = merge_read(tmp_path, f"@babysitter merge {A}", [A, B])
    assert out["handoff"] is None
    assert [i["approved_sha"] for i in out["instructions"]] == [A]


def test_merge_approval_by_an_ambiguous_prefix_hands_off(tmp_path):
    out = merge_read(tmp_path, "@babysitter merge a1b2c3d", [A, B])
    assert out["handoff"] == MERGE_HANDOFF
    assert out["instructions"] == []


def test_merge_approval_by_a_too_short_prefix_hands_off(tmp_path):
    out = merge_read(tmp_path, "@babysitter merge a1b2c3", [A])
    assert out["handoff"] == MERGE_HANDOFF
    assert out["instructions"] == []


def test_merge_approval_by_a_non_hex_token_hands_off(tmp_path):
    out = merge_read(tmp_path, "@babysitter merge zz12345", ["zz12345" + "0" * 33])
    assert out["handoff"] == MERGE_HANDOFF
    assert out["instructions"] == []


def test_merge_approval_without_a_sha_hands_off(tmp_path):
    out = merge_read(tmp_path, "@babysitter merge", [A])
    assert out["handoff"] == MERGE_HANDOFF
    assert out["instructions"] == []


def test_explicit_approval_stays_valid_when_a_later_commit_becomes_the_head(tmp_path):
    out = merge_read(tmp_path, "@babysitter merge a1b2c3d", [A, "e4f5a6b" + "3" * 33])
    assert out["handoff"] is None
    assert [i["approved_sha"] for i in out["instructions"]] == [A]


HANDOFF_BODY = "## 🧑 Needs human: 5 fix rounds\n\nnext: reply\n"


def test_handoff_comment_edited_by_another_account_hands_off_and_hides_earlier_instructions(tmp_path):
    w = World(tmp_path, comments=[
        comment(1001, "jesdi", "@babysitter old one", at=T - 20 * MIN),
        comment(1002, "jesdi", HANDOFF_BODY, at=T - 10 * MIN, editor="alice")])
    out = read(w)
    assert out["handoff"]
    assert out["instructions"] == []


def test_review_comment_submitted_exactly_at_the_handoff_hands_off(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", HANDOFF_BODY, at=T)])
    st = w.github()
    st["reviews"] = [{"id": 5, "user": {"login": "alice"}, "state": "COMMENTED",
                      "body": "why this change?", "submitted_at": iso(T)}]
    w.state.write_text(json.dumps(st))
    assert read(w)["handoff"] == "review comment from alice"


def test_resolved_review_thread_created_exactly_at_the_handoff_hands_off(tmp_path):
    w = World(tmp_path, comments=[comment(1001, "jesdi", HANDOFF_BODY, at=T)])
    st = w.github()
    st["threads"] = [{"user": {"login": "alice"}, "resolved": True, "created_at": iso(T)}]
    w.state.write_text(json.dumps(st))
    assert read(w)["handoff"] == "review comment from alice"


def test_instruction_after_a_lease_acquired_and_renewed_through_the_cli_is_returned(tmp_path):
    w = World(tmp_path)
    assert w.run("acquire", "s1").returncode == 0
    w.set_now(T + 10 * MIN)
    assert w.run("renew", "s1").returncode == 0
    st = w.github()
    for c in st["comments"]:  # GitHub records an editor on every body edit; the fake gh does not
        c["editor"] = {"login": "jesdi"}; c["last_edited_at"] = c["updated_at"]
    st["comments"].append(comment(st["next_id"], "jesdi", "@babysitter add a test", at=T + 11 * MIN))
    w.state.write_text(json.dumps(st))
    w.log.write_text("")
    out = read(w)
    assert out["handoff"] is None
    assert [i["id"] for i in out["instructions"]] == [st["next_id"]]
