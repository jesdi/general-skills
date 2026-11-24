#!/usr/bin/env python3
"""pr_exam: every deterministic rule of a PR exam, one subcommand each (see `main`).

Each command reads files and prints JSON, or exits non-zero with every error on stderr."""
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
QUESTION_FIELDS = {"id", "concept", "type", "prompt", "options"}
MAX_QUESTIONS = 8
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_slug(v):
    return isinstance(v, str) and bool(SLUG.fullmatch(v))


def nonempty(v):
    return isinstance(v, str) and bool(v.strip())


def broken(rules):
    """The message of every (is_broken, message) rule that is broken, in order."""
    return [message for is_broken, message in rules if is_broken]


def rule_of(table, name):
    """table[name]; None for a name the table does not hold, whatever its JSON type."""
    return table.get(name) if isinstance(name, str) else None


def options_ok(options):
    return isinstance(options, list) and all(nonempty(o) for o in options)


def is_object_list(v):
    return isinstance(v, list) and all(isinstance(q, dict) for q in v)


def shape_errors(q):
    """Served fields must be plain strings, so nothing private can hide inside them."""
    qid = q.get("id")
    return broken((
        (not nonempty(qid), f"question {qid!r}: id must be a non-empty string"),
        (not nonempty(q.get("prompt")), f"question {qid}: prompt must be a non-empty string"),
        (rule_of(QUESTION_TYPES, q.get("type")) is None, f"question {qid}: type must be choice or text"),
        ("options" in q and not options_ok(q["options"]), f"question {qid}: options must be non-empty strings"),
    ))


def questions_shape_errors(questions):
    if not is_object_list(questions):
        return ["questions must be a list of objects"]
    return [e for q in questions for e in shape_errors(q)]


def served_errors(exam):
    """Every field `public` copies must have its plain shape, so no answer can ride inside one."""
    if not isinstance(exam, dict):
        return ["exam must be a JSON object"]
    version = exam.get("version")
    return broken((
        (type(version) is not int or version != 1, "version must be 1"),
        *((not nonempty(exam.get(f)), f"{f} must be a non-empty string") for f in ("repo", "head_sha")),
        (type(exam.get("pr")) is not int, "pr must be an integer"),
    )) + questions_shape_errors(exam.get("questions"))


def request_errors(exam, request):
    """The exam answers the request it was generated for."""
    mode = exam.get("mode")
    return broken((
        (rule_of(MODE_RULES, mode) is None, "mode must be first, followup or delta"),
        ("mode" in request and mode != request["mode"],
         f"mode {mode!r} differs from the request's {request.get('mode')!r}"),
        *((exam.get(f) != request.get(f), f"{f} differs from the request's") for f in ("repo", "pr", "head_sha")),
    ))


def concept_errors(concepts):
    """Each concept: a slug id, a known kind, listed once."""
    errors, seen = [], set()
    for c in concepts:
        cid = c.get("id")
        errors += broken((
            (not is_slug(cid), f"concept {cid!r}: id must be a slug (lowercase letters, digits, single hyphens)"),
            (c.get("kind") not in KINDS, f"concept {cid}: kind must be one of {KINDS}"),
            (cid in seen, f"concept {cid}: listed twice"),
        ))
        seen.add(cid)
    return errors


def count_errors(mode, n):
    """Every exam asks at least its mode's minimum, and at most MAX_QUESTIONS."""
    low, _ = rule_of(MODE_RULES, mode) or (1, None)
    return [] if low <= n <= MAX_QUESTIONS else [f"question count {n} is outside the allowed range"]


def choice_options(q):
    """A choice question's options when they are a list of two or more, else None."""
    options = q.get("options")
    return options if isinstance(options, list) and len(options) >= 2 else None


def choice_options_errors(q):
    qid, options = q.get("id"), choice_options(q)
    if options is None:
        return [f"question {qid}: choice needs a list of options"]
    return broken((
        (len({len(str(o).split()) for o in options}) > 1, f"question {qid}: options differ in word count"),
        (len(set(map(str, options))) != len(options), f"question {qid}: options contain duplicates"),
    ))


