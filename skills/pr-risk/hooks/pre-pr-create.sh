#!/bin/sh
# PreToolUse hook (Claude and Codex): in a repo with .merge-risk.json, deny `gh pr create`
# unless its body (--body or --body-file) holds a valid merge-risk block. Otherwise print nothing.
here="$(cd "$(dirname "$0")" && pwd)"
PAYLOAD="$(cat)" exec python3 - "$here/../merge_risk.py" <<'PY'
import json, os, re, shlex, subprocess, sys
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import merge_risk

try:
    payload = json.loads(os.environ["PAYLOAD"])
except ValueError:
    sys.exit(0)
command = (payload.get("tool_input") or {}).get("command") or ""
start_cwd = payload.get("cwd") or "."


def skip_quoted(s, i):
    """i is just past an opening quote; return the index past the closing one."""
    q = s[i - 1]
    while i < len(s) and s[i] != q:
        i += 2 if s[i] == "\\" and q == '"' else 1
    return i + 1


def skip_subst(s, i):
    """i is at `$(`; return the index past its matching `)`, skipping quotes and heredoc bodies."""
    depth, i = 1, i + 2
    while i < len(s) and depth:
        c = s[i]
        if c in "'\"":
            i = skip_quoted(s, i + 1)
            continue
        if c == "\\":
            i += 2
            continue
        h = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?").match(s, i) if c == "<" else None
        if h:
            end = re.compile(r"^\s*" + h.group(1) + r"\s*$", re.M).search(s, s.find("\n", h.end()) + 1)
            i = end.end() if end else len(s)
            continue
        depth += (c == "(") - (c == ")")
        i += 1
    return i


def parse(s):
    """Split into (words, dir) commands: POSIX-ish words with `$(...)` kept raw inside its word, and
    dir the directory left by `cd`s in the command's subshell scope."""
    out, words, cur, i, where = [], [], None, 0, start_cwd
    directories, heredocs = [], []

    def end_word():
        nonlocal cur
        if cur is not None:
            words.append(cur)
        cur = None

    def end_command():
        nonlocal words, where
        end_word()
        # Shell control words introduce a command rather than forming its argv.
        while words and words[0] in {"if", "then", "elif", "else", "while", "until", "do", "!", "{"}:
            words = words[1:]
        while words and "=" in words[0] and not words[0].startswith("-"):
            words = words[1:]
        if words[:1] == ["cd"] and len(words) > 1:
            args = words[1:]
            while args and args[0] in ("-L", "-P"):
                args = args[1:]
            if args[:1] == ["--"]:
                args = args[1:]
            if args:
                where = os.path.join(where, os.path.expanduser(args[0]))
        elif words:
            out.append((words, where))
        words = []

    while i < len(s):
        c, base = s[i], cur or ""
        if s.startswith("\\\n", i):
            i += 2
        elif s.startswith("$(", i):
            j = skip_subst(s, i)
            cur, i = base + s[i:j], j
        elif c == "'":
            j = skip_quoted(s, i + 1)
            cur, i = base + s[i + 1:j - 1], j
        elif c == '"':  # a `$(...)` inside stays raw: its quotes must not end this string
            k, inner = i + 1, ""
            while k < len(s) and s[k] != '"':
                if s.startswith("$(", k):
                    e = skip_subst(s, k)
                    inner, k = inner + s[k:e], e
                elif s.startswith("\\\n", k):
                    k += 2
                elif s[k] == "\\" and s[k + 1:k + 2] in ('"', "\\", "$", "`"):
                    inner, k = inner + s[k + 1], k + 2
                else:
                    inner, k = inner + s[k], k + 1
            cur, i = base + inner, k + 1
        elif c == "\\":
            cur, i = base + s[i + 1:i + 2], i + 2
        elif c in " \t":
            end_word()
            i += 1
        elif s.startswith("<<", i) and not s.startswith("<<<", i):
            # Delimiters undergo quote removal; the following lines are data,
            # even when they contain shell commands or subshell punctuation.
            h = re.match(r'''<<(-?)[ \t]*((?:[^\s;|&()<>"'\\]|'[^']*'|"(?:\\.|[^"\\])*"|\\.)+)''', s[i:])
            if h:
                end_word()
                heredocs.append((shlex.split(h.group(2))[0], bool(h.group(1))))
                i += h.end()
            else:
                cur, i = base + c, i + 1
        elif c in "();&|\n":
            end_command()
            i += 1
            if c == "(":
                directories.append(where)
            elif c == ")" and directories:
                where = directories.pop()
            elif c == "\n":
                for delimiter, strip_tabs in heredocs:
                    while i < len(s):
                        j = s.find("\n", i)
                        j = len(s) if j < 0 else j
                        line, i = s[i:j], min(j + 1, len(s))
                        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                            break
                heredocs.clear()
        else:
            cur, i = base + c, i + 1
    end_command()
    return out


