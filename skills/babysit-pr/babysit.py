#!/usr/bin/env python3
"""babysit-pr: `babysit.py lease acquire|renew|check|release --repo R --pr N --session S --host H`.

The lease is a PR comment `<!-- babysit-pr lease: <session> <host> <expires-at> -->` written by the
gh user. Expiry is judged against GitHub's clock (the `Date` header of `gh api -i`), never the host
clock. Among valid lease comments the lowest comment id holds the lease; the label is not the lock.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

LEASE = re.compile(r"\A<!-- babysit-pr lease: (\S+) (\S+) (\S+) -->")
TTL = timedelta(minutes=90)
INSTALL = "npx @jesdi/skills-cli install pr-risk"
PR_RISK_DIRS = (".my-skills/pr-risk", ".agents/skills/pr-risk", ".claude/skills/pr-risk")
CLAIM_PENDING = "<!-- babysit-pr claim: pending -->\n"
CLAIM_HELD = "<!-- babysit-pr claim: held -->\n"
MAX_ROUNDS = 5
HANDOFF = re.compile(r"\A## 🧑 Needs human: (.*)")
NEW_BUDGET = ("5 fix rounds", "same failure twice")
SEEN = "<!-- babysit-pr seen -->\n"
PASS = {"success", "neutral", "skipped"}
MENTION = "@babysitter"
MERGE = re.compile(rf"\A{MENTION} merge(?:\s+(\S+))?\s*\Z")
CONNECTIONS = {
    "comments": "databaseId author { login } editor { login } lastEditedAt createdAt body",
    "reviews": "author { login } state body submittedAt",
    "reviewThreads": "isResolved comments(first: 100) { nodes { author { login } createdAt } pageInfo { hasNextPage } }",
    "commits": "commit { oid }",
}


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def gh(*args, check=True):
    p = subprocess.run(["gh", *args], text=True, capture_output=True)
    if check and p.returncode:
        fail(f"gh {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return parsedate_to_datetime(s)


class Pr:
    def __init__(self, repo, number):
        self.identity_scope = (repo, number)
        self.repo = f"repos/{repo}"
        self.base = f"{self.repo}/issues/{number}"

    def now(self):
        out = gh("api", "-i", "user")
        return parse(re.search(r"^Date: (.+)$", out, re.M).group(1).strip())

    def user(self):
        return gh("api", "user", "--jq", ".login").strip()

    def comments(self):
        out = gh("api", "--paginate", f"{self.base}/comments", "--jq", ".[] | tojson")
        return [json.loads(l) for l in out.splitlines() if l]

    def write_comment(self, cid, body):
        return json.loads(gh("api", "-X", "PATCH", f"{self.repo}/issues/comments/{cid}", "-f", f"body={body}"))

    def post(self, body):
        c = json.loads(gh("api", "-X", "POST", f"{self.base}/comments", "-f", f"body={body}"))
        CommentIdentities(*self.identity_scope, c["user"]["login"]).remember(c["id"], body)
        return c

    def label(self, add=(), remove=()):
        for name in add:
            gh("api", "-X", "POST", f"{self.base}/labels", "-f", f"labels[]={name}")
        for name in remove:
            gh("api", "-X", "DELETE", f"{self.base}/labels/{name}", check=False)


class CommentIdentities:
    """Local creation receipts: mutable GitHub bodies cannot change a known identity.

    Scope by repository, PR and authenticated login. Separate immutable files per
    comment survive process restarts and concurrent sessions without lost updates.
    Missing receipts never establish trust from an edited body.
    """
    def __init__(self, repo, number, owner):
        scope = hashlib.sha256(json.dumps([repo, str(number), owner]).encode()).hexdigest()
        self.root = Path.home() / ".local/state/babysit-pr/comments" / scope

    def kind(self, cid):
        path = self.root / str(cid)
        return path.read_text() if path.exists() else None

    def remember(self, cid, body):
        kind = "lease" if LEASE.match(body) else "handoff" if HANDOFF.match(body) else None
        if kind:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with (self.root / str(cid)).open("x") as f:
                    f.write(kind)
            except FileExistsError:
                pass  # the original identity wins, even if the body has changed


def marker(session, host, expires):
    return f"<!-- babysit-pr lease: {session} {host} {iso(expires)} -->"


def leases(comments, user, include_ended=False):
    """Lease comments by `user`: (id, session, host, expires, comment)."""
    out = []
    for c in comments:
        m = LEASE.match(c["body"])
        if m and c["user"]["login"] == user and (include_ended or not is_ended(c)):
            out.append((c["id"], m[1], m[2], parse(m[3]), c))
    return sorted(out, key=lambda l: l[0])


def holder(found, now):
    return next((l for l in found if l[3] > now), None)


def is_ended(comment):
    lines = comment["body"].split("\n")
    return len(lines) > 1 and lines[1] == "ended"


def rest_of(comment):
    rest = comment["body"].partition("\n")[2]
    while rest.startswith("ended\n") or rest == "ended":
        rest = rest.partition("\n")[2]
    return rest


def end(pr, comment):
    pr.write_comment(comment["id"], comment["body"].split("\n", 1)[0] + "\nended\n" + rest_of(comment))


def inherited_state(comments, user):
    # Unmarked comments are leases from before claim confirmation was introduced.
    # Pending claims (including ended losers and crashed attempts) never own history.
    history = [l for l in leases(comments, user, include_ended=True)
               if CLAIM_PENDING not in l[4]["body"]]
    if not history or budget_spent(comments, user, history[-1][0]):
        return "rounds: 0\n"
    return rest_of(history[-1][4]).replace(CLAIM_HELD, "")


def budget_spent(comments, user, after_id):
    """The owner's latest handoff newer than the last lease ended the budget (rounds or repeat)."""
    handoffs = [(c["id"], m[1].strip()) for c in comments if c["user"]["login"] == user
                and (m := HANDOFF.match(c["body"]))]
    return bool(handoffs) and max(handoffs)[0] > after_id and max(handoffs)[1] in NEW_BUDGET