def text_options_errors(q):
    return [f"question {q.get('id')}: text question must not have options"] if "options" in q else []


def is_index(a, options):
    return isinstance(a, int) and not isinstance(a, bool) and options is not None and 0 <= a < len(options)


def choice_key_errors(q, entry):
    qid = q.get("id")
    return broken((
        (not is_index(entry.get("answer"), choice_options(q)), f"key {qid}: answer is not an index into options"),
        ("rubric" in entry, f"key {qid}: choice question must not have a rubric"),
    ))


def text_key_errors(q, entry):
    qid, rubric = q.get("id"), entry.get("rubric")
    rubric = rubric if isinstance(rubric, dict) else {}
    return broken((
        *((not nonempty(rubric.get(part)), f"key {qid}: rubric.{part} must be non-empty")
          for part in ("pass", "partial")),
        ("answer" in entry, f"key {qid}: text question must not have an answer"),
    ))


# Per question type: the rule of its options, and the rule of its key entry. A choice has distinct
# options of one word count, an answer index and no rubric; a text has no options, a rubric and no answer.
QUESTION_TYPES = {"choice": (choice_options_errors, choice_key_errors),
                  "text": (text_options_errors, text_key_errors)}


def type_rules(q):
    """(options rule, key rule) of the question's type; a type that is none of them has no rule to break."""
    return rule_of(QUESTION_TYPES, q.get("type")) or (lambda q: [], lambda q, entry: [])


def key_errors(q, key, diff):
    """The question's key entry explains it with a hunk of the diff."""
    qid = q.get("id")
    entry = key.get(qid)
    if not isinstance(entry, dict):
        return [f"key {qid}: entry must be an object"] if qid in key else []
    hunk = entry.get("hunk")
    return broken((
        *((not nonempty(entry.get(f)), f"key {qid}: {f} must be non-empty") for f in ("explanation", "hunk")),
        (nonempty(hunk) and hunk not in diff, f"key {qid}: hunk is not in the diff"),
    )) + type_rules(q)[1](q, entry)


def question_errors(q, kinds, key, diff):
    qid, extra = q.get("id"), set(q) - QUESTION_FIELDS
    return broken((
        (bool(extra), f"question {qid}: unexpected field(s) {sorted(extra)}"),
        (q.get("concept") not in kinds, f"question {qid}: concept {q.get('concept')!r} is not a listed concept"),
    )) + type_rules(q)[0](q) + key_errors(q, key, diff)


def learned_errors(q, learned):
    """A concept learned as skip is not asked; one learned as due is asked as choice only."""
    concept = q.get("concept")
    state = learned.get(concept)
    return broken((
        (state == "skip", f"concept {concept}: learned as skip, so it may not be asked"),
        (state == "due" and q.get("type") != "choice", f"concept {concept}: learned as due, so it may not be asked as text"),
    ))


def question_list_errors(questions, kinds, key, diff, learned):
    """Per question, in order: its concept asked once, its own rules and key, its learned state."""
    errors, seen = [], set()
    for q in questions:
        if q.get("concept") in seen:
            errors.append(f"concept {q.get('concept')}: asked by more than one question")
        seen.add(q.get("concept"))
        errors += question_errors(q, kinds, key, diff) + learned_errors(q, learned)
    return errors


def duplicate_id_errors(qids):
    return [f"question {qid}: id used more than once" for qid in {i for i in qids if qids.count(i) > 1}]


def key_match_errors(qids, key):
    """One key entry per question, none for anything else."""
    return ([f"key: no entry for question {qid}" for qid in qids if qid not in key]
            + [f"key: entry {qid} matches no question" for qid in key if qid not in qids])


