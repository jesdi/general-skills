"""Merge refuses when GitHub's base is not the commit the local gate read the policy from."""
from test_merge_and_handoff import merge, world


def test_remote_base_ahead_of_the_local_ref_merges_nothing(tmp_path):
    w = world(tmp_path, base_sha="f" * 40)  # origin/main locally is another commit
    p = merge(w, tmp_path, "a1b2c3d", "--judge", "agree")
    assert p.returncode != 0
    assert "gate again" in p.output
    assert w.github().get("merges", []) == []
