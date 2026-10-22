#!/bin/sh
# Manual e2e for the babysit-pr skill: builds a PR in the sandbox repo, runs a real session on it, and
# prints PASS or FAIL from the GitHub state. Usage: e2e-babysit.sh <scenario>
# Needs gh, claude (codex for the codex-* scenarios) and the private repo jesdi/babysit-pr-sandbox.
set -eu

REPO=jesdi/babysit-pr-sandbox
SCENARIOS="docs fix cannot-reproduce conftest ci-config no-skip fails-on-main codex-no-network codex-profile
    base-moved conflict-code conflict-merge conflict-tests stacked stacked-base-fails owner-instruction owner-resume
    owner-resume-held owner-merge"
MERGED="🤖 Merged by babysit-pr: gate passed, judge"

# The sandbox is the only repo this script may push to or merge in.
if [ "${E2E_REPO:-$REPO}" != "$REPO" ]; then
    echo "refusing E2E_REPO=$E2E_REPO: this script only runs against $REPO" >&2
    exit 2
fi
scenario=${1:-}
case " $(echo $SCENARIOS) " in
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
    base-moved|conflict-code|conflict-merge|conflict-tests|owner-instruction|owner-resume|owner-resume-held|owner-merge)
        # A private base, so that moving it or merging into it never touches main.
        base=e2e/base-${branch#e2e/}
        start=$(git rev-parse HEAD)
        git push -q origin "$start:refs/heads/$base" ;;
esac
git checkout -q -b "$branch" "$start"

job=${JOB_CMD:-"pytest -q"}
touches="[logic, tests]"
title="e2e $scenario"
door=two-way
[ "$scenario" = owner-merge ] && door=one-way
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
    fails-on-main|stacked-base-fails)
        # The failing test lives on the PR's base, so a clean checkout of the base fails too.
        base=e2e/base-$scenario-$(date +%s)
        [ "$scenario" = stacked-base-fails ] && base=e2e/parent-$scenario-$(date +%s)
        printf 'from datetime import datetime\n\n\ndef test_clock():\n    assert datetime.now().year < 2000\n' > tests/test_clock.py
        commit "$title: failing base"
        git push -q origin "$branch:$base"
        printf '\nAn extra line for the %s scenario.\n' "$scenario" >> docs/guide.md ;;
    base-moved)
        touches="[docs]"
        printf '\nThe PR line for the base-moved scenario.\n' >> docs/guide.md ;;
    conflict-code)
        # Two branch commits touch src/demo/parse.py; the first conflicts with the moved base.
        touches="[logic]"
        sed -i.bak 's/"""Parse every non-empty line."""/"""Parse every non-empty line (PR_SIDE)."""/' src/demo/parse.py
        rm src/demo/parse.py.bak
        commit "$title: one"
        sed -i.bak 's/strip each cell\."""/strip each cell (PR_SIDE two)."""/' src/demo/parse.py
        rm src/demo/parse.py.bak ;;
    conflict-tests|conflict-merge)
        # Branch commits touch tests/test_parse.py; the first conflicts with the moved base.
        touches="[tests]"
        printf '\n\ndef test_from_pr():\n    assert parse_row("x, y") == ["x", "y"]\n' >> tests/test_parse.py
        commit "$title: one"
        sed -i.bak 's/\(parse_row(" a , b ") == \["a", "b"\]\)$/\1  # kept by the PR/' tests/test_parse.py
        rm tests/test_parse.py.bak
        if [ "$scenario" = conflict-merge ]; then
            commit "$title: two"
            sed -i.bak 's/^\(    assert parse("") == \[\]\)$/\1  # kept by the PR/' tests/test_parse.py
            rm tests/test_parse.py.bak
        fi ;;
    stacked)
        # The parent holds a logic change that main does not have; the child changes only docs.
        base=e2e/parent-$scenario-$(date +%s)
        printf 'def parent_helper() -> int:\n    return 1\n' > src/demo/extra.py
        commit "$title: parent logic"
        git push -q origin "$branch:$base"
        start=$(git rev-parse HEAD)  # the parent tip, which the fixture then advances
        touches="[docs]"
        printf '\nThe child line for the stacked scenario.\n' >> docs/guide.md ;;
    owner-instruction)
        touches="[logic, tests]"
        printf '\n\ndef parse_x(line: str) -> list[str]:\n    """Alias of parse_row."""\n    return parse_row(line)\n' >> src/demo/parse.py
        printf 'from demo.parse import parse_x\n\n\ndef test_parse_x():\n    assert parse_x("a, b") == ["a", "b"]\n' > tests/test_parse_x.py ;;
    owner-resume|owner-resume-held)
        touches="[logic, tests]"
        cat > src/demo/parse.py <<'PY'