def misconception_errors(learned, asked, kinds, n):
    """With room left, a recorded misconception that is not a listed concept is asked."""
    if n >= MAX_QUESTIONS:
        return []
    covered = asked | set(kinds)
    return [f"concept {cid}: recorded misconception is not asked"
            for cid, state in learned.items() if state == "misconception" and cid not in covered]


RANK = {k: i for i, k in enumerate(KINDS)}


def lowest_asked_rank(kinds, asked):
    return max((RANK[kinds[c]] for c in asked if kinds.get(c) in RANK), default=-1)


def unasked(kinds, asked, learned):
    """[(id, kind)] of listed concepts of a known kind that no question asks, other than those learned as skip."""
    return [(cid, kind) for cid, kind in kinds.items()
            if not (cid in asked or kind not in RANK or learned.get(cid) == "skip")]


def unasked_errors(kinds, asked, learned, n):
    """With room left every listed concept is asked; when full, none dropped outranks one asked."""
    lowest = lowest_asked_rank(kinds, asked)
    missing = unasked(kinds, asked, learned)
    if n < MAX_QUESTIONS:
        return [f"concept {cid}: not asked, but only {n} of {MAX_QUESTIONS} questions used" for cid, _ in missing]
    return [f"concept {cid}: dropped while a lower-priority concept is asked" for cid, kind in missing
            if lowest > RANK[kind]]


def coverage_errors(questions, kinds, learned):
    """Which concepts the exam must ask: recorded misconceptions, then listed concepts by priority."""
    n, asked = len(questions), {q.get("concept") for q in questions}
    return misconception_errors(learned, asked, kinds, n) + unasked_errors(kinds, asked, learned, n)


def asks_why_as_text(questions, kinds):
    return any(q.get("type") == "text" and kinds.get(q.get("concept")) == "why" for q in questions)


def first_errors(questions, kinds, request):
    """A first exam mixes choice and text, and asks a why concept as text."""
    n = len(questions)
    types = {q.get("type") for q in questions if rule_of(QUESTION_TYPES, q.get("type"))}
    return broken((
        (n >= 2 and types != set(QUESTION_TYPES), "a first exam with 2 or more questions needs both choice and text"),
        (n >= 3 and not asks_why_as_text(questions, kinds),
         "a first exam with 3 or more questions needs a text question on a why concept"),
    ))


def followup_concept_errors(questions, only):
    asked = [q.get("concept") for q in questions]
    return ([f"concept {cid}: missed, but no followup question asks it" for cid in only if cid not in asked]
            + [f"concept {cid}: not in only_concepts, so a followup may not ask it" for cid in set(asked) - set(only)])


def followup_errors(questions, kinds, request):
    """A followup asks exactly the missed concepts, each with a prompt not seen before."""
    prior = request.get("prior_prompts", [])
    return followup_concept_errors(questions, request.get("only_concepts", [])) + [
        f"question {q.get('id')}: prompt repeats one of an earlier attempt"
        for q in questions if q.get("prompt") in prior]


def delta_errors(questions, kinds, request):
    """A delta lists only concepts no earlier exam covered."""
    prior = request.get("prior_concepts", [])
    return [f"concept {cid}: an earlier exam covered it, so a delta may not list it" for cid in kinds if cid in prior]


# Per mode: the fewest questions it may ask, and its own rule over (questions, kinds, request).
MODE_RULES = {"first": (1, first_errors), "followup": (1, followup_errors), "delta": (0, delta_errors)}


def mode_errors(exam, request, kinds):
    _, rule = rule_of(MODE_RULES, exam.get("mode")) or (None, lambda *args: [])
    return rule(exam["questions"], kinds, request)