def signature(check, error):
    error = re.sub(r"/tmp/\S+", "<tmp>", error)
    error = re.sub(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{7,}\b|\d+", "<n>", error, flags=re.I)
    return f"{check}: {' '.join(error.split())}"


def round_state(comment):
    """(rounds, [(round, signature)], halt reason) kept after the marker line of a lease comment."""
    rest = rest_of(comment)
    rounds = int(re.search(r"^rounds: (\d+)", rest, re.M)[1])
    sigs = [(int(m[1] or 0), m[2]) for m in re.finditer(r"^signature: (?:r(\d+) )?(.*)$", rest, re.M)]
    halt = re.search(r"^halt: (.*)$", rest, re.M)
    return rounds, sigs, halt[1] if halt else None


def save_round_state(pr, comment, rounds, sigs, halt, ops=()):
    body = comment["body"].split("\n", 1)[0] + "\n" + ("ended\n" if is_ended(comment) else "")
    held = CLAIM_HELD if CLAIM_HELD in comment["body"] else ""
    lines = [f"rounds: {rounds}"] + [f"op: {n} {o}" for n, o in ops] + [f"signature: r{n} {s}" for n, s in sigs]
    return pr.write_comment(comment["id"], body + held + "\n".join(lines + ([f"halt: {halt}"] if halt else [])) + "\n")


def round_ops(comment):
    """[(round, op)]: every start operation of this budget, so a late retry of an old one is recognised."""
    return [(int(m[1]), m[2]) for m in re.finditer(r"^op: (\d+) (.+)$", rest_of(comment), re.M)]


def own_lease(repo, number, session):
    require_pr_risk(repo, number)
    pr = Pr(repo, number)
    top = holder(leases(pr.comments(), pr.user()), pr.now())
    if top is None or top[1] != session:
        fail(f"{session} does not hold the lease")
    return pr, top[4]


@contextmanager
def locked():
    """Serialize read-modify-write of babysit state on this host."""
    with open(Path.home() / ".babysit-pr.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def round_cmd(*args, **kw):
    with locked():
        round_locked(*args, **kw)


def round_locked(verb, repo, number, session, check, error, op=None):
    if not op:
        fail(f"round {verb} needs --op (reuse the same ID on retries)")
    pr, c = own_lease(repo, number, session)
    user = pr.user()
    rounds, sigs, halt = round_state(c)
    if verb == "start":
        op = f"{session} {op}"  # ops are scoped to their session
        done = dict((o, n) for n, o in round_ops(c))
        if op in done:  # retry of a start whose answer was lost: finish its renewal, spend nothing
            extend(pr, c, session, LEASE.match(c["body"])[2], pr.now() + TTL, user)
            print(f"round {done[op]}")
            return
        if halt:
            fail(halt)
        if rounds >= MAX_ROUNDS:
            fail("5 fix rounds")
        c = save_round_state(pr, c, rounds + 1, sigs, halt, round_ops(c) + [(rounds + 1, op)])
        extend(pr, c, session, LEASE.match(c["body"])[2], pr.now() + TTL, user)
        print(f"round {rounds + 1}")
        return
    sig = signature(check, error)
    failure_round = dict((o, n) for n, o in round_ops(c)).get(f"{session} {op}")
    if failure_round is None:
        fail("record-failure --op must name its originating round start")
    if (failure_round, sig) in sigs:
        return  # response lost, including a retry after a later round started
    if any(s == sig and n != failure_round for n, s in sigs):
        save_round_state(pr, c, rounds, sigs, "same failure twice", round_ops(c))
        own_lease(repo, number, session)
        fail("same failure twice")
    sigs.append((failure_round, sig))
    save_round_state(pr, c, rounds, sigs, halt, round_ops(c))
    # A PATCH can cross expiry on another host. Never acknowledge that write
    # from the pre-PATCH lease snapshot: the new holder may already own history.
    own_lease(repo, number, session)


def seen_comment(pr):
    """The owner's shared comment of first sightings, so the stall bound survives host changes."""
    user = pr.user()
    return next((c for c in pr.comments() if c["user"]["login"] == user and c["body"].startswith(SEEN)), None)


def load_seen(pr):
    c = seen_comment(pr)
    try:
        return json.loads(c["body"][len(SEEN):]) if c else {}
    except ValueError:  # damaged: start over
        return {}


def first_seen(pr, key, now, since=None):
    """Earliest pending time of a check: when we first saw it (GitHub time), or its created_at if earlier."""
    with locked():
        seen = load_seen(pr)
        earliest = iso(min(now, since) if since else now)
        if key not in seen or earliest < seen[key]:  # an earlier created_at learned later still counts
            prefix = key.rsplit("#", 1)[0] + "#"  # only the current head commit's entries matter
            seen = {k: v for k, v in seen.items() if k.startswith(prefix)}
            seen[key] = earliest if key not in seen else min(earliest, seen[key])
            body = SEEN + json.dumps(seen)
            c = seen_comment(pr)
            pr.write_comment(c["id"], body) if c else pr.post(body)
    return parse(seen[key])


def checks_cmd(repo, number, wait):
    pr = Pr(repo, number)
    sha = json.loads(gh("api", f"{pr.repo}/pulls/{number}"))["head"]["sha"]
    out = gh("api", "--paginate", f"{pr.repo}/commits/{sha}/check-runs", "--jq", ".check_runs[] | tojson")
    runs = [json.loads(l) for l in out.splitlines() if l]
    now = pr.now()
    red = any(r["status"] == "completed" and r["conclusion"] not in PASS for r in runs)
    # Commit statuses are separate from check runs. Newest first, so the first per context is current.
    latest = {}
    out = gh("api", "--paginate", f"{pr.repo}/commits/{sha}/statuses", "--jq", ".[] | tojson")
    for line in out.splitlines():
        if line:
            status = json.loads(line)
            latest.setdefault(status["context"], status)
    reset_terminal_statuses(pr, f"{repo}#{sha}#status:", latest)
    red = red or any(s["state"] in ("failure", "error") for s in latest.values())
    pending_runs = [r for r in runs if r["status"] != "completed"]
    pending_statuses = [s for s in latest.values() if s["state"] == "pending"]
    if not runs and not latest:
        print("no checks")
    elif red:
        print("red")
    elif pending_runs or pending_statuses:
        # Observe every pending item, even when an earlier one is already stalled.
        times = [pending_since(pr, f"{repo}#{sha}#run:{r.get('id', r['name'])}",
                               min(filter(None, (r.get("started_at"), r.get("created_at"))), default=None), now) for r in pending_runs]
        times += [pending_since(pr, f"{repo}#{sha}#status:{s['context']}",
                                s.get("created_at"), now) for s in pending_statuses]
        print("CI stalled" if now - min(times) >= timedelta(minutes=wait) else "pending")
    else:
        print("green")


def reset_terminal_statuses(pr, prefix, statuses):
    """Only a terminal observation ends a context's continuous pending interval."""
    terminal = {prefix + name for name, s in statuses.items() if s["state"] != "pending"}
    with locked():
        seen = load_seen(pr)
        if terminal.intersection(seen):
            c = seen_comment(pr)
            pr.write_comment(c["id"], SEEN + json.dumps({k: v for k, v in seen.items() if k not in terminal}))


def pending_since(pr, key, timestamp, now):
    """Keep queued time through a running transition, without sharing it across runs."""
    return first_seen(pr, key, now, parse(timestamp) if timestamp else None)


def confirm(pr, comment):
    if CLAIM_PENDING in comment["body"]:
        pr.write_comment(comment["id"], comment["body"].replace(CLAIM_PENDING, CLAIM_HELD))


def extend(pr, c, session, host, expires, user):
    """Renew, then verify election and whether a takeover crossed our old expiry.

    A contender posted after our previous expiry may already be cleaning up our
    comment. Yield even if this PATCH temporarily resurrected the lower id.
    A contender posted while we were valid remains subject to lowest-id election.
    """
    previous_expiry = parse(LEASE.match(c["body"])[3])
    c = pr.write_comment(c["id"], marker(session, host, expires) + "\n" + rest_of(c))
    found = leases(pr.comments(), user)
    now = pr.now()
    takeover = next((l for l in found if l[0] != c["id"] and l[3] > now
                     and parse(l[4]["created_at"]) >= previous_expiry), None)
    winner = takeover or holder(found, now)
    if winner is None or winner[0] != c["id"]:
        end(pr, c)
        fail(f"lost the lease to {winner[1] if winner else 'another session'}")
    confirm(pr, c)


def require_pr_risk(repo, number):
    if trusted_pr_risk_dirs():
        return
    if subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True).returncode == 0:
        base = gh("api", f"repos/{repo}/pulls/{number}", "--jq", ".base.ref", check=False).strip()
        if base and base_install(base):
            return
    fail(f"pr-risk is not installed. Run: {INSTALL}")


def renew(repo, number, session):
    pr, c = own_lease(repo, number, session)
    extend(pr, c, session, LEASE.match(c["body"])[2], pr.now() + TTL, pr.user())


def lease(verb, repo, number, session, host):
    # Every lease-body writer reads its snapshot inside the same lock as rounds.
    with locked():
        return lease_locked(verb, repo, number, session, host)


def lease_locked(verb, repo, number, session, host):
    require_pr_risk(repo, number)
    pr = Pr(repo, number)
    user = pr.user()
    comments = pr.comments()
    now = pr.now()
    found = leases(comments, user)
    top = holder(found, now)
    mine = top is not None and top[1] == session

    if verb == "check":
        if not mine:
            fail(f"{session} does not hold the lease" + (f" (babysat by {top[2]})" if top else ""))
    elif verb == "renew":
        return renew(repo, number, session)
    elif verb == "release":
        for l in found:
            if l[1] == session:
                end(pr, l[4])
        if not top or mine:
            pr.label(remove=["babysitting"])
    else:  # acquire
        if top and not mine:
            fail(f"already babysat by {top[2]}")
        carried = inherited_state(comments, user)
        if mine:
            c = top[4]
            extend(pr, c, session, host, now + TTL, user)
        else:
            claim = CLAIM_PENDING + carried
            c = pr.post(marker(session, host, now + TTL) + "\n" + claim)
            c = pr.write_comment(c["id"], marker(session, host, parse(c["updated_at"]) + TTL) + "\n" + claim)
            # Re-read: a session that posted within the same second may have the lower id.
            found = leases(pr.comments(), user)
            now = pr.now()
            winner = holder(found, now)
            if winner is None or winner[0] != c["id"]:
                end(pr, c)
                fail(f"already babysat by {winner[2] if winner else 'another session'}")
            # The initial read can predate a final write by the old holder.
            # Inherit after publishing our claim, which fences successful old
            # writers via their post-write lease check, before confirming ours.
            carried = inherited_state(pr.comments(), user)
            c = pr.write_comment(c["id"], c["body"].split("\n", 1)[0] + "\n" + CLAIM_PENDING + carried)
            confirm(pr, c)
            # Publish and elect our claim before touching expired comments. A
            # renewal that completed before our post wins the fresh election;
            # one racing this cleanup sees our claim in its own post-write check.
            for l in leases(pr.comments(), user):
                if l[0] != c["id"] and l[3] <= now:
                    end(pr, l[4])
        pr.label(add=["babysitting"], remove=["needs-human"])


def connection(repo, number, name):
    """All nodes of one PR connection over GraphQL (REST has no comment `editor`)."""
    owner, _, repo_name = repo.partition("/")
    query = ("query($o: String!, $r: String!, $n: Int!, $after: String) { repository(owner: $o, name: $r) "
             f"{{ pullRequest(number: $n) {{ {name}(first: 100, after: $after) "
             f"{{ nodes {{ {CONNECTIONS[name]} }} pageInfo {{ hasNextPage endCursor }} }} }} }} }}")
    nodes, after = [], []
    while True:
        out = json.loads(gh("api", "graphql", "-f", f"query={query}", "-f", f"o={owner}",
                            "-f", f"r={repo_name}", "-F", f"n={number}", *after))
        page = out["data"]["repository"]["pullRequest"][name]
        nodes += page["nodes"]
        if not page.get("pageInfo", {}).get("hasNextPage"):
            return nodes
        after = ["-f", f"after={page['pageInfo']['endCursor']}"]


def login_of(node):
    return (node.get("author") or {}).get("login") or "ghost"


def owner_comments(repo, number):
    """Hand off for any review or non-owner comment; else the owner's `@babysitter` instructions."""
    owner = Pr(repo, number).user()
    identities = CommentIdentities(repo, number, owner)
    comments = sorted(connection(repo, number, "comments"), key=lambda c: c["databaseId"])
    for c in comments:
        if login_of(c) == owner and not c.get("lastEditedAt"):
            identities.remember(c["databaseId"], c["body"])
    kinds = {c["databaseId"]: identities.kind(c["databaseId"]) for c in comments}
    last = max((i for i, c in enumerate(comments)
                if login_of(c) == owner and kinds[c["databaseId"]] != "lease"
                and HANDOFF.match(c["body"]) and not c.get("lastEditedAt")), default=-1)  # an edited handoff may be forged
    since = parse(comments[last]["createdAt"]) if last >= 0 else None
    handoff, instructions = None, []
    for c in comments[last + 1:]:
        author = login_of(c)
        editor = (c.get("editor") or {}).get("login")
        kind = kinds[c["databaseId"]]
        if author == owner and kind == "lease":
            continue  # creation receipt, independent of the lease's current body
        if author == owner and not c["body"].startswith(MENTION):
            # An unknown edited lease may be a handoff with its heading removed.
            # Without a receipt it is safe to ignore only if no earlier instruction
            # could be replayed. This also supports leases from another host.
            if c.get("lastEditedAt") and (kind == "handoff" or not LEASE.match(c["body"])
                                          or editor != owner or instructions):
                handoff = handoff or "handoff comment edited, boundary unverifiable"
            continue  # the babysitter's own lease comments and the owner's plain chatter
        if author != owner:
            handoff = handoff or f"review comment from {author}"
        elif editor not in (None, owner):
            handoff = handoff or f"owner comment edited by {editor}"
        elif c.get("lastEditedAt"):  # the last editor alone cannot rule out an earlier edit by another account
            handoff = handoff or "owner comment edited, edit history unverifiable"
        else:
            instructions.append({"id": c["databaseId"], "text": c["body"], "approved_sha": None})
            m = MERGE.match(c["body"])
            if m:
                oids = [k["commit"]["oid"] for k in connection(repo, number, "commits")]
                hits = {o for o in oids if re.fullmatch(r"[0-9a-f]{7,40}", m[1] or "") and o.startswith(m[1])}
                sha = hits.pop() if len(hits) == 1 else None  # an ambiguous prefix approves nothing
                if sha:  # the approval names its commit; the head is never inferred from dates
                    instructions[-1]["approved_sha"] = sha
                else:
                    handoff = handoff or "merge approval must name a commit: @babysitter merge <sha>"
    for r in connection(repo, number, "reviews"):
        author = login_of(r)
        if r["state"] == "CHANGES_REQUESTED":
            handoff = handoff or f"changes requested by {author}"
        elif author != owner and r["body"].strip() and (since is None or parse(r["submittedAt"]) >= since):
            handoff = handoff or f"review comment from {author}"
    for t in connection(repo, number, "reviewThreads"):
        if not t["isResolved"]:
            handoff = handoff or f"review comment from {login_of(t['comments']['nodes'][0])}"
        for c in t["comments"]["nodes"]:
            stamp = c.get("createdAt")  # resolving a thread must not hide its comments
            if login_of(c) != owner and (since is None or not stamp or parse(stamp) >= since):  # equal stamps: order unknown, hand off
                handoff = handoff or f"review comment from {login_of(c)}"
        if t["comments"].get("pageInfo", {}).get("hasNextPage"):  # unread comments: fail closed
            handoff = handoff or "review thread too long to read"
    return {"handoff": handoff, "instructions": [] if handoff else instructions}


def checkout_root():
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(p.stdout.strip() if p.returncode == 0 else ".").resolve()


def trusted_pr_risk_dirs():
    """Installs a PR cannot change: next to this skill, and under $HOME. Never inside the checkout.

    A path counts only if it, and the module files in it, resolve outside the checkout:
    HOME may be the checkout, and a symlink may point into it. A project install is
    read from origin/<base> instead.
    """
    root = checkout_root()
    home = Path.home()
    outside = lambda p: not p.resolve().is_relative_to(root)
    return [d.resolve() for d in [Path(__file__).resolve().parent.parent / "pr-risk",
                                  *(home / n for n in PR_RISK_DIRS)]
            if d.is_dir() and outside(d)
            and all((d / n).is_file() and outside(d / n) for n in ("merge_risk.py", "merge_policy.py"))]


def base_install(base):
    """[(file name, bytes)] of a pr-risk install committed on origin/<base>, else None."""
    names = ("merge_risk.py", "merge_policy.py")
    for d in PR_RISK_DIRS:  # a project install counts only as committed on origin/<base>
        shown = [subprocess.run(["git", "show", f"origin/{base}:{d}/{n}"], capture_output=True)
                 for n in names]
        if all(p.returncode == 0 for p in shown):
            return [(n, p.stdout) for n, p in zip(names, shown)]


def import_merge_risk(folder):
    sys.modules.pop("merge_risk", None)
    sys.modules.pop("merge_policy", None)
    saved = sys.path[:]
    root = checkout_root()  # PYTHONPATH or "" may name the checkout: the trusted import must not see it
    sys.path[:] = [str(folder)] + [p for p in saved if p and not Path(p).resolve().is_relative_to(root)]
    try:
        import merge_risk
    finally:
        sys.path[:] = saved
    return merge_risk


def load_merge_risk(base=None):
    for d in trusted_pr_risk_dirs():
        if (d / "merge_risk.py").is_file():
            return import_merge_risk(d)
    files = base_install(base) if base else None
    if files:
        with tempfile.TemporaryDirectory(prefix="babysit-pr-risk-", dir="/tmp") as tmp:
            for n, data in files:
                (Path(tmp) / n).write_bytes(data)
            return import_merge_risk(tmp)
    fail(f"pr-risk is not installed. Run: {INSTALL}")


def judge_input(mr, body, base, head):
    """Counted-file diffs, other files by name and count, the block, the categories. Nothing else."""
    base = mr.git("rev-parse", "--verify", f"origin/{base}^{{commit}}").stdout.strip()
    policy, _ = mr.load_policy(base)
    paths = {c: list(g) for c, g in mr.DEFAULT_PATHS.items()}
    for c, globs in policy["paths"].items():
        paths[c] = paths.get(c, []) + globs
    diff_base = mr.git("merge-base", base, head).stdout.strip()
    numstat = mr.git("diff", "-z", "--numstat", "--no-renames", "--no-textconv",
                     "--no-ext-diff", diff_base, head).stdout
    cats, diffs, others = set(), [], []
    for line in filter(None, numstat.split("\0")):
        added, removed, path = line.split("\t", 2)
        fc = mr.file_categories(path, paths)
        cats |= fc
        if "docs" in fc or mr.is_generated(path, diff_base, head):
            others.append(f"{path}: {0 if added == '-' else int(added) + int(removed)} lines")
        else:
            diffs.append(mr.git("diff", "--no-renames", "--no-textconv", "--no-ext-diff",
                                diff_base, head, "--", f":(literal){path}").stdout)
    block = mr.FENCE.search(body)
    return (f"## Diff of counted files\n\n{''.join(diffs)}\n## Other files\n\n"
            + "\n".join(others) + f"\n\n## Merge-risk block\n\n{block[0] if block else '(none)'}\n"
            f"\n## Categories of the diff\n\n{', '.join(sorted(cats))}\n")


def ask(cli, prompt, timeout):
    """Run one judge CLI. Returns its answer text; None when it fails; raises TimeoutExpired."""
    if timeout <= 0:
        raise subprocess.TimeoutExpired(cli, 0)
    try:
        # Use /tmp explicitly: TMPDIR may point into the untrusted PR checkout.
        # Read-only alone still permits reading author-controlled instructions.
        with tempfile.TemporaryDirectory(prefix="babysit-judge-", dir="/tmp") as d:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GIT_") and k not in
                   {"CLAUDECODE", "CLAUDE_PROJECT_DIR", "CODEX_THREAD_ID"}}
            env["PWD"] = d
            if cli == "codex":
                out = Path(d) / "answer"
                cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral",
                       "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
                       "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                       "-c", "mcp_servers={}"]
                for feature in ("shell_tool", "unified_exec", "multi_agent", "view_image",
                                "apps", "plugins", "hooks", "skill_search"):
                    cmd.extend(["-c", f"features.{feature}=false"])
                cmd.extend(["-o", str(out), "-"])
                p = subprocess.run(cmd, input=prompt, text=True, cwd=d, env=env,
                                   capture_output=True, timeout=timeout)
                return (out.read_text() if out.is_file() else p.stdout) if p.returncode == 0 else None
            p = subprocess.run(["claude", "-p", "--safe-mode", "--tools", "",
                                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                                "--no-session-persistence"], input=prompt, text=True,
                               cwd=d, env=env, capture_output=True, timeout=timeout)
            return p.stdout if p.returncode == 0 else None
    except OSError:  # the CLI could not start: same as a failed run, so the fallback runs
        return None


