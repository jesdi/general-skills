#!/usr/bin/env python3
"""pr_exam: deterministic exam rules. `validate --request <request.json> <exam.json>`, `public <exam.json>`."""
import argparse
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

KINDS = ["why", "concept", "architecture", "risk", "behaviour"]  # highest priority first
MODES = ("first", "followup", "delta")
QUESTION_FIELDS = {"id", "concept", "type", "prompt", "options"}
MAX_QUESTIONS = 8
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_slug(v):
    return isinstance(v, str) and bool(SLUG.fullmatch(v))


def nonempty(v):
    return isinstance(v, str) and bool(v.strip())


def shape_errors(q):
    """Served fields must be plain strings, so nothing private can hide inside them."""
    qid, errors = q.get("id"), []
    if not nonempty(qid):
        errors.append(f"question {qid!r}: id must be a non-empty string")
    if not nonempty(q.get("prompt")):
        errors.append(f"question {qid}: prompt must be a non-empty string")
    if q.get("type") not in ("choice", "text"):
        errors.append(f"question {qid}: type must be choice or text")
    if "options" in q and not (isinstance(q["options"], list) and all(nonempty(o) for o in q["options"])):
        errors.append(f"question {qid}: options must be non-empty strings")
    return errors


def served_errors(exam):
    """Every field `public` copies must have its plain shape, so no answer can ride inside one."""
    if not isinstance(exam, dict):
        return ["exam must be a JSON object"]
    errors = []
    if type(exam.get("version")) is not int or exam["version"] != 1:
        errors.append("version must be 1")
    for f in ("repo", "head_sha"):
        if not nonempty(exam.get(f)):
            errors.append(f"{f} must be a non-empty string")
    if type(exam.get("pr")) is not int:
        errors.append("pr must be an integer")
    questions = exam.get("questions")
    if not (isinstance(questions, list) and all(isinstance(q, dict) for q in questions)):
        return errors + ["questions must be a list of objects"]
    return errors + [e for q in questions for e in shape_errors(q)]


def check_question(q, concepts, key, diff, errors):
    qid = q.get("id")
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
    errors = served_errors(exam)
    if not isinstance(exam, dict) or "questions must be a list of objects" in errors:
        return errors  # the rules below walk the questions
    if exam.get("mode") not in MODES:
        errors.append("mode must be first, followup or delta")
    if "mode" in request and exam.get("mode") != request["mode"]:
        errors.append(f"mode {exam.get('mode')!r} differs from the request's {request['mode']!r}")
    for f in ("repo", "pr", "head_sha"):
        if exam.get(f) != request.get(f):
            errors.append(f"{f} differs from the request's")
    clist, questions, key = exam.get("concepts", []), exam.get("questions", []), exam.get("key", {})
    kinds = {}
    for c in clist:
        if not is_slug(c.get("id")):
            errors.append(f"concept {c.get('id')!r}: id must be a slug (lowercase letters, digits, single hyphens)")
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
        types = {q.get("type") for q in questions if q.get("type") in ("choice", "text")}
        if n >= 2 and types != {"choice", "text"}:
            errors.append("a first exam with 2 or more questions needs both choice and text")
        if n >= 3 and not any(q.get("type") == "text" and kinds.get(q.get("concept")) == "why" for q in questions):
            errors.append("a first exam with 3 or more questions needs a text question on a why concept")
    if exam.get("mode") == "followup":
        only = request.get("only_concepts", [])
        asked = [q.get("concept") for q in questions]
        for cid in only:
            if cid not in asked:
                errors.append(f"concept {cid}: missed, but no followup question asks it")
        for cid in set(asked) - set(only):
            errors.append(f"concept {cid}: not in only_concepts, so a followup may not ask it")
        for q in questions:
            if q.get("prompt") in request.get("prior_prompts", []):
                errors.append(f"question {q.get('id')}: prompt repeats one of an earlier attempt")
    if exam.get("mode") == "delta":
        for cid in kinds:
            if cid in request.get("prior_concepts", []):
                errors.append(f"concept {cid}: an earlier exam covered it, so a delta may not list it")
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


def config_errors(c):
    if not isinstance(c, dict):
        return ["config: must be a JSON object"]
    errors = [f"unknown key: {k}" for k in c if k not in ("enabled", "bypassLabels")]
    if not isinstance(c.get("enabled", True), bool):
        errors.append("enabled: must be true or false")
    labels = c.get("bypassLabels", {})
    if not isinstance(labels, dict):
        errors.append("bypassLabels: must be an object of label to skip or defer")
    else:
        errors += [f"bypassLabels: {k!r} has mode {m!r}, allowed values: skip, defer"
                   for k, m in labels.items() if m not in ("skip", "defer")]
    return errors


def config(args):
    path = Path(args.path)
    try:
        c = json.loads(path.read_text()) if path.exists() else {}
    except ValueError as e:
        print(f"invalid json in {path}: {e}", file=sys.stderr)
        return 1
    errors = config_errors(c)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(json.dumps({"enabled": c.get("enabled", True), "bypassLabels": c.get("bypassLabels", {})}))
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
    return sorted(out, key=lambda r: r[0])  # oldest first: when two records are active, the newest counts


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
    bad = [q.get("concept") for q in result["questions"] if not is_slug(q.get("concept"))]
    if bad:
        print("\n".join(f"concept {c!r}: not a slug, so it cannot name a record" for c in bad), file=sys.stderr)
        return 1
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


