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


def broken(rules):
    """The message of every (is_broken, message) rule that is broken, in order."""
    return [message for is_broken, message in rules if is_broken]


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
        (q.get("type") not in ("choice", "text"), f"question {qid}: type must be choice or text"),
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
        (not nonempty(exam.get("repo")), "repo must be a non-empty string"),
        (not nonempty(exam.get("head_sha")), "head_sha must be a non-empty string"),
        (type(exam.get("pr")) is not int, "pr must be an integer"),
    )) + questions_shape_errors(exam.get("questions"))


def request_errors(exam, request):
    """The exam answers the request it was generated for."""
    mode = exam.get("mode")
    return broken((
        (mode not in MODES, "mode must be first, followup or delta"),
        ("mode" in request and mode != request["mode"],
         f"mode {mode!r} differs from the request's {request.get('mode')!r}"),
        (exam.get("repo") != request.get("repo"), "repo differs from the request's"),
        (exam.get("pr") != request.get("pr"), "pr differs from the request's"),
        (exam.get("head_sha") != request.get("head_sha"), "head_sha differs from the request's"),
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
    """A delta may ask nothing; every exam asks at most MAX_QUESTIONS."""
    low = 0 if mode == "delta" else 1
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


def options_errors(q):
    """A choice question has distinct options of one word count; a text question has none."""
    if q.get("type") == "choice":
        return choice_options_errors(q)
    if q.get("type") == "text" and "options" in q:
        return [f"question {q.get('id')}: text question must not have options"]
    return []


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
        (not nonempty(rubric.get("pass")), f"key {qid}: rubric.pass must be non-empty"),
        (not nonempty(rubric.get("partial")), f"key {qid}: rubric.partial must be non-empty"),
        ("answer" in entry, f"key {qid}: text question must not have an answer"),
    ))


def typed_key_errors(q, entry):
    """A choice key has an answer index and no rubric; a text key has a rubric and no answer."""
    if q.get("type") == "choice":
        return choice_key_errors(q, entry)
    if q.get("type") == "text":
        return text_key_errors(q, entry)
    return []


def key_errors(q, key, diff):
    """The question's key entry explains it with a hunk of the diff."""
    qid = q.get("id")
    entry = key.get(qid)
    if not isinstance(entry, dict):
        return [f"key {qid}: entry must be an object"] if qid in key else []
    hunk = entry.get("hunk")
    return broken((
        (not nonempty(entry.get("explanation")), f"key {qid}: explanation must be non-empty"),
        (not nonempty(hunk), f"key {qid}: hunk must be non-empty"),
        (nonempty(hunk) and hunk not in diff, f"key {qid}: hunk is not in the diff"),
    )) + typed_key_errors(q, entry)


def question_errors(q, kinds, key, diff):
    qid, extra = q.get("id"), set(q) - QUESTION_FIELDS
    return broken((
        (bool(extra), f"question {qid}: unexpected field(s) {sorted(extra)}"),
        (q.get("concept") not in kinds, f"question {qid}: concept {q.get('concept')!r} is not a listed concept"),
    )) + options_errors(q) + key_errors(q, key, diff)


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


def first_errors(questions, kinds):
    """A first exam mixes choice and text, and asks a why concept as text."""
    n = len(questions)
    types = {q.get("type") for q in questions if q.get("type") in ("choice", "text")}
    return broken((
        (n >= 2 and types != {"choice", "text"}, "a first exam with 2 or more questions needs both choice and text"),
        (n >= 3 and not asks_why_as_text(questions, kinds),
         "a first exam with 3 or more questions needs a text question on a why concept"),
    ))


def followup_concept_errors(questions, only):
    asked = [q.get("concept") for q in questions]
    return ([f"concept {cid}: missed, but no followup question asks it" for cid in only if cid not in asked]
            + [f"concept {cid}: not in only_concepts, so a followup may not ask it" for cid in set(asked) - set(only)])


def followup_errors(questions, request):
    """A followup asks exactly the missed concepts, each with a prompt not seen before."""
    prior = request.get("prior_prompts", [])
    return followup_concept_errors(questions, request.get("only_concepts", [])) + [
        f"question {q.get('id')}: prompt repeats one of an earlier attempt"
        for q in questions if q.get("prompt") in prior]


def delta_errors(kinds, request):
    """A delta lists only concepts no earlier exam covered."""
    prior = request.get("prior_concepts", [])
    return [f"concept {cid}: an earlier exam covered it, so a delta may not list it" for cid in kinds if cid in prior]


def mode_errors(exam, request, kinds):
    mode, questions = exam.get("mode"), exam["questions"]
    if mode == "first":
        return first_errors(questions, kinds)
    if mode == "followup":
        return followup_errors(questions, request)
    if mode == "delta":
        return delta_errors(kinds, request)
    return []


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
    if f["state"] not in ("demonstrated", "misconception"):
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


def publish_record(args, q, n, explanation, source):
    concept = q["concept"]
    state = "demonstrated" if q["points"] == 1 else "misconception"
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
        if q["points"] in (0, 1):
            n += 1
            # The successor is published before its predecessor is superseded: a failure in between
            # leaves two active records, and the newest one counts, so no state is ever lost.
            publish_record(args, q, n, explanations.get(q["id"], ""), source)
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


