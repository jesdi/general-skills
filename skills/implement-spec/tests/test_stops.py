"""Ticket 05 acceptance tests: human stops as decisions, the review page, answers, modes.

All through `sh run.sh --runner <script> [--unattended --stage-json <path>] [--answer <qid>=<optid>]` on the fixture
repository of test_review (a Review rig). Reruns reuse the rig: the fake runner counts calls per role over all runs, so
a plan lists the steps of every run in order.

Interface choices (ticket 05):
- decisions: `.agent/implement/decisions.json` is `{revision, questions: [{id, title, why, evidence: [str],
  options: [{id, description}], recommended}]}`. `revision` is the hex SHA-256 of
  `json.dumps(file without "revision", sort_keys=True, separators=(",", ":"))` encoded as UTF-8 (ASCII content here).
- evidence strings are file pointers: `.agent/implement/<NN>/implementer.json` and `.agent/implement/ledger.md:<line>`,
  the line being a ledger line that names the ticket and BLOCKED.
- question ids are `<NN>-locked-test-<n>` with n counting per run (the first is 1, a second stop on the ticket is 2),
  and a capped ticket with unmet criteria blocks without a finding question. Options for `locked-test` are
  `overrule-test` / `overrule-implementer` (recommended: overrule-implementer).
- an implementer is BLOCKED with a `locked-test` stop only when a `concerns` entry starts with "locked test
  contradicts spec:"; any other BLOCKED report is an `implementer-stopped` stop (issue #123).
- the answers file is `.agent/implement-answers.json` in the schema.md shape: `v`, `stage: "implement"`, `submitted`
  (`"changes"`), `revision`, `answers`. A rerun applies it only with `submitted` set and `revision` equal to the current
  decisions revision; otherwise a ledger line says "ignored" (and "revision" for a stale file, the option id for an
  option the question does not offer), and the stop repeats with exit 2 and the same decisions file.
- `--answer` with an unknown question id exits 3 before any agent and names the id on stdout or stderr.
- the ruling for an overruled test is the ledger line "Ruling: 01 locked test overruled"; a finding ruling line
  names the ticket, the finding title and the chosen option.
- the stop of the correction is observed in the runner log: a second `test-writer` call on the ticket (before the
  implementer's second call) and a second `implementer` call whose HEAD is the correction commit.
- stage.json: `{"stage": "implement", "status": "awaiting-review", "artifact": ".agent/implement-review.html",
  "open_questions": N}` at a stop; at a BLOCKED end `{"stage": "implement", "status": "blocked", "blocked": ["01"]}`.
- ticket 08: `--unattended` pushes at the end, so it is refused in a repository with no `origin`. The unattended tests
  run on the rig of test_final, which has a bare `origin` and a fake `gh` (imported in the test: test_final imports
  this module).
"""
import hashlib
import json
import re

import pytest

import driver_review_page
from test_cli import git
from test_review import ONE, Review, finding, fix, review
from test_review_page import TEMPLATE, check_page
from test_walk import impl, plan, tw

CONTRA = "locked test contradicts spec: tests/test_01.py::test_x"
UNATTENDED = ("--unattended", "--stage-json", ".agent/stage.json")
NEW_TEST = "def test_x():\n    assert '01' == '01'\n"
Q1 = "01-locked-test-1"