def publish(path, text):
    """Write a whole file or nothing: readers never see a partial record."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_records(args, result, existing, explanations, source):
    n = max([r[0] for r in existing], default=0)
    for q in result["questions"]:
        if q["points"] not in (0, 1):
            continue
        n += 1
        concept = q["concept"]
        state = "demonstrated" if q["points"] == 1 else "misconception"
        # The successor is published before its predecessor is superseded: a failure in between
        # leaves two active records, and the newest one counts, so no state is ever lost.
        publish(Path(args.records) / f"{n:04d}-{concept}.md",
                f"---\nconcept: {concept}\nstate: {state}\ndate: {args.today}\nsource: {source}\n"
                f"status: active\n---\n# {concept}\n\n{explanations.get(q['id'], '')}\n")
        for _, path, f in existing:
            if f["concept"] == concept and f["status"] == "active":
                publish(path, re.sub(r"^status: *active *$", f"status: superseded by LR-{n:04d}",
                                     path.read_text(), count=1, flags=re.M))
        existing = [e for e in existing if e[2]["concept"] != concept or e[2]["status"] != "active"]
    return 0


def public(args):
    exam = load(args.exam)
    errors = served_errors(exam)
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


UPGRADE = "Upgrade to GitHub Pro or make this repository public"
# Settings GitHub returns as {"enabled": bool} and takes back as plain booleans.
PROTECTION_FLAGS = ("required_linear_history", "allow_force_pushes", "allow_deletions", "block_creations",
                    "required_conversation_resolution", "lock_branch", "allow_fork_syncing")


def gh_api(*args, body=None):
    return subprocess.run(["gh", "api", *args], input=None if body is None else json.dumps(body),
                          capture_output=True, text=True)


def app_id(host):
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/exam/host", timeout=30) as r:
            v = json.load(r)["app_id"]
    except Exception as e:
        raise SystemExit(f"the host cannot report its GitHub App id: {e}")
    if type(v) is not int or v <= 0:  # GitHub reads -1 as "any app"
        raise SystemExit(f"the host reported a bad App id: {v!r}")
    return v


def actors(v):
    """GET shape {users: [{login}], teams: [{slug}], apps: [{slug}]} -> PUT shape of plain names."""
    if not v:
        return v
    return {k: [a["login"] if k == "users" else a["slug"] for a in v.get(k, [])] for k in ("users", "teams", "apps")}


def protection_body(current, app):
    """The PUT body: `current` as GitHub returned it, plus `exam` bound to the App (None: unprotected)."""
    rsc = (current or {}).get("required_status_checks") or {"strict": False}
    checks = rsc.get("checks") or [{"context": c, "app_id": None} for c in rsc.get("contexts", [])]
    checks = [c for c in checks if c["context"] != "exam"] + [{"context": "exam", "app_id": app}]
    body = {"required_status_checks": {"strict": rsc.get("strict", False), "checks": checks}}
    if current is None:
        return {**body, "enforce_admins": True, "required_pull_request_reviews": None, "restrictions": None}
    flag = lambda k: (current.get(k) or {}).get("enabled", False)
    reviews = current.get("required_pull_request_reviews")
    if reviews:  # keep every review setting; the allowance lists come back as objects, go in as names
        reviews = {k: actors(v) if k in ("dismissal_restrictions", "bypass_pull_request_allowances") else v
                   for k, v in reviews.items() if k != "url" and not k.endswith("_url")}
    body.update(enforce_admins=flag("enforce_admins"), restrictions=actors(current.get("restrictions")),
                required_pull_request_reviews=reviews)
    body.update({k: flag(k) for k in PROTECTION_FLAGS})
    return body


def setup(args):
    host = os.environ.get("PR_EXAM_HOST")
    if not host:
        return print("PR_EXAM_HOST is not set: point it at the pr-exam host", file=sys.stderr) or 1
    app = app_id(host)
    repo = args.repo
    info = gh_api(f"repos/{repo}")
    if info.returncode:
        return print(info.stderr.strip(), file=sys.stderr) or 1
    branch = json.loads(info.stdout)["default_branch"]
    path = f"repos/{repo}/branches/{branch}/protection"
    got = gh_api(path)
    if got.returncode and UPGRADE in got.stderr:
        return print(f"GitHub will not protect {repo}: the exam gate is agent-enforced only") or 0
    if got.returncode and "Branch not protected" not in got.stderr:
        return print(got.stderr.strip(), file=sys.stderr) or 1
    current = None if got.returncode else json.loads(got.stdout)
    body = protection_body(current, app)
    if current and exam_bound(current, app):
        return print(f"exam is already required on {repo}@{branch}") or 0
    put = gh_api("-X", "PUT", path, "--input", "-", body=body)
    if put.returncode:
        return print(put.stderr.strip(), file=sys.stderr) or 1
    print(f"exam is now required on {repo}@{branch}")
    return 0


def exam_bound(current, app):
    return {"context": "exam", "app_id": app} in (current["required_status_checks"] or {}).get("checks", [])


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
    cf = sub.add_parser("config")
    cf.add_argument("path", nargs="?", default=".pr-exam.json")
    cf.set_defaults(fn=config)
    st = sub.add_parser("setup")
    st.add_argument("repo")
    st.set_defaults(fn=setup)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
