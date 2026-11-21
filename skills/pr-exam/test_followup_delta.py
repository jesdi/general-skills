"""Acceptance tests: `pr_exam.py validate` for `followup` and `delta` modes, run as subprocesses.

Interface: `validate --request <request.json> <exam.json>` with request `mode` "followup" or "delta"
(`only_concepts`, `prior_concepts`, `prior_prompts` in the request); exit 0 when valid, exit 1 with
every broken rule on stderr otherwise.
Spec specs/pr-exam: requirement 7; design.md "exam.json" (`followup`, `delta` rows).
"""
from test_exam_format import make_exam, refused, run

LEASE, RECHECK = "lease-ownership", "head-sha-recheck"
MISSED = [(LEASE, "why"), (RECHECK, "concept")]


def followup(questions, concepts=None, **over):
    return make_exam(concepts or MISSED, questions, mode="followup", **over)


def req(mode, **over):
    return {"mode": mode, **over}


def test_followup_accepts_exactly_one_question_per_missed_concept(tmp_path):
    r = req("followup", only_concepts=[LEASE, RECHECK])
    ok = followup([(LEASE, "text"), (RECHECK, "text")])
    result = run(tmp_path, ok, r)
    assert result.returncode == 0, result.stderr or result.stdout
    refused(run(tmp_path, followup([(LEASE, "text")]), r), RECHECK)
    three = followup([(LEASE, "text"), (RECHECK, "text"), ("extra", "text")], MISSED + [("extra", "behaviour")])
    refused(run(tmp_path, three, r), "extra")
    other = followup([(LEASE, "text"), ("other", "text")], MISSED + [("other", "behaviour")])
    refused(run(tmp_path, other, r), "other")


def test_followup_refuses_a_prompt_of_an_earlier_attempt(tmp_path):
    exam = followup([(LEASE, "text"), (RECHECK, "text")])
    only = [LEASE, RECHECK]
    fine = run(tmp_path, exam, req("followup", only_concepts=only, prior_prompts=["Something else?"]))
    assert fine.returncode == 0, fine.stderr or fine.stdout
    refused(run(tmp_path, exam, req("followup", only_concepts=only, prior_prompts=["Prompt q2?"])), "q2")


def test_type_mix_rule_is_off_but_options_key_and_due_rules_stay_on(tmp_path):
    fr = req("followup", only_concepts=[LEASE, RECHECK])
    ok = run(tmp_path, followup([(LEASE, "text"), (RECHECK, "text")]), fr)
    assert ok.returncode == 0, ok.stderr or ok.stdout
    dr = req("delta", prior_concepts=["old"])
    cs = [("a-one", "concept"), ("a-two", "concept"), ("a-three", "concept")]
    delta = make_exam(cs, [(s, "choice") for s, _ in cs], mode="delta")
    ok = run(tmp_path, delta, dr)
    assert ok.returncode == 0, ok.stderr or ok.stdout
    # the loosened mix does not loosen the prior-concepts rule
    refused(run(tmp_path, delta, req("delta", prior_concepts=["a-two"])), "a-two")
    # options of different word counts are still refused
    bad = followup([(LEASE, "choice"), (RECHECK, "choice")])
    bad["questions"][0]["options"] = ["alpha beta", "gamma", "zeta eta"]
    refused(run(tmp_path, bad, fr), "q1")
    # a key entry still has to exist for every question
    nokey = followup([(LEASE, "text"), (RECHECK, "text")])
    del nokey["key"]["q2"]
    refused(run(tmp_path, nokey, fr), "q2", "key")
    # a concept due for re-testing is still choice only
    records = tmp_path / "records"
    records.mkdir(exist_ok=True)
    (records / "0001-lease-ownership.md").write_text(
        "---\nconcept: lease-ownership\nstate: demonstrated\ndate: 2026-10-19\nsource: jesdi/general-skills#1\n"
        "status: active\n---\n# Lease\n\nText.\n")
    refused(run(tmp_path, followup([(LEASE, "text"), (RECHECK, "text")]), fr), LEASE)


def test_delta_with_no_questions_and_no_concepts_is_valid(tmp_path):
    result = run(tmp_path, make_exam([], [], mode="delta"), req("delta", prior_concepts=["old"]))
    assert result.returncode == 0, result.stderr or result.stdout
    # "nothing new" lists no concept: one that an earlier exam covered is refused, even unasked
    stale = make_exam([("old", "concept")], [("old", "choice")], mode="delta")
    refused(run(tmp_path, stale, req("delta", prior_concepts=["old"])), "old")


def test_delta_with_a_new_concept_is_valid_and_a_prior_concept_is_refused(tmp_path):
    exam = make_exam([("retry-budget", "concept")], [("retry-budget", "choice")], mode="delta")
    ok = run(tmp_path, exam, req("delta", prior_concepts=["merge-lease"]))
    assert ok.returncode == 0, ok.stderr or ok.stdout
    refused(run(tmp_path, exam, req("delta", prior_concepts=["retry-budget"])), "retry-budget")


def test_an_exam_whose_mode_differs_from_the_request_is_refused(tmp_path):
    ok = run(tmp_path, followup([(LEASE, "text"), (RECHECK, "text")]), req("followup", only_concepts=[LEASE, RECHECK]))
    assert ok.returncode == 0, ok.stderr or ok.stdout
    refused(run(tmp_path, make_exam([], [], mode="delta"), req("first")), "mode")
    refused(run(tmp_path, make_exam([], [], mode="delta"), req("followup", only_concepts=[])), "mode")
    refused(run(tmp_path, make_exam([("x", "behaviour")], [("x", "choice")], mode="first"), req("delta")), "mode")
