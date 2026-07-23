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
- Python 3.13+ with `pytest` (only needed for skills that ship Python tests)

```bash
pnpm install
```

## Running tests

All of these run in CI on every pull request and must pass:

```bash
pnpm test                              # vitest: root tests/ and cli/tests/
pnpm --filter @jesdi/skills-cli build  # CLI must build cleanly
python3 -m pytest skills/backlog -q    # Python tests for the backlog skill
```

## Guidelines

- **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `ci:`, `docs:`, ...), with a subject of at most ~50 characters.
- **Do not edit generated files** (`skills-manifest.json`, CHANGELOG files) — they are produced by CI.
- **Skills** live under `skills/<name>/` with a `SKILL.md` entry point. Keep tests next to the skill they cover.
- Keep pull requests focused: one logical change per PR.

## Releases

Publishing to npm is automated: merges to `main` trigger the publish workflows, which version-bump and publish `@jesdi/skills` and `@jesdi/skills-cli` when their contents change. Contributors never need to touch versions.
