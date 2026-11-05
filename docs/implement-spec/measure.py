"""Cost of one implement-spec run, for docs/implement-spec/runs.md.

    python3 docs/implement-spec/measure.py SESSION_ID [--since ISO] [--until ISO]
        [--ledger PATH ...] [--project-dir DIR]

SESSION_ID is the Claude Code session that drove the run (or did the work by
hand). Its transcript and its subagents' transcripts are read from
~/.claude/projects/<project-dir>/. --since/--until cut the window (UTC ISO,
e.g. the PR's createdAt). Each --ledger is a driver ledger
(.agent/implement/ledger.md); its "cost N USD" lines are summed, Codex runs
included: the driver prices them at list price since its Codex cost change. A
Codex run of an older driver, or of a model it has no price for, is counted
as not priced.

Prices are Claude Opus 5.5 per million tokens (claude-api skill, cached
2026-10-06); Claude Code writes the cache with a 1 h TTL, billed at 2x input.
Update PRICES when the model or the price changes.
"""
import argparse
import glob
import json
import os
import re

PRICES = {"input": 4.00, "cache_write": 8.00, "cache_read": 0.20, "output": 20.00}


def tally(paths, since=None, until=None):
    t = dict(input=0, cache_write=0, cache_read=0, output=0)
    seen, first, last = set(), None, None
    for path in paths:
        for line in open(path):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            usage, ts = msg.get("usage"), d.get("timestamp", "")
            if d.get("type") != "assistant" or not usage or msg.get("id") in seen:
                continue
            if (since and ts < since) or (until and ts > until):
                continue
            seen.add(msg.get("id"))   # a message streams as several lines with one usage
            t["input"] += usage.get("input_tokens", 0)
            t["cache_write"] += usage.get("cache_creation_input_tokens", 0)
            t["cache_read"] += usage.get("cache_read_input_tokens", 0)
            t["output"] += usage.get("output_tokens", 0)
            first, last = min(first or ts, ts), max(last or ts, ts)
    t["usd"] = sum(t[k] * PRICES[k] / 1e6 for k in PRICES)
    return t, first, last


def ledger(path):
    text = open(path).read()
    usd = sum(float(x) for x in re.findall(r"cost ([0-9.]+) USD", text))
    unpriced = [line for line in text.splitlines() if "ran on openai/" in line and " USD" not in line]
    return usd, len(unpriced)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("session")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--ledger", action="append", default=[])
    p.add_argument("--project-dir", default="-Users-jesdi-Projects-agent-ops")
    a = p.parse_args()
    base = os.path.expanduser(f"~/.claude/projects/{a.project_dir}/{a.session}")
    for name, paths in (("session", [base + ".jsonl"]),
                        ("subagents", glob.glob(base + "/subagents/*.jsonl"))):
        t, first, last = tally(paths, a.since, a.until)
        print(f"{name:10s} ${t['usd']:7.2f}  cache read {t['cache_read']:>11,}  "
              f"output {t['output']:>8,}  {first} .. {last}")
    for path in a.ledger:
        usd, codex = ledger(path)
        print(f"driver     ${usd:7.2f}  {codex} Codex runs not priced  {path}")


if __name__ == "__main__":
    main()
