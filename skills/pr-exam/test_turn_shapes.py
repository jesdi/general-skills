"""`context` holds each turn kind to its documented shape (design.md, turn.json) and, after submit,
gives a question turn the submitted answer in place of the draft (spec requirement 2)."""
import pytest

from test_context import context, graded_turn, ok, refused, tail, tip


def test_submitted_answer_replaces_the_draft(tmp_path):
    p = tail(ok(context(tmp_path, graded_turn(draft="NEW DRAFT", submitted="OLD SUBMISSION"))))
    assert "OLD SUBMISSION" in p and "NEW DRAFT" not in p and "draft" not in p.lower(), p


@pytest.mark.parametrize("turn, words", [
    ({**tip(1, ""), "submitted": "answer", "points": 0}, ("submitted", "tip turn")),
    ({**tip(1, ""), "message": "hi"}, ("message", "tip turn")),
    ({**tip(2, ""), "tips": "previous tip"}, ("tips", "list of strings")),
    ({**tip(2, ""), "tips": [1]}, ("tips", "list of strings")),
    ({**tip(1, ""), "thread": []}, ("thread", "tip turn")),
    ({"kind": "question", "question": "q2", "tips": [], "message": "m"}, ("tips", "question turn")),
    ({"kind": "question", "question": "q2", "thread": ["hi"], "message": "m"}, ("thread", "role")),
    ({"kind": "question", "question": "q2", "thread": [{"role": "user"}], "message": "m"}, ("thread", "role")),
    ({"kind": "question", "question": "q2", "message": 3}, ("message", "string")),
    (graded_turn(points="0"), ("points", "number")),
    (graded_turn(feedback=["f"]), ("feedback", "string")),
    (graded_turn(submitted=None), ("submitted", "string")),
    ({"kind": "general", "question": "q2", "message": "m"}, ("question", "general")),
    ({"kind": "general", "thread": {}, "message": "m"}, ("thread", "role")),
])
def test_turn_outside_its_shape_is_refused(tmp_path, turn, words):
    refused(context(tmp_path, turn), *words)


def test_turn_that_is_not_an_object_is_refused(tmp_path):
    refused(context(tmp_path, ["tip"]), "turn kind")
