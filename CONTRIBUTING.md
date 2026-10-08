# Contributing

Thanks for your interest in contributing! This repository accepts contributions through the standard fork-based workflow.

## How to contribute

Direct pushes and in-repo branches are restricted to maintainers. To propose a change:

1. **Fork** this repository to your own account.
2. Create a branch in your fork for your change.
3. Make your changes and make sure the test suite passes (see below).
4. Open a **pull request against `main`** of this repository.
5. A maintainer will review your PR. CI must pass before it can be merged; only maintainers can merge.

## Development setup

Requirements:

- Node.js >= 20
- [pnpm](https://pnpm.io) 10 (pinned via the `packageManager` field — `corepack enable` handles it)
- Python 3.13+ with the dependencies in `requirements-dev.txt`

```bash
pnpm install
python3 -m pip install -r requirements-dev.txt
```

## Running tests

All of these run in CI on every pull request and must pass:

```bash
pnpm check
```

This command runs the TypeScript and Python suites once with branch coverage,
builds the CLI, checks types, then scores every changed runtime function with
CRAP. `.crap-gate.json` uses the same limits as our other repositories: existing
functions must score at most 15 and new functions at most 9. It compares the
working tree with the merge-base of `origin/main`; fetch that ref before checking
a fresh clone. Tests and declarations are excluded from scoring. Reports and
logs stay in the ignored `.crap/` directory.

`pnpm check --base <commit>` chooses another comparison point. CI passes the
target branch SHA for pull requests and the previous main SHA for pushes, so
direct pushes are scored even after `origin/main` has advanced to the new tip.

For a focused check, `pnpm crap-gate` runs coverage only for changed targets.
`pnpm crap-gate --no-run` reuses existing reports; use it only when those reports
match the current source and tests. `pnpm test` remains available for a quick
TypeScript test run without coverage.

## Guidelines

- **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `ci:`, `docs:`, ...), with a subject of at most ~50 characters.
- **Do not edit generated files** (`skills-manifest.json`, CHANGELOG files) — they are produced by CI.
- **Skills** live under `skills/<name>/` with a `SKILL.md` entry point. Keep tests next to the skill they cover.
- Keep pull requests focused: one logical change per PR.

## Releases

Publishing to npm is automated: merges to `main` trigger the publish workflows, which patch-bump and publish `@jesdi/skills` when a skill changes and `@jesdi/skills-cli` when `cli/src` or `cli/package.json` changes. Contributors never need to touch versions; bump `cli/package.json` by hand only for a minor or major CLI release, and the workflow publishes that version as-is.