def revision(decisions):
    body = {k: v for k, v in decisions.items() if k != "revision"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def blocked(*concerns):
    return {"report": {"status": "BLOCKED", "commits": [], "files": [], "concerns": list(concerns or [CONTRA]),
                       "green": []}}


def correction():
    return {**tw("01"), "files": {"tests/test_01.py": NEW_TEST}, "commit": "test: 01 overruled"}


def steps(*implementers, **more):
    return plan(**{"01/implementer": list(implementers), "01/test-writer": [tw("01"), correction()], **more})


def decisions(rig):
    return json.loads((rig.repo / ".agent/implement/decisions.json").read_text())


def answers_file(rig):
    return rig.repo / ".agent/implement-answers.json"


def stage(rig):
    return json.loads((rig.repo / ".agent/stage.json").read_text())


def write_answers(rig, answers, **fields):
    body = {"v": 1, "stage": "implement", "submitted": "changes", "revision": decisions(rig)["revision"],
            "answers": answers, **fields}
    answers_file(rig).write_text(json.dumps(body))


def stopped(tmp, *extra, then=(impl("01"),), files=ONE, rig=Review):
    """A rig after run 1: 01's implementer reported the contradicted locked test."""
    rig = rig(tmp, files)
    return rig.run(steps(blocked(), *then), *extra)


@pytest.fixture(scope="module")
def local(tmp_path_factory):
    return stopped(tmp_path_factory.mktemp("local"))


# ---------------------------------------------------------------- the question

def test_a_contradicted_locked_test_writes_one_question_with_pointers(local):
    assert local.code == 2, local.output()
    (q,) = decisions(local)["questions"]
    assert q["id"] == Q1
    assert {o["id"] for o in q["options"]} == {"overrule-test", "overrule-implementer"}
    assert q["recommended"] == "overrule-implementer"
    assert q["title"] and q["why"]
    evidence = "\n".join(q["evidence"])
    assert ".agent/implement/01/implementer-0.json" in evidence and (local.folder() / "implementer-0.json").exists()
    (pointer,) = re.findall(r"\.agent/implement/ledger\.md:(\d+)", evidence)
    line = local.ledger()[int(pointer) - 1]
    assert "01" in line and "BLOCKED" in line, line


def test_revision_is_the_sha256_of_the_file_without_it_and_ids_are_valid_and_unique(local):
    doc = decisions(local)
    assert doc["revision"] == revision(doc)
    ids = [q["id"] for q in doc["questions"]]
    assert all(re.fullmatch(r"[a-z0-9_-]{1,64}", i) for i in ids) and len(ids) == len(set(ids))
    assert all(2 <= len(q["options"]) <= 4 and q["recommended"] in [o["id"] for o in q["options"]]
               for q in doc["questions"])


def test_a_second_stop_on_the_same_ticket_gets_a_new_id_and_a_new_revision(tmp_path):
    rig = stopped(tmp_path, then=(blocked(), impl("01")))
    first = decisions(rig)
    rig.run(steps(blocked(), blocked(), impl("01")), "--answer", f"{Q1}=overrule-test")
    assert rig.code == 2, rig.output()
    second = decisions(rig)
    ids = [q["id"] for q in second["questions"]]
    assert "01-locked-test-2" in ids and len(ids) == len(set(ids))
    assert second["revision"] != first["revision"] == revision(first) and second["revision"] == revision(second)
    assert "01-locked-test-2" in rig.out


# ---------------------------------------------------------------- the page

def test_the_stop_renders_the_review_page_from_the_template(local):
    html = (local.repo / ".agent/implement-review.html").read_text()
    check_page(html, decisions(local))
    assert all(part in html for part in re.findall(r"<(?:style|script)>.*?</(?:style|script)>", TEMPLATE, re.S))


# ---------------------------------------------------------------- local mode

def test_without_unattended_the_driver_prints_question_and_options_exits_2_and_launches_nothing_more(local):
    assert local.code == 2
    for word in (Q1, "overrule-test", "overrule-implementer"):
        assert word in local.out + local.err, word
    assert [c["role"] for c in local.calls()] == ["test-writer", "implementer"]
    assert "feat: 01 rows" not in local.pr_subjects()


def test_answer_overrule_test_writes_the_answers_file_rules_corrects_the_test_and_resumes(tmp_path):
    rig = stopped(tmp_path)
    rev = decisions(rig)["revision"]
    rig.run(steps(blocked(), impl("01")), "--answer", f"{Q1}=overrule-test")
    assert rig.code == 0, rig.output()
    file = json.loads(answers_file(rig).read_text())
    assert (file["v"], file["stage"], file["submitted"], file["revision"]) == (1, "implement", "changes", rev)
    assert file["answers"] == {Q1: "overrule-test"}
    assert rig.led("Ruling: 01 locked test overruled")
    fixes, resumed = rig.calls("01", "test-writer"), rig.calls("01", "implementer")
    assert len(fixes) == 2 and len(resumed) == 2, "one test-writer correction, then the implementer again"
    assert fixes[1]["t1"] <= resumed[1]["t0"] and resumed[1]["head"] == fixes[1]["sha"]
    assert git(rig.repo, "show", "feat/csv:tests/test_01.py") == NEW_TEST and "feat: 01 rows" in rig.pr_subjects()


def test_an_answers_file_from_another_revision_is_ignored_with_a_ledger_line_and_the_stop_repeats(tmp_path):
    rig = stopped(tmp_path)
    before = decisions(rig)
    write_answers(rig, {Q1: "overrule-test"}, revision="0" * 64)
    rig.run(steps(blocked(), impl("01")))
    assert rig.code == 2, rig.output()
    assert rig.led("ignored", "revision") and not rig.led("Ruling: 01 locked test overruled")
    assert decisions(rig) == before and Q1 in rig.out
    assert [c["role"] for c in rig.calls()] == ["test-writer", "implementer"]


def test_an_answer_naming_an_option_the_question_does_not_offer_is_ignored_with_a_ledger_line(tmp_path):
    rig = stopped(tmp_path)
    write_answers(rig, {Q1: "bogus-option"})
    rig.run(steps(blocked(), impl("01")))
    assert rig.code == 2, rig.output()
    assert rig.led("ignored", "bogus-option") and not rig.led("Ruling: 01 locked test overruled")
    assert [c["role"] for c in rig.calls()] == ["test-writer", "implementer"]


def test_answer_for_an_unknown_question_id_exits_3_naming_it_before_any_agent(tmp_path):
    rig = stopped(tmp_path)
    seen = len(rig.calls())
    rig.run(steps(blocked(), impl("01")), "--answer", "no-such-question=accept")
    assert rig.code == 3, rig.output()
    assert "no-such-question" in rig.out + rig.err and len(rig.calls()) == seen


def test_a_blocked_report_that_is_not_a_contradicted_locked_test_is_an_implementer_stop(tmp_path):
    rig = Review(tmp_path)
    rig.run(steps(blocked("cannot reach the database")))
    assert rig.code == 2, rig.output()
    assert [q["id"] for q in decisions(rig)["questions"]] == ["01-implementer-stopped-1"]
    assert rig.state()["tickets"]["01"]["phase"] == "blocked"


# ---------------------------------------------------------------- capped ticket

def test_unmet_criteria_at_cap_block_without_a_finding_question(tmp_path):
    rig = Review(tmp_path)
    bad = review("01", [finding("STUCK-A", line=98), finding("STUCK-B", line=99)])
    rig.run(plan(**{"01/reviewer": [bad] * 3, "01/fixer": [fix("01", n) for n in (1, 2)]}))
    assert rig.code == 1, rig.output()
    assert rig.state()["tickets"]["01"]["phase"] == "blocked"
    assert not rig.state()["run"]["questions"]
    assert len(rig.calls("01", "fixer")) == 2


# ---------------------------------------------------------------- unattended

def test_unattended_stop_writes_stage_json_and_exits_2(tmp_path):
    from test_final import Final
    rig = stopped(tmp_path, *UNATTENDED, rig=Final)
    assert rig.code == 2, rig.output()
    assert stage(rig) == {"stage": "implement", "status": "awaiting-review",
                          "artifact": ".agent/implement-review.html", "open_questions": 1}
    assert (rig.repo / ".agent/implement-review.html").exists()


def test_unattended_rerun_with_a_submitted_answers_file_at_the_current_revision_continues_silently(tmp_path):
    from test_final import Final
    rig = stopped(tmp_path, *UNATTENDED, rig=Final)
    write_answers(rig, {Q1: "overrule-test"})
    rig.run(steps(blocked(), impl("01")), *UNATTENDED)
    assert rig.code == 0, rig.output()
    assert Q1 not in rig.out + rig.err and rig.led("Ruling: 01 locked test overruled")
    assert len(rig.calls("01", "implementer")) == 2 and "feat: 01 rows" in rig.pr_subjects()
    assert stage(rig) == {"stage": "implement", "status": "done"}


def test_unattended_blocked_ticket_ends_with_stage_json_blocked_naming_it_and_exit_1(tmp_path):
    from test_final import Final
    rig = Final(tmp_path, gate={"red": ["ticket/feat%2Fcsv/01"]})
    rig.run(plan(), *UNATTENDED)
    assert rig.code == 1, rig.output()
    assert stage(rig) == {"stage": "implement", "status": "blocked", "blocked": ["01"]}
