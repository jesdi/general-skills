---
name: pr-visual-diff
description: Capture before/after screenshots of UI views with Playwright and embed them in a PR. Use this skill whenever creating or preparing a pull request that touches frontend code (components, views, styles, layout, Tailwind classes) — even if the user just says "open a PR" or "create the PR" after UI work, and especially if they mention screenshots, visual diff, before/after, or "show what changed". Also use when the user asks to add screenshots to an existing PR.
---

# PR Visual Diff

Produce before/after screenshots of the views affected by a branch's frontend
changes, commit them on the PR branch under `docs/pr-screenshots/`, and embed
them in the PR body. Reviewers should see what changed visually without
checking out the branch.

The whole approach leans on the repo's existing Playwright e2e harness
(`frontend/e2e/`): the `authenticatedPage` fixture seeds auth, `mockApi(page)`
stubs every `/api` route with fixture data, and the config self-boots vite on
port 5174. Because the data is mocked and identical in both runs, any pixel
difference between "before" and "after" is caused by the code change alone —
that's the property that makes the comparison trustworthy. Do not screenshot
against the live dev backend for this purpose.

## When NOT to run the capture

If `git diff --name-only $(git merge-base main HEAD)...HEAD` shows no changes
under `frontend/src/`, there is nothing visual to diff — create the PR
normally and say so. Test-only, docs-only, or backend-only diffs don't need
screenshots.

## Always run the capture in a subagent

Steps 2–6 (writing the spec, capturing, **reading every PNG back to eyeball
it**, committing, embedding) MUST run inside a single dispatched subagent — not
in the main context. Reading screenshots back is this skill's dominant token
cost, and those image bytes are worthless to the main thread; keeping them in a
subagent's throwaway context is the whole savings, with no loss of verification
(the subagent still eyeballs every shot).

