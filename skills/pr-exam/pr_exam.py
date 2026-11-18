#!/usr/bin/env python3
"""pr_exam: deterministic exam rules. `validate --request <request.json> <exam.json>`, `public <exam.json>`."""
import argparse
import datetime
import fcntl
import json
import re
import sys
from pathlib import Path

KINDS = ["why", "concept", "architecture", "risk", "behaviour"]  # highest priority first
MODES = ("first", "followup", "delta")
QUESTION_FIELDS = {"id", "concept", "type", "prompt", "options"}
MAX_QUESTIONS = 8


def nonempty(v):
    return isinstance(v, str) and bool(v.strip())


def shape_errors(q):
    """Served fields must be plain strings, so nothing private can hide inside them."""
    qid, errors = q.get("id"), []
    if not nonempty(qid):
        errors.append(f"question {qid!r}: id must be a non-empty string")
    if not nonempty(q.get("prompt")):
        errors.append(f"question {qid}: prompt must be a non-empty string")
    if "options" in q and not (isinstance(q["options"], list) and all(nonempty(o) for o in q["options"])):
        errors.append(f"question {qid}: options must be non-empty strings")
    return errors


def check_question(q, concepts, key, diff, errors):
    qid = q.get("id")
    errors += shape_errors(q)
    extra = set(q) - QUESTION_FIELDS
    if extra:
        errors.append(f"question {qid}: unexpected field(s) {sorted(extra)}")
    if q.get("concept") not in concepts:
        errors.append(f"question {qid}: concept {q.get('concept')!r} is not a listed concept")
    qtype, options = q.get("type"), q.get("options")
    if qtype == "choice":
        if not isinstance(options, list) or len(options) < 2:
            errors.append(f"question {qid}: choice needs a list of options")
            options = None
        else:
            if len({len(str(o).split()) for o in options}) > 1:
                errors.append(f"question {qid}: options differ in word count")
            if len(set(map(str, options))) != len(options):
                errors.append(f"question {qid}: options contain duplicates")
    elif qtype == "text":
        if "options" in q:
            errors.append(f"question {qid}: text question must not have options")
    else:
        errors.append(f"question {qid}: type must be choice or text")
    entry = key.get(qid)
    if not isinstance(entry, dict):
        if qid in key:
            errors.append(f"key {qid}: entry must be an object")
        return
    for f in ("explanation", "hunk"):
        if not nonempty(entry.get(f)):
            errors.append(f"key {qid}: {f} must be non-empty")
    if nonempty(entry.get("hunk")) and entry["hunk"] not in diff:
        errors.append(f"key {qid}: hunk is not in the diff")
    if qtype == "choice":
        a = entry.get("answer")
        if not (isinstance(a, int) and not isinstance(a, bool) and options and 0 <= a < len(options)):
            errors.append(f"key {qid}: answer is not an index into options")
        if "rubric" in entry:
            errors.append(f"key {qid}: choice question must not have a rubric")
    elif qtype == "text":
        rubric = entry.get("rubric")
        for part in ("pass", "partial"):
            if not (isinstance(rubric, dict) and nonempty(rubric.get(part))):
                errors.append(f"key {qid}: rubric.{part} must be non-empty")
        if "answer" in entry:
            errors.append(f"key {qid}: text question must not have an answer")