def judge(base, head, body_file, agent):
    mr = load_merge_risk(base)
    prompt = (Path(__file__).with_name("judge-prompt.md").read_text()
              + judge_input(mr, Path(body_file).read_text(), base, head))
    deadline = time.monotonic() + float(os.environ.get("BABYSIT_JUDGE_TIMEOUT", 600))
    other, same = ("codex", "claude") if agent == "claude-code" else ("claude", "codex")
    same_model = False
    try:
        answer = ask(other, prompt, deadline - time.monotonic()) if shutil.which(other) else None
        if answer is None:
            same_model = True
            answer = ask(same, prompt, deadline - time.monotonic()) if shutil.which(same) else None
    except subprocess.TimeoutExpired:
        return {"verdict": "disagree", "reason": "judge timeout", "sameModel": same_model}
    try:
        data = json.loads(answer)
        verdict, reason = data["verdict"], data.get("reason")
    except (TypeError, ValueError, KeyError, AttributeError):
        verdict, reason = None, None
    if verdict == "agree" and not (isinstance(reason, str) and reason.strip()):
        verdict = None
    if verdict == "agree":
        return {"verdict": "agree", "sameModel": same_model,
                "label": "same-model agree" if same_model else "agree"}
    if verdict != "disagree":
        reason = "invalid answer"
    return {"verdict": "disagree", "reason": f"judge: {reason or 'disagree'}", "sameModel": same_model}


