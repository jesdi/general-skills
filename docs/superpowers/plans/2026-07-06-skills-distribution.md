# Skills Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A monorepo publishing `@jesdi/skills` (skill content + per-skill version manifest, auto-released by CI) and `@jesdi/skills-cli` (an npx wizard that installs skills into Claude Code / OpenCode via a store-and-symlink model).

**Architecture:** Skills live as `skills/<name>/SKILL.md` directories. A generator script hashes each skill dir and patch-bumps changed skills in a committed `skills-manifest.json`; CI publishes `@jesdi/skills` on every skill change. The CLI fetches the latest `@jesdi/skills` tarball from the npm registry at runtime, copies chosen skills into a store (`~/.my-skills` or `<project>/.my-skills`), and symlinks them into each selected agent's skills directory. State (installed versions, declined updates) lives in `~/.config/my-skills/state.json` globally and a committed `<project>/.my-skills.json` locally.

**Tech Stack:** TypeScript, Node ≥20 (global `fetch`), npm workspaces, vitest, tsx, gray-matter, commander, @clack/prompts, tar, tsup.

**Reference spec:** `DESIGN.md` at repo root. One refinement over the spec: `skills-manifest.json` is committed to the repo (CI regenerates and commits it before publishing) so "previous version" lookup is a local file read and version history is reviewable in git.

## Global Constraints

- npm scope is `@jesdi`: packages are `@jesdi/skills` and `@jesdi/skills-cli`.
- Node `>=20`, ESM only (`"type": "module"` everywhere).
- macOS/Linux only for now; Windows symlink handling is out of scope.
- Installs are ALWAYS store + symlink. The CLI must never overwrite a file/dir in an agent directory that it did not create (i.e., anything that is not a symlink pointing into our store) — raise `ForeignEntryError` instead.
- Agent dirs — claude: `~/.claude/skills` (global) / `<project>/.claude/skills` (local); opencode: `~/.agents/skills` / `<project>/.agents/skills` (the agent-agnostic standard).
- Store dirs — global `~/.my-skills/<skill>`, local `<project>/.my-skills/<skill>`. Cache: `~/.cache/my-skills/<pkg-version>/`.
- Manifest and state files carry `schemaVersion: 1`; a CLI seeing a newer manifest schema must abort with "update @jesdi/skills-cli".
- Update declines are remembered per skill **per version** (declining `0.1.2` re-prompts when `0.1.3` ships).
- New skills start at version `0.1.0`; any content change patch-bumps.
- Commit messages: Conventional Commits, subject ≤50 chars, NO co-author trailers.
- All tests use temp dirs (`fs.mkdtemp`) — never touch the real `$HOME`.

---

### Task 1: Repo scaffold and seed skill

**Files:**
- Create: `package.json`, `tsconfig.json`, `vitest.config.ts`, `.gitignore`, `skills/hello-world/SKILL.md`

**Interfaces:**
- Produces: npm workspace root named `@jesdi/skills` with `files: ["skills", "skills-manifest.json"]`; a valid seed skill later tasks' tests rely on (`skills/hello-world` with frontmatter `name: hello-world`).

- [ ] **Step 1: Write root config files**

`package.json`:

```json
{
  "name": "@jesdi/skills",
  "version": "0.0.0",
  "description": "jesdi's agent skills (content package consumed by @jesdi/skills-cli)",
  "license": "MIT",
  "type": "module",
  "files": ["skills", "skills-manifest.json"],
  "workspaces": ["cli"],
  "scripts": {
    "test": "vitest run",
    "generate-manifest": "tsx scripts/generate-manifest.ts"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "gray-matter": "^4.0.3",
    "tsx": "^4.19.0",
    "typescript": "^5.5.0",
    "vitest": "^2.1.0"
  },
  "engines": { "node": ">=20" }
}
```

`tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["node"]
  },
  "include": ["lib", "scripts", "tests", "cli/src", "cli/tests"]
}
```

`vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts', 'cli/tests/**/*.test.ts'],
    passWithNoTests: true,
  },
});
```

`.gitignore`:

```
node_modules/
dist/
.my-skills/
```

- [ ] **Step 2: Write the seed skill**

`skills/hello-world/SKILL.md`:

```markdown
---
name: hello-world
description: Minimal example skill proving the distribution pipeline works. Responds to "test my skills setup" by confirming the skill is loaded.
---

# Hello World

When the user asks to test their skills setup, reply exactly:
"✅ hello-world skill is installed and loaded."
```

- [ ] **Step 3: Install and verify**

Run: `npm install && npm test`
Expected: install succeeds; vitest prints "No test files found" and exits 0 (passWithNoTests).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: scaffold skills monorepo with seed skill"
```

---

### Task 2: Skill discovery (`lib/skills.ts`)

**Files:**
- Create: `lib/skills.ts`
- Test: `tests/skills.test.ts`

**Interfaces:**
- Produces: `interface SkillInfo { name: string; description: string; dir: string }` and `async function listSkills(skillsDir: string): Promise<SkillInfo[]>` — sorted by name; throws on missing `SKILL.md`, missing `description`, or frontmatter `name` ≠ directory name.

- [ ] **Step 1: Write the failing test**

`tests/skills.test.ts`:

```ts
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { listSkills } from '../lib/skills.js';

async function makeSkill(root: string, name: string, frontmatter: string) {
  const dir = join(root, name);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, 'SKILL.md'), `---\n${frontmatter}\n---\n\nBody.\n`);
  return dir;
}

