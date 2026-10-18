#!/bin/sh
# PostToolUse hook (Claude and Codex): after a ready `gh pr create` in a repo with
# .merge-risk.json, tell the agent to run /babysit-pr <url>. Otherwise print nothing.
payload="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')"
# Split only at unquoted operators, then parse each command as POSIX shell words.
printf '%s' "$cmd" | python3 -c '
import shlex, sys
command = sys.stdin.read()
segments, start, quote, escaped = [], 0, None, False
for i, char in enumerate(command):
    if escaped:
        escaped = False
    elif char == "\\" and quote != chr(39):
        escaped = True
    elif quote:
        if char == quote:
            quote = None
    elif char in (chr(39), chr(34)):
        quote = char
    elif char in "();&|\n":
        segments.append(command[start:i])
        start = i + 1
segments.append(command[start:])
try:
    segs = [shlex.split(segment) for segment in segments]
except ValueError:
    sys.exit(1)
VALUED = {"--title", "-t", "--body", "-b", "--body-file", "-F", "--base", "-B", "--head", "-H",
          "--assignee", "-a", "--label", "-l", "--milestone", "-m", "--project", "-p",
          "--reviewer", "-r", "--template", "-T", "--repo", "-R"}
for s in segs:
    while s and "=" in s[0] and not s[0].startswith("-"):
        s = s[1:]
    if s[:3] == ["gh", "pr", "create"]:
        skip = False
        for a in s[3:]:
            if skip:
                skip = False
            elif a in ("--draft", "-d") or a.startswith("--draft="):
                sys.exit(1)
            elif a in VALUED:
                skip = True
        sys.exit(0)
sys.exit(1)
' || exit 0
cwd="$(printf '%s' "$payload" | jq -r '.cwd // "."')"
root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$cwd")"
[ -f "$root/.merge-risk.json" ] || exit 0
# Claude sends {stdout,...}, Codex a plain string.
url="$(printf '%s' "$payload" | jq -r '.tool_response | if type == "object" then .stdout // "" else . // "" end' |
  grep -Eo 'https://[^ /]+/[^ /]+/[^ /]+/pull/[0-9]+' | tail -n 1)"
[ -n "$url" ] || exit 0
jq -n --arg url "$url" '{hookSpecificOutput: {hookEventName: "PostToolUse",
  additionalContext: "A pull request was opened. Run `/babysit-pr \($url)`."}}'