Main context does only: step 1 (map the diff, get the user's go-ahead), then
dispatch a subagent with the plan (view list, device projects, before/after
scratchpad dirs, branch slug, whether each view is changed vs new). The
subagent returns a compact report: the committed screenshot paths + pinned
URLs, the PR-body markdown, and any views it flagged (missing mock, new view).
The main context creates/edits the PR from that report — it never reads a PNG.

## Workflow

### 1. Map the diff to views, then get the user's go-ahead

List changed files against the merge base. For each changed component, trace
upward (imports → page/route component → route path in the router) to find the
route(s) where it renders, and note any interaction needed to make it visible
(e.g. a modal that must be opened, a tab that must be selected). Classify each
view as **changed** (existed at the merge base) or **new** (introduced by this
branch) — new ones get an after-only shot, no comparison.

This capture is heavy (temp worktree, two Playwright passes, a commit pushed to
the branch, PR body rewritten). ALWAYS confirm with AskUserQuestion before
running any of it — never kick it off silently. The question must cover **both**
(a) whether to run the whole before/after capture at all, and (b) that it will
commit the screenshots and embed them in the PR. Include the proposed plan:
which routes/states, and which device projects — default `desktop-chromium` +
`mobile-iphone` (the app must stay consistent across desktop and mobile — a
change that looks fine on desktop can break the bottom-tab mobile shell); add
`tablet-ipad` / `mobile-pixel` only when the change is layout-sensitive. The
user may know about affected states you can't see in the diff, or may want a
plain PR with no screenshots — this gate is where that surfaces. If they
decline, create the PR normally and stop.

### 2. Write the capture spec

Copy `references/capture-spec-template.ts` from this skill into
`frontend/e2e/specs/__pr-capture__.spec.ts` and fill in the `VIEWS` array.
Each view is a route plus an optional `prepare` step for interactions
(opening modals, selecting tabs, hovering). The template uses the repo's own
`test` fixture and `mockApi`, so it needs no new dependencies.

This file is scaffolding: never commit it, and delete it when done (it lives
in `specs/`, so a forgotten copy would run in every future e2e invocation).

### 3. Capture "after" (current branch)

From `frontend/` (node 24.16.0 via nvm). First make sure nothing is listening
on 5174 (`lsof -nP -i :5174`) and kill any stray vite there: the Playwright
config has `reuseExistingServer` on locally, so a leftover server from another
worktree would be silently reused and you'd screenshot the *wrong code*. The
Docker dev container owns 5173 and is not a conflict.

```bash
PR_SHOT_DIR=<scratchpad>/after PR_SHOT_LABEL=after \
  pnpm exec playwright test e2e/specs/__pr-capture__.spec.ts \
  --project=desktop-chromium --project=mobile-iphone
```

Playwright boots its own vite and tears it down. The template pins
`deviceScaleFactor: 1` (small, review-cheap PNGs) and asserts no spinner is
left on screen, so a raced/under-mocked view fails the test instead of
producing a misleading shot. That assertion is a backstop, not a substitute for
looking: the subagent still eyeballs each PNG after capture (Read them) — an
empty state, error toast, or wrong content passes the spinner check but is still
wrong. On a genuine failure, fix the spec (add `mockApi` stubs, a wait for a
stable element, or correct the spinner selector for this app) rather than
shipping a bad screenshot.

### 4. Capture "before" (merge base, temp worktree)

A "before" shot only makes sense for views that already existed. If a view or
component is introduced by this branch, there is no previous form to compare
against — capture only its "after" shot and present it as **New** (step 6).
If *every* captured view is new (a pure new-feature PR), skip this whole step:
no worktree, no before pass.

For views that did exist, reconstruct the pre-change app from git instead of
trusting anyone to have captured it earlier:

```bash
BASE=$(git merge-base main HEAD)
git worktree add <scratchpad>/pr-before-shots "$BASE"
cd <scratchpad>/pr-before-shots/frontend && pnpm install
```

Copy the same `__pr-capture__.spec.ts` into that worktree's
`e2e/specs/`, then run the same command with `PR_SHOT_DIR=<scratchpad>/before
PR_SHOT_LABEL=before`. Run it sequentially after the "after" pass — both
configs use port 5174.

Edge cases:
- A route that doesn't exist at the base (new view): skip it and label it
  **New** in the PR table — an "after" shot alone is the honest artifact.
- The e2e helpers (`support/test.ts`, `support/mock-api.ts`) missing or
  different at the base: adapt the spec copy to what exists there; the goal is
  the same rendered view, not identical spec code.
- `pnpm install` in the worktree failing on a native binding (rolldown): rerun
  `pnpm install` once; if it persists, report it rather than screenshotting a
  broken build.

Copy the same 5174 stale-server check before this pass — both trees' configs
use the same port, so run before/after sequentially, never in parallel.

Remove the worktree when done: `git worktree remove --force
<scratchpad>/pr-before-shots` (`--force` because `pnpm install` left untracked
artifacts in it).

### 5. Commit the images on the PR branch

```
docs/pr-screenshots/<branch-slug>/<view>--<project>--{before|after}.png
```

`<branch-slug>` = branch name with `/` → `-`. Keep only the shots you'll
reference. Commit them (a `docs/`-only commit doesn't trigger the frontend
pre-commit hook), delete the temp spec, and push.

### 6. Embed in the PR body

After pushing, pin image URLs to the exact commit so they keep rendering after
later force-pushes or branch deletion:

```bash
SHA=$(git rev-parse HEAD)
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
# → https://github.com/$REPO/blob/$SHA/docs/pr-screenshots/<slug>/<file>.png?raw=true
```

Per view, one table. Put **Before on the left and After on the right** (one
device per row) so a reviewer sees the original component and the changed
component side by side — that adjacency is what makes the diff readable:

```markdown
### <View name> (`/route`)

| | Before | After |
|---|---|---|
| **Desktop** | ![](<blob-url>?raw=true) | ![](<blob-url>?raw=true) |
| **Mobile** | ![](<blob-url>?raw=true) | ![](<blob-url>?raw=true) |
```

This repo is private, so GitHub's image proxy may refuse to inline blob URLs
in the PR body. Add a one-line note under the tables linking to the
`docs/pr-screenshots/<slug>/` directory on the branch as a fallback — the
images always render in the Files-changed tab and in blob view for
authenticated viewers.

The subagent's job ends here: it has pushed the commit and produced the PR-body
markdown, and returns both to the main context. The **main context** creates
(or edits) the PR with that body, then opens it (`gh pr view --web` or tells the
user) so they can confirm the embeds render.

For a **new** view there is no Before, so put a **New view** label in the left
cell instead of an image:

```markdown
| | Before | After |
|---|---|---|
| **Desktop** | _New view_ | ![](<blob-url>?raw=true) |
| **Mobile** | _New view_ | ![](<blob-url>?raw=true) |
```

## Adding shots to an existing PR

Same flow; the only differences: diff against the PR's base branch
(`gh pr view --json baseRefName`), and edit the body with
`gh pr edit --body-file` instead of creating.
