---
name: pr-visual-diff
description: Capture before/after screenshots of UI views with Playwright and embed them in a PR. Use this skill whenever creating or preparing a pull request that touches frontend code (components, views, styles, layout, Tailwind classes) — even if the user just says "open a PR" or "create the PR" after UI work, and especially if they mention screenshots, visual diff, before/after, or "show what changed". Also use when the user asks to add screenshots to an existing PR.
---

# PR Visual Diff

Produce before/after screenshots of the views affected by a branch's frontend
changes, commit them on the PR branch, and embed them in the PR body. Reviewers
should see what changed visually without checking out the branch.

The approach leans on the project's existing Playwright e2e harness with the
**API mocked**: because the data is stubbed and identical across both runs, any
pixel difference between "before" and "after" is caused by the code change
alone — that's the property that makes the comparison trustworthy. Never
screenshot against a live backend for this purpose.

Because every project wires this differently, the skill **discovers** the
project's setup once and caches it, then reuses the cache on later runs.

## Step 0. Load or build the per-project config

The config cache lives beside this skill at
`<skill-dir>/pr-visual-diff.<slug>.json`, where `<slug>` is the target repo's
directory name. It is gitignored — local to this machine.

- **If the cache exists**, read it and skip to "When NOT to run the capture".
  If it says `{"supported": false}`, tell the user this project has no
  mocked-API harness and create a plain PR (see Preconditions), then stop.
- **If it does not exist**, run the discovery pass below to build it.

### Discovery pass (first run only)

Inspect the target repo and record these values, then write them to the cache
file as JSON:

| Key | How to find it |
|---|---|
| `frontendDir` | the dir holding the app + its `src/` (repo root or a subdir like `frontend/`) |
| `e2eDir`, `specsDir` | the Playwright spec/support tree (look for `playwright.config.*`, a `specs/` dir) |
| `authFixtureImport`, `authFixtureName`, `authedPageProp` | the support file exporting a `test` fixture that seeds auth; note its import path, the exported name, and the authed-page property (e.g. `authenticatedPage`) |
| `mockHelperImport`, `mockHelperName` | the helper that stubs `/api` routes with fixture data; its import path + exported name |
| `port` | the `webServer` port in the Playwright config |
| `deviceProjects` | the config's `projects` (default to a desktop + a mobile project) |
| `baseBranch` | the repo's default branch (`main`/`master`; `git symbolic-ref refs/remotes/origin/HEAD`) |
| `packageManager` | infer from lockfile: `pnpm-lock.yaml`→pnpm, `package-lock.json`→npm, `yarn.lock`→yarn, `bun.lockb`→bun |
| `screenshotDir` | default `docs/pr-screenshots` |
| `spinnerSelector` | the app's loading-indicator selector (search for a spinner/`role="status"`/`aria-busy` component) |

### Preconditions gate

The discovery pass is also the gate. **If there is no Playwright e2e harness
that mocks the API** (no mock helper, or no Playwright at all), write
`{ "supported": false, "reason": "<why>" }` to the cache, tell the user that a
trustworthy before/after diff isn't possible here (unmocked data would differ
between runs for reasons other than the code change), and **create a plain PR
instead** — no screenshots. Then stop.

## When NOT to run the capture

If `git diff --name-only $(git merge-base <baseBranch> HEAD)...HEAD` shows no
changes under `<frontendDir>/src/`, there is nothing visual to diff — create the
PR normally and say so. Test-only, docs-only, or backend-only diffs don't need
screenshots.

## Always run the capture in a subagent

Steps 2–6 (writing the spec, capturing, **reading every PNG back to eyeball
it**, committing, embedding) MUST run inside a single dispatched subagent — not
in the main context. Reading screenshots back is this skill's dominant token
cost, and those image bytes are worthless to the main thread; keeping them in a
subagent's throwaway context is the whole savings, with no loss of verification.

