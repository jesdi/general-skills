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

It looks like this:

```json
{
  "schemaVersion": 1,
  "agents": ["claude", "opencode"],
  "skills": {
    "crap-gate": { "version": "1.2.0", "package": "1.4.0" },
    "backlog":   { "version": "0.3.0", "package": "1.4.0", "agents": ["claude"] }
  }
}
```

The top-level `agents` is the default for every skill; a skill's own `agents`
overrides it. `install <skill>` inherits the default (the first interactive
install asks once and stores it), while `install --agent ...` writes a
per-skill override. In scripts, pass `--agent` or commit the top-level default.

## Commands

```
npx @jesdi/skills-cli                      # interactive wizard
npx @jesdi/skills-cli install <skill...> [--agent claude,opencode] [--global]
npx @jesdi/skills-cli update [skill] [--all] [--global]
npx @jesdi/skills-cli sync
npx @jesdi/skills-cli list
npx @jesdi/skills-cli uninstall <skill> [--global]
npx @jesdi/skills-cli pin <skill> [--global]     # keep the installed version
npx @jesdi/skills-cli unpin <skill> [--global]
npx @jesdi/skills-cli hook                 # register the session update hook
```

## Automatic updates

A global install registers a session-start hook in Claude Code
(`~/.claude/settings.json`) and Codex (`~/.codex/hooks.json`), for each one
that is present. The first session of a day runs
`skills-cli update --all --global` in the background; the result is in
`~/.my-skills/.session-update.log`. Project installs do not change: they stay
at the versions committed in `.my-skills.json`.

To keep a skill at its installed version, pin it: `skills-cli pin <skill>
--global`. No update touches a pinned skill until `unpin`. To stop all
automatic updates, remove the hook entry from the two config files; the next
global install or `skills-cli hook` adds it again.

## Third-party skills (not vendored)

Skills authored by other people are **not** copied into this repo — they keep
their own authors, upstreams, and licenses. `external-skills.json` records
which ones are part of the standard setup, where they come from, and (under
`pins`) the upstream commit the box set is taken from. Install them from
upstream with the [skills.sh](https://skills.sh/) CLI:

```bash
npx skills add mattpocock/skills          # grill-me, grill-with-docs, grilling, improve-codebase-architecture, to-questionnaire, tdd, code-review, codebase-design, diagnosing-bugs, resolving-merge-conflicts, pr, handoff, teach, wait-what
npx skills add JuliusBrussee/caveman      # caveman suite
npx skills add vercel-labs/skills         # find-skills
npx skills add vercel-labs/agent-skills   # vercel-react-best-practices
npx skills add anthropics/skills          # frontend-design (skip if using the Claude Code plugin)
```

The skills.sh lockfile (`~/.agents/.skill-lock.json`) tracks installed
versions; `npx skills update` refreshes them.

### Forks (the one exception)

`implement-spec`, `to-spec`, `to-tickets`, `prototype` and `wizard` are
modified copies of the mattpocock/skills originals, vendored under
`skills/<name>/` with the upstream MIT `LICENSE` and a header naming the
upstream commit. They keep their upstream names on purpose, so **never install
the upstream copies of those five names alongside them** (`npx skills add
mattpocock/skills` installs the whole set — deselect those five, or uninstall
them afterwards). `external-skills.json` lists them under `forks`.

### The box set

`external-skills.json` → `sets.box` names the skills the agent-ops box
receives: `own` from this package, `external` from the pinned upstream.
`pr` is in the external set and is installed directly from mattpocock/skills.
agent-ops vendors that set into its claude-home seed with its own refresh
command; this repo's job is to publish the set and keep the pins resolvable.

## Authoring skills

Add `skills/<name>/SKILL.md` (frontmatter `name` must match the directory,
`description` required). Push to `main` — CI hashes the skill, bumps its
version in `skills-manifest.json`, and publishes `@jesdi/skills`. See
`DESIGN.md` for the full architecture.