def errors_for(exam, request, diff, learned):
    """Every broken rule of `exam`, one message each."""
    errors = served_errors(exam)
    if not isinstance(exam, dict) or not is_object_list(exam.get("questions")):
        return errors  # the rules below walk the questions
    concepts, questions, key = exam.get("concepts", []), exam["questions"], exam.get("key", {})
    kinds = {c.get("id"): c.get("kind") for c in concepts}
    errors += (request_errors(exam, request) + concept_errors(concepts)
               + count_errors(exam.get("mode"), len(questions))
               + question_list_errors(questions, kinds, key, diff, learned))
    qids = [q.get("id") for q in questions]
    return (errors + duplicate_id_errors(qids) + key_match_errors(qids, key)
            + coverage_errors(questions, kinds, learned) + mode_errors(exam, request, kinds))


def load(path):
    return json.loads(Path(path).read_text())


def validate(args):
    exam, request = load(args.exam), load(args.request)
    diff = Path(request["diff"]).read_text()
    try:
        learned = learned_states(request["records"], request["today"]) if request.get("records") else {}
    except ValueError as e:
        return fail(e)
    errors = errors_for(exam, request, diff, learned)
    if errors:
        return fail("\n".join(errors))
    print(f"valid: {len(exam['questions'])} questions, {len(exam['concepts'])} concepts")
    return 0


def bypass_label_errors(labels):
    if not isinstance(labels, dict):
        return ["bypassLabels: must be an object of label to skip or defer"]
    return [f"bypassLabels: {k!r} has mode {m!r}, allowed values: skip, defer"
            for k, m in labels.items() if m not in ("skip", "defer")]


def config_errors(c):
    if not isinstance(c, dict):
        return ["config: must be a JSON object"]
    errors = [f"unknown key: {k}" for k in c if k not in ("enabled", "bypassLabels")]
    if not isinstance(c.get("enabled", True), bool):
        errors.append("enabled: must be true or false")
    return errors + bypass_label_errors(c.get("bypassLabels", {}))


def config(args):
    path = Path(args.path)
    try:
        c = json.loads(path.read_text()) if path.exists() else {}
    except ValueError as e:
        return fail(f"invalid json in {path}: {e}")
    errors = config_errors(c)
    if errors:
        return fail("\n".join(errors))
    print(json.dumps({"enabled": c.get("enabled", True), "bypassLabels": c.get("bypassLabels", {})}))
    return 0


FILE_RE = re.compile(r"^(\d{4,})-(.+)\.md$")
FIELDS = ("concept", "state", "date", "source", "status")


def check_record(f):
    """ValueError naming what is wrong with a record's frontmatter fields."""
    if any(not f.get(k) for k in FIELDS):
        raise ValueError("missing field")
    if f["state"] not in ("demonstrated", "assisted", "misconception"):
        raise ValueError("state")
    datetime.date.fromisoformat(f["date"])
    if not re.fullmatch(r"active|superseded by LR-\d{4,}", f["status"]):
        raise ValueError("status")


def record_fields(path):
    """The frontmatter fields of one record; ValueError naming the file when it is malformed."""
    head = path.read_text().split("---")
    f = dict(re.findall(r"^(\w+): *(.*?) *$", head[1] if len(head) > 2 else "", re.M))
    try:
        check_record(f)
    except ValueError as e:
        raise ValueError(f"{path.name}: malformed record ({e})") from None
    return f


def read_records(directory):
    """[(number, path, fields)] for every record; ValueError naming the file when one is malformed."""
    out = []
    for path in sorted(Path(directory).glob("*.md")):
        m = FILE_RE.match(path.name)
        if m:
            out.append((int(m.group(1)), path, record_fields(path)))
    return sorted(out, key=lambda r: r[0])  # oldest first: when two records are active, the newest counts


def learned_states(directory, today):
    today = datetime.date.fromisoformat(today)
    states = {}
    for _, _, f in read_records(directory):
        if f["status"] != "active":
            continue
        if f["state"] == "misconception":
            states[f["concept"]] = "misconception"
        elif f["state"] == "assisted":
            states[f["concept"]] = "due"
        else:
            age = (today - datetime.date.fromisoformat(f["date"])).days
            states[f["concept"]] = "skip" if age < 30 else "due"
    return states