def errors_for(exam, request, diff, learned=None):
    learned = learned or {}
    errors = []
    if exam.get("version") != 1:
        errors.append("version must be 1")
    if exam.get("mode") not in MODES:
        errors.append("mode must be first, followup or delta")
    for f in ("repo", "pr", "head_sha"):
        if exam.get(f) != request.get(f):
            errors.append(f"{f} differs from the request's")
    clist, questions, key = exam.get("concepts", []), exam.get("questions", []), exam.get("key", {})
    kinds = {}
    for c in clist:
        if c.get("kind") not in KINDS:
            errors.append(f"concept {c.get('id')}: kind must be one of {KINDS}")
        if c.get("id") in kinds:
            errors.append(f"concept {c.get('id')}: listed twice")
        kinds[c.get("id")] = c.get("kind")

    n = len(questions)
    if not (1 if exam.get("mode") != "delta" else 0) <= n <= MAX_QUESTIONS:
        errors.append(f"question count {n} is outside the allowed range")
    seen = set()
    for q in questions:
        if q.get("concept") in seen:
            errors.append(f"concept {q.get('concept')}: asked by more than one question")
        seen.add(q.get("concept"))
        check_question(q, kinds, key, diff, errors)
        state = learned.get(q.get("concept"))
        if state == "skip" or (state == "due" and q.get("type") != "choice"):
            errors.append(f"concept {q.get('concept')}: learned as {state}, so it may not be asked"
                          + (" as text" if state == "due" else ""))

    qids = [q.get("id") for q in questions]
    for qid in {i for i in qids if qids.count(i) > 1}:
        errors.append(f"question {qid}: id used more than once")
    for qid in qids:
        if qid not in key:
            errors.append(f"key: no entry for question {qid}")
    for qid in key:
        if qid not in qids:
            errors.append(f"key: entry {qid} matches no question")

    for cid, state in learned.items():
        if state == "misconception" and cid not in seen and n < MAX_QUESTIONS and cid not in kinds:
            errors.append(f"concept {cid}: recorded misconception is not asked")
    rank = {k: i for i, k in enumerate(KINDS)}
    asked = [rank[kinds[c]] for c in seen if kinds.get(c) in rank]
    for cid, kind in kinds.items():
        if cid in seen or kind not in rank or learned.get(cid) == "skip":
            continue
        if n < MAX_QUESTIONS:
            errors.append(f"concept {cid}: not asked, but only {n} of {MAX_QUESTIONS} questions used")
        elif asked and max(asked) > rank[kind]:
            errors.append(f"concept {cid}: dropped while a lower-priority concept is asked")

    if exam.get("mode") == "first":
        types = {q.get("type") for q in questions}
        if n >= 2 and types != {"choice", "text"}:
            errors.append("a first exam with 2 or more questions needs both choice and text")
        if n >= 3 and not any(q.get("type") == "text" and kinds.get(q.get("concept")) == "why" for q in questions):
            errors.append("a first exam with 3 or more questions needs a text question on a why concept")
    return errors


def load(path):
    return json.loads(Path(path).read_text())


def validate(args):
    exam, request = load(args.exam), load(args.request)
    diff = Path(request["diff"]).read_text()
    try:
        learned = learned_states(request["records"], request["today"]) if request.get("records") else {}
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    errors = errors_for(exam, request, diff, learned)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"valid: {len(exam['questions'])} questions, {len(exam['concepts'])} concepts")
    return 0


FILE_RE = re.compile(r"^(\d{4,})-(.+)\.md$")
FIELDS = ("concept", "state", "date", "source", "status")


def read_records(directory):
    """[(number, path, fields)] for every record; ValueError naming the file when one is malformed."""
    out = []
    for path in sorted(Path(directory).glob("*.md")):
        m = FILE_RE.match(path.name)
        if not m:
            continue
        head = path.read_text().split("---")
        f = dict(re.findall(r"^(\w+): *(.*?) *$", head[1] if len(head) > 2 else "", re.M))
        try:
            if any(not f.get(k) for k in FIELDS):
                raise ValueError("missing field")
            if f["state"] not in ("demonstrated", "misconception"):
                raise ValueError("state")
            datetime.date.fromisoformat(f["date"])
            if f["status"] != "active" and not re.fullmatch(r"superseded by LR-\d{4,}", f["status"]):
                raise ValueError("status")
        except ValueError as e:
            raise ValueError(f"{path.name}: malformed record ({e})") from None
        out.append((int(m.group(1)), path, f))
    return out


def learned_states(directory, today):
    today = datetime.date.fromisoformat(today)
    states = {}
    for _, _, f in read_records(directory):
        if f["status"] != "active":
            continue
        if f["state"] == "misconception":
            states[f["concept"]] = "misconception"
        else:
            age = (today - datetime.date.fromisoformat(f["date"])).days
            states[f["concept"]] = "skip" if age < 30 else "due"
    return states