"""Parse CSV-like rows. Small on purpose: babysit-pr scenarios break and fix it."""


def parse_row(line: str) -> list[str]:
    """Split one line on commas and strip each cell; an empty line has no cells."""
    if not line.strip():
        return []
    return [cell.strip() for cell in line.split(",")]


def parse(text: str) -> list[list[str]]:
    """Parse every non-empty line."""
    return [parse_row(line) for line in text.splitlines() if line.strip()]
PY
        ;;
    owner-merge)
        touches="[docs]"
        printf '\nThe one-way line for the owner-merge scenario.\n' >> docs/guide.md ;;
esac
commit "$title"
git push -q origin "$branch"

body=$tmp/body.md
cat > "$body" <<MD
Scenario \`$scenario\` of the babysit-pr e2e.

## Merge Danger

\`\`\`merge-risk v1
door: $door
blast_radius: isolated
touches: $touches
rollback: revert
verdict: $([ "$door" = one-way ] && echo human || echo auto)
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

# Fixtures that need the PR: a moved base, a handoff and the owner's comments.
moved=
commit_moved() { # commit_moved <file> <text>: one commit on the scratch branch
    printf '%s\n' "$2" >> "$1"; commit "base moved: $1"
}
advance_base() { # advance_base <function>: run it on a scratch branch off the old base tip, push it to the base
    git checkout -q -b moving "$start"
    "$@"
    git push -q origin "HEAD:refs/heads/$base"
    moved=$(git rev-parse HEAD)
    git checkout -q "$branch"
}
moved_docs() { for i in 1 2 3; do commit_moved "docs/moved$i.md" "Moved $i."; done; }
moved_code() {
    sed -i.bak 's/"""Parse every non-empty line."""/"""Parse every non-empty line (BASE_SIDE)."""/' src/demo/parse.py
    rm src/demo/parse.py.bak; commit "base moved: parse.py"
}
moved_tests() { printf '\n\ndef test_from_base():\n    assert parse_row("m, n") == ["m", "n"]\n' >> tests/test_parse.py; commit "base moved: tests"; }
comment() { gh pr comment "$num" -R "$REPO" --body "$1" >/dev/null; }
handoff_fixture() { # handoff_fixture <reason>: the comment and label of an earlier handoff
    gh label create needs-human -R "$REPO" --force >/dev/null 2>&1 || true
    gh pr edit "$num" -R "$REPO" --add-label needs-human >/dev/null
    comment "## 🧑 Needs human: $1

State: CI pending · 0 fix rounds

Resume with \`/babysit-pr $url\`"
    sleep 3  # the owner's comment must be newer than the handoff
}
case $scenario in
    base-moved|stacked) advance_base moved_docs ;;
    conflict-code) advance_base moved_code ;;
    conflict-tests|conflict-merge) advance_base moved_tests ;;
    owner-instruction) comment '@babysitter rename `parse_x` to `parse_cells`' ;;
    owner-resume) handoff_fixture "CI stalled"; comment '@babysitter add a test for the empty case' ;;
    owner-resume-held)
        handoff_fixture "CI stalled"
        comment '<!-- babysit-pr lease: other-session other-host 2099-01-01T00:00:00Z -->'
        comment '@babysitter add a test for the empty case' ;;
    owner-merge) handoff_fixture "door one-way"; comment "@babysitter merge $before" ;;
esac

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
file_at() { gh api "repos/$REPO/contents/$1?ref=$after" -H "Accept: application/vnd.github.raw" 2>/dev/null || true; }
head_parents() { gh api "repos/$REPO/commits/$after" --jq '.parents | length'; }
moved_in_head() {
    [ "$after" != "$before" ] || fail "the babysitter did not update the branch"
    [ "$(gh api "repos/$REPO/compare/$moved...$after" --jq .status)" = ahead ] || fail "the head lacks the moved base"
}
both_sides() { # both_sides <path> <token> <token>: the file keeps both sides and has no conflict marker
    f=$(file_at "$1")
    ! printf '%s\n' "$f" | grep -q '^<<<<<<<' || fail "conflict markers in $1"
    for t in "$2" "$3"; do printf '%s\n' "$f" | grep -q "$t" || fail "$1 lost $t"; done
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
    base-moved)
        merged_ok; moved_in_head
        [ "$(head_parents)" = 1 ] || fail "the head is not a rebase of the branch" ;;
    conflict-code)
        [ "$state" = OPEN ] || fail "PR is $state, not open"
        case " $labels " in *" needs-human "*) ;; *) fail "no needs-human label" ;; esac
        case " $labels " in *" babysitting "*) fail "babysitting label left" ;; esac
        printf '%s\n' "$comments" | grep -q "^## 🧑 Needs human: conflict resolved in code" || fail "no handoff: conflict resolved in code"
        moved_in_head; both_sides src/demo/parse.py BASE_SIDE PR_SIDE ;;
    conflict-merge)
        merged_ok; moved_in_head
        [ "$(head_parents)" = 2 ] || fail "the head is not a merge of the base"
        both_sides tests/test_parse.py test_from_base test_from_pr ;;
    conflict-tests)
        merged_ok; moved_in_head
        [ "$(head_parents)" = 1 ] || fail "the head is not a rebase of the branch"
        both_sides tests/test_parse.py test_from_base test_from_pr ;;
    stacked)
        moved_in_head
        [ "$(head_parents)" = 1 ] || fail "the head is not a rebase onto the parent" ;;
    stacked-base-fails)
        handed_off "fails on main too"
        [ "$(printf '%s\n' "$comments" | grep -c '```')" -ge 4 ] || fail "need the PR output and the parent output" ;;
    owner-instruction)
        [ "$after" != "$before" ] || fail "no rename pushed"
        file_at src/demo/parse.py | grep -q "def parse_cells" || fail "parse_x was not renamed to parse_cells"
        ! file_at src/demo/parse.py | grep -Eq "(^|[^_[:alnum:]])parse_x" || fail "parse_x is still in parse.py"
        ! file_at tests/test_parse_x.py | grep -Eq "(^|[^_[:alnum:]])parse_x" || fail "a test still uses parse_x" ;;
    owner-resume)
        merged_ok; [ "$after" != "$before" ] || fail "no test pushed"
        added=$(gh api "repos/$REPO/compare/$before...$after" -H "Accept: application/vnd.github.v3.diff")
        printf '%s\n' "$added" | grep -Eq '^\+def test_' || fail "no test was added"
        printf '%s\n' "$comments" | grep -q "<!-- babysit-pr lease:" || fail "no lease comment"
        [ "$(printf '%s\n' "$comments" | grep -c '^## 🧑 Needs human')" = 1 ] || fail "handed off again" ;;
    owner-resume-held)
        [ "$state" = OPEN ] || fail "PR is $state, not open"
        [ "$after" = "$before" ] || fail "the babysitter pushed while another session held the lease"
        ! printf '%s\n' "$comments" | grep -q "^$MERGED" || fail "merged while another session held the lease" ;;
    owner-merge)
        [ "$state" = MERGED ] || fail "PR is $state, not merged"
        [ "$after" = "$before" ] || fail "the babysitter pushed" ;;
    codex-no-network)
        grep -q "no network for gh" "$LOG" || fail "no 'no network for gh' in the log"
        [ "$state" = OPEN ] && [ "$after" = "$before" ] || fail "the PR changed" ;;
esac
echo "PASS $scenario"