def score_errors(exam, answers, grades):
    texts = {q["id"] for q in exam["questions"] if q["type"] == "text"}
    errors = []
    for q in exam["questions"]:
        errors += broken((
            (q["id"] not in answers, f"question {q['id']}: unanswered"),
            (q["id"] in texts and q["id"] not in grades, f"question {q['id']}: text question has no grade"),
        ))
    return errors + grade_errors(grades, texts)


def points(q, k, answers, grades):
    if q["type"] == "choice":
        return 1 if answers[q["id"]] == k["answer"] else 0
    return GRADE_POINTS[grades[q["id"]]["grade"]]


def question_result(q, k, answers, grades):
    """A question's points; below full points, also its explanation, hunk and (text) grader feedback."""
    r = {"id": q["id"], "concept": q["concept"], "points": points(q, k, answers, grades)}
    if r["points"] < 1:
        r.update(explanation=k["explanation"], hunk=k["hunk"])
        if q["type"] == "text":
            r["feedback"] = grades[q["id"]].get("feedback", "")
    return r


def passed(exam, missed, total):
    """At least 80% of the points, and no why concept missed."""
    kinds = {c["id"]: c["kind"] for c in exam["concepts"]}
    why_ok = all(kinds.get(c) != "why" for c in missed)
    return total / len(exam["questions"]) >= 0.8 and why_ok


def scored(exam, answers, grades):
    """result.json: points per question, the missed concepts, and whether the attempt passed."""
    out = [question_result(q, exam["key"][q["id"]], answers, grades) for q in exam["questions"]]
    missed = [r["concept"] for r in out if r["points"] < 1]
    total = sum(r["points"] for r in out)
    return {"passed": passed(exam, missed, total), "score": total, "max": len(out), "questions": out, "missed": missed}


def score(args):
    exam, answers, grades = load(args.exam), load(args.answers), load(args.grades)
    errors = score_errors(exam, answers, grades)
    if errors:
        return fail("\n".join(errors))
    print(json.dumps(scored(exam, answers, grades)))
    return 0


UPGRADE = "Upgrade to GitHub Pro or make this repository public"
# Settings GitHub returns as {"enabled": bool} and takes back as plain booleans.
PROTECTION_FLAGS = ("required_linear_history", "allow_force_pushes", "allow_deletions", "block_creations",
                    "required_conversation_resolution", "lock_branch", "allow_fork_syncing")
ACTOR_LISTS = ("dismissal_restrictions", "bypass_pull_request_allowances")


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


def legacy_checks(rsc):
    """Checks named only by context are not bound to an app."""
    return [{"context": c, "app_id": None} for c in rsc.get("contexts", [])]


def status_checks(current, app):
    """The required checks of `current`, with `exam` bound to the App in place of any earlier `exam`."""
    rsc = current.get("required_status_checks") or {"strict": False}
    checks = rsc.get("checks") or legacy_checks(rsc)
    return {"strict": rsc.get("strict", False),
            "checks": [c for c in checks if c["context"] != "exam"] + [{"context": "exam", "app_id": app}]}


def flag(current, k):
    return (current.get(k) or {}).get("enabled", False)


def is_url_key(k):
    return k == "url" or k.endswith("_url")


def review_settings(reviews):
    """Every review setting but the urls; the allowance lists come back as objects, go in as names."""
    if not reviews:
        return reviews
    return {k: actors(v) if k in ACTOR_LISTS else v for k, v in reviews.items() if not is_url_key(k)}


def protection_body(current, app):
    """The PUT body: `current` as GitHub returned it, plus `exam` bound to the App (None: unprotected)."""
    body = {"required_status_checks": status_checks(current or {}, app)}
    if current is None:
        return {**body, "enforce_admins": True, "required_pull_request_reviews": None, "restrictions": None}
    return {**body, "enforce_admins": flag(current, "enforce_admins"),
            "restrictions": actors(current.get("restrictions")),
            "required_pull_request_reviews": review_settings(current.get("required_pull_request_reviews")),
            **{k: flag(current, k) for k in PROTECTION_FLAGS}}


def fail(message):
    print(message, file=sys.stderr)
    return 1


def require_exam(repo, branch, current, app):
    """PUT the protection with `exam` required, unless `current` already requires it from the App."""
    path = f"repos/{repo}/branches/{branch}/protection"
    body = protection_body(current, app)
    if current and exam_bound(current, app):
        print(f"exam is already required on {repo}@{branch}")
        return 0
    put = gh_api("-X", "PUT", path, "--input", "-", body=body)
    if put.returncode:
        return fail(put.stderr.strip())
    print(f"exam is now required on {repo}@{branch}")
    return 0


def protect_unread(repo, branch, stderr, app):
    """The protection read failed: a free private repo stays agent-enforced, an unprotected branch gets
    protection, any other error fails."""
    if UPGRADE in stderr:
        print(f"GitHub will not protect {repo}: the exam gate is agent-enforced only")
        return 0
    if "Branch not protected" not in stderr:
        return fail(stderr.strip())
    return require_exam(repo, branch, None, app)


def setup(args):
    host = os.environ.get("PR_EXAM_HOST")
    if not host:
        return fail("PR_EXAM_HOST is not set: point it at the pr-exam host")
    app = app_id(host)
    repo = args.repo
    info = gh_api(f"repos/{repo}")
    if info.returncode:
        return fail(info.stderr.strip())
    branch = json.loads(info.stdout)["default_branch"]
    got = gh_api(f"repos/{repo}/branches/{branch}/protection")
    if got.returncode:
        return protect_unread(repo, branch, got.stderr, app)
    return require_exam(repo, branch, json.loads(got.stdout), app)


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
