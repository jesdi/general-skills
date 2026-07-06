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

## Authoring skills

Add `skills/<name>/SKILL.md` (frontmatter `name` must match the directory,
`description` required). Push to `main` — CI hashes the skill, bumps its
version in `skills-manifest.json`, and publishes `@jesdi/skills`. See
`DESIGN.md` for the full architecture.
