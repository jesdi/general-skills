"""Acceptance tests: `pr_exam.py validate` and `pr_exam.py public`, run as subprocesses.

Interface: `validate --request <request.json> <exam.json>` exits 0 and prints a summary for a valid
exam; for an invalid one it exits 1 with every broken rule, one per line, on stderr.
`public <exam.json>` prints the served projection as JSON.
Spec specs/pr-exam: requirements 2-5; design.md "Data model".
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "pr_exam.py"
SHA = "a" * 40
DIFF = "diff --git a/lease.py b/lease.py\n+def take_lease(): ...\n+def recheck_head(): ...\n"
KINDS = ["why", "concept", "architecture", "risk", "behaviour"]


def cid(kind, n=0):
    return f"{kind}-{n}"


def question(qid, concept, kind):
    q = {"id": qid, "concept": concept, "type": "text" if kind == "text" else "choice", "prompt": f"Prompt {qid}?"}
    if kind == "choice":
        q["options"] = ["alpha beta", "gamma delta", "zeta eta"]
    return q


def key_for(q):
    entry = {"explanation": "Because.", "hunk": "+def take_lease(): ..."}
    if q["type"] == "text":
        entry["rubric"] = {"pass": "Names the lease.", "partial": "Mentions locking."}
    else:
        entry["answer"] = 1
    return entry


def make_exam(concepts, questions, **over):
    """concepts: [(slug, kind)]; questions: [(slug, 'text'|'choice')] in order."""
    qs = [question(f"q{i + 1}", slug, t) for i, (slug, t) in enumerate(questions)]
    exam = {
        "version": 1, "mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
        "concepts": [{"id": s, "title": s.title(), "kind": k} for s, k in concepts],
        "questions": qs,
        "key": {q["id"]: key_for(q) for q in qs},
    }
    exam.update(over)
    return exam


def five():
    concepts = [("c-why", "why"), ("c-con", "concept"), ("c-arch", "architecture"),
                ("c-risk", "risk"), ("c-beh", "behaviour")]
    return make_exam(concepts, [("c-why", "text"), ("c-con", "text"), ("c-arch", "choice"),
                                ("c-risk", "choice"), ("c-beh", "choice")])


def run(tmp_path, exam, request_over=None, command="validate"):
    (tmp_path / "records").mkdir(exist_ok=True)
    (tmp_path / "diff.patch").write_text(DIFF)
    request = {"mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
               "diff": str(tmp_path / "diff.patch"), "body": str(tmp_path / "body.md"), "glossary": None,
               "records": str(tmp_path / "records"), "today": "2026-11-18",
               "only_concepts": [], "prior_concepts": [], "prior_prompts": [], "out": str(tmp_path / "exam.json")}
    request.update(request_over or {})
    (tmp_path / "body.md").write_text("body")
    (tmp_path / "request.json").write_text(json.dumps(request))
    (tmp_path / "exam.json").write_text(json.dumps(exam))
    args = ["validate", "--request", str(tmp_path / "request.json")] if command == "validate" else ["public"]
    return subprocess.run([sys.executable, str(SCRIPT), *args, str(tmp_path / "exam.json")],
                          capture_output=True, text=True)


def refused(result, *words):
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    for w in words:
        assert w in result.stderr, (w, result.stderr)


def test_well_formed_five_question_exam_is_valid(tmp_path):
    r = run(tmp_path, five())
    assert r.returncode == 0, r.stderr or r.stdout
    assert r.stdout.strip()


@pytest.mark.parametrize("concepts", [[("only", "behaviour")], [("no-concept-fallback", "behaviour")]])
def test_trivial_pr_validates_only_with_exactly_one_question(tmp_path, concepts):
    # the second fixture is the design's fallback for a PR with no concept: one listed behaviour concept
    slug = concepts[0][0]
    assert run(tmp_path, make_exam(concepts, [(slug, "choice")])).returncode == 0
    refused(run(tmp_path, make_exam(concepts, [])))
    refused(run(tmp_path, with_extra_question(make_exam(concepts, [(slug, "choice")]), "ghost")))


def test_zero_concepts_and_zero_questions_is_refused(tmp_path):
    refused(run(tmp_path, make_exam([], [])))


def twelve():
    kinds = ["why"] * 2 + ["concept"] * 2 + ["architecture"] * 2 + ["risk"] * 2 + ["behaviour"] * 4
    return [(f"k{i}", k) for i, k in enumerate(kinds)]


def types_for(concepts, slugs):
    kind = dict(concepts)
    return [(s, "text" if kind[s] == "why" else "choice") for s in slugs]


def test_twelve_concepts_validate_with_eight_questions_by_priority(tmp_path):
    cs = twelve()
    assert run(tmp_path, make_exam(cs, types_for(cs, [s for s, _ in cs[:8]]))).returncode == 0


def test_twelve_concepts_with_nine_questions_is_refused(tmp_path):
    cs = twelve()
    refused(run(tmp_path, make_exam(cs, types_for(cs, [s for s, _ in cs[:9]]))))


def test_behaviour_asked_while_a_why_is_dropped_is_refused(tmp_path):
    cs = twelve()
    asked = ["k0"] + [s for s, _ in cs[2:8]] + ["k8"]
    refused(run(tmp_path, make_exam(cs, types_for(cs, asked))))


def test_choice_options_with_different_word_counts_name_the_question(tmp_path):
    e = five()
    e["questions"][2]["options"] = ["alpha beta", "gamma", "zeta eta"]
    refused(run(tmp_path, e), "q3")


def with_extra_question(e, concept):
    q = question("q6", concept, "choice")
    e["questions"].append(q)
    e["key"]["q6"] = key_for(q)
    return e


def mut_dup_concept(e): return with_extra_question(e, "c-why")
def mut_unlisted(e): return with_extra_question(e, "ghost")
def mut_field(e): e["questions"][0]["hint"] = "x"
def mut_key_missing(e): del e["key"]["q2"]
def mut_key_extra(e): e["key"]["q9"] = copy.deepcopy(e["key"]["q3"])
def mut_answer(e): e["key"]["q3"]["answer"] = 7
def mut_pass(e): e["key"]["q1"]["rubric"]["pass"] = ""
def mut_partial(e): e["key"]["q1"]["rubric"]["partial"] = ""
def mut_hunk(e): e["key"]["q3"]["hunk"] = "+not in the diff"
def mut_repo(e): e["repo"] = "jesdi/other"
def mut_pr(e): e["pr"] = 241
def mut_sha(e): e["head_sha"] = "b" * 40
def mut_version(e): e["version"] = 2


@pytest.mark.parametrize("mutate,words", [
    (mut_dup_concept, ["c-why"]), (mut_unlisted, ["ghost"]), (mut_field, ["hint"]),
    (mut_key_missing, ["q2", "key"]), (mut_key_extra, ["q9", "key"]), (mut_answer, ["answer", "q3"]),
    (mut_pass, ["rubric", "pass"]), (mut_partial, ["rubric", "partial"]), (mut_hunk, ["hunk", "q3"]),
    (mut_repo, ["repo"]), (mut_pr, ["pr"]), (mut_sha, ["head_sha"]), (mut_version, ["version"]),
])
def test_each_broken_rule_is_refused_with_its_own_message(tmp_path, mutate, words):
    e = five()
    e = mutate(e) or e
    refused(run(tmp_path, e), *words)


def test_two_questions_with_one_type_is_refused(tmp_path):
    cs = [("c-why", "why"), ("c-beh", "behaviour")]
    refused(run(tmp_path, make_exam(cs, [("c-why", "text"), ("c-beh", "text")])))


def test_three_questions_without_a_why_text_question_is_refused(tmp_path):
    cs = [("c-why", "why"), ("c-con", "concept"), ("c-beh", "behaviour")]
    refused(run(tmp_path, make_exam(cs, [("c-why", "choice"), ("c-con", "text"), ("c-beh", "choice")])))


def test_an_exam_breaking_three_rules_prints_all_three_errors(tmp_path):
    e = five()
    e["version"] = 2
    e["repo"] = "jesdi/other"
    e["head_sha"] = "b" * 40
    refused(run(tmp_path, e), "version", "repo", "head_sha")
    assert len([ln for ln in run(tmp_path, e).stderr.splitlines() if ln.strip()]) >= 3


def test_public_prints_only_what_the_page_may_see(tmp_path):
    e = five()
    r = run(tmp_path, e, command="public")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {
        "version": 1, "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
        "questions": [{k: q[k] for k in ("id", "type", "prompt", "options") if k in q} for q in e["questions"]],
    }
    for word in ("key", "concepts", "answer", "rubric", "explanation", "hunk"):
        assert f'"{word}"' not in r.stdout
