# Skills Distribution — Design

Personal skills repo published to npm, installable into local agents via an interactive CLI.

## Goal

Author agent skills once, in this repo, and install them into any project or machine with:

```bash
npx @jesdi/skills-cli
```

The wizard lets the user pick skills, target agents (Claude Code, OpenCode), and scope (project-local or global).

## Packages

Two npm packages, decoupled release cadences:

| Package | Contents | Published |
|---|---|---|
| `@jesdi/skills` | `skills/` directories + CI-generated `skills-manifest.json` | Every push to `main` that changes a skill |
| `@jesdi/skills-cli` | The CLI | Only when the CLI itself changes |

The CLI resolves skills **at runtime**: it queries the npm registry for the latest `@jesdi/skills`, downloads the tarball into a cache, and reads the manifest from it. An old CLI therefore always sees new skills. The manifest carries a `schemaVersion`; if it's newer than the CLI understands, the CLI tells the user to update it. Offline, the CLI falls back to the last cached tarball.

## Skill format

- One directory per skill: `skills/<name>/SKILL.md` plus optional supporting files (scripts, references, templates), per the [Agent Skills spec](https://agentskills.io).
- No per-agent transformation: Claude Code and OpenCode both consume the same `SKILL.md` format. Installation only differs in *where* the directory is linked.

## Versioning

- Per-skill versions live in the CI-generated `skills-manifest.json` (`name`, `version`, content hash per skill). Authors never hand-bump versions.
- CI (GitHub Actions, on push to `main`): diff each skill directory against the last release, patch-bump changed skills in the manifest, publish `@jesdi/skills`.

## Installation model

Real copies live in a dedicated store owned by the CLI; every selected agent directory gets a **symlink** into the store.

| Scope | Store | Claude Code link | OpenCode link |
|---|---|---|---|
| Global | `~/.my-skills/<skill>/` | `~/.claude/skills/<skill>` | `~/.agents/skills/<skill>` |
| Local | `<project>/.my-skills/<skill>/` | `<project>/.claude/skills/<skill>` | `<project>/.agents/skills/<skill>` |

`.agents/skills` is the agent-agnostic standard location (supported by OpenCode and other agents), so it doubles as the forward-compatible target for future agents.

Rationale: a neutral store means the canonical copy never migrates when agents are added or removed, and nothing is parked inside `~/.agents/skills` (which OpenCode reads) unless opencode was actually selected. The CLI must only ever replace symlinks it created — never clobber a foreign file or directory in an agent dir.

macOS/Linux first; Windows symlink handling deferred.

## State

- Global state: `~/.config/my-skills/state.json` — installed skills, versions, agent selections, declined updates.
- Project state: `<project>/.my-skills.json` — **committed** to the project repo so teammates can reproduce the setup with `npx @jesdi/skills-cli sync`. The store (`.my-skills/`) and the agent symlinks are gitignored.

## Updates

- On every CLI run: compare installed versions (state) against the freshly fetched manifest; prompt to update outdated skills.
- Declines are remembered **per version**: declining `1.2.3` silences prompts for `1.2.3` only; the next release asks again. `update` still lists declined versions as available.

## CLI surface

Bare `npx @jesdi/skills-cli` → interactive wizard (update prompts first, then pick skills → agents → scope). Subcommands for scripting:

```
install <skill...> [--agent claude,opencode] [--global]
update  [skill]
sync                  # materialize from committed .my-skills.json
list
uninstall <skill>
```

Stack: TypeScript, `@clack/prompts` (wizard), `commander` (subcommands).
