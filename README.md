# @jesdi/skills

Personal agent skills, installable into Claude Code and OpenCode.

## Install skills

```bash
npx @jesdi/skills-cli
```

Pick skills → pick agents → pick project-local or global. Skills are stored
in `.my-skills/` (or `~/.my-skills/`) and symlinked into each agent's skills
directory.

## Team sync

`.my-skills.json` is meant to be committed. Teammates run:

```bash
npx @jesdi/skills-cli sync
```

## Commands

```
npx @jesdi/skills-cli                      # interactive wizard
npx @jesdi/skills-cli install <skill...> [--agent claude,opencode] [--global]
npx @jesdi/skills-cli update [skill] [--all] [--global]
npx @jesdi/skills-cli sync
npx @jesdi/skills-cli list
npx @jesdi/skills-cli uninstall <skill> [--global]
```

## Third-party skills (not vendored)

Skills authored by other people are **not** copied into this repo — they keep
their own authors, upstreams, and licenses. `external-skills.json` records
which ones are part of the standard setup and where they come from. Install
them from upstream with the [skills.sh](https://skills.sh/) CLI:

```bash
npx skills add mattpocock/skills          # grill-me, grill-with-docs, improve-codebase-architecture
npx skills add JuliusBrussee/caveman      # caveman suite
npx skills add vercel-labs/skills         # find-skills
npx skills add vercel-labs/agent-skills   # vercel-react-best-practices
npx skills add anthropics/skills          # frontend-design (skip if using the Claude Code plugin)
```

The skills.sh lockfile (`~/.agents/.skill-lock.json`) tracks installed
versions; `npx skills update` refreshes them.

## Authoring skills

Add `skills/<name>/SKILL.md` (frontmatter `name` must match the directory,
`description` required). Push to `main` — CI hashes the skill, bumps its
version in `skills-manifest.json`, and publishes `@jesdi/skills`. See
`DESIGN.md` for the full architecture.
