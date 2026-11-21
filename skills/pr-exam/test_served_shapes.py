"""Regression tests: nothing answer-like rides on a served field, and concept ids are slugs.

`public` and `validate` refuse an exam whose served fields (`version`, `repo`, `pr`, `head_sha`,
question `type`) are not their plain shapes, and `validate` refuses a concept id that is not a slug.
Spec specs/pr-exam: requirement 2; design.md "exam.json" (concept `id`: slug).
"""
import pytest

from test_exam_format import five, refused, run

SECRET = {"answer": 1, "rubric": {"pass": "x", "partial": "y"}}


@pytest.mark.parametrize("command", ["validate", "public"])
def test_question_type_holding_an_object_is_refused(tmp_path, command):
    exam = five()
    exam["questions"][2]["type"] = SECRET
    r = run(tmp_path, exam, command=command)
    refused(r, "q3", "type")
    assert "rubric" not in r.stdout


@pytest.mark.parametrize("field", ["repo", "pr", "head_sha", "version"])
@pytest.mark.parametrize("command", ["validate", "public"])
def test_envelope_field_holding_an_object_is_refused_even_when_the_request_mirrors_it(tmp_path, field, command):
    exam = five()
    exam[field] = SECRET
    r = run(tmp_path, exam, {field: SECRET}, command=command)
    refused(r, field)
    assert "rubric" not in r.stdout


@pytest.mark.parametrize("bad", ["../escape", "Merge-Lease", "two\nlines", "", 7])
def test_concept_id_that_is_not_a_slug_is_refused(tmp_path, bad):
    exam = five()
    exam["concepts"][4]["id"] = bad
    exam["questions"][4]["concept"] = bad
    refused(run(tmp_path, exam), "slug")