def learned(args):
    try:
        print(json.dumps(learned_states(args.records, args.today)))
    except ValueError as e:
        return fail(e)
    return 0


def slug_errors(result):
    return [f"concept {q.get('concept')!r}: not a slug, so it cannot name a record"
            for q in result["questions"] if not is_slug(q.get("concept"))]


def record(args):
    exam, result = load(args.exam), load(args.result)
    bad = slug_errors(result)
    if bad:
        return fail("\n".join(bad))
    explanations = {qid: k["explanation"] for qid, k in exam["key"].items()}
    source = f"{exam['repo']}#{exam['pr']}"
    Path(args.records).mkdir(parents=True, exist_ok=True)
    # exclusive lock: read, number, supersede and write are one critical section across processes
    with open(Path(args.records) / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = read_records(args.records)
        except ValueError as e:
            return fail(e)
        return write_records(args, result, existing, explanations, source)


def publish(path, text):
    """Write a whole file or nothing: readers never see a partial record."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def record_state(q):
    """The state a question's record takes, or None when it leaves no record (half a point)."""
    if q.get("help") == "bypassed" or q["points"] == 0:
        return "misconception"
    if q["points"] == 1:
        return "assisted" if q.get("help") == "assisted" else "demonstrated"


def publish_record(args, q, state, n, explanation, source):
    concept = q["concept"]
    publish(Path(args.records) / f"{n:04d}-{concept}.md",
            f"---\nconcept: {concept}\nstate: {state}\ndate: {args.today}\nsource: {source}\n"
            f"status: active\n---\n# {concept}\n\n{explanation}\n")


def supersede(existing, concept, n):
    """Mark the concept's active records superseded by LR-n, on disk and in `existing`."""
    for _, path, f in existing:
        if f["concept"] == concept and f["status"] == "active":
            publish(path, re.sub(r"^status: *active *$", f"status: superseded by LR-{n:04d}",
                                 path.read_text(), count=1, flags=re.M))
            f["status"] = f"superseded by LR-{n:04d}"


def write_records(args, result, existing, explanations, source):
    n = max([r[0] for r in existing], default=0)
    for q in result["questions"]:
        state = record_state(q)
        if state:
            n += 1
            # The successor is published before its predecessor is superseded: a failure in between
            # leaves two active records, and the newest one counts, so no state is ever lost.
            publish_record(args, q, state, n, explanations.get(q["id"], ""), source)
            supersede(existing, q["concept"], n)
    return 0


def served(exam):
    """The projection the exam page may see: no concept, key, answer or rubric."""
    out = {k: exam[k] for k in ("version", "repo", "pr", "head_sha")}
    out["questions"] = [{k: q[k] for k in ("id", "type", "prompt", "options") if k in q} for q in exam["questions"]]
    return out


def public(args):
    exam = load(args.exam)
    errors = served_errors(exam)
    if errors:
        return fail("\n".join(errors))
    print(json.dumps(served(exam)))
    return 0


GRADE_POINTS = {"pass": 1, "partial": 0.5, "fail": 0}


def grade_errors(grades, texts):
    """Only text questions are graded, each pass, partial or fail."""
    errors = []
    for qid, g in grades.items():
        if qid not in texts:
            errors.append(f"grade for {qid}: not a text question")
        elif g.get("grade") not in GRADE_POINTS:
            errors.append(f"question {qid}: grade {g.get('grade')!r} is not pass, partial or fail")
    return errors


FLAG_KEYS = ("bypassed", "assisted")


def flag_errors(exam, flags):
    ids = {q["id"] for q in exam["questions"]}
    errors = [f"flags: unknown key {k!r}" for k in flags if k not in FLAG_KEYS]
    errors += [f"flags: unknown question {qid}" for k in FLAG_KEYS for qid in flags.get(k, []) if qid not in ids]
    if len(flags.get("bypassed", [])) > 1:
        errors.append("flags: at most one question can be bypassed, got " + ", ".join(flags["bypassed"]))
    return errors


def score_errors(exam, answers, grades, flags=None):
    flags = flags or {}
    errors = flag_errors(exam, flags)
    if errors:
        return errors
    texts = {q["id"] for q in exam["questions"] if q["type"] == "text"}
    for q in exam["questions"]:
        if q["id"] in flags.get("bypassed", []):
            continue
        errors += broken((
            (q["id"] not in answers, f"question {q['id']}: unanswered"),
            (q["id"] in texts and q["id"] not in grades, f"question {q['id']}: text question has no grade"),
        ))
    kept = {k: v for k, v in grades.items() if k not in flags.get("bypassed", [])}
    return errors + grade_errors(kept, texts)


def points(q, k, answers, grades):
    if q["type"] == "choice":
        return 1 if answers[q["id"]] == k["answer"] else 0
    return GRADE_POINTS[grades[q["id"]]["grade"]]


def question_result(q, k, answers, grades, help=None):
    """A question's points; below full points, also its explanation, hunk and (text) grader feedback.
    A bypassed question has no points but carries its explanation and hunk."""
    if help == "bypassed":
        return {"id": q["id"], "concept": q["concept"], "help": help, "explanation": k["explanation"], "hunk": k["hunk"]}
    r = {"id": q["id"], "concept": q["concept"], "points": points(q, k, answers, grades)}
    if r["points"] < 1:
        r.update(explanation=k["explanation"], hunk=k["hunk"])
        if q["type"] == "text":
            r["feedback"] = grades[q["id"]].get("feedback", "")
    if help:
        r["help"] = help
    return r


def passed(exam, missed, total, maximum):
    """At least 80% of the points, and no why concept missed."""
    kinds = {c["id"]: c["kind"] for c in exam["concepts"]}
    why_ok = all(kinds.get(c) != "why" for c in missed)
    return total >= 0.8 * maximum and why_ok


def helped(qid, flags):
    return next((k for k in FLAG_KEYS if qid in flags.get(k, [])), None)


def scored(exam, answers, grades, flags=None):
    """result.json: points per question, the missed concepts, and whether the attempt passed."""
    flags = flags or {}
    out = [question_result(q, exam["key"][q["id"]], answers, grades, helped(q["id"], flags)) for q in exam["questions"]]
    counted = [r for r in out if r.get("help") != "bypassed"]
    missed = [r["concept"] for r in counted if r["points"] < 1]
    total = sum(r["points"] for r in counted)
    return {"passed": passed(exam, missed, total, len(counted)), "score": total, "max": len(counted), "questions": out, "missed": missed}


def score(args):
    exam, answers, grades = load(args.exam), load(args.answers), load(args.grades)
    flags = load(args.flags) if args.flags else {}
    errors = score_errors(exam, answers, grades, flags)
    if errors:
        return fail("\n".join(errors))
    print(json.dumps(scored(exam, answers, grades, flags)))
    return 0


UPGRADE = "Upgrade to GitHub Pro or make this repository public"
UNPROTECTED = "Branch not protected"
CHECKS_OFF = "Required status checks not enabled"


def fail(message):
    print(message, file=sys.stderr)
    return 1


def gh_api(*args, body=None):
    """`gh api`, with `body` sent as the JSON request body."""
    return subprocess.run(["gh", "api", *args, *(["--input", "-"] if body is not None else [])],
                          input=None if body is None else json.dumps(body), capture_output=True, text=True)


def app_id():
    """The id of the host's GitHub App: the only sender whose `exam` status counts."""
    host = os.environ.get("PR_EXAM_HOST")  # babysit-pr's exam.py reads the same variable for its requests
    if not host:
        raise SystemExit("PR_EXAM_HOST is not set: point it at the pr-exam host")
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/exam/host", timeout=30) as r:
            v = json.load(r)["app_id"]
    except Exception as e:
        raise SystemExit(f"the host cannot report its GitHub App id: {e}")
    if type(v) is not int or v <= 0:  # GitHub reads -1 as "any app"
        raise SystemExit(f"the host reported a bad App id: {v!r}")
    return v


def required_checks(rsc):
    """[{context, app_id}] of a required-status-checks answer; a check named only by context is bound to no app."""
    return rsc.get("checks") or [{"context": c, "app_id": None} for c in rsc.get("contexts", [])]


def exam_bound(checks, app=None):
    """Whether `exam` is among the required `checks`: sent by `app`, or by anyone when no app is given."""
    return any(c["context"] == "exam" and app in (None, c.get("app_id")) for c in checks)


def add_exam(path, checks, app):
    """PATCH the required status checks and nothing else: `checks` as they are, plus `exam` from the App."""
    kept = [{"context": c["context"], "app_id": -1 if c.get("app_id") is None else c["app_id"]}
            for c in checks if c["context"] != "exam"]  # a check bound to no app goes back as -1, "any app"
    return gh_api("-X", "PATCH", f"{path}/required_status_checks",
                  body={"checks": kept + [{"context": "exam", "app_id": app}]})


def enable_checks(path, app):
    """A protected branch whose status checks are off: the PATCH turns them on. Where GitHub answers
    404 to it, adding the context creates the sub-resource, and the PATCH then binds `exam` to the App."""
    patched = add_exam(path, [], app)
    if patched.returncode == 0 or "HTTP 404" not in patched.stderr:
        return patched
    added = gh_api("-X", "POST", f"{path}/required_status_checks/contexts", body={"contexts": ["exam"]})
    return added if added.returncode else add_exam(path, [], app)


def protect(path, app):
    """An unprotected branch gets a protection holding only `exam`, enforced for admins too."""
    return gh_api("-X", "PUT", path, body={
        "required_status_checks": {"strict": False, "checks": [{"context": "exam", "app_id": app}]},
        "enforce_admins": True, "required_pull_request_reviews": None, "restrictions": None})


def written(write, where):
    if write.returncode:
        return fail(write.stderr.strip())
    print(f"exam is now required on {where}")
    return 0


def require_unread(stderr, path, where, app):
    """The required checks could not be read: a free private repo stays agent-enforced, a branch with
    no protection or no status checks gets them, any other error fails."""
    if UPGRADE in stderr:
        print(f"GitHub will not protect {where.partition('@')[0]}: the exam gate is agent-enforced only")
        return 0
    write = next((w for why, w in ((UNPROTECTED, protect), (CHECKS_OFF, enable_checks)) if why in stderr), None)
    return written(write(path, app), where) if write else fail(stderr.strip())


def require_exam(got, path, where, app):
    """Make `exam` from the App a required check, given the read `got` of the branch's required checks."""
    if got.returncode:
        return require_unread(got.stderr, path, where, app)
    checks = required_checks(json.loads(got.stdout))
    if exam_bound(checks, app):
        print(f"exam is already required on {where}")
        return 0
    return written(add_exam(path, checks, app), where)


def check_required(got, branch):
    """`setup --check`: whether `exam` is a required check, whoever sends it. Reads only; unknown is an error."""
    if got.returncode and not any(off in got.stderr for off in (UNPROTECTED, CHECKS_OFF)):
        return fail(got.stderr.strip())
    checks = [] if got.returncode else required_checks(json.loads(got.stdout))
    print(json.dumps({"required": exam_bound(checks), "branch": branch}))
    return 0


def setup(args):
    app = None if args.check else app_id()
    info = gh_api(f"repos/{args.repo}")
    if info.returncode:
        return fail(info.stderr.strip())
    branch = json.loads(info.stdout)["default_branch"]
    path = f"repos/{args.repo}/branches/{branch}/protection"
    got = gh_api(f"{path}/required_status_checks")
    if args.check:
        return check_required(got, branch)
    return require_exam(got, path, f"{args.repo}@{branch}", app)


PROMPT_LIMIT = 400_000
TURN_KINDS = {"tip": "Tips", "question": "Chat", "general": "Chat"}
TURN_FIELDS = {"kind", "question", "tip", "draft", "submitted", "points", "feedback", "tips", "thread", "message"}


def skill_section(name):
    m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", (Path(__file__).parent / "SKILL.md").read_text(), re.M | re.S)
    if not m:
        raise ValueError(f"SKILL.md has no '## {name}' section")
    return m.group(1).strip()


def turn_errors(turn, exam):
    kind = turn.get("kind")
    if kind not in TURN_KINDS:
        return ["turn kind: must be tip, question or general"]
    errors = [f"turn field {f}: unknown" for f in sorted(set(turn) - TURN_FIELDS)]
    if kind == "general":
        errors += [f"a {f} is not allowed on a general turn" for f in ("question", "tip", "submitted", "points", "feedback") if f in turn]
        if "draft" in turn:
            errors.append("a draft is not allowed on a general turn")
    else:
        if turn.get("question") not in {q["id"] for q in exam["questions"]}:
            errors.append(f"question {turn.get('question')}: not in the exam")
        if not isinstance(turn.get("draft", ""), str):
            errors.append("draft: must be a string")
    if kind == "tip":
        t = turn.get("tip")
        if type(t) is not int or not 1 <= t <= 3:
            errors.append(f"tip {t}: must be in the range 1-3")
    elif "tip" in turn:
        errors.append("tip: only on a tip turn")
    if kind != "tip" and not isinstance(turn.get("message"), str):
        errors.append("message: required on a chat turn")
    if ("points" in turn or "feedback" in turn) and "submitted" not in turn:
        errors.append("points and feedback need a submitted answer")
    return errors


def key_block(exam, qid):
    q = next(q for q in exam["questions"] if q["id"] == qid)
    return f"Question: {json.dumps(q)}\nKey: {json.dumps(exam['key'][qid])}"


def turn_parts(turn, exam):
    kind, parts = turn["kind"], []
    if kind == "general":
        parts.append("All questions:\n" + "\n".join(key_block(exam, q["id"]) for q in exam["questions"]))
    else:
        parts.append(key_block(exam, turn["question"]))
        if "draft" in turn:
            parts.append(f"Draft: {turn['draft']}")
    if kind == "tip":
        parts.append(f"Tip number: {turn['tip']}")
        parts += [f"Tip {i}: {t}" for i, t in enumerate(turn.get("tips", []), 1)]
        return parts
    if "submitted" in turn:
        parts.append(f"Submitted answer: {turn['submitted']}")
    if "points" in turn:
        parts.append(f"Points: {turn['points']}")
    if "feedback" in turn:
        parts.append(f"Feedback: {turn['feedback']}")
    parts += [f"{m['role']}: {m['text']}" for m in turn.get("thread", [])]
    parts.append(f"Message: {turn['message']}")
    return parts


def context(args):
    exam, turn = load(args.exam), load(args.turn)
    errors = turn_errors(turn, exam)
    if errors:
        return fail("\n".join(errors))
    try:
        rules = skill_section(TURN_KINDS[turn["kind"]])
    except ValueError as e:
        return fail(e)
    prompt = "\n\n".join([rules, "Diff:\n" + Path(args.diff).read_text(), "PR body:\n" + Path(args.body).read_text(),
                          *turn_parts(turn, exam)])
    if len(prompt) > PROMPT_LIMIT:
        return fail(f"prompt is {len(prompt):,} characters, over the {PROMPT_LIMIT:,}-character limit")
    print(prompt)
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
    sc.add_argument("--flags")
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
    st.add_argument("--check", action="store_true", help="only report whether `exam` is a required check")
    st.add_argument("repo")
    st.set_defaults(fn=setup)
    cx = sub.add_parser("context")
    for f in ("exam", "diff", "body", "turn"):
        cx.add_argument(f"--{f}", required=True)
    cx.set_defaults(fn=context)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
