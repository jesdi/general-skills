"""Acceptance tests: `pr_exam.py learned`, `record` and `validate` with records, run as subprocesses.

Interface defined here:
- `learned --records <dir> --today <date>` prints a JSON object {concept: "skip"|"due"|"misconception"}
  for the active record of each concept; a missing or empty dir prints {}; a bad record exits 1 with the
  file name on stderr.
- `record --records <dir> --exam <exam.json> --result <result.json> --today <date>` writes
  `NNNN-<concept>.md` (frontmatter concept/state/date/source/status) for each question of 1 point
  (demonstrated) or 0 points (misconception), numbered after the highest file, and marks the concept's
  earlier active record `status: superseded by LR-NNNN`.
Spec specs/pr-exam: requirement 8; design.md "Learning record".
"""
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "pr_exam.py"
SHA = "a" * 40
DIFF = "diff --git a/lease.py b/lease.py\n+def take_lease(): ...\n"
TODAY = "2026-11-18"
D29, D30, WEEK = "2026-10-20", "2026-10-19", "2026-11-11"


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def write_record(records, n, concept, state, date, status="active"):
    records.mkdir(exist_ok=True)
    (records / f"{n:04d}-{concept}.md").write_text(
        f"---\nconcept: {concept}\nstate: {state}\ndate: {date}\nsource: jesdi/general-skills#1\n"
        f"status: {status}\n---\n# {concept}\n\nText.\n")


def learned(records, today=TODAY):
    return cli("learned", "--records", records, "--today", today)


def learned_ok(records, today=TODAY):
    r = learned(records, today)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    return json.loads(r.stdout)


def exam_with(questions):
    """questions: [(concept, type, kind)]"""
    qs, key = [], {}
    for i, (c, t, _) in enumerate(questions, 1):
        q = {"id": f"q{i}", "concept": c, "type": t, "prompt": f"Prompt {i}?"}
        k = {"explanation": f"Explain {i}.", "hunk": "+def take_lease(): ..."}
        if t == "choice":
            q["options"] = ["alpha beta", "gamma delta", "zeta eta"]
            k["answer"] = 1
        else:
            k["rubric"] = {"pass": "Names it.", "partial": "Mentions it."}
        qs.append(q)
        key[q["id"]] = k
    seen = {}
    for c, _, kind in questions:
        seen.setdefault(c, kind)
    return {"version": 1, "mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
            "concepts": [{"id": c, "title": c, "kind": k} for c, k in seen.items()],
            "questions": qs, "key": key}


def validate(tmp_path, records, exam, extra_concepts=()):
    exam["concepts"] += [{"id": c, "title": c, "kind": k} for c, k in extra_concepts]
    (tmp_path / "diff.patch").write_text(DIFF)
    (tmp_path / "body.md").write_text("body")
    (tmp_path / "exam.json").write_text(json.dumps(exam))
    request = {"mode": "first", "repo": "jesdi/general-skills", "pr": 240, "head_sha": SHA,
               "diff": str(tmp_path / "diff.patch"), "body": str(tmp_path / "body.md"), "glossary": None,
               "records": str(records), "today": TODAY, "only_concepts": [], "prior_concepts": [],
               "prior_prompts": [], "out": str(tmp_path / "exam.json")}
    (tmp_path / "request.json").write_text(json.dumps(request))
    return cli("validate", "--request", tmp_path / "request.json", tmp_path / "exam.json")


def record(tmp_path, records, points, today=TODAY):
    """points: {concept: points}; one text question per concept."""
    exam = exam_with([(c, "text", "concept") for c in points])
    result = {"passed": False, "score": 0, "max": len(points),
              "questions": [{"id": f"q{i}", "concept": c, "points": p}
                            for i, (c, p) in enumerate(points.items(), 1)],
              "missed": [c for c, p in points.items() if p < 1]}
    (tmp_path / "rexam.json").write_text(json.dumps(exam))
    (tmp_path / "result.json").write_text(json.dumps(result))
    r = cli("record", "--records", records, "--exam", tmp_path / "rexam.json",
            "--result", tmp_path / "result.json", "--today", today)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