def learned(args):
    try:
        print(json.dumps(learned_states(args.records, args.today)))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def record(args):
    exam, result = load(args.exam), load(args.result)
    explanations = {qid: k["explanation"] for qid, k in exam["key"].items()}
    source = f"{exam['repo']}#{exam['pr']}"
    Path(args.records).mkdir(parents=True, exist_ok=True)
    # exclusive lock: read, number, supersede and write are one critical section across processes
    with open(Path(args.records) / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = read_records(args.records)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        return write_records(args, result, existing, explanations, source)


def write_records(args, result, existing, explanations, source):
    n = max([r[0] for r in existing], default=0)
    for q in result["questions"]:
        if q["points"] not in (0, 1):
            continue
        n += 1
        concept = q["concept"]
        for _, path, f in existing:
            if f["concept"] == concept and f["status"] == "active":
                path.write_text(re.sub(r"^status: *active *$", f"status: superseded by LR-{n:04d}", path.read_text(), count=1, flags=re.M))
        state = "demonstrated" if q["points"] == 1 else "misconception"
        (Path(args.records) / f"{n:04d}-{concept}.md").write_text(
            f"---\nconcept: {concept}\nstate: {state}\ndate: {args.today}\nsource: {source}\n"
            f"status: active\n---\n# {concept}\n\n{explanations.get(q['id'], '')}\n")
        existing = [e for e in existing if e[2]["concept"] != concept or e[2]["status"] != "active"]
    return 0


def public(args):
    exam = load(args.exam)
    errors = [e for q in exam["questions"] for e in shape_errors(q)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    out = {k: exam[k] for k in ("version", "repo", "pr", "head_sha")}
    out["questions"] = [{k: q[k] for k in ("id", "type", "prompt", "options") if k in q} for q in exam["questions"]]
    print(json.dumps(out))
    return 0


GRADE_POINTS = {"pass": 1, "partial": 0.5, "fail": 0}


def score_errors(exam, answers, grades):
    errors = []
    texts = {q["id"] for q in exam["questions"] if q["type"] == "text"}
    for q in exam["questions"]:
        if q["id"] not in answers:
            errors.append(f"question {q['id']}: unanswered")
        if q["id"] in texts and q["id"] not in grades:
            errors.append(f"question {q['id']}: text question has no grade")
    for qid, g in grades.items():
        if qid not in texts:
            errors.append(f"grade for {qid}: not a text question")
        elif g.get("grade") not in GRADE_POINTS:
            errors.append(f"question {qid}: grade {g.get('grade')!r} is not pass, partial or fail")
    return errors


def score(args):
    exam, answers, grades = load(args.exam), load(args.answers), load(args.grades)
    errors = score_errors(exam, answers, grades)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    kinds = {c["id"]: c["kind"] for c in exam["concepts"]}
    out, missed, total, why_ok = [], [], 0, True
    for q in exam["questions"]:
        k = exam["key"][q["id"]]
        if q["type"] == "choice":
            pts = 1 if answers[q["id"]] == k["answer"] else 0
        else:
            pts = GRADE_POINTS[grades[q["id"]]["grade"]]
        r = {"id": q["id"], "concept": q["concept"], "points": pts}
        if pts < 1:
            r.update(explanation=k["explanation"], hunk=k["hunk"])
            if q["type"] == "text":
                r["feedback"] = grades[q["id"]].get("feedback", "")
            missed.append(q["concept"])
            why_ok = why_ok and kinds.get(q["concept"]) != "why"
        total += pts
        out.append(r)
    n = len(out)
    print(json.dumps({"passed": total / n >= 0.8 and why_ok, "score": total, "max": n,
                      "questions": out, "missed": missed}))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pr_exam")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--request", required=True)
    v.add_argument("exam")
    v.set_defaults(fn=validate)
    pub = sub.add_parser("public")
    pub.add_argument("exam")
    pub.set_defaults(fn=public)
    sc = sub.add_parser("score")
    for f in ("exam", "answers", "grades"):
        sc.add_argument(f"--{f}", required=True)
    sc.set_defaults(fn=score)
    ln = sub.add_parser("learned")
    ln.add_argument("--records", required=True)
    ln.add_argument("--today", required=True)
    ln.set_defaults(fn=learned)
    rc = sub.add_parser("record")
    for f in ("records", "exam", "result", "today"):
        rc.add_argument(f"--{f}", required=True)
    rc.set_defaults(fn=record)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