describe('listSkills', () => {
  it('lists skills sorted by name with descriptions', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await makeSkill(root, 'zeta', 'name: zeta\ndescription: Z skill');
    await makeSkill(root, 'alpha', 'name: alpha\ndescription: A skill');
    const skills = await listSkills(root);
    expect(skills.map((s) => s.name)).toEqual(['alpha', 'zeta']);
    expect(skills[0].description).toBe('A skill');
    expect(skills[0].dir).toBe(join(root, 'alpha'));
  });

  it('rejects a skill whose frontmatter name mismatches its directory', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await makeSkill(root, 'alpha', 'name: beta\ndescription: broken');
    await expect(listSkills(root)).rejects.toThrow(/must match directory/);
  });

  it('rejects a skill without a description', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await makeSkill(root, 'alpha', 'name: alpha');
    await expect(listSkills(root)).rejects.toThrow(/description/);
  });

  it('rejects a skill directory without SKILL.md', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await mkdir(join(root, 'empty'));
    await expect(listSkills(root)).rejects.toThrow(/SKILL\.md/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/skills.test.ts`
Expected: FAIL — cannot find module `../lib/skills.js`.

- [ ] **Step 3: Implement**

`lib/skills.ts`:

```ts
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import matter from 'gray-matter';

export interface SkillInfo {
  name: string;
  description: string;
  dir: string;
}

export async function listSkills(skillsDir: string): Promise<SkillInfo[]> {
  const entries = await readdir(skillsDir, { withFileTypes: true });
  const dirs = entries.filter((e) => e.isDirectory()).sort((a, b) => a.name.localeCompare(b.name));
  const skills: SkillInfo[] = [];
  for (const entry of dirs) {
    const dir = join(skillsDir, entry.name);
    let raw: string;
    try {
      raw = await readFile(join(dir, 'SKILL.md'), 'utf8');
    } catch {
      throw new Error(`skills/${entry.name}: missing SKILL.md`);
    }
    const { data } = matter(raw);
    if (data.name !== entry.name) {
      throw new Error(
        `skills/${entry.name}: frontmatter name "${data.name}" must match directory name`,
      );
    }
    if (!data.description || typeof data.description !== 'string') {
      throw new Error(`skills/${entry.name}: frontmatter must include a description`);
    }
    skills.push({ name: entry.name, description: data.description, dir });
  }
  return skills;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/skills.test.ts`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/skills.ts tests/skills.test.ts
git commit -m "feat: skill discovery with frontmatter checks"
```

---

### Task 3: Hashing and manifest generation (`lib/manifest.ts`)

**Files:**
- Create: `lib/manifest.ts`
- Test: `tests/manifest.test.ts`

**Interfaces:**
- Consumes: `listSkills(skillsDir)` from `lib/skills.js`.
- Produces:
  - `interface ManifestSkill { name: string; version: string; hash: string; description: string }`
  - `interface Manifest { schemaVersion: 1; generatedAt: string; skills: ManifestSkill[] }`
  - `async function hashSkillDir(dir: string): Promise<string>` — `"sha256-<hex>"` over sorted relative paths + contents.
  - `async function generateManifest(skillsDir: string, previous: Manifest | null): Promise<Manifest>` — new skill → `0.1.0`; same hash → keep version; changed hash → patch-bump previous version; removed skills drop out.

- [ ] **Step 1: Write the failing test**

`tests/manifest.test.ts`:

```ts
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { generateManifest, hashSkillDir, type Manifest } from '../lib/manifest.js';

async function makeSkill(root: string, name: string, body: string) {
  const dir = join(root, name);
  await mkdir(dir, { recursive: true });
  await writeFile(
    join(dir, 'SKILL.md'),
    `---\nname: ${name}\ndescription: ${name} skill\n---\n\n${body}\n`,
  );
  return dir;
}

describe('hashSkillDir', () => {
  it('is stable for identical content and changes when content changes', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    const dir = await makeSkill(root, 'alpha', 'v1');
    const h1 = await hashSkillDir(dir);
    expect(h1).toMatch(/^sha256-[0-9a-f]{64}$/);
    expect(await hashSkillDir(dir)).toBe(h1);
    await writeFile(join(dir, 'SKILL.md'), 'changed');
    expect(await hashSkillDir(dir)).not.toBe(h1);
  });
});

describe('generateManifest', () => {
  it('assigns 0.1.0 to new skills', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    await makeSkill(root, 'alpha', 'v1');
    const m = await generateManifest(root, null);
    expect(m.schemaVersion).toBe(1);
    expect(m.skills).toHaveLength(1);
    expect(m.skills[0]).toMatchObject({ name: 'alpha', version: '0.1.0' });
  });

  it('keeps versions for unchanged skills and patch-bumps changed ones', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    await makeSkill(root, 'alpha', 'v1');
    await makeSkill(root, 'beta', 'v1');
    const first = await generateManifest(root, null);
    await writeFile(
      join(root, 'beta', 'SKILL.md'),
      '---\nname: beta\ndescription: beta skill\n---\n\nv2\n',
    );
    const second = await generateManifest(root, first);
    const byName = Object.fromEntries(second.skills.map((s) => [s.name, s.version]));
    expect(byName.alpha).toBe('0.1.0');
    expect(byName.beta).toBe('0.1.1');
  });

  it('drops removed skills', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    await makeSkill(root, 'alpha', 'v1');
    await makeSkill(root, 'beta', 'v1');
    const first: Manifest = await generateManifest(root, null);
    await rm(join(root, 'beta'), { recursive: true });
    const second = await generateManifest(root, first);
    expect(second.skills.map((s) => s.name)).toEqual(['alpha']);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/manifest.test.ts`
Expected: FAIL — cannot find module `../lib/manifest.js`.

- [ ] **Step 3: Implement**

`lib/manifest.ts`:

```ts
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { listSkills } from './skills.js';

export interface ManifestSkill {
  name: string;
  version: string;
  hash: string;
  description: string;
}

export interface Manifest {
  schemaVersion: 1;
  generatedAt: string;
  skills: ManifestSkill[];
}

async function walk(dir: string, prefix = ''): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const e of entries) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) files.push(...(await walk(join(dir, e.name), rel)));
    else files.push(rel);
  }
  return files.sort();
}

export async function hashSkillDir(dir: string): Promise<string> {
  const hash = createHash('sha256');
  for (const rel of await walk(dir)) {
    hash.update(rel);
    hash.update('\0');
    hash.update(await readFile(join(dir, rel)));
    hash.update('\0');
  }
  return `sha256-${hash.digest('hex')}`;
}

function bumpPatch(version: string): string {
  const [major, minor, patch] = version.split('.').map(Number);
  return `${major}.${minor}.${patch + 1}`;
}

export async function generateManifest(
  skillsDir: string,
  previous: Manifest | null,
): Promise<Manifest> {
  const prev = new Map((previous?.skills ?? []).map((s) => [s.name, s]));
  const skills: ManifestSkill[] = [];
  for (const skill of await listSkills(skillsDir)) {
    const hash = await hashSkillDir(skill.dir);
    const before = prev.get(skill.name);
    const version =
      before === undefined ? '0.1.0' : before.hash === hash ? before.version : bumpPatch(before.version);
    skills.push({ name: skill.name, version, hash, description: skill.description });
  }
  return { schemaVersion: 1, generatedAt: new Date().toISOString(), skills };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/manifest.test.ts`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/manifest.ts tests/manifest.test.ts
git commit -m "feat: skill hashing and manifest generation"
```

---

### Task 4: Manifest generator script

**Files:**
- Create: `scripts/generate-manifest.ts`, `skills-manifest.json` (generated)

**Interfaces:**
- Consumes: `generateManifest` from `lib/manifest.js`.
- Produces: committed `skills-manifest.json` at repo root; `npm run generate-manifest` regenerates it in place using the committed file as "previous".

- [ ] **Step 1: Implement the script**

`scripts/generate-manifest.ts`:

```ts
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { generateManifest, type Manifest } from '../lib/manifest.js';

const root = new URL('..', import.meta.url).pathname;
const manifestPath = join(root, 'skills-manifest.json');

let previous: Manifest | null = null;
try {
  previous = JSON.parse(await readFile(manifestPath, 'utf8'));
} catch {
  /* first run: no previous manifest */
}

const manifest = await generateManifest(join(root, 'skills'), previous);
await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
console.log(`Wrote ${manifestPath} (${manifest.skills.length} skills)`);
```

- [ ] **Step 2: Run it and inspect output**

Run: `npm run generate-manifest && cat skills-manifest.json`
Expected: file contains `"schemaVersion": 1` and one entry `{"name": "hello-world", "version": "0.1.0", ...}`.

- [ ] **Step 3: Verify idempotence**

Run: `npm run generate-manifest && git diff --stat skills-manifest.json`
Expected: only the `generatedAt` line differs (hash/version stable). Then run `git checkout -- skills-manifest.json 2>/dev/null || true` — skip if the file is untracked (first run).

- [ ] **Step 4: Commit**

```bash
git add scripts/generate-manifest.ts skills-manifest.json
git commit -m "feat: manifest generator script"
```

---

### Task 5: CI publish workflow for `@jesdi/skills`

**Files:**
- Create: `.github/workflows/publish-skills.yml`

**Interfaces:**
- Produces: on push to `main` touching `skills/**`, `lib/**`, or `scripts/**`: tests run, manifest regenerated; if it changed, CI commits it with a patch-bumped package version (`[skip ci]`) and publishes `@jesdi/skills`.

- [ ] **Step 1: Write the workflow**

`.github/workflows/publish-skills.yml`:

```yaml
name: publish-skills

on:
  push:
    branches: [main]
    paths: ['skills/**', 'lib/**', 'scripts/**']

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          registry-url: 'https://registry.npmjs.org'
      - run: npm ci
      - run: npm test
      - run: npm run generate-manifest
      - name: Publish if skills changed
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
        run: |
          if git diff --quiet -I'"generatedAt"' skills-manifest.json; then
            echo "No skill changes; skipping publish."
            git checkout -- skills-manifest.json
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          npm version patch --no-git-tag-version
          git add skills-manifest.json package.json
          git commit -m "chore: release skills [skip ci]"
          git push
          npm publish --provenance --access public
```

- [ ] **Step 2: Sanity-check the YAML**

Run: `npx --yes yaml-lint .github/workflows/publish-skills.yml || python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/publish-skills.yml')); print('OK')"`
Expected: `OK` (or yaml-lint success).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish-skills.yml
git commit -m "ci: auto-publish skills package on main"
```

Note for the human: create the `NPM_TOKEN` repo secret (npm automation token for the `@jesdi` scope) and allow Actions to push to `main` (Settings → Actions → Workflow permissions → Read and write) before the first push.

---

### Task 6: CLI package scaffold

**Files:**
- Create: `cli/package.json`, `cli/src/index.ts`, `cli/src/program.ts`
- Test: `cli/tests/program.test.ts`

**Interfaces:**
- Produces: `function buildProgram(): Command` (commander `Command` named `skills-cli`, version from `cli/package.json`); `cli/src/index.ts` executable entry. Later tasks attach subcommands inside `buildProgram`.

- [ ] **Step 1: Write CLI package config**

`cli/package.json`:

```json
{
  "name": "@jesdi/skills-cli",
  "version": "0.1.0",
  "description": "Install jesdi's agent skills into Claude Code and OpenCode",
  "license": "MIT",
  "type": "module",
  "bin": { "skills-cli": "./dist/index.js" },
  "files": ["dist"],
  "scripts": {
    "build": "tsup src/index.ts --format esm --clean",
    "dev": "tsx src/index.ts"
  },
  "dependencies": {
    "@clack/prompts": "^0.11.0",
    "commander": "^12.1.0",
    "tar": "^7.4.0"
  },
  "devDependencies": {
    "tsup": "^8.3.0"
  },
  "engines": { "node": ">=20" }
}
```

Run `npm install` at repo root after writing it.

- [ ] **Step 2: Write the failing test**

`cli/tests/program.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { buildProgram } from '../src/program.js';

describe('buildProgram', () => {
  it('is named skills-cli and reports a semver version', () => {
    const program = buildProgram();
    expect(program.name()).toBe('skills-cli');
    expect(program.version()).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run cli/tests/program.test.ts`
Expected: FAIL — cannot find module `../src/program.js`.

- [ ] **Step 4: Implement**

`cli/src/program.ts`:

```ts
import { readFileSync } from 'node:fs';
import { Command } from 'commander';

const pkg = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
);

export function buildProgram(): Command {
  const program = new Command('skills-cli');
  program
    .description("Install jesdi's agent skills into Claude Code and OpenCode")
    .version(pkg.version);
  return program;
}
```

`cli/src/index.ts`:

```ts
#!/usr/bin/env node
import { buildProgram } from './program.js';

await buildProgram().parseAsync(process.argv);
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run cli/tests/program.test.ts`
Expected: 1 test PASS. Also run `npx tsx cli/src/index.ts --version` → prints `0.1.0`.

- [ ] **Step 6: Commit**

```bash
git add cli package.json package-lock.json
git commit -m "feat: scaffold skills-cli package"
```

---

### Task 7: Path resolution (`cli/src/paths.ts`)

**Files:**
- Create: `cli/src/paths.ts`
- Test: `cli/tests/paths.test.ts`

**Interfaces:**
- Produces:
  - `type AgentId = 'claude' | 'opencode'`; `type Scope = 'global' | 'local'`
  - `const AGENTS: Record<AgentId, { label: string }>`
  - `interface Ctx { home: string; project: string }`
  - `function agentSkillsDir(agent: AgentId, scope: Scope, ctx: Ctx): string`
  - `function storeDir(scope: Scope, ctx: Ctx): string`
  - `function globalStateFile(ctx: Ctx): string` → `<home>/.config/my-skills/state.json`
  - `function projectStateFile(ctx: Ctx): string` → `<project>/.my-skills.json`
  - `function cacheDir(ctx: Ctx): string` → `<home>/.cache/my-skills`

- [ ] **Step 1: Write the failing test**

`cli/tests/paths.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  agentSkillsDir,
  cacheDir,
  globalStateFile,
  projectStateFile,
  storeDir,
} from '../src/paths.js';

const ctx = { home: '/home/u', project: '/proj' };

describe('paths', () => {
  it('resolves agent skills dirs per scope', () => {
    expect(agentSkillsDir('claude', 'global', ctx)).toBe('/home/u/.claude/skills');
    expect(agentSkillsDir('claude', 'local', ctx)).toBe('/proj/.claude/skills');
    expect(agentSkillsDir('opencode', 'global', ctx)).toBe('/home/u/.agents/skills');
    expect(agentSkillsDir('opencode', 'local', ctx)).toBe('/proj/.agents/skills');
  });

  it('resolves store, state and cache locations', () => {
    expect(storeDir('global', ctx)).toBe('/home/u/.my-skills');
    expect(storeDir('local', ctx)).toBe('/proj/.my-skills');
    expect(globalStateFile(ctx)).toBe('/home/u/.config/my-skills/state.json');
    expect(projectStateFile(ctx)).toBe('/proj/.my-skills.json');
    expect(cacheDir(ctx)).toBe('/home/u/.cache/my-skills');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/paths.test.ts`
Expected: FAIL — cannot find module `../src/paths.js`.

- [ ] **Step 3: Implement**

`cli/src/paths.ts`:

```ts
import { join } from 'node:path';

export type AgentId = 'claude' | 'opencode';
export type Scope = 'global' | 'local';

export const AGENTS: Record<AgentId, { label: string }> = {
  claude: { label: 'Claude Code' },
  opencode: { label: 'OpenCode (.agents standard)' },
};

export interface Ctx {
  home: string;
  project: string;
}

const AGENT_SUBDIR: Record<AgentId, string> = {
  claude: join('.claude', 'skills'),
  opencode: join('.agents', 'skills'),
};

function scopeRoot(scope: Scope, ctx: Ctx): string {
  return scope === 'global' ? ctx.home : ctx.project;
}

export function agentSkillsDir(agent: AgentId, scope: Scope, ctx: Ctx): string {
  return join(scopeRoot(scope, ctx), AGENT_SUBDIR[agent]);
}

export function storeDir(scope: Scope, ctx: Ctx): string {
  return join(scopeRoot(scope, ctx), '.my-skills');
}

export function globalStateFile(ctx: Ctx): string {
  return join(ctx.home, '.config', 'my-skills', 'state.json');
}

export function projectStateFile(ctx: Ctx): string {
  return join(ctx.project, '.my-skills.json');
}

export function cacheDir(ctx: Ctx): string {
  return join(ctx.home, '.cache', 'my-skills');
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/paths.test.ts`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/src/paths.ts cli/tests/paths.test.ts
git commit -m "feat: cli path resolution for agents and store"
```

---

### Task 8: Registry client (`cli/src/registry.ts`)

**Files:**
- Create: `cli/src/registry.ts`, `cli/src/manifest.ts` (types, duplicated from root lib on purpose — the packages publish independently)
- Test: `cli/tests/registry.test.ts`

**Interfaces:**
- Produces:
  - `cli/src/manifest.ts`: `interface ManifestSkill { name: string; version: string; hash: string; description: string }`, `interface Manifest { schemaVersion: 1; generatedAt: string; skills: ManifestSkill[] }`
  - `interface FetchLatestResult { packageVersion: string; manifest: Manifest; skillsDir: string }`
  - `async function fetchLatest(opts: { cacheDir: string; registryUrl?: string; fetchImpl?: typeof fetch }): Promise<FetchLatestResult>` — queries `<registry>/@jesdi%2fskills/latest`, downloads + extracts the tarball into `<cacheDir>/<version>/`, reuses the cache when present, falls back to the newest cached version when the network fails, and throws `Error(/update @jesdi\/skills-cli/)` when `manifest.schemaVersion > 1`.

- [ ] **Step 1: Write the failing test (with an in-memory fixture tarball)**

`cli/tests/registry.test.ts`:

```ts
import { mkdtemp, mkdir, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { fetchLatest } from '../src/registry.js';

async function buildFixture(version: string, schemaVersion = 1) {
  const root = await mkdtemp(join(tmpdir(), 'fixture-'));
  const pkg = join(root, 'package');
  await mkdir(join(pkg, 'skills', 'hello-world'), { recursive: true });
  await writeFile(
    join(pkg, 'skills', 'hello-world', 'SKILL.md'),
    '---\nname: hello-world\ndescription: demo\n---\nhi\n',
  );
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion,
      generatedAt: new Date().toISOString(),
      skills: [{ name: 'hello-world', version: '0.1.0', hash: 'sha256-x', description: 'demo' }],
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);

  const fetchImpl = (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(
        JSON.stringify({ version, dist: { tarball: `https://reg.test/pkg-${version}.tgz` } }),
      );
    }
    if (u.endsWith('.tgz')) return new Response(new Uint8Array(body));
    return new Response('not found', { status: 404 });
  }) as typeof fetch;

  return fetchImpl;
}

describe('fetchLatest', () => {
  it('downloads, extracts and returns the manifest', async () => {
    const cache = await mkdtemp(join(tmpdir(), 'cache-'));
    const fetchImpl = await buildFixture('1.2.3');
    const result = await fetchLatest({ cacheDir: cache, fetchImpl });
    expect(result.packageVersion).toBe('1.2.3');
    expect(result.manifest.skills[0].name).toBe('hello-world');
    const skillMd = await readFile(
      join(result.skillsDir, 'hello-world', 'SKILL.md'),
      'utf8',
    );
    expect(skillMd).toContain('description: demo');
  });

  it('reuses the cache and falls back to it when the network fails', async () => {
    const cache = await mkdtemp(join(tmpdir(), 'cache-'));
    const fetchImpl = await buildFixture('1.2.3');
    await fetchLatest({ cacheDir: cache, fetchImpl });
    const failing = (async () => {
      throw new Error('offline');
    }) as unknown as typeof fetch;
    const result = await fetchLatest({ cacheDir: cache, fetchImpl: failing });
    expect(result.packageVersion).toBe('1.2.3');
  });

  it('rejects a manifest with a newer schemaVersion', async () => {
    const cache = await mkdtemp(join(tmpdir(), 'cache-'));
    const fetchImpl = await buildFixture('2.0.0', 99);
    await expect(fetchLatest({ cacheDir: cache, fetchImpl })).rejects.toThrow(
      /update @jesdi\/skills-cli/,
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/registry.test.ts`
Expected: FAIL — cannot find module `../src/registry.js`.

- [ ] **Step 3: Implement**

`cli/src/manifest.ts`:

```ts
export interface ManifestSkill {
  name: string;
  version: string;
  hash: string;
  description: string;
}

export interface Manifest {
  schemaVersion: 1;
  generatedAt: string;
  skills: ManifestSkill[];
}
```

`cli/src/registry.ts`:

```ts
import { mkdir, readFile, readdir, writeFile, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import * as tar from 'tar';
import type { Manifest } from './manifest.js';

const PACKAGE = '@jesdi%2fskills';
const DEFAULT_REGISTRY = 'https://registry.npmjs.org';

export interface FetchLatestResult {
  packageVersion: string;
  manifest: Manifest;
  skillsDir: string;
}

async function loadFromCache(cacheDir: string, version: string): Promise<FetchLatestResult> {
  const pkgDir = join(cacheDir, version, 'package');
  const manifest: Manifest = JSON.parse(
    await readFile(join(pkgDir, 'skills-manifest.json'), 'utf8'),
  );
  if (manifest.schemaVersion > 1) {
    throw new Error(
      `skills manifest schemaVersion ${manifest.schemaVersion} is newer than this CLI supports — update @jesdi/skills-cli`,
    );
  }
  return { packageVersion: version, manifest, skillsDir: join(pkgDir, 'skills') };
}

async function newestCached(cacheDir: string, cause: unknown): Promise<FetchLatestResult> {
  let versions: string[] = [];
  try {
    versions = (await readdir(cacheDir)).sort();
  } catch {
    /* no cache dir yet */
  }
  const newest = versions.at(-1);
  if (!newest) {
    throw new Error(`could not reach the npm registry and no cached skills exist: ${cause}`);
  }
  return loadFromCache(cacheDir, newest);
}

export async function fetchLatest(opts: {
  cacheDir: string;
  registryUrl?: string;
  fetchImpl?: typeof fetch;
}): Promise<FetchLatestResult> {
  const f = opts.fetchImpl ?? fetch;
  const registry = opts.registryUrl ?? DEFAULT_REGISTRY;

  let meta: { version: string; dist: { tarball: string } };
  try {
    const res = await f(`${registry}/${PACKAGE}/latest`);
    if (!res.ok) throw new Error(`registry responded ${res.status}`);
    meta = (await res.json()) as typeof meta;
  } catch (err) {
    return newestCached(opts.cacheDir, err);
  }

  const versionDir = join(opts.cacheDir, meta.version);
  if (!existsSync(join(versionDir, 'package', 'skills-manifest.json'))) {
    const res = await f(meta.dist.tarball);
    if (!res.ok) throw new Error(`tarball download failed: ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    await rm(versionDir, { recursive: true, force: true });
    await mkdir(versionDir, { recursive: true });
    const tmpTar = join(versionDir, 'pkg.tgz');
    await writeFile(tmpTar, buf);
    await tar.extract({ file: tmpTar, cwd: versionDir });
    await rm(tmpTar);
  }
  return loadFromCache(opts.cacheDir, meta.version);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/registry.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/src/manifest.ts cli/src/registry.ts cli/tests/registry.test.ts
git commit -m "feat: registry client with cache and offline fallback"
```

---

### Task 9: State files (`cli/src/state.ts`)

**Files:**
- Create: `cli/src/state.ts`
- Test: `cli/tests/state.test.ts`

**Interfaces:**
- Consumes: `Ctx`, `globalStateFile`, `projectStateFile`, `AgentId`, `Scope` from `paths.js`.
- Produces:
  - `interface InstalledSkill { version: string; agents: AgentId[] }`
  - `interface GlobalState { schemaVersion: 1; skills: Record<string, InstalledSkill>; declined: Record<string, string> }` (declined: skill name → declined version)
  - `interface ProjectState { schemaVersion: 1; skills: Record<string, InstalledSkill> }`
  - `async function loadState(scope: Scope, ctx: Ctx): Promise<GlobalState | ProjectState>` — returns an empty default when the file is missing.
  - `async function saveState(scope: Scope, ctx: Ctx, state: GlobalState | ProjectState): Promise<void>` — creates parent dirs.
  - `async function loadGlobalState(ctx: Ctx): Promise<GlobalState>` / `async function saveGlobalState(ctx: Ctx, state: GlobalState): Promise<void>` — declines always live here, regardless of scope.

- [ ] **Step 1: Write the failing test**

`cli/tests/state.test.ts`:

```ts
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadGlobalState, loadState, saveGlobalState, saveState } from '../src/state.js';

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
  };
}

describe('state', () => {
  it('returns an empty default when no file exists', async () => {
    const ctx = await makeCtx();
    expect(await loadState('global', ctx)).toEqual({
      schemaVersion: 1,
      skills: {},
      declined: {},
    });
    expect(await loadState('local', ctx)).toEqual({ schemaVersion: 1, skills: {} });
  });

  it('round-trips global state including declines', async () => {
    const ctx = await makeCtx();
    const state = await loadGlobalState(ctx);
    state.skills['hello-world'] = { version: '0.1.0', agents: ['claude'] };
    state.declined['hello-world'] = '0.1.1';
    await saveGlobalState(ctx, state);
    expect(await loadGlobalState(ctx)).toEqual(state);
  });

  it('round-trips project state', async () => {
    const ctx = await makeCtx();
    const state = { schemaVersion: 1 as const, skills: { x: { version: '0.1.0', agents: ['opencode' as const] } } };
    await saveState('local', ctx, state);
    expect(await loadState('local', ctx)).toEqual(state);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/state.test.ts`
Expected: FAIL — cannot find module `../src/state.js`.

- [ ] **Step 3: Implement**

`cli/src/state.ts`:

```ts
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { globalStateFile, projectStateFile, type AgentId, type Ctx, type Scope } from './paths.js';

export interface InstalledSkill {
  version: string;
  agents: AgentId[];
}

export interface GlobalState {
  schemaVersion: 1;
  skills: Record<string, InstalledSkill>;
  declined: Record<string, string>;
}

export interface ProjectState {
  schemaVersion: 1;
  skills: Record<string, InstalledSkill>;
}

function stateFile(scope: Scope, ctx: Ctx): string {
  return scope === 'global' ? globalStateFile(ctx) : projectStateFile(ctx);
}

function emptyState(scope: Scope): GlobalState | ProjectState {
  return scope === 'global'
    ? { schemaVersion: 1, skills: {}, declined: {} }
    : { schemaVersion: 1, skills: {} };
}

export async function loadState(scope: Scope, ctx: Ctx): Promise<GlobalState | ProjectState> {
  try {
    return JSON.parse(await readFile(stateFile(scope, ctx), 'utf8'));
  } catch {
    return emptyState(scope);
  }
}

export async function saveState(
  scope: Scope,
  ctx: Ctx,
  state: GlobalState | ProjectState,
): Promise<void> {
  const file = stateFile(scope, ctx);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(state, null, 2) + '\n');
}

export async function loadGlobalState(ctx: Ctx): Promise<GlobalState> {
  return (await loadState('global', ctx)) as GlobalState;
}

export async function saveGlobalState(ctx: Ctx, state: GlobalState): Promise<void> {
  await saveState('global', ctx, state);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/state.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/src/state.ts cli/tests/state.test.ts
git commit -m "feat: cli state persistence"
```

---

### Task 10: Store and symlinks (`cli/src/store.ts`)

**Files:**
- Create: `cli/src/store.ts`
- Test: `cli/tests/store.test.ts`

**Interfaces:**
- Produces:
  - `class ForeignEntryError extends Error`
  - `async function installSkill(opts: { name: string; sourceDir: string; storeDir: string; agentDirs: string[] }): Promise<void>` — copies `sourceDir` → `<storeDir>/<name>` (replacing any previous copy), then creates absolute symlinks `<agentDir>/<name>` → store copy. Throws `ForeignEntryError` when an agent entry exists and is not a symlink into `storeDir`; replaces symlinks that ARE ours.
  - `async function uninstallSkill(opts: { name: string; storeDir: string; agentDirs: string[] }): Promise<void>` — removes our symlinks (same safety rule) and the store copy; missing entries are fine.

- [ ] **Step 1: Write the failing test**

`cli/tests/store.test.ts`:

```ts
import { lstat, mkdir, mkdtemp, readFile, readlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ForeignEntryError, installSkill, uninstallSkill } from '../src/store.js';

async function setup() {
  const root = await mkdtemp(join(tmpdir(), 'store-'));
  const sourceDir = join(root, 'src', 'hello-world');
  await mkdir(sourceDir, { recursive: true });
  await writeFile(join(sourceDir, 'SKILL.md'), 'v1');
  const storeDir = join(root, 'store');
  const agentDir = join(root, 'claude', 'skills');
  return { root, sourceDir, storeDir, agentDir };
}

describe('installSkill', () => {
  it('copies to the store and symlinks agent dirs', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    const link = join(agentDir, 'hello-world');
    expect((await lstat(link)).isSymbolicLink()).toBe(true);
    expect(await readlink(link)).toBe(join(storeDir, 'hello-world'));
    expect(await readFile(join(link, 'SKILL.md'), 'utf8')).toBe('v1');
  });

  it('is idempotent and refreshes content', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    await writeFile(join(sourceDir, 'SKILL.md'), 'v2');
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    expect(await readFile(join(agentDir, 'hello-world', 'SKILL.md'), 'utf8')).toBe('v2');
  });

  it('refuses to clobber a foreign entry in an agent dir', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await mkdir(join(agentDir, 'hello-world'), { recursive: true });
    await writeFile(join(agentDir, 'hello-world', 'SKILL.md'), 'user-made');
    await expect(
      installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] }),
    ).rejects.toThrow(ForeignEntryError);
  });
});

describe('uninstallSkill', () => {
  it('removes links and store copy, tolerating missing entries', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    await uninstallSkill({ name: 'hello-world', storeDir, agentDirs: [agentDir] });
    expect(existsSync(join(agentDir, 'hello-world'))).toBe(false);
    expect(existsSync(join(storeDir, 'hello-world'))).toBe(false);
    await uninstallSkill({ name: 'hello-world', storeDir, agentDirs: [agentDir] });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/store.test.ts`
Expected: FAIL — cannot find module `../src/store.js`.

- [ ] **Step 3: Implement**

`cli/src/store.ts`:

```ts
import { cp, lstat, mkdir, readlink, rm, symlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';

export class ForeignEntryError extends Error {}

async function removeOurEntry(linkPath: string, storeDir: string): Promise<void> {
  const stat = await lstat(linkPath).catch(() => null);
  if (!stat) return;
  if (!stat.isSymbolicLink()) {
    throw new ForeignEntryError(
      `${linkPath} exists and was not created by skills-cli — refusing to touch it`,
    );
  }
  const target = await readlink(linkPath);
  if (!resolve(target).startsWith(resolve(storeDir))) {
    throw new ForeignEntryError(
      `${linkPath} is a symlink to ${target}, outside the skills-cli store — refusing to touch it`,
    );
  }
  await rm(linkPath);
}

export async function installSkill(opts: {
  name: string;
  sourceDir: string;
  storeDir: string;
  agentDirs: string[];
}): Promise<void> {
  const storeCopy = join(opts.storeDir, opts.name);
  // Validate every agent entry BEFORE mutating anything.
  for (const dir of opts.agentDirs) {
    const stat = await lstat(join(dir, opts.name)).catch(() => null);
    if (stat && !stat.isSymbolicLink()) {
      throw new ForeignEntryError(
        `${join(dir, opts.name)} exists and was not created by skills-cli — refusing to touch it`,
      );
    }
  }
  await rm(storeCopy, { recursive: true, force: true });
  await mkdir(opts.storeDir, { recursive: true });
  await cp(opts.sourceDir, storeCopy, { recursive: true });
  for (const dir of opts.agentDirs) {
    await mkdir(dir, { recursive: true });
    const linkPath = join(dir, opts.name);
    await removeOurEntry(linkPath, opts.storeDir);
    await symlink(storeCopy, linkPath, 'dir');
  }
}

export async function uninstallSkill(opts: {
  name: string;
  storeDir: string;
  agentDirs: string[];
}): Promise<void> {
  for (const dir of opts.agentDirs) {
    await removeOurEntry(join(dir, opts.name), opts.storeDir);
  }
  await rm(join(opts.storeDir, opts.name), { recursive: true, force: true });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/store.test.ts`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/src/store.ts cli/tests/store.test.ts
git commit -m "feat: store copy and safe agent symlinking"
```

---

### Task 11: Core operations — install & uninstall (`cli/src/ops.ts`)

**Files:**
- Create: `cli/src/ops.ts`
- Test: `cli/tests/ops.install.test.ts`

**Interfaces:**
- Consumes: `fetchLatest` (registry.js), `installSkill`/`uninstallSkill` (store.js), state fns (state.js), path fns (paths.js).
- Produces:
  - `interface CliCtx extends Ctx { registryUrl?: string; fetchImpl?: typeof fetch }`
  - `async function opInstall(names: string[], agents: AgentId[], scope: Scope, ctx: CliCtx): Promise<{ name: string; version: string }[]>` — fetches latest, errors on unknown skill names, installs each into store + agent links, records `{version, agents}` in the scope's state file.
  - `async function opUninstall(name: string, scope: Scope, ctx: CliCtx): Promise<void>` — removes links for the agents recorded in state, removes store copy, deletes the state entry; errors if the skill is not in state.

- [ ] **Step 1: Write the failing test**

`cli/tests/ops.install.test.ts`:

```ts
import { mkdtemp, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { opInstall, opUninstall } from '../src/ops.js';

// Reuse the fixture builder from the registry test.
import { mkdir, writeFile } from 'node:fs/promises';
import * as tar from 'tar';

async function fixtureFetch(version = '1.0.0') {
  const root = await mkdtemp(join(tmpdir(), 'fix-'));
  const pkg = join(root, 'package');
  for (const name of ['hello-world', 'other']) {
    await mkdir(join(pkg, 'skills', name), { recursive: true });
    await writeFile(
      join(pkg, 'skills', name, 'SKILL.md'),
      `---\nname: ${name}\ndescription: ${name}\n---\nbody\n`,
    );
  }
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      skills: [
        { name: 'hello-world', version: '0.1.0', hash: 'sha256-a', description: 'hello-world' },
        { name: 'other', version: '0.2.0', hash: 'sha256-b', description: 'other' },
      ],
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);
  return (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(JSON.stringify({ version, dist: { tarball: 'https://r.test/p.tgz' } }));
    }
    return new Response(new Uint8Array(body));
  }) as typeof fetch;
}

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch(),
  };
}