Main context does only: step 1 (map the diff, get the user's go-ahead), then
dispatch a subagent with the plan and the config values. The subagent returns a
compact report: the committed screenshot paths + pinned URLs, the PR-body
markdown, and any views it flagged. The main context creates/edits the PR from
that report — it never reads a PNG.

## Workflow

### 1. Map the diff to views, then get the user's go-ahead

List changed files against the merge base (`git merge-base <baseBranch> HEAD`).
For each changed component, trace upward (imports → page/route component → route
path) to find the route(s) where it renders, and note any interaction needed to
make it visible (open a modal, select a tab). Classify each view as **changed**
(existed at the merge base) or **new** (introduced by this branch) — new ones
get an after-only shot.

This capture is heavy (temp worktree, two Playwright passes, a commit pushed to
the branch, PR body rewritten). ALWAYS confirm with AskUserQuestion before
running any of it. The question must cover **both** (a) whether to run the whole
before/after capture at all, and (b) that it will commit the screenshots and
embed them in the PR. Include the proposed plan: which routes/states, and which
device projects — default to the config's `deviceProjects`; add more only when
the change is layout-sensitive. If they decline, create the PR normally and stop.

### 2. Write the capture spec

Copy `references/capture-spec-template.ts` from this skill into
`<specsDir>/__pr-capture__.spec.ts` and substitute the `__PVD_*__` markers from
the config:

- `__PVD_AUTH_FIXTURE_IMPORT__` → `authFixtureImport` (and, if the fixture isn't
  named `test`, adjust the `import { <authFixtureName> as test }` accordingly)
- `__PVD_MOCK_HELPER_IMPORT__` → `mockHelperImport` (and `mockHelperName` if not
  `mockApi`)
- `__PVD_SPINNER_SELECTOR__` → `spinnerSelector`
- the `{ authenticatedPage: page }` destructure → `{ <authedPageProp>: page }`

Then fill in the `VIEWS` array. This file is scaffolding: never commit it, and
delete it when done (it lives in the specs dir, so a forgotten copy would run in
every future e2e invocation).

### 3. Capture "after" (current branch)

From `<frontendDir>`. First make sure nothing is listening on `<port>`
(`lsof -nP -i :<port>`) and kill any stray server there: if the Playwright
config has `reuseExistingServer` on locally, a leftover server from another
worktree would be silently reused and you'd screenshot the *wrong code*.

```bash
PR_SHOT_DIR=<scratchpad>/after PR_SHOT_LABEL=after \
  <packageManager> exec playwright test <specsDir>/__pr-capture__.spec.ts \
  --project=<deviceProject> [--project=<deviceProject>...]
```

Playwright boots its own server and tears it down. The template pins
`deviceScaleFactor: 1` and asserts no spinner is left on screen, so a
raced/under-mocked view fails the test instead of producing a misleading shot.
That assertion is a backstop, not a substitute for looking: the subagent still
Reads each PNG after capture — an empty state, error toast, or wrong content
passes the spinner check but is still wrong. On a genuine failure, fix the spec
(add mock stubs, a wait for a stable element, or correct the spinner selector)
rather than shipping a bad screenshot.

### 4. Capture "before" (merge base, temp worktree)

A "before" shot only makes sense for views that already existed. If a view is
introduced by this branch, capture only its "after" shot and present it as
**New** (step 6). If *every* captured view is new, skip this whole step.

For views that did exist, reconstruct the pre-change app from git:

```bash
BASE=$(git merge-base <baseBranch> HEAD)
git worktree add <scratchpad>/pr-before-shots "$BASE"
cd <scratchpad>/pr-before-shots/<frontendDir> && <packageManager> install
```

Copy the same filled `__pr-capture__.spec.ts` into that worktree's specs dir,
then run the same command with `PR_SHOT_DIR=<scratchpad>/before
PR_SHOT_LABEL=before`. Run it sequentially after the "after" pass — both configs
use `<port>`.

Edge cases:
- A route that doesn't exist at the base (new view): skip it and label it
  **New** in the PR table.
- The e2e helpers (auth fixture, mock helper) missing or different at the base:
  adapt the spec copy to what exists there; the goal is the same rendered view,
  not identical spec code.
- `<packageManager> install` failing on a native binding: rerun once; if it
  persists, report it rather than screenshotting a broken build.

Remove the worktree when done:
`git worktree remove --force <scratchpad>/pr-before-shots` (`--force` because
install left untracked artifacts).

### 5. Commit the images on the PR branch

```
<screenshotDir>/<branch-slug>/<view>--<project>--{before|after}.png
```

`<branch-slug>` = branch name with `/` → `-`. Keep only the shots you'll
reference. Commit them, delete the temp spec, and push.

### 6. Embed in the PR body

After pushing, pin image URLs to the exact commit so they keep rendering after
later force-pushes or branch deletion:

```bash
SHA=$(git rev-parse HEAD)
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
# → https://github.com/$REPO/blob/$SHA/<screenshotDir>/<slug>/<file>.png?raw=true
```

Per view, one table. Put **Before on the left and After on the right** (one
device per row) so a reviewer sees the original and changed component side by
side:

```markdown
### <View name> (`/route`)

| | Before | After |
|---|---|---|
| **Desktop** | ![](<blob-url>?raw=true) | ![](<blob-url>?raw=true) |
| **Mobile** | ![](<blob-url>?raw=true) | ![](<blob-url>?raw=true) |
```

If the repo is private, GitHub's image proxy may refuse to inline blob URLs in
the PR body. Add a one-line note under the tables linking to the
`<screenshotDir>/<slug>/` directory on the branch as a fallback — the images
always render in the Files-changed tab and in blob view for authenticated
viewers.

The subagent's job ends here: it has pushed the commit and produced the PR-body
markdown, and returns both. The **main context** creates (or edits) the PR with
that body, then opens it (`gh pr view --web`) so the user can confirm the embeds
render.

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
(`gh pr view --json baseRefName`) instead of `<baseBranch>`, and edit the body
with `gh pr edit --body-file` instead of creating.