def creation_args(words):
    """Recognize the subcommand while consuming gh's inherited repo option."""
    if words[:1] != ["gh"]:
        return None
    i = 1
    for expected in ("pr", "create"):
        while i < len(words):
            arg = words[i]
            if arg in ("--repo", "-R"):
                i += 2
            elif arg.startswith("--repo=") or (arg.startswith("-R") and len(arg) > 2):
                i += 1
            else:
                break
        if i >= len(words) or words[i] != expected:
            return None
        i += 1
    return words[i:]


creations = []
for words, cwd in parse(command):
    args = creation_args(words)
    if args is not None:
        creations.append((args, cwd))


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


VALUED = {"--title", "-t", "--base", "-B", "--head", "-H", "--assignee", "-a", "--label", "-l",
          "--milestone", "-m", "--project", "-p", "--reviewer", "-r", "--template", "-T",
          "--repo", "-R"}
BODY_SOURCE_REQUIRED = (
    "the body must come from --body or --body-file, so its merge-risk block can be checked. "
    "Write the body with /pr, then /pr-risk."
)


def check(args, cwd):
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip() or cwd
    if not os.path.isfile(os.path.join(top, ".merge-risk.json")):
        return
    body = body_file = None
    i = 0
    while i < len(args):
        a, i = args[i], i + 1
        name, eq, val = a.partition("=")
        if name in ("--body", "-b", "--body-file", "-F"):
            if not eq and i < len(args):
                val, i = args[i], i + 1
            if name in ("--body", "-b"):
                body = val
            else:
                body_file = val
        elif name in {"--fill", "--fill-first", "--web"}:
            deny(BODY_SOURCE_REQUIRED)
        elif name in VALUED and not eq:
            i += 1
    if body is None and body_file is None:
        deny(BODY_SOURCE_REQUIRED)

    def read(path):
        with open(os.path.join(cwd, os.path.expanduser(path))) as f:
            return f.read()

    try:
        if body_file is not None:
            text = read(body_file)
        else:
            m = re.fullmatch(r"\s*\$\(\s*cat\s+(?:<<-?\s*'?(\w+)'?\n(.*?)\n\s*\1\s*|('[^']*'|\"[^\"]*\"|\S+)\s*)\)\s*",
                             body, re.S)
            if m and m.group(1):
                text = m.group(2) + "\n"
            elif m:
                text = read(m.group(3).strip("'\""))
            else:
                text = body
    except OSError as e:
        deny(f"cannot read the PR body ({e.strerror}: {e.filename}); pass --body-file with a file that exists. "
             "Write the body with /pr, then /pr-risk.")

    errors = merge_risk.parse(text)["errors"]
    if errors:
        deny("The PR body has no valid merge-risk block: " + "; ".join(errors) +
             ". Write the body with /pr, then /pr-risk.")


for args, cwd in creations:
    check(args, cwd)
PY
