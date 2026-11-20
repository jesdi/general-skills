"""babysit-pr's side of the PR exam: whether exams are on, the host's route, the `exam` commit status.

babysit-pr is a client: it sends a repo and a PR number, and reads the `exam` status of a commit. It
never generates, grades or decides a bypass. This module knows nothing of leases or rounds; every
failure is an ExamError, and the caller decides whether it stops the run.
"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

CONTEXT = "exam"  # the commit status the host posts


class ExamError(Exception):
    """Why the exam cannot go on. The message is for the user."""


def gh_api(*args):
    p = subprocess.run(["gh", "api", *args], text=True, capture_output=True)
    if p.returncode:
        raise ExamError(f"gh api {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def validated(config, script, base):
    """`config` as the trusted pr_exam.py reads it; an invalid file is never read as "exams off"."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir="/tmp") as f:  # never TMPDIR: it may be the checkout
        f.write(config)
        f.flush()
        p = subprocess.run([sys.executable, "-I", str(script), "config", f.name], capture_output=True, text=True)
    if p.returncode:
        raise ExamError(f".pr-exam.json on origin/{base} is invalid:\n{p.stderr.strip()}")
    return json.loads(p.stdout)


def enabled(base, script):
    """Whether exams run for the repo, from `.pr-exam.json` on origin/<base>. A config only on the PR
    branch never counts.

    `script` is a trusted pr_exam.py, or None when there is none. It alone reads the config, so
    without it the run stops, whatever the file says (`"enabled": false` included).
    """
    known = subprocess.run(["git", "rev-parse", "--verify", "-q", f"origin/{base}^{{commit}}"], capture_output=True)
    if known.returncode:
        raise ExamError(f"origin/{base} is not readable: run git fetch origin")
    if script is None:
        raise ExamError("pr-exam is not installed. Run: npx @jesdi/skills-cli install pr-exam")
    shown = subprocess.run(["git", "show", f"origin/{base}:.pr-exam.json"], capture_output=True, text=True)
    return shown.returncode != 0 or validated(shown.stdout, script, base)["enabled"]


def required(repo, script):
    """Whether `exam` is a required check of the repo's default branch, as the trusted pr_exam.py reads
    GitHub (`setup --check`); None when it could not tell."""
    p = subprocess.run([sys.executable, "-I", str(script), "setup", "--check", repo], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)["required"] if p.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        return None


def host():
    url = os.environ.get("PR_EXAM_HOST")
    if not url:
        raise ExamError("PR_EXAM_HOST is not set: point it at the pr-exam host")
    return url.rstrip("/")


def post(repo, number):
    """The one call babysit-pr makes, `POST /api/exam` with {repo, pr}: "bring this PR's exam in line with GitHub"."""
    req = urllib.request.Request(host() + "/api/exam", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"repo": repo, "pr": int(number)}).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise ExamError(f"pr-exam host unreachable at {host()}: {e}") from None


def request(repo, number):
    """Ask the host for the PR's exam and print what it answers."""
    answer = post(repo, number)
    print(json.dumps({k: answer.get(k) for k in ("url", "state", "debt", "detail")}), flush=True)
    if answer.get("state") == "skipped":
        print("exam bypassed")
    elif answer.get("debt"):
        print("exam deferred")
    return answer


def notify_merged(repo, number):
    """Best effort after a merge: the host cleans up at once. Its sweep catches a call that failed."""
    try:
        post(repo, number)
    except ExamError:
        pass


def passed(repo, sha):
    """Whether the newest `exam` commit status of `sha` is success, read over every page."""
    out = gh_api("--paginate", f"repos/{repo}/commits/{sha}/statuses", "--jq", ".[] | tojson")
    latest = next((st for st in map(json.loads, out.splitlines()) if st["context"] == CONTEXT), None)
    return latest is not None and latest["state"] == "success"


def wait(repo, number, head, keep_lease):
    """Block until the head's `exam` status is success. No time limit; `keep_lease()` runs on every poll.

    A host answer never ends the wait: it may be about an earlier head. A head that moved is requested
    again at once. While the status stays pending the host is asked again 2, 4, 8, then every 16
    polls after the last request, whatever it answered: a job that fails after an `open` answer is
    retried by the host only when asked again. A host that cannot be reached then is said once per
    request and the wait goes on, on the same schedule.
    """
    pull = lambda: json.loads(gh_api(f"repos/{repo}/pulls/{number}"))
    asks, polls = 1, 0  # requests for this head; polls since the last one
    while True:
        pr = pull()
        if pr.get("merged") or pr["state"] != "open":
            raise ExamError(f"PR {number} is {'merged' if pr.get('merged') else pr['state']}")
        keep_lease()
        sha = pull()["head"]["sha"]  # it may have moved while the lease was renewed
        polls += 1
        if sha != head or polls >= min(2 ** asks, 16):
            asks = 1 if sha != head else asks + 1
            try:
                request(repo, number)
            except ExamError as e:
                print(e, flush=True)
            head, polls = sha, 0
        if passed(repo, sha):
            return print("success")
        time.sleep(float(os.environ.get("BABYSIT_EXAM_POLL_SECONDS", "60")))
