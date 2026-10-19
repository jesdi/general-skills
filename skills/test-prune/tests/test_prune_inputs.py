"""Agent-input guards and proof rules, checked in-process: each rejection names its id."""
import json

import pytest

from prune_repo import BadInput, Fail, parse_ratchet, read_agent_json
from prune_review import candidate_problems, group_problems
from prune_strengthen import changed_tests, commit_problems, item_proof, reopened

TESTS = {"tests/t.py::a", "tests/t.py::b"}


@pytest.mark.parametrize("entry, problem", [
    ({"verdict": "drop", "reason": "r"}, "verdict 'drop'"),
    ({"verdict": "delete", "into": "tests/t.py::a", "reason": "r"}, "not another collected test id"),
    ({"verdict": "strengthen", "assertion": " ", "reason": "r"}, "names no assertion"),
    ({"verdict": "delete", "into": "tests/t.py::b"}, "no reason"),
])
def test_candidate_problems_name_the_candidate(entry, problem):
    (message,) = candidate_problems("tests/t.py::a", entry, TESTS)
    assert message.startswith("candidate tests/t.py::a:") and problem in message


def test_a_kept_candidate_needs_no_reason():
    assert candidate_problems("tests/t.py::a", {"verdict": "keep"}, TESTS) == []


GROUP = {"group": "m.f", "mutant_ids": ["x_f__mutmut_1", "x_f__mutmut_2"]}


@pytest.mark.parametrize("entry, problem", [
    ({"verdict": "delete", "reason": "r"}, "verdict 'delete'"),
    ({"verdict": "strengthen", "mutant_ids": [], "reason": "r"}, "non-empty list"),
    ({"verdict": "equivalent", "mutant_ids": ["x_f__mutmut_9"], "reason": "r"}, "x_f__mutmut_9 is not one"),
    ({"verdict": "strengthen"}, "no reason"),
])
def test_group_problems_name_the_group(entry, problem):
    (message,) = group_problems(GROUP, entry)
    assert message.startswith("weak spot m.f:") and problem in message


@pytest.mark.parametrize("content", ["not json", "[]", '{"reviews": {}}', '{"reviews": [1]}'])
def test_read_agent_json_rejects_malformed_files(tmp_path, content):
    path = tmp_path / "reviews.json"
    path.write_text(content)
    with pytest.raises(BadInput, match="reviews"):
        read_agent_json(str(path), "reviews")


def test_read_agent_json_names_a_missing_file(tmp_path):
    with pytest.raises(BadInput, match="cannot read"):
        read_agent_json(str(tmp_path / "nope.json"), "reviews")
    (tmp_path / "ok.json").write_text(json.dumps({"reviews": [{"id": "a"}]}))
    assert read_agent_json(str(tmp_path / "ok.json"), "reviews") == [{"id": "a"}]


def test_commit_problems_need_one_known_trailer_and_test_paths_only():
    assert commit_problems("a" * 40, ["m.f"], ["backend/tests/t.py"], {"m.f"}) == []
    trailer, outside = commit_problems("a" * 40, ["m.f", "m.g"], ["backend/services/x.py"], {"m.f"})
    assert "exactly one" in trailer and "backend/services/x.py" in outside
    assert commit_problems("a" * 40, [], [], {"m.f"})


def test_item_proof_needs_survived_on_base_and_killed_on_head_per_id():
    item = {"module": "s.py", "mutant_ids": ["m1", "m2"]}
    base = {"s.py": {"m1": 0, "m2": 33}}

    assert item_proof(base, {"s.py": {"m1": 1, "m2": 3}}, item, True)["status"] == "proven"
    assert item_proof(base, {"s.py": {"m1": 1, "m2": 0}}, item, True)["status"] == "unproven"
    assert item_proof(base, {"s.py": {"m1": 1, "m2": 3}}, item, False)["status"] == "unproven"
    assert item_proof({"s.py": {"m1": 1, "m2": 0}}, {"s.py": {"m1": 1, "m2": 1}}, item, True)["status"] == "unproven"


def test_changed_tests_lists_new_and_edited_tests():
    before = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n"
    after = "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 3\n\n\nclass TestC:\n    def test_c(self):\n        pass\n"

    assert changed_tests(before, after, "backend/tests/t.py") == ["tests/t.py::test_b", "tests/t.py::TestC::test_c"]


def test_a_ratchet_failure_without_survivor_counts_is_operational():
    with pytest.raises(Fail, match="without a survivor count"):
        parse_ratchet("  ✗ services/x.py: no mutmut results, run 'make mutation'\n", 1)
    ratchet = parse_ratchet("  ✗ services/x.py: 23 survivors > baseline 20 (score 1.00)\n", 1)
    assert ratchet["failed"] == ["services/x.py"] and ratchet["modules"]["services/x.py"] == [23, 20]


def test_an_item_without_mutant_ids_is_never_proven():
    assert item_proof({}, {}, {"module": None, "mutant_ids": []}, True)["status"] == "unproven"


def test_commits_after_an_item_closed_are_reopenings():
    history = [("a" * 40, ["m.f"], []), ("b" * 40, ["m.f"], []), ("c" * 40, ["m.f"], []), ("d" * 40, ["m.g"], [])]

    (problem,) = reopened(history, {"m.f": "b" * 40})
    assert problem.startswith("cccccccccccc: m.f was closed")
    assert reopened(history, {"m.g": "start"}) and not reopened(history, {})