describe('opInstall', () => {
  it('installs globally: store copy, agent links, state entry', async () => {
    const ctx = await makeCtx();
    const installed = await opInstall(['hello-world'], ['claude', 'opencode'], 'global', ctx);
    expect(installed).toEqual([{ name: 'hello-world', version: '0.1.0' }]);
    expect(existsSync(join(ctx.home, '.my-skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    const state = JSON.parse(
      await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8'),
    );
    expect(state.skills['hello-world']).toEqual({
      version: '0.1.0',
      agents: ['claude', 'opencode'],
    });
  });

  it('installs locally into the project and writes .my-skills.json', async () => {
    const ctx = await makeCtx();
    await opInstall(['other'], ['opencode'], 'local', ctx);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'other', 'SKILL.md'))).toBe(true);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills.other).toEqual({ version: '0.2.0', agents: ['opencode'] });
  });

  it('rejects unknown skill names', async () => {
    const ctx = await makeCtx();
    await expect(opInstall(['nope'], ['claude'], 'global', ctx)).rejects.toThrow(/unknown skill/i);
  });
});

describe('opUninstall', () => {
  it('removes links, store copy and state entry', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    await opUninstall('hello-world', 'global', ctx);
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world'))).toBe(false);
    expect(existsSync(join(ctx.home, '.my-skills', 'hello-world'))).toBe(false);
    const state = JSON.parse(
      await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8'),
    );
    expect(state.skills['hello-world']).toBeUndefined();
  });

  it('errors when the skill is not installed', async () => {
    const ctx = await makeCtx();
    await expect(opUninstall('hello-world', 'global', ctx)).rejects.toThrow(/not installed/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/ops.install.test.ts`
Expected: FAIL — cannot find module `../src/ops.js`.

- [ ] **Step 3: Implement**

`cli/src/ops.ts`:

```ts
import {
  agentSkillsDir,
  cacheDir,
  storeDir,
  type AgentId,
  type Ctx,
  type Scope,
} from './paths.js';
import { fetchLatest, type FetchLatestResult } from './registry.js';
import { loadState, saveState } from './state.js';
import { installSkill, uninstallSkill } from './store.js';
import { join } from 'node:path';

export interface CliCtx extends Ctx {
  registryUrl?: string;
  fetchImpl?: typeof fetch;
}

export async function fetchSkills(ctx: CliCtx): Promise<FetchLatestResult> {
  return fetchLatest({
    cacheDir: cacheDir(ctx),
    registryUrl: ctx.registryUrl,
    fetchImpl: ctx.fetchImpl,
  });
}

export async function opInstall(
  names: string[],
  agents: AgentId[],
  scope: Scope,
  ctx: CliCtx,
): Promise<{ name: string; version: string }[]> {
  const { manifest, skillsDir } = await fetchSkills(ctx);
  const available = new Map(manifest.skills.map((s) => [s.name, s]));
  for (const name of names) {
    if (!available.has(name)) throw new Error(`unknown skill: ${name}`);
  }
  const state = await loadState(scope, ctx);
  const installed: { name: string; version: string }[] = [];
  for (const name of names) {
    const skill = available.get(name)!;
    await installSkill({
      name,
      sourceDir: join(skillsDir, name),
      storeDir: storeDir(scope, ctx),
      agentDirs: agents.map((a) => agentSkillsDir(a, scope, ctx)),
    });
    state.skills[name] = { version: skill.version, agents };
    installed.push({ name, version: skill.version });
  }
  await saveState(scope, ctx, state);
  return installed;
}

export async function opUninstall(name: string, scope: Scope, ctx: CliCtx): Promise<void> {
  const state = await loadState(scope, ctx);
  const entry = state.skills[name];
  if (!entry) throw new Error(`skill not installed (${scope}): ${name}`);
  await uninstallSkill({
    name,
    storeDir: storeDir(scope, ctx),
    agentDirs: entry.agents.map((a) => agentSkillsDir(a, scope, ctx)),
  });
  delete state.skills[name];
  await saveState(scope, ctx, state);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/ops.install.test.ts`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/src/ops.ts cli/tests/ops.install.test.ts
git commit -m "feat: install and uninstall operations"
```

---

### Task 12: Core operations — updates with per-version declines

**Files:**
- Modify: `cli/src/ops.ts` (append)
- Test: `cli/tests/ops.update.test.ts`

**Interfaces:**
- Consumes: everything Task 11 defined; `loadGlobalState`/`saveGlobalState` from state.js.
- Produces (append to `ops.ts`):
  - `interface UpdateCandidate { name: string; from: string; to: string }`
  - `async function opCheckUpdates(scope: Scope, ctx: CliCtx): Promise<UpdateCandidate[]>` — installed skills whose manifest version differs from state, excluding those whose manifest version equals the recorded decline.
  - `async function opApplyUpdates(names: string[], scope: Scope, ctx: CliCtx): Promise<void>` — reinstalls each with its recorded agents and clears any decline for it.
  - `async function opDecline(name: string, version: string, ctx: CliCtx): Promise<void>` — records `declined[name] = version` in GLOBAL state (declines are global regardless of scope).

- [ ] **Step 1: Write the failing test**

`cli/tests/ops.update.test.ts` (reuses the fixture pattern from Task 11 — copy the `fixtureFetch`/`makeCtx` helpers, but make `fixtureFetch(version, skillVersion)` parameterize the `hello-world` manifest version so a "new release" can be simulated):

```ts
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { opApplyUpdates, opCheckUpdates, opDecline, opInstall } from '../src/ops.js';

async function fixtureFetch(pkgVersion: string, skillVersion: string) {
  const root = await mkdtemp(join(tmpdir(), 'fix-'));
  const pkg = join(root, 'package');
  await mkdir(join(pkg, 'skills', 'hello-world'), { recursive: true });
  await writeFile(
    join(pkg, 'skills', 'hello-world', 'SKILL.md'),
    `---\nname: hello-world\ndescription: demo\n---\ncontent ${skillVersion}\n`,
  );
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      skills: [
        { name: 'hello-world', version: skillVersion, hash: `sha256-${skillVersion}`, description: 'demo' },
      ],
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);
  return (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(
        JSON.stringify({ version: pkgVersion, dist: { tarball: 'https://r.test/p.tgz' } }),
      );
    }
    return new Response(new Uint8Array(body));
  }) as typeof fetch;
}

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch('1.0.0', '0.1.0'),
  };
}

describe('updates', () => {
  it('reports an update when the manifest moves ahead', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    expect(await opCheckUpdates('global', ctx)).toEqual([
      { name: 'hello-world', from: '0.1.0', to: '0.1.1' },
    ]);
  });

  it('applies updates: new content, new state version', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await opApplyUpdates(['hello-world'], 'global', ctx);
    const content = await readFile(
      join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'),
      'utf8',
    );
    expect(content).toContain('content 0.1.1');
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
  });

  it('suppresses a declined version but re-prompts on the next one', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await opDecline('hello-world', '0.1.1', ctx);
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
    ctx.fetchImpl = await fixtureFetch('1.0.2', '0.1.2');
    expect(await opCheckUpdates('global', ctx)).toEqual([
      { name: 'hello-world', from: '0.1.0', to: '0.1.2' },
    ]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/ops.update.test.ts`
Expected: FAIL — `opCheckUpdates` is not exported.

- [ ] **Step 3: Implement (append to `cli/src/ops.ts`)**

```ts
export interface UpdateCandidate {
  name: string;
  from: string;
  to: string;
}

export async function opCheckUpdates(scope: Scope, ctx: CliCtx): Promise<UpdateCandidate[]> {
  const { manifest } = await fetchSkills(ctx);
  const available = new Map(manifest.skills.map((s) => [s.name, s]));
  const state = await loadState(scope, ctx);
  const globalState = await loadGlobalState(ctx);
  const candidates: UpdateCandidate[] = [];
  for (const [name, installed] of Object.entries(state.skills)) {
    const latest = available.get(name);
    if (!latest || latest.version === installed.version) continue;
    if (globalState.declined[name] === latest.version) continue;
    candidates.push({ name, from: installed.version, to: latest.version });
  }
  return candidates;
}

export async function opApplyUpdates(names: string[], scope: Scope, ctx: CliCtx): Promise<void> {
  const state = await loadState(scope, ctx);
  const globalState = await loadGlobalState(ctx);
  for (const name of names) {
    const entry = state.skills[name];
    if (!entry) throw new Error(`skill not installed (${scope}): ${name}`);
    await opInstall([name], entry.agents, scope, ctx);
    if (globalState.declined[name]) {
      delete globalState.declined[name];
      await saveGlobalState(ctx, globalState);
    }
  }
}

export async function opDecline(name: string, version: string, ctx: CliCtx): Promise<void> {
  const globalState = await loadGlobalState(ctx);
  globalState.declined[name] = version;
  await saveGlobalState(ctx, globalState);
}
```

Add to the imports at the top of `ops.ts`: `loadGlobalState, saveGlobalState` from `./state.js`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/ops.update.test.ts`
Expected: 3 tests PASS. Also run the full suite: `npm test` → all green.

- [ ] **Step 5: Commit**

```bash
git add cli/src/ops.ts cli/tests/ops.update.test.ts
git commit -m "feat: update checks with per-version declines"
```

---

### Task 13: Core operations — sync & list

**Files:**
- Modify: `cli/src/ops.ts` (append)
- Test: `cli/tests/ops.sync.test.ts`

**Interfaces:**
- Produces (append to `ops.ts`):
  - `async function opSync(ctx: CliCtx): Promise<{ name: string; version: string }[]>` — reads the project's `.my-skills.json` and installs every listed skill (latest available version, recorded agents) locally; errors if the file lists nothing.
  - `interface ListedSkill { name: string; description: string; latest: string; installedGlobal?: string; installedLocal?: string }`
  - `async function opList(ctx: CliCtx): Promise<ListedSkill[]>` — all manifest skills merged with both state files.

- [ ] **Step 1: Write the failing test**

`cli/tests/ops.sync.test.ts` (reuse `fixtureFetch`/`makeCtx` helpers from Task 11's test verbatim — two skills `hello-world`@0.1.0 and `other`@0.2.0):

```ts
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { opInstall, opList, opSync } from '../src/ops.js';

async function fixtureFetch(version = '1.0.0') {
  const root = await mkdtemp(join(tmpdir(), 'fix-'));
  const pkg = join(root, 'package');
  for (const name of ['hello-world', 'other']) {
    await mkdir(join(pkg, 'skills', name), { recursive: true });
    await writeFile(
      join(pkg, 'skills', name, 'SKILL.md'),
      `---\nname: ${name}\ndescription: ${name}\n---\nbody\n`,
    );
  }
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      skills: [
        { name: 'hello-world', version: '0.1.0', hash: 'sha256-a', description: 'hello-world' },
        { name: 'other', version: '0.2.0', hash: 'sha256-b', description: 'other' },
      ],
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);
  return (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(JSON.stringify({ version, dist: { tarball: 'https://r.test/p.tgz' } }));
    }
    return new Response(new Uint8Array(body));
  }) as typeof fetch;
}

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch(),
  };
}

