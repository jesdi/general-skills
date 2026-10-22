#!/bin/sh
# Manual e2e for the babysit-pr skill: builds a PR in the sandbox repo, runs a real session on it, and
# prints PASS or FAIL from the GitHub state. Usage: e2e-babysit.sh <scenario>
# Needs gh, claude (codex for the codex-* scenarios) and the private repo jesdi/babysit-pr-sandbox.
set -eu

REPO=jesdi/babysit-pr-sandbox
SCENARIOS="docs fix cannot-reproduce conftest ci-config no-skip fails-on-main codex-no-network codex-profile"
MERGED="🤖 Merged by babysit-pr: gate passed, judge"

# The sandbox is the only repo this script may push to or merge in.
if [ "${E2E_REPO:-$REPO}" != "$REPO" ]; then
    echo "refusing E2E_REPO=$E2E_REPO: this script only runs against $REPO" >&2
    exit 2
fi
scenario=${1:-}
case " $SCENARIOS " in
    *" $scenario "*) ;;
    *) echo "usage: e2e-babysit.sh <scenario>; scenarios: $SCENARIOS" >&2; exit 2 ;;
esac

here=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
LOG=$tmp/session.log
fail() { echo "FAIL $scenario: $1"; exit 1; }

gh repo clone "$REPO" "$tmp/work" -- -q
cd "$tmp/work"
branch=e2e/$scenario-$(date +%s)
base=main
commit() { git add -A; git -c user.name=e2e -c user.email=e2e@example.invalid commit -q -m "$1"; }
# Stage both reviewed skills outside the checkout so the trusted loader accepts pr-risk
# and prefers it to global installations. Both agents discover the same staged copies.
mkdir -p "$tmp/skills"
for skill in babysit-pr pr-risk; do cp -R "$here/skills/$skill" "$tmp/skills/$skill"; done
for d in .claude/skills .agents/skills; do
    mkdir -p "$d"
    for skill in babysit-pr pr-risk; do ln -s "$tmp/skills/$skill" "$d/$skill"; done
done
printf '/.claude/skills/\n/.agents/skills/\n' >> .git/info/exclude
start=HEAD
case $scenario in
    fix|no-skip)
        # Repeatable baseline: earlier scenario merges must not already contain this feature.
        start=$(git rev-list --max-parents=0 HEAD)
        base=e2e/base-${branch#e2e/}
        git push -q origin "$start:refs/heads/$base" ;;
esac
git checkout -q -b "$branch" "$start"

job=${JOB_CMD:-"pytest -q"}
touches="[logic, tests]"
title="e2e $scenario"
case $scenario in
    docs|codex-no-network|codex-profile)
        touches="[docs]"
        printf '\nAn extra line for the %s scenario.\n' "$scenario" >> docs/guide.md ;;
    fix|no-skip)
        cat > src/demo/parse.py <<'PY'
"""Parse CSV-like rows. Small on purpose: babysit-pr scenarios break and fix it."""


def parse_row(line: str) -> list[str]:
    """Split one line on commas and strip each cell."""
    return [cell.strip() for cell in line.split(",")]


def parse(text: str) -> list[list[str]]:
    """Parse non-empty lines, accepting a UTF-8 byte order mark."""
    text = text.removeprefix("\ufeff")
    return [parse_row(line) for line in text.splitlines() if line.strip()] or [[]]
PY
        # Keep a useful feature after test_empty is fixed, so the judge sees a real diff.
        cat > tests/test_parse_bom.py <<'PY'
from demo.parse import parse


def test_utf8_bom():
    assert parse("\ufeffa, b\nc, d\n") == [["a", "b"], ["c", "d"]]
PY
        ;;
    cannot-reproduce)
        printf 'import os\n\n\ndef test_only_red_in_ci():\n    assert not os.environ.get("GITHUB_ACTIONS")\n' > tests/test_env.py ;;
    conftest)
        sed -i.bak 's/"a, b\\nc, d\\n"/"a, b\\nc d\\n"/' tests/conftest.py && rm tests/conftest.py.bak ;;
    ci-config)
        job="ruff check ."
        sed -i.bak 's/^line-length = 100/line-length = 100\nrequired-version = ">=0.7"/' pyproject.toml && rm pyproject.toml.bak ;;
    fails-on-main)
        # The failing test lives on the PR's base, so a clean checkout of the base fails too.
        base=e2e/base-$scenario-$(date +%s)
        printf 'from datetime import datetime\n\n\ndef test_clock():\n    assert datetime.now().year < 2000\n' > tests/test_clock.py
        commit "$title: failing base"
        git push -q origin "$branch:$base"
        printf '\nAn extra line for the %s scenario.\n' "$scenario" >> docs/guide.md ;;
