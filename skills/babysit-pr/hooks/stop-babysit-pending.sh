#!/bin/sh
# Stop hook (Claude): run /babysit-pr for each PR a subagent of this session opened.
# post-pr-create.sh queues them, since a subagent's own nudge ends with the subagent.
payload="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
# Only the main session takes the queue: a subagent's stop leaves it for its parent.
session="$(printf '%s' "$payload" | jq -r 'select(.agent_id // "" | . == "") | .session_id // ""' | tr -c 'A-Za-z0-9_-' '_')"
[ -n "$session" ] || exit 0
file="${TMPDIR:-/tmp}/babysit-pr-pending/$session"
[ -s "$file" ] || exit 0
# Take the queue with one rename, so a URL appended meanwhile starts a new queue.
taken="$file.$$"
mv "$file" "$taken" 2>/dev/null || exit 0
runs="$(sort -u "$taken" | sed 's|.*|/babysit-pr &|')"
rm -f "$taken"
jq -n --arg runs "$runs" '{decision: "block",
  reason: ("A subagent opened pull requests in this session. Babysit each one, in turn:\n" + $runs)}'