def field(path, name):
    m = re.search(rf"^{name}: (.*)$", path.read_text().split("---")[1], re.M)
    return m.group(1).strip() if m else None


def test_record_writes_one_file_per_one_or_zero_point_question_after_the_highest(tmp_path):
    records = tmp_path / "records"
    write_record(records, 5, "older", "demonstrated", D29)
    record(tmp_path, records, {"merge-lease": 1, "base-sha-recheck": 0, "half-credit": 0.5})
    assert sorted(p.name for p in records.glob("*.md") if not p.name.startswith("0005")) == [
        "0006-merge-lease.md", "0007-base-sha-recheck.md"]
    lease, base = records / "0006-merge-lease.md", records / "0007-base-sha-recheck.md"
    assert (field(lease, "concept"), field(lease, "state"), field(lease, "date")) == (
        "merge-lease", "demonstrated", TODAY)
    assert (field(base, "state"), field(base, "date")) == ("misconception", TODAY)


def test_second_record_supersedes_the_first_and_learned_reports_the_new_state(tmp_path):
    records = tmp_path / "records"
    record(tmp_path, records, {"merge-lease": 0}, today="2026-09-01")
    assert learned_ok(records) == {"merge-lease": "misconception"}
    record(tmp_path, records, {"merge-lease": 1})
    first = records / "0001-merge-lease.md"
    assert field(first, "status") == "superseded by LR-0002"
    assert field(records / "0002-merge-lease.md", "status") == "active"
    assert learned_ok(records) == {"merge-lease": "skip"}


def test_learned_skip_at_29_days_due_at_30_misconception_at_any_age(tmp_path):
    records = tmp_path / "records"
    write_record(records, 1, "merge-lease", "demonstrated", D29)
    assert learned_ok(records) == {"merge-lease": "skip"}
    write_record(records, 2, "merge-lease", "demonstrated", D30)
    (records / "0001-merge-lease.md").unlink()
    assert learned_ok(records) == {"merge-lease": "due"}
    (records / "0002-merge-lease.md").unlink()
    write_record(records, 3, "merge-lease", "misconception", "2020-01-01")
    assert learned_ok(records) == {"merge-lease": "misconception"}
    (records / "0003-merge-lease.md").unlink()
    write_record(records, 4, "merge-lease", "misconception", TODAY)
    assert learned_ok(records) == {"merge-lease": "misconception"}


def test_exam_on_a_skipped_concept_or_missing_a_misconception_is_refused(tmp_path):
    records = tmp_path / "records"
    write_record(records, 1, "merge-lease", "demonstrated", WEEK)
    write_record(records, 2, "base-sha-recheck", "misconception", WEEK)
    good = exam_with([("base-sha-recheck", "choice", "architecture"), ("other", "text", "why")])
    r = validate(tmp_path, records, good, [("merge-lease", "concept")])
    assert r.returncode == 0, r.stderr
    asks_lease = exam_with([("merge-lease", "choice", "concept"), ("other", "text", "why")])
    r = validate(tmp_path, records, asks_lease, [("base-sha-recheck", "architecture")])
    assert r.returncode == 1 and "merge-lease" in r.stderr, (r.returncode, r.stderr)
    no_base = exam_with([("other", "text", "why")])
    r = validate(tmp_path, records, no_base, [("merge-lease", "concept"), ("base-sha-recheck", "architecture")])
    assert r.returncode == 1 and "base-sha-recheck" in r.stderr, (r.returncode, r.stderr)


def test_due_concept_validates_as_choice_and_is_refused_as_text(tmp_path):
    records = tmp_path / "records"
    write_record(records, 1, "merge-lease", "demonstrated", D30)
    ok = exam_with([("merge-lease", "choice", "concept"), ("other", "text", "why")])
    r = validate(tmp_path, records, ok)
    assert r.returncode == 0, r.stderr
    bad = exam_with([("merge-lease", "text", "concept"), ("other", "choice", "why")])
    r = validate(tmp_path, records, bad)
    assert r.returncode == 1 and "merge-lease" in r.stderr, (r.returncode, r.stderr)