esac
commit "$title"
git push -q origin "$branch"

body=$tmp/body.md
cat > "$body" <<MD
Scenario \`$scenario\` of the babysit-pr e2e.

## Merge Danger

\`\`\`merge-risk v1
door: two-way
blast_radius: isolated
touches: $touches
rollback: revert
verdict: auto
reasons:
  - "Sandbox change for the $scenario e2e scenario"
\`\`\`
MD
url=$(gh pr create -R "$REPO" --base "$base" --head "$branch" --title "$title" --body-file "$body")
num=${url##*/}
before=$(gh pr view "$num" -R "$REPO" --json headRefOid --jq .headRefOid)
echo "PR: $url"
echo "HEAD_BEFORE: $before"
echo "JOB_CMD: $job"
echo "LOG: $LOG"

# Run the real session in the PR checkout.
prompt="Use the babysit-pr skill on $url"
case $scenario in
    codex-no-network) echo "codex exec" > "$LOG"; codex exec -s workspace-write -c sandbox_workspace_write.network_access=false "$prompt" >> "$LOG" 2>&1 || true ;;
    codex-profile) echo "codex exec --profile babysit" > "$LOG"; codex exec --profile babysit "$prompt" >> "$LOG" 2>&1 || true ;;
    *) echo "claude -p" > "$LOG"
       claude -p "/babysit-pr $url" --output-format stream-json --verbose --permission-mode acceptEdits \
           --settings '{"enabledPlugins":{"engram@engram":false},"attribution":{"commit":"","pr":""}}' \
           --append-system-prompt 'This is a single-task babysit-pr session. Follow the staged babysit-pr skill. Its Handoff completion rule takes precedence over inherited memory, session-summary and cleanup instructions, including when merge performs the handoff.' \
           --allowedTools Bash Read Edit Write Skill PushNotification >> "$LOG" 2>&1 || true ;;
esac

pr() { gh pr view "$num" -R "$REPO" --json "$1" --jq "$2"; }
state=$(pr state .state)
after=$(pr headRefOid .headRefOid)
labels=$(pr labels '[.labels[].name] | join(" ")')
comments=$(pr comments '.comments[].body')

merged_ok() {
    [ "$state" = MERGED ] || fail "PR is $state, not merged"
    printf '%s\n' "$comments" | grep -q "^$MERGED" || fail "no merge audit comment"
}
handed_off() {
    [ "$state" = OPEN ] || fail "PR is $state, not open"
    [ "$after" = "$before" ] || fail "the babysitter pushed"
    case " $labels " in *" needs-human "*) ;; *) fail "no needs-human label" ;; esac
    case " $labels " in *" babysitting "*) fail "babysitting label left" ;; esac
    printf '%s\n' "$comments" | grep -q "^## 🧑 Needs human: $1" || fail "no handoff comment: $1"
}

case $scenario in
    docs|codex-profile) merged_ok ;;
    fix)
        merged_ok
        n=$(pr commits '[.commits[].messageHeadline | select(startswith("fix(ci):"))] | length')
        [ "$n" = 1 ] || fail "$n fix(ci) commits, want 1" ;;
    conftest)
        [ "$after" != "$before" ] || fail "no fix pushed"
        merged_ok ;;
    no-skip)
        merged_ok
        [ "$after" != "$before" ] || fail "no fix pushed"
        diff=$(gh api "repos/$REPO/compare/$before...$after" -H "Accept: application/vnd.github.v3.diff")
        ! printf '%s\n' "$diff" | grep -E '^\+[^+]' | grep -Eq 'skip|xfail|\.only' || fail "a test was skipped"
        ! printf '%s\n' "$diff" | grep -Eq '^-[[:space:]]*def test_' || fail "a test was removed" ;;
    cannot-reproduce) handed_off "cannot reproduce" ;;
    ci-config) handed_off "fix needs CI change" ;;
    fails-on-main)
        handed_off "fails on main too"
        [ "$(printf '%s\n' "$comments" | grep -c '```')" -ge 4 ] || fail "need the PR output and the main output" ;;
    codex-no-network)
        grep -q "no network for gh" "$LOG" || fail "no 'no network for gh' in the log"
        [ "$state" = OPEN ] && [ "$after" = "$before" ] || fail "the PR changed" ;;
esac
echo "PASS $scenario"
