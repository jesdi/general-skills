# @jesdi/skills

Personal agent skills, installable into Claude Code and OpenCode.

## Development

The checks require Node 20 or newer, pnpm, and Python 3.10 or newer (CI uses 3.13).
Prepare a local Python environment once:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
pnpm install --frozen-lockfile
pnpm check
```

`pnpm test:python` and `pnpm check` use `.venv` automatically when no other
virtual environment is active. Otherwise they use `python3` from your PATH.

`pnpm check:fast [file…]` is the fast gate: the type check, then the given
test files (default: `IMPLEMENT_SPEC_LOCKED`), each `.py` in its own pytest run
and the `.ts` files in one vitest run. implement-spec takes it as `--fast-gate`.

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
npx @jesdi/skills-cli hook [--off]         # turn automatic updates on (or off)
npx @jesdi/skills-cli session-update       # run the session checks now
```

## Automatic updates

The first global install asks once whether global skills update themselves,
and `~/.config/my-skills/state.json` records the answer (`autoUpdate`). A
non-interactive install does not ask and turns nothing on. To change the
answer later, run `skills-cli hook` (on) or `skills-cli hook --off` (off).

With automatic updates on, a session-start hook is registered in Claude Code
(`~/.claude/settings.json`) and Codex (`~/.codex/hooks.json`), for each one
that is present. Every session runs `skills-cli session-update` in the
background. It checks all external skills on every run and updates own global
skills once a day. The result is in `~/.my-skills/.session-update.log`.
Claude and Codex share a lock so concurrent starts cannot change the installs
at the same time. External checks run before npm updates, whose requests have
a 30-second deadline. Because the check runs in the background, a repaired skill
may appear in the next session. Project installs stay
at the versions committed in `.my-skills.json`.

The hook runs the latest CLI release, so a new release needs no new `hook`.
A hook written by an earlier CLI keeps the version that wrote it: run
`npx -y @jesdi/skills-cli@latest hook` once to move it to `@latest`.

To keep a skill at its installed version, pin it: `skills-cli pin <skill>
--global`. No own-skill update touches a pinned skill until `unpin`. To stop all
automatic updates, run `skills-cli hook --off`: it removes the hook from both
config files and deletes the script.

## Third-party skills (not vendored)

Skills authored by other people are **not** copied into this repo — they keep
their own authors, upstreams, and licenses. `external-skills.json` records
which ones are part of the standard setup, where they come from, and (under
`pins`) the upstream commits for the standard setup and the box set.
Session checks fetch this catalogue from this repository's `main` branch at
most once a day. They compare installed files, including resources and executable
permissions, with the files at each pinned ref. Missing or different files
are repaired from upstream or a verified local cache. A failed or invalid
catalogue refresh uses the last valid cached catalogue and reports the error.
Ref changes take effect
even when the version label stays the same. Sources without version labels
are identified by their commit ref.

External installs live in `~/.agents/skills/` for Codex and OpenCode, with links
in `~/.claude/skills/` when Claude Code is present. Their source, path, ref,
optional version and content hash are recorded in
`~/.config/my-skills/external.json`. Upstream attribution and the identities
of directories created by the updater establish
ownership. Interrupted swaps retain ownership only of their known directory
identities; failed replacements roll back uncommitted provenance. Existing
skills.sh entries from the same
source can be adopted, including Claude copies with matching content and current
source attribution. Copies awaiting recovery also retain their directory identity.
Publication and rollback use the OS's atomic no-replace rename through Koffi:
[`RENAME_EXCL`](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/APFS_Guide/ToolsandAPIs/ToolsandAPIs.html)
on macOS, [`RENAME_NOREPLACE`](https://man7.org/linux/man-pages/man2/rename.2.html)
on Linux, and [`MoveFileExW`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
without replacement flags on Windows. Unsupported filesystems fail without a
replacement fallback. If a concurrent install blocks rollback, both copies are
preserved and the log names the backup location. Current
conflicting skills.sh provenance and independently replaced Claude directories
are preserved and reported in
the log. An enabled Claude frontend-design plugin takes precedence over a
direct Claude install of that skill; the Codex entry is still installed.

For a manual setup, use the [skills.sh](https://skills.sh/) CLI:

```bash
npx skills add mattpocock/skills          # grill-me, grill-with-docs, grilling, improve-codebase-architecture, to-questionnaire, tdd, code-review, codebase-design, diagnosing-bugs, resolving-merge-conflicts, pr, handoff, teach, wait-what
npx skills add JuliusBrussee/caveman      # caveman suite
npx skills add vercel-labs/skills         # find-skills
npx skills add vercel-labs/agent-skills   # vercel-react-best-practices
npx skills add anthropics/skills          # frontend-design (skip if using the Claude Code plugin)
```

The skills.sh lockfile (`~/.agents/.skill-lock.json`) records its own installs.
Session checks leave that file in place. `npx skills update` can move an install
away from the configured pin; the next session check restores the catalogue's
version.

### Forks (the one exception)

`implement-spec`, `to-spec`, `to-tickets`, `prototype` and `wizard` are
modified copies of the mattpocock/skills originals, vendored under
`skills/<name>/` with the upstream MIT `LICENSE` and a header naming the
upstream commit. They keep their upstream names on purpose, so **never install
the upstream copies of those five names alongside them** (`npx skills add
mattpocock/skills` installs the whole set — deselect those five, or uninstall
them afterwards). `external-skills.json` lists them under `forks`.

`agents-wayfinder` is a modified copy of the upstream `wayfinder` under its own
name, so the two can be installed side by side. It hands off each increment as a
`spec-ready` build issue while the map is still being worked.

### The box set

`external-skills.json` → `sets.box.external` names the third-party skills the
agent-ops box receives from the pinned upstream. `pr` is in this set and is
installed directly from mattpocock/skills. agent-ops-infra lists the own skills
of the box (`provision/my-skills.json`) and pins the commit of this file it
reads (`provision/external-skills.pin`); this repo's job is to publish the set
and keep the pins resolvable.

## Authoring skills

Add `skills/<name>/SKILL.md` (frontmatter `name` must match the directory,
`description` required). Push to `main` — CI hashes the skill, bumps its
version in `skills-manifest.json`, and publishes `@jesdi/skills`. See
`DESIGN.md` for the full architecture.
