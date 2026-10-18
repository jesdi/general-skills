#!/usr/bin/env python3
"""babysit-pr: `babysit.py lease acquire|renew|check|release --repo R --pr N --session S --host H`.

The lease is a PR comment `<!-- babysit-pr lease: <session> <host> <expires-at> -->` written by the
gh user. Expiry is judged against GitHub's clock (the `Date` header of `gh api -i`), never the host
clock. Among valid lease comments the lowest comment id holds the lease; the label is not the lock.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

LEASE = re.compile(r"\A<!-- babysit-pr lease: (\S+) (\S+) (\S+) -->")
TTL = timedelta(minutes=90)
INSTALL = "npx @jesdi/skills-cli install pr-risk"
PR_RISK_DIRS = (".my-skills/pr-risk", ".agents/skills/pr-risk", ".claude/skills/pr-risk")
CLAIM_PENDING = "<!-- babysit-pr claim: pending -->\n"
CLAIM_HELD = "<!-- babysit-pr claim: held -->\n"


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
        return json.loads(gh("api", "-X", "POST", f"{self.base}/comments", "-f", f"body={body}"))

    def label(self, add=(), remove=()):
        for name in add:
            gh("api", "-X", "POST", f"{self.base}/labels", "-f", f"labels[]={name}")
        for name in remove:
            gh("api", "-X", "DELETE", f"{self.base}/labels/{name}", check=False)


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
    if not history:
        return "rounds: 0\n"
    return rest_of(history[-1][4]).replace(CLAIM_HELD, "")


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


def require_pr_risk():
    home = Path.home()
    if not any(Path(d).is_dir() for d in (*PR_RISK_DIRS, home / ".my-skills/pr-risk")):
        fail(f"pr-risk is not installed. Run: {INSTALL}")


def lease(verb, repo, number, session, host):
    require_pr_risk()
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
        if not mine:
            fail(f"{session} does not hold the lease")
        c = top[4]
        extend(pr, c, session, top[2], now + TTL, user)
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
            confirm(pr, c)
            # Publish and elect our claim before touching expired comments. A
            # renewal that completed before our post wins the fresh election;
            # one racing this cleanup sees our claim in its own post-write check.
            for l in found:
                if l[0] != c["id"] and l[3] <= now:
                    end(pr, l[4])
        pr.label(add=["babysitting"], remove=["needs-human"])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lease")
    lp.add_argument("verb", choices=["acquire", "renew", "check", "release"])
    for a in ("repo", "pr", "session", "host"):
        lp.add_argument(f"--{a}", required=True)
    a = ap.parse_args()
    lease(a.verb, a.repo, a.pr, a.session, a.host)


if __name__ == "__main__":
    main()