describe('opSync', () => {
  it('materializes a committed project manifest on a fresh machine', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({
        schemaVersion: 1,
        skills: { 'hello-world': { version: '0.1.0', agents: ['claude', 'opencode'] } },
      }),
    );
    const result = await opSync(ctx);
    expect(result).toEqual([{ name: 'hello-world', version: '0.1.0' }]);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
  });

  it('errors when there is nothing to sync', async () => {
    const ctx = await makeCtx();
    await expect(opSync(ctx)).rejects.toThrow(/no .my-skills.json/i);
  });
});

describe('opList', () => {
  it('merges manifest with installed state', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    const list = await opList(ctx);
    expect(list).toEqual([
      {
        name: 'hello-world',
        description: 'hello-world',
        latest: '0.1.0',
        installedGlobal: '0.1.0',
        installedLocal: undefined,
      },
      {
        name: 'other',
        description: 'other',
        latest: '0.2.0',
        installedGlobal: undefined,
        installedLocal: undefined,
      },
    ]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/ops.sync.test.ts`
Expected: FAIL — `opSync` is not exported.

- [ ] **Step 3: Implement (append to `cli/src/ops.ts`)**

```ts
export async function opSync(ctx: CliCtx): Promise<{ name: string; version: string }[]> {
  const state = await loadState('local', ctx);
  const entries = Object.entries(state.skills);
  if (entries.length === 0) {
    throw new Error('no .my-skills.json with skills found in this project — nothing to sync');
  }
  const results: { name: string; version: string }[] = [];
  for (const [name, entry] of entries) {
    const [installed] = await opInstall([name], entry.agents, 'local', ctx);
    results.push(installed);
  }
  return results;
}

export interface ListedSkill {
  name: string;
  description: string;
  latest: string;
  installedGlobal?: string;
  installedLocal?: string;
}

export async function opList(ctx: CliCtx): Promise<ListedSkill[]> {
  const { manifest } = await fetchSkills(ctx);
  const globalState = await loadState('global', ctx);
  const localState = await loadState('local', ctx);
  return manifest.skills.map((s) => ({
    name: s.name,
    description: s.description,
    latest: s.version,
    installedGlobal: globalState.skills[s.name]?.version,
    installedLocal: localState.skills[s.name]?.version,
  }));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/ops.sync.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/src/ops.ts cli/tests/ops.sync.test.ts
git commit -m "feat: sync and list operations"
```

---

### Task 14: Commander subcommands

**Files:**
- Modify: `cli/src/program.ts`
- Test: `cli/tests/cli.test.ts`

**Interfaces:**
- Consumes: all `op*` functions from `ops.js`.
- Produces: `buildProgram(ctx?: CliCtx)` now accepts an optional ctx (defaults to `{ home: os.homedir(), project: process.cwd() }`) and registers:
  - `install <skills...> [--agent <list>] [--global]` (agent list comma-separated, default `claude`; default scope local)
  - `update [skill] [--all]` — no args: print candidates; `--all` or a name: apply
  - `sync`, `list`, `uninstall <skill> [--global]`
  - Default action (no subcommand) runs the interactive wizard (Task 15 — until then it prints "run with a subcommand").

- [ ] **Step 1: Write the failing test**

`cli/tests/cli.test.ts` (drives the real command wiring against the fixture registry — copy the `fixtureFetch`/`makeCtx` helpers from Task 13's test verbatim):

```ts
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildProgram } from '../src/program.js';
// ... paste fixtureFetch + makeCtx helpers from cli/tests/ops.sync.test.ts here ...

describe('cli wiring', () => {
  it('install subcommand installs with flags', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    await program.parseAsync(
      ['install', 'hello-world', '--agent', 'claude,opencode', '--global'],
      { from: 'user' },
    );
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
  });

  it('uninstall subcommand removes a global install', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    await program.parseAsync(['install', 'hello-world', '--global'], { from: 'user' });
    await buildProgram(ctx).parseAsync(['uninstall', 'hello-world', '--global'], { from: 'user' });
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world'))).toBe(false);
  });

  it('rejects an unknown agent id', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    program.exitOverride();
    await expect(
      program.parseAsync(['install', 'hello-world', '--agent', 'cursor'], { from: 'user' }),
    ).rejects.toThrow(/unknown agent/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run cli/tests/cli.test.ts`
Expected: FAIL — commander reports unknown command `install` (or buildProgram signature mismatch).

- [ ] **Step 3: Implement**

Replace `cli/src/program.ts` with:

```ts
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { Command } from 'commander';
import {
  opApplyUpdates,
  opCheckUpdates,
  opInstall,
  opList,
  opSync,
  opUninstall,
  type CliCtx,
} from './ops.js';
import { AGENTS, type AgentId, type Scope } from './paths.js';

const pkg = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
);

function parseAgents(value: string): AgentId[] {
  const agents = value.split(',').map((a) => a.trim());
  for (const a of agents) {
    if (!(a in AGENTS)) throw new Error(`unknown agent: ${a} (expected claude|opencode)`);
  }
  return agents as AgentId[];
}

function scopeOf(opts: { global?: boolean }): Scope {
  return opts.global ? 'global' : 'local';
}

export function buildProgram(ctx?: CliCtx): Command {
  const cliCtx: CliCtx = ctx ?? { home: homedir(), project: process.cwd() };
  const program = new Command('skills-cli');
  program
    .description("Install jesdi's agent skills into Claude Code and OpenCode")
    .version(pkg.version);

  program
    .command('install')
    .argument('<skills...>', 'skill names to install')
    .option('--agent <list>', 'comma-separated agents: claude,opencode', 'claude')
    .option('--global', 'install for the whole machine instead of this project')
    .action(async (skills: string[], opts: { agent: string; global?: boolean }) => {
      const installed = await opInstall(skills, parseAgents(opts.agent), scopeOf(opts), cliCtx);
      for (const s of installed) console.log(`installed ${s.name}@${s.version}`);
    });

  program
    .command('update')
    .argument('[skill]', 'update a single skill')
    .option('--all', 'apply every available update')
    .option('--global', 'operate on the global scope')
    .action(async (skill: string | undefined, opts: { all?: boolean; global?: boolean }) => {
      const scope = scopeOf(opts);
      const candidates = await opCheckUpdates(scope, cliCtx);
      if (candidates.length === 0) {
        console.log('everything is up to date');
        return;
      }
      const names = skill ? [skill] : opts.all ? candidates.map((c) => c.name) : null;
      if (!names) {
        for (const c of candidates) console.log(`${c.name}: ${c.from} -> ${c.to}`);
        console.log('run `skills-cli update --all` or `skills-cli update <skill>` to apply');
        return;
      }
      await opApplyUpdates(names, scope, cliCtx);
      for (const n of names) console.log(`updated ${n}`);
    });

  program
    .command('sync')
    .description('install everything listed in this project\'s .my-skills.json')
    .action(async () => {
      const results = await opSync(cliCtx);
      for (const s of results) console.log(`synced ${s.name}@${s.version}`);
    });

  program
    .command('list')
    .description('list available skills and where they are installed')
    .action(async () => {
      for (const s of await opList(cliCtx)) {
        const marks = [
          s.installedGlobal ? `global@${s.installedGlobal}` : null,
          s.installedLocal ? `local@${s.installedLocal}` : null,
        ]
          .filter(Boolean)
          .join(', ');
        console.log(`${s.name}@${s.latest}${marks ? ` [${marks}]` : ''} — ${s.description}`);
      }
    });

  program
    .command('uninstall')
    .argument('<skill>')
    .option('--global', 'uninstall from the global scope')
    .action(async (skill: string, opts: { global?: boolean }) => {
      await opUninstall(skill, scopeOf(opts), cliCtx);
      console.log(`uninstalled ${skill}`);
    });

  program.action(() => {
    console.log('interactive wizard coming in the next task — use a subcommand for now');
  });

  return program;
}
```

Update `cli/tests/program.test.ts` if needed (the `buildProgram()` no-arg call must still work).

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run cli/tests/cli.test.ts cli/tests/program.test.ts`
Expected: all PASS. Full suite `npm test` green.

- [ ] **Step 5: Commit**

```bash
git add cli/src/program.ts cli/tests/cli.test.ts
git commit -m "feat: commander subcommands for all operations"
```

---

### Task 15: Interactive wizard

**Files:**
- Create: `cli/src/wizard.ts`
- Modify: `cli/src/program.ts` (default action)

**Interfaces:**
- Consumes: `opCheckUpdates`, `opApplyUpdates`, `opDecline`, `opInstall`, `opList`, `CliCtx`; `@clack/prompts`.
- Produces: `async function runWizard(ctx: CliCtx): Promise<void>`.

- [ ] **Step 1: Implement the wizard**

`cli/src/wizard.ts`:

```ts
import * as p from '@clack/prompts';
import {
  opApplyUpdates,
  opCheckUpdates,
  opDecline,
  opInstall,
  opList,
  type CliCtx,
} from './ops.js';
import { AGENTS, type AgentId, type Scope } from './paths.js';

export async function runWizard(ctx: CliCtx): Promise<void> {
  p.intro('@jesdi/skills');

  // 1. Update prompts first (DESIGN.md: check on every run, remember declines per version).
  for (const scope of ['global', 'local'] as Scope[]) {
    const candidates = await opCheckUpdates(scope, ctx);
    for (const c of candidates) {
      const answer = await p.confirm({
        message: `Update ${c.name} (${scope}) ${c.from} -> ${c.to}?`,
      });
      if (p.isCancel(answer)) return cancel();
      if (answer) await opApplyUpdates([c.name], scope, ctx);
      else await opDecline(c.name, c.to, ctx);
    }
  }

  // 2. Pick skills.
  const available = await opList(ctx);
  const skills = await p.multiselect({
    message: 'Which skills do you want to install?',
    options: available.map((s) => ({
      value: s.name,
      label: s.name,
      hint: s.description.slice(0, 80),
    })),
    required: true,
  });
  if (p.isCancel(skills)) return cancel();

  // 3. Pick agents.
  const agents = await p.multiselect({
    message: 'Install for which agents?',
    options: (Object.entries(AGENTS) as [AgentId, { label: string }][]).map(([id, a]) => ({
      value: id,
      label: a.label,
    })),
    initialValues: ['claude' as AgentId],
    required: true,
  });
  if (p.isCancel(agents)) return cancel();

  // 4. Pick scope.
  const scope = await p.select({
    message: 'Install where?',
    options: [
      { value: 'local' as Scope, label: `This project (${ctx.project})` },
      { value: 'global' as Scope, label: 'Globally (whole machine)' },
    ],
  });
  if (p.isCancel(scope)) return cancel();

  const spinner = p.spinner();
  spinner.start('Installing…');
  const installed = await opInstall(skills as string[], agents as AgentId[], scope, ctx);
  spinner.stop(`Installed ${installed.map((s) => `${s.name}@${s.version}`).join(', ')}`);
  p.outro('Done — your agents will pick the skills up on next launch.');
}

function cancel(): void {
  p.cancel('Cancelled — nothing was changed.');
}
```

- [ ] **Step 2: Wire it as the default action**

In `cli/src/program.ts`, replace the placeholder default action with:

```ts
program.action(async () => {
  const { runWizard } = await import('./wizard.js');
  await runWizard(cliCtx);
});
```

- [ ] **Step 3: Regression-check the suite**

Run: `npm test`
Expected: all tests PASS (wizard is not unit-tested; clack TTY flows are covered by manual verification).

- [ ] **Step 4: Manual verification (requires a TTY)**

```bash
HOME_SANDBOX=$(mktemp -d)
cd "$(mktemp -d)"
HOME="$HOME_SANDBOX" npx tsx /path/to/repo/cli/src/index.ts
```

Note: the wizard fetches `@jesdi/skills` from npm; before the first publish exists, point it at the fixture or skip to after Task 16's first publish. Walk through: select `hello-world` → both agents → "This project". Then verify:

```bash
ls -la .claude/skills .agents/skills .my-skills && cat .my-skills.json
```

Expected: both agent dirs contain `hello-world` symlinks into `.my-skills/hello-world`, and `.my-skills.json` records `{"version": ..., "agents": ["claude", "opencode"]}`.

- [ ] **Step 5: Commit**

```bash
git add cli/src/wizard.ts cli/src/program.ts
git commit -m "feat: interactive install wizard"
```

---

### Task 16: CLI build, publish workflow, README

**Files:**
- Create: `.github/workflows/publish-cli.yml`, `README.md`

**Interfaces:**
- Produces: `npm run build -w cli` emits `cli/dist/index.js` (the published bin); CI publishes `@jesdi/skills-cli` on pushes to `main` touching `cli/**` when `cli/package.json`'s version isn't on npm yet (version bumps stay manual — the CLI releases rarely, per DESIGN.md).

- [ ] **Step 1: Verify the build works**

Run: `npm run build -w cli && node cli/dist/index.js --version`
Expected: prints `0.1.0`.

- [ ] **Step 2: Write the CLI publish workflow**

`.github/workflows/publish-cli.yml`:

```yaml
name: publish-cli

on:
  push:
    branches: [main]
    paths: ['cli/**']

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          registry-url: 'https://registry.npmjs.org'
      - run: npm ci
      - run: npm test
      - run: npm run build -w cli
      - name: Publish if this version is new
        working-directory: cli
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
        run: |
          VERSION=$(node -p "require('./package.json').version")
          if npm view "@jesdi/skills-cli@$VERSION" version >/dev/null 2>&1; then
            echo "@jesdi/skills-cli@$VERSION already published; skipping."
          else
            npm publish --provenance --access public
          fi
```

- [ ] **Step 3: Write the README**

`README.md`:

```markdown
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
```

- [ ] **Step 4: Full check and commit**

Run: `npm test && npm run build -w cli`
Expected: all green.

```bash
git add .github/workflows/publish-cli.yml README.md
git commit -m "ci: cli publish workflow and readme"
```

---

## Post-plan manual steps (human, not automatable here)

1. Create the GitHub repo (e.g. `jesdi/skills`), add remote, push `main`.
2. Add the `NPM_TOKEN` secret (npm automation token with publish rights on `@jesdi`).
3. Repo Settings → Actions → Workflow permissions → "Read and write permissions" (the skills workflow pushes release commits).
4. First `@jesdi/skills` publish happens automatically on the first push that touches `skills/**`; then re-run Task 15's manual wizard verification against the real registry.

## Self-Review Notes

- Spec coverage: two packages ✔ (Tasks 1, 6, 16), runtime fetch + cache + schemaVersion guard ✔ (Task 8), CI per-skill patch-bump ✔ (Tasks 3–5), store + symlink + foreign-entry safety ✔ (Task 10), state files + committed project manifest ✔ (Tasks 9, 11), per-version declines ✔ (Task 12), sync/list ✔ (Task 13), full command surface ✔ (Task 14), wizard with update-prompts-first ✔ (Task 15).
- Known deviation from DESIGN.md (agreed refinement): manifest committed to git; CI commits release bumps with `[skip ci]`.
- `sync` installs the latest available version of each listed skill (registry only serves the latest `@jesdi/skills`); pinned-version restore is out of scope for v1.