def test_any_question_on_a_concept_demonstrated_29_days_ago_is_refused(tmp_path):
    records = tmp_path / "records"
    write_record(records, 1, "merge-lease", "demonstrated", D29)
    assert validate(tmp_path, records, exam_with([("other", "text", "why")]),
                    [("merge-lease", "concept")]).returncode == 0
    for t in ("choice", "text"):
        other = "text" if t == "choice" else "choice"
        r = validate(tmp_path, records, exam_with([("merge-lease", t, "concept"), ("other", other, "why")]))
        assert r.returncode == 1 and "merge-lease" in r.stderr, (t, r.returncode, r.stderr)


def test_missing_or_empty_records_directory_reads_as_no_records(tmp_path):
    assert learned_ok(tmp_path / "nowhere") == {}
    (tmp_path / "empty").mkdir()
    assert learned_ok(tmp_path / "empty") == {}


def test_record_with_missing_or_malformed_field_is_an_error_naming_the_file(tmp_path):
    for i, bad in enumerate(["date: 2026-10-20\n", "state: demonstrated\ndate: yesterday\n",
                             "state: learned\ndate: 2026-10-20\n"], 1):
        records = tmp_path / f"r{i}"
        records.mkdir()
        name = "0001-merge-lease.md"
        head = "concept: merge-lease\n" + bad + "source: jesdi/general-skills#1\nstatus: active\n"
        (records / name).write_text(f"---\n{head}---\n# x\n")
        r = learned(records)
        assert r.returncode == 1 and name in r.stderr, (bad, r.returncode, r.stdout, r.stderr)


def concurrent_records(tmp_path, records, concepts):
    """Release ready workers together; each execs the public CLI without instrumenting it."""
    wrapper = ("import os, sys\nprint('ready', flush=True)\nsys.stdin.readline()\n"
               "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n")
    procs = []
    for i, c in enumerate(concepts):
        d = tmp_path / f"w{i}"
        d.mkdir()
        (d / "exam.json").write_text(json.dumps(exam_with([(c, "text", "concept")])))
        (d / "result.json").write_text(json.dumps({"passed": False, "score": 1, "max": 1, "missed": [],
            "questions": [{"id": "q1", "concept": c, "points": 1}]}))
        procs.append(subprocess.Popen(
            [sys.executable, "-c", wrapper, str(SCRIPT), "record", "--records", str(records),
             "--exam", str(d / "exam.json"), "--result", str(d / "result.json"), "--today", TODAY],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    for p in procs:
        assert p.stdout.readline() == "ready\n"
    for p in procs:
        p.stdin.write("go\n")
        p.stdin.flush()
    completed = [(p, *p.communicate(timeout=30)) for p in procs]
    for p, out, err in completed:
        assert p.returncode == 0, (p.returncode, out, err)


def test_concurrent_record_calls_in_a_shared_directory_keep_every_write_and_unique_numbers(tmp_path):
    for rnd in range(4):
        base = tmp_path / f"round{rnd}"
        base.mkdir()
        records = base / "records"
        concepts = [f"concept-{i}" for i in range(8)]
        concurrent_records(base, records, concepts)
        files = sorted(p.name for p in records.glob("*.md"))
        assert len(files) == 8, (rnd, files)
        assert len({n.split("-")[0] for n in files}) == 8, (rnd, files)
        assert sorted(learned_ok(records)) == sorted(concepts), (rnd, files)


def test_concurrent_record_calls_on_one_concept_leave_exactly_one_active_record(tmp_path):
    for rnd in range(4):
        base = tmp_path / f"round{rnd}"
        base.mkdir()
        records = base / "records"
        write_record(records, 1, "merge-lease", "misconception", "2026-09-01")
        concurrent_records(base, records, ["merge-lease"] * 6)
        files = sorted(records.glob("*.md"))
        assert len(files) == 7, (rnd, [p.name for p in files])
        assert [field(p, "status") for p in files].count("active") == 1, (rnd, [field(p, "status") for p in files])