def git(*args, ok=(0,)):
    p = subprocess.run(["git", *args], text=True, capture_output=True)
    if p.returncode not in ok:
        fail(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p


def update_plan(base):
    """Dry-run a base update with merge-tree: no worktree, index, HEAD or ref change."""
    compare = f"origin/{base}"
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    head = git("rev-parse", "HEAD").stdout.strip()
    out = {"compare": compare, "branch": branch}
    if git("merge-base", "--is-ancestor", compare, "HEAD", ok=(0, 1)).returncode == 0:
        return {**out, "plan": "none"}
    force = shlex.join(["git", "push", f"--force-with-lease={branch}:{head}", "origin", branch])
    mt = git("merge-tree", "--write-tree", "--name-only", "-z", "--no-messages", "HEAD", compare, ok=(0, 1))
    files = [f for f in mt.stdout.split("\0")[1:] if f] if mt.returncode else []
    if not files:
        return {**out, "plan": "rebase", "push": force}
    mb = git("merge-base", "HEAD", compare).stdout.strip()
    # Rebase replays each branch commit that touches a conflicting file: one conflict stop per commit.
    touching = git("--literal-pathspecs", "rev-list", f"{mb}..HEAD", "--", *files).stdout.split()
    if len(touching) >= 3:
        return {**out, "plan": "merge", "push": shlex.join(["git", "push", "origin", branch])}
    return {**out, "plan": "rebase-with-conflict", "files": files, "push": force}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lease")
    lp.add_argument("verb", choices=["acquire", "renew", "check", "release"])
    for a in ("repo", "pr", "session", "host"):
        lp.add_argument(f"--{a}", required=True)
    op = sub.add_parser("owner-comments")
    for a in ("repo", "pr"):
        op.add_argument(f"--{a}", required=True)
    cp = sub.add_parser("checks")
    cp.add_argument("--repo", required=True)
    cp.add_argument("--pr", required=True)
    cp.add_argument("--wait-minutes", type=int, required=True)
    rp = sub.add_parser("round")
    rp.add_argument("verb", choices=["start", "record-failure"])
    for a in ("repo", "pr", "session", "host"):
        rp.add_argument(f"--{a}", required=True)
    rp.add_argument("--check")
    rp.add_argument("--error")
    rp.add_argument("--op", help="stable round start ID; record-failure reuses its originating start ID on retries")
    jp = sub.add_parser("judge")
    for a in ("body", "base", "head"):
        jp.add_argument(f"--{a}", required=True)
    jp.add_argument("--agent", choices=["claude-code", "codex"], required=True)
    up = sub.add_parser("update-plan")
    up.add_argument("--base", required=True)
    a = ap.parse_args()
    if a.cmd == "owner-comments":
        print(json.dumps(owner_comments(a.repo, a.pr)))
    elif a.cmd == "checks":
        checks_cmd(a.repo, a.pr, a.wait_minutes)
    elif a.cmd == "round":
        if a.verb == "record-failure" and not (a.check and a.error):
            ap.error("record-failure needs --check and --error")
        round_cmd(a.verb, a.repo, a.pr, a.session, a.check, a.error, a.op)
    elif a.cmd == "judge":
        print(json.dumps(judge(a.base, a.head, a.body, a.agent)))
    elif a.cmd == "update-plan":
        print(json.dumps(update_plan(a.base)))
    else:
        lease(a.verb, a.repo, a.pr, a.session, a.host)


if __name__ == "__main__":
    main()
