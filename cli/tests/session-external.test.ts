import { chmod, cp, mkdir, mkdtemp, readFile, readlink, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it, vi } from 'vitest';
import { buildProgram } from '../src/program.js';
import { opSessionUpdate } from '../src/session.js';
import { parseCatalogue } from '../src/external-catalogue.js';
import type { CliCtx } from '../src/ops.js';

const REF = 'a'.repeat(40);
const NEXT = 'b'.repeat(40);
const SOURCE = 'author/skills';
const races = vi.hoisted(() => ({
  afterCopy: undefined as undefined | ((destination: string) => Promise<void>),
  afterLink: undefined as undefined | ((destination: string) => Promise<void>),
  beforeMove: undefined as undefined | ((source: string, destination: string) => Promise<void>),
  afterMove: undefined as undefined | ((source: string, destination: string) => Promise<void>),
}));
vi.mock('../src/atomic-rename.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/atomic-rename.js')>();
  return { renameNoReplace: async (source: string, destination: string) => {
    await races.beforeMove?.(source, destination);
    await actual.renameNoReplace(source, destination);
    await races.afterMove?.(source, destination);
  } };
});
vi.mock('node:fs/promises', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs/promises')>();
  return { ...actual,
    cp: async (...args: Parameters<typeof actual.cp>) => {
      await actual.cp(...args);
      await races.afterCopy?.(String(args[1]));
    },
    symlink: async (...args: Parameters<typeof actual.symlink>) => {
      await actual.symlink(...args);
      await races.afterLink?.(String(args[1]));
    },
  };
});
const json = async (file: string) => JSON.parse(await readFile(file, 'utf8'));
const save = async (file: string, value: unknown) => {
  await mkdir(join(file, '..'), { recursive: true });
  await writeFile(file, JSON.stringify(value));
};

async function fixture(names = ['external-a', 'external-b']) {
  const home = await mkdtemp(join(tmpdir(), 'external-home-'));
  const project = await mkdtemp(join(tmpdir(), 'external-project-'));
  await mkdir(join(home, '.claude'));
  const catalogue = {
    schemaVersion: 2,
    pins: { [SOURCE]: { ref: REF, version: '1.2.3' } },
    forks: { skills: ['own-fork'], doNotInstallUpstream: true },
    skills: names.map((name) => ({ name, source: SOURCE, path: `skills/${name}` })),
  };
  const bodies = new Map<string, Uint8Array>();
  for (const ref of [REF, NEXT]) {
    const root = await mkdtemp(join(tmpdir(), 'external-source-'));
    for (const name of names) {
      for (const path of ['skills', 'relocated']) {
        const dir = join(root, 'repo', path, name);
        await mkdir(dir, { recursive: true });
        await writeFile(join(dir, 'SKILL.md'), `---\nname: ${name}\n---\n${ref}\n`);
        await writeFile(join(dir, 'rule.txt'), `resource at ${ref}`);
        await writeFile(join(dir, 'helper.sh'), '#!/bin/sh\nexit 0\n');
        await chmod(join(dir, 'helper.sh'), 0o755);
      }
    }
    const file = join(root, 'source.tgz');
    await tar.create({ gzip: true, file, cwd: root }, ['repo']);
    bodies.set(ref, new Uint8Array(await readFile(file)));
  }
  const calls: string[] = [];
  let offline = false;
  let archiveFailure = false;
  const fetchImpl = (async (url: RequestInfo | URL) => {
    const u = String(url);
    calls.push(u);
    if (offline) throw new Error('offline');
    if (u.endsWith('external-skills.json')) return new Response(JSON.stringify(catalogue));
    if (u.includes('codeload.github.com')) {
      if (archiveFailure) return new Response('unavailable', { status: 503 });
      return new Response(new Uint8Array(bodies.get(u.split('/').at(-1)!)!));
    }
    // Own skills are current, independent of the external catalogue.
    if (u.endsWith('/latest')) return new Response(JSON.stringify({ version: '1.0.0', dist: { tarball: 'https://registry.test/own.tgz' } }));
    throw new Error(`unexpected network request: ${u}`);
  }) as typeof fetch;
  const ctx: CliCtx = { home, project, fetchImpl };
  const ownPackage = join(home, '.cache', 'my-skills', '1.0.0', 'package');
  await save(join(ownPackage, 'skills-manifest.json'), { schemaVersion: 1, skills: [] });
  const stateFile = join(home, '.config', 'my-skills', 'external.json');
  const canonical = (name = names[0]) => join(home, '.agents', 'skills', name);
  const claude = (name = names[0]) => join(home, '.claude', 'skills', name);
  const run = () => buildProgram(ctx).parseAsync(['node', 'skills-cli', 'session-update']);
  const expire = async () => {
    const file = join(home, '.cache', 'my-skills-external', 'catalogue.json');
    const cached = await json(file);
    cached.refreshedAt = 0;
    await save(file, cached);
  };
  return { ctx, catalogue, calls, canonical, claude, stateFile, bodies, run, expire,
    offline: () => { offline = true; }, online: () => { offline = false; },
    failArchives: () => { archiveFailure = true; }, allowArchives: () => { archiveFailure = false; } };
}

describe('session-update external skills', () => {
  it('installs every listed skill at its declared source/ref/version, with Claude and Codex paths', async () => {
    const f = await fixture();
    await f.run();
    for (const name of ['external-a', 'external-b']) {
      expect(await readFile(join(f.canonical(name), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
      expect(await readlink(f.claude(name))).toBe(f.canonical(name));
      expect((await json(f.stateFile))[name]).toMatchObject({ source: SOURCE, ref: REF, version: '1.2.3', path: `skills/${name}` });
    }
    expect(f.calls.filter((u) => u.includes('codeload'))).toHaveLength(1);
    expect(existsSync(f.canonical('own-fork'))).toBe(false);
  });

  it('repairs missing skills, resource drift, missing links and metadata within the same day without network calls', async () => {
    const f = await fixture();
    await f.run();
    const initialCalls = [...f.calls];
    await rm(f.canonical('external-a'), { recursive: true });
    await writeFile(join(f.canonical('external-b'), 'rule.txt'), 'locally changed');
    await rm(f.claude('external-b'));
    const state = await json(f.stateFile);
    state['external-b'].ref = NEXT;
    state['external-b'].version = 'wrong-version';
    await save(f.stateFile, state);
    await f.run();
    expect(f.calls).toEqual(initialCalls);
    expect(await readFile(join(f.canonical('external-a'), 'SKILL.md'), 'utf8')).toContain(REF);
    expect(await readFile(join(f.canonical('external-b'), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
    expect(await readlink(f.claude('external-b'))).toBe(f.canonical('external-b'));
    expect((await json(f.stateFile))['external-b']).toMatchObject({ ref: REF, version: '1.2.3' });
  });

  it('does no writes to installed files when the desired content and metadata already match', async () => {
    const f = await fixture();
    await f.run();
    const file = join(f.canonical(), 'SKILL.md');
    const before = await stat(file);
    await f.run();
    expect((await stat(file)).mtimeMs).toBe(before.mtimeMs);
    expect(f.calls.filter((u) => u.endsWith('/latest'))).toHaveLength(1);
  });

  it('restores executable resource permissions from cache in the same day', async () => {
    const f = await fixture();
    await f.run();
    const helper = join(f.canonical(), 'helper.sh');
    expect((await stat(helper)).mode & 0o777).toBe(0o755);
    const requests = [...f.calls];
    await chmod(helper, 0o644);
    f.offline();
    await f.run();
    expect((await stat(helper)).mode & 0o777).toBe(0o755);
    expect(f.calls).toEqual(requests);
  });

  it('validates managed provenance with the same identity rules as the catalogue', async () => {
    const f = await fixture();
    await f.run();
    const state = await json(f.stateFile);
    state['external-a'].path = '../invalid';
    await save(f.stateFile, state);
    await expect(f.run()).rejects.toThrow('unsafe external path');
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
  });

  it.each([undefined, [], ['invalid'], ['1:2:3', '4:5:6', '7:8:9'], [123]])(
    'refuses invalid canonical ownership %j without changing the install', async (canonicalIds) => {
      const f = await fixture();
      await f.run();
      const state = await json(f.stateFile);
      state['external-a'].canonicalIds = canonicalIds;
      await save(f.stateFile, state);
      await expect(f.run()).rejects.toThrow('invalid managed external ownership');
      expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
    },
  );

  it.each([{ id: 'foreign', hash: 'a'.repeat(64) }, { id: '1:2:3', hash: 'invalid' }])(
    'refuses invalid legacy directory recovery metadata %j', async (claudeDirectory) => {
      const f = await fixture();
      await f.run();
      const state = await json(f.stateFile);
      state['external-a'].claudeDirectory = claudeDirectory;
      await save(f.stateFile, state);
      await expect(f.run()).rejects.toThrow(/invalid (Claude directory identity|external content hash)/);
      expect(await readlink(f.claude())).toBe(f.canonical());
    },
  );

  it('installs the full catalogue when Claude is not configured', async () => {
    const f = await fixture();
    await rm(join(f.ctx.home, '.claude'), { recursive: true });
    await f.run();
    expect(existsSync(join(f.canonical(), 'SKILL.md'))).toBe(true);
    expect(existsSync(join(f.ctx.home, '.claude'))).toBe(false);
  });

  it('refreshes own skills when the daily stamp is invalid', async () => {
    const f = await fixture();
    await f.run();
    await writeFile(join(f.ctx.home, '.my-skills', '.session-update.stamp'), 'invalid');
    await f.run();
    expect(f.calls.filter((url) => url.endsWith('/latest'))).toHaveLength(2);
    expect(Number(await readFile(join(f.ctx.home, '.my-skills', '.session-update.stamp'), 'utf8'))).toBeGreaterThan(0);
  });

  it('follows a new ref even when the declared version label stays the same', async () => {
    const f = await fixture();
    await f.run();
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.expire();
    await f.run();
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${NEXT}`);
    expect((await json(f.stateFile))['external-a']).toMatchObject({ ref: NEXT, version: '1.2.3' });
  });

  it('updates a changed version label at the same ref without rewriting the files', async () => {
    const f = await fixture();
    await f.run();
    const file = join(f.canonical(), 'SKILL.md');
    const before = await stat(file);
    f.catalogue.pins[SOURCE].version = '1.2.4';
    await f.expire();
    await f.run();
    expect((await json(f.stateFile))['external-a'].version).toBe('1.2.4');
    expect((await stat(file)).mtimeMs).toBe(before.mtimeMs);
    expect(f.calls.filter((u) => u.includes('codeload'))).toHaveLength(1);
  });

  it('tracks a changed upstream path at the same source and ref', async () => {
    const f = await fixture();
    await f.run();
    f.catalogue.skills[0].path = 'relocated/external-a';
    await f.expire();
    await f.run();
    expect((await json(f.stateFile))['external-a'].path).toBe('relocated/external-a');
    expect(f.calls.filter((u) => u.includes('codeload'))).toHaveLength(2);
  });

  it('refuses to overwrite an installed own skill even if the external catalogue names it', async () => {
    const f = await fixture();
    await save(join(f.ctx.home, '.config', 'my-skills', 'state.json'), {
      schemaVersion: 1, declined: {}, skills: { 'external-a': { version: '1.0.0', agents: ['opencode'], pinned: true } },
    });
    await expect(f.run()).rejects.toThrow('own skill');
    expect(existsSync(f.canonical())).toBe(false);
    expect(existsSync(f.canonical('external-b'))).toBe(true);
  });

  it('rejects archive links without changing the working install', async () => {
    const f = await fixture();
    await f.run();
    const root = await mkdtemp(join(tmpdir(), 'unsafe-source-'));
    const dir = join(root, 'repo', 'skills', 'external-a');
    await mkdir(dir, { recursive: true });
    await writeFile(join(dir, 'SKILL.md'), 'new skill');
    await symlink('/etc/passwd', join(dir, 'rule.txt'));
    const archive = join(root, 'unsafe.tgz');
    await tar.create({gzip: true, file: archive, cwd: root}, ['repo']);
    f.bodies.set(NEXT, new Uint8Array(await readFile(archive)));
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.expire();
    await expect(f.run()).rejects.toThrow('unsafe external archive');
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
  });

  it('adopts matching skills.sh installs while preserving its lock and unrelated skills', async () => {
    const f = await fixture();
    await mkdir(f.canonical(), { recursive: true });
    await writeFile(join(f.canonical(), 'SKILL.md'), 'old version');
    await mkdir(f.claude(), { recursive: true });
    await writeFile(join(f.claude(), 'SKILL.md'), 'old version');
    const lockFile = join(f.ctx.home, '.agents', '.skill-lock.json');
    const lock = { version: 3, skills: { 'external-a': { source: SOURCE, skillFolderHash: 'upstream-tree' }, unrelated: { source: 'another/source' } } };
    await save(lockFile, lock);
    await mkdir(f.canonical('unrelated'));
    await writeFile(join(f.canonical('unrelated'), 'SKILL.md'), 'keep this');
    await f.run();
    expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toContain(REF);
    expect(await readlink(f.claude())).toBe(f.canonical());
    expect(await json(lockFile)).toEqual(lock);
    expect(await readFile(join(f.canonical('unrelated'), 'SKILL.md'), 'utf8')).toBe('keep this');
  });

  it('refuses an unrelated Claude entry before changing the recognized canonical copy', async () => {
    const f = await fixture();
    await mkdir(f.canonical(), { recursive: true });
    await writeFile(join(f.canonical(), 'SKILL.md'), 'keep canonical');
    await save(join(f.ctx.home, '.agents', '.skill-lock.json'), { skills: { 'external-a': { source: SOURCE } } });
    const elsewhere = join(f.ctx.home, 'custom');
    await mkdir(elsewhere);
    await mkdir(join(f.claude(), '..'), { recursive: true });
    await symlink(elsewhere, f.claude());
    await expect(f.run()).rejects.toThrow('unrelated install');
    expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toBe('keep canonical');
    expect(await readlink(f.claude())).toBe(elsewhere);
    expect((await json(f.stateFile))['external-a']).toBeUndefined();
    expect(existsSync(f.canonical('external-b'))).toBe(true);
  });

  it('refuses an unmanaged canonical directory and leaves its content intact', async () => {
    const f = await fixture(['external-a']);
    await mkdir(f.canonical(), { recursive: true });
    await writeFile(join(f.canonical(), 'SKILL.md'), 'custom skill');
    await expect(f.run()).rejects.toThrow('unrelated install');
    expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toBe('custom skill');
    expect(existsSync(f.stateFile)).toBe(false);
  });

  it('preserves a skill replaced from another source despite stale managed ownership', async () => {
    const f = await fixture();
    await f.run();
    await save(join(f.ctx.home, '.agents', '.skill-lock.json'), { skills: { 'external-a': { source: 'another/repository' } } });
    await writeFile(join(f.canonical(), 'SKILL.md'), 'replacement by another author');
    await expect(f.run()).rejects.toThrow('conflicting current skills.sh provenance');
    expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toBe('replacement by another author');
  });

  it('preserves an unrelated install made while its new upstream archive is downloading', async () => {
    const f = await fixture();
    await f.run();
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.expire();
    const original = f.ctx.fetchImpl!;
    let entered!: () => void;
    let unblock!: () => void;
    const started = new Promise<void>((r) => { entered = r; });
    const download = new Promise<void>((r) => { unblock = r; });
    f.ctx.fetchImpl = (async (url, opts) => {
      if (String(url).includes('codeload')) { entered(); await download; }
      return original(url, opts);
    }) as typeof fetch;
    const pending = f.run();
    await started;
    await save(join(f.ctx.home, '.agents', '.skill-lock.json'), { skills: { 'external-a': { source: 'another/repository' } } });
    await writeFile(join(f.canonical(), 'SKILL.md'), 'concurrent foreign replacement');
    unblock();
    await expect(pending).rejects.toThrow('conflicting current skills.sh provenance');
    expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toBe('concurrent foreign replacement');
    expect((await json(f.stateFile))['external-a'].ref).toBe(REF);
  });

  it('does not adopt an unattributed Claude directory even when its files match upstream', async () => {
    const f = await fixture();
    await f.run();
    await rm(f.claude());
    await cp(f.canonical(), f.claude(), { recursive: true });
    await rm(f.canonical(), { recursive: true });
    await rm(f.stateFile);
    await expect(f.run()).rejects.toThrow('unrelated install');
    expect((await stat(f.claude())).isDirectory()).toBe(true);
    expect(await readFile(join(f.claude(), 'SKILL.md'), 'utf8')).toContain(REF);
    expect(existsSync(f.canonical())).toBe(false);
  });

  it.each(['directory', 'symlink'])('preserves a concurrent unrelated canonical %s on this and the next run', async (kind) => {
    const f = await fixture(['external-a']);
    const elsewhere = join(f.ctx.home, 'foreign');
    await mkdir(elsewhere);
    await writeFile(join(elsewhere, 'SKILL.md'), 'foreign skill');
    races.afterCopy = async (destination) => {
      if (!destination.startsWith(join(f.ctx.home, '.agents', 'skills'))) return;
      races.afterCopy = undefined;
      if (kind === 'directory') await cp(elsewhere, f.canonical(), { recursive: true });
      else await symlink(elsewhere, f.canonical());
    };
    try {
      await expect(f.run()).rejects.toThrow('changed during installation');
      expect((await json(f.stateFile))['external-a']).toBeUndefined();
      await expect(f.run()).rejects.toThrow('unrelated install');
      expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toBe('foreign skill');
      if (kind === 'symlink') expect(await readlink(f.canonical())).toBe(elsewhere);
    } finally { races.afterCopy = undefined; }
  });

  it('preserves a Claude directory created while its replacement link is being staged', async () => {
    const f = await fixture(['external-a']);
    await f.run();
    await rm(f.claude());
    races.afterLink = async (destination) => {
      if (!destination.startsWith(join(f.ctx.home, '.claude', 'skills'))) return;
      races.afterLink = undefined;
      await mkdir(f.claude());
      await writeFile(join(f.claude(), 'SKILL.md'), 'concurrent independent Claude skill');
    };
    try {
      await expect(f.run()).rejects.toThrow('changed during installation');
      expect(await readFile(join(f.claude(), 'SKILL.md'), 'utf8')).toBe('concurrent independent Claude skill');
      await expect(f.run()).rejects.toThrow('unrelated install');
      expect(await readFile(join(f.claude(), 'SKILL.md'), 'utf8')).toBe('concurrent independent Claude skill');
    } finally { races.afterLink = undefined; }
  });

  it('preserves a foreign Claude symlink created at final publication', async () => {
    const f = await fixture(['external-a']);
    await f.run();
    await rm(f.claude());
    const foreign = join(f.ctx.home, 'foreign-claude');
    await mkdir(foreign);
    races.beforeMove = async (source, destination) => {
      if (destination !== f.claude() || !source.endsWith('/entry')) return;
      races.beforeMove = undefined;
      await symlink(foreign, f.claude());
    };
    try {
      await expect(f.run()).rejects.toThrow('EEXIST');
      expect(await readlink(f.claude())).toBe(foreign);
      await expect(f.run()).rejects.toThrow('unrelated install');
    } finally { races.beforeMove = undefined; }
  });

  it('preserves a foreign canonical identity with identical bytes before provenance commit', async () => {
    const f = await fixture(['external-a']);
    await f.run();
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.expire();
    races.afterMove = async (source, destination) => {
      if (destination !== f.canonical() || !source.endsWith('/entry')) return;
      races.afterMove = undefined;
      const foreign = join(f.ctx.home, 'identical-foreign');
      await cp(f.canonical(), foreign, { recursive: true });
      await rm(f.canonical(), { recursive: true });
      const { rename } = await import('node:fs/promises');
      await rename(foreign, f.canonical());
    };
    try {
      await expect(f.run()).rejects.toThrow('identity changed after installation');
      f.catalogue.pins[SOURCE].ref = REF;
      await f.expire();
      await expect(f.run()).rejects.toThrow('unrelated install');
      expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${NEXT}`);
    } finally { races.afterMove = undefined; }
  });

  it('does not adopt an independent Claude copy using only canonical ownership', async () => {
    const f = await fixture(['external-a']);
    await f.run();
    await rm(f.claude());
    await cp(f.canonical(), f.claude(), { recursive: true });
    await expect(f.run()).rejects.toThrow('unrelated install');
    expect((await stat(f.claude())).isDirectory()).toBe(true);
  });

  it('preserves both entries if another install wins the rollback publication race', async () => {
    const f = await fixture(['external-a']);
    races.afterCopy = async (destination) => {
      if (!destination.startsWith(join(f.ctx.home, '.agents', 'skills'))) return;
      races.afterCopy = undefined;
      await mkdir(f.canonical());
      await writeFile(join(f.canonical(), 'SKILL.md'), 'first foreign install');
    };
    races.beforeMove = async (source, destination) => {
      if (!source.endsWith('/old') || destination !== f.canonical()) return;
      races.beforeMove = undefined;
      await mkdir(f.canonical());
      await writeFile(join(f.canonical(), 'SKILL.md'), 'newer foreign install');
    };
    try {
      let message = '';
      try { await f.run(); } catch (error) { message = String(error); }
      expect(message).toContain('original entry preserved at');
      const backup = message.split('original entry preserved at ').at(-1)!.trim();
      expect(await readFile(join(backup, 'SKILL.md'), 'utf8')).toBe('first foreign install');
      expect(await readFile(join(f.canonical(), 'SKILL.md'), 'utf8')).toBe('newer foreign install');
      await expect(f.run()).rejects.toThrow('unrelated install');
    } finally { races.afterCopy = undefined; races.beforeMove = undefined; }
  });

  it('preserves an independently replaced Claude directory despite managed canonical ownership', async () => {
    const f = await fixture();
    await f.run();
    await rm(f.claude());
    await mkdir(f.claude());
    await writeFile(join(f.claude(), 'SKILL.md'), 'independent Claude skill');
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.expire();
    await expect(f.run()).rejects.toThrow('unrelated install');
    expect(await readFile(join(f.claude(), 'SKILL.md'), 'utf8')).toBe('independent Claude skill');
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
  });

  it('repairs from cached upstream bytes offline and reports the failed daily refresh', async () => {
    const f = await fixture();
    await f.run();
    await f.expire();
    await rm(f.canonical(), { recursive: true });
    f.offline();
    await f.run();
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
  });

  it('does not treat a corrupt source cache as the expected upstream content', async () => {
    const f = await fixture();
    await f.run();
    const cache = join(f.ctx.home, '.cache', 'my-skills-external');
    const { readdir } = await import('node:fs/promises');
    const source = (await readdir(cache)).find((name) => /^[0-9a-f]{64}$/.test(name))!;
    await writeFile(join(cache, source, 'external-a', 'rule.txt'), 'corrupt cache');
    f.offline();
    await expect(f.run()).rejects.toThrow('offline');
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
    f.online();
    await f.run();
    expect(await readFile(join(cache, source, 'external-a', 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
  });

  it('repairs using a valid cached catalogue when a refresh contains unsupported data', async () => {
    const f = await fixture();
    await f.run();
    await f.expire();
    f.catalogue.schemaVersion = 99;
    await rm(f.canonical(), { recursive: true });
    await f.run();
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
    const cached = await json(join(f.ctx.home, '.cache', 'my-skills-external', 'catalogue.json'));
    expect(cached.catalogue.schemaVersion).toBe(2);
    f.catalogue.schemaVersion = 2;
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.run();
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${NEXT}`);
  });

  it('preserves the working install on a failed pin change, then retries successfully', async () => {
    const f = await fixture();
    await f.run();
    f.catalogue.pins[SOURCE].ref = NEXT;
    await f.expire();
    f.failArchives();
    await expect(f.run()).rejects.toThrow('503');
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${REF}`);
    expect((await json(f.stateFile))['external-a'].ref).toBe(REF);
    f.allowArchives();
    await f.run();
    expect(await readFile(join(f.canonical(), 'rule.txt'), 'utf8')).toBe(`resource at ${NEXT}`);
  });

  it('continues external reconciliation if the own-skill updater fails', async () => {
    const f = await fixture();
    await rm(join(f.ctx.home, '.cache', 'my-skills'), { recursive: true });
    const original = f.ctx.fetchImpl!;
    f.ctx.fetchImpl = (async (url, opts) => String(url).includes('registry.npmjs.org')
      ? new Response('failed', { status: 503 }) : original(url, opts)) as typeof fetch;
    await expect(f.run()).rejects.toThrow('own skill update');
    expect(existsSync(join(f.canonical(), 'SKILL.md'))).toBe(true);
    expect(existsSync(join(f.ctx.home, '.my-skills', '.session-update.stamp'))).toBe(false);
  });

  it('repairs external files before a stalled npm request and gives that request a deadline', async () => {
    const f = await fixture();
    await f.run();
    await writeFile(join(f.ctx.home, '.my-skills', '.session-update.stamp'), '0');
    await rm(f.canonical(), { recursive: true });
    const controller = new AbortController();
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(controller.signal);
    let entered!: () => void;
    const started = new Promise<void>((r) => { entered = r; });
    f.ctx.fetchImpl = (async (_url, options) => {
      entered();
      return new Promise<Response>((_resolve, reject) => {
        options!.signal!.addEventListener('abort', () => reject(new Error('timed out')), { once: true });
      });
    }) as typeof fetch;
    const pending = f.run();
    try {
      await started;
      expect(existsSync(join(f.canonical(), 'SKILL.md'))).toBe(true);
      expect(timeout).toHaveBeenCalledWith(30_000);
      controller.abort();
      await pending;
    } finally { controller.abort(); timeout.mockRestore(); }
    expect(existsSync(join(f.ctx.home, '.my-skills', '.session-update.lock'))).toBe(false);
  });

  it('keeps the Claude frontend plugin while installing the catalogue entry for Codex', async () => {
    const f = await fixture(['frontend-design']);
    await save(join(f.ctx.home, '.claude', 'settings.json'), { enabledPlugins: { 'frontend-design@claude-plugins-official': true } });
    await f.run();
    expect(existsSync(join(f.canonical(), 'SKILL.md'))).toBe(true);
    expect(existsSync(f.claude())).toBe(false);
  });

  it('serializes concurrent session starts and releases the lock on completion', async () => {
    const f = await fixture();
    const original = f.ctx.fetchImpl!;
    let unblock!: () => void;
    let entered!: () => void;
    const waiting = new Promise<void>((r) => { unblock = r; });
    const started = new Promise<void>((r) => { entered = r; });
    f.ctx.fetchImpl = (async (url, opts) => {
      if (String(url).endsWith('external-skills.json')) { entered(); await waiting; }
      return original(url, opts);
    }) as typeof fetch;
    const first = opSessionUpdate(f.ctx);
    await started;
    await opSessionUpdate(f.ctx);
    expect(existsSync(f.stateFile)).toBe(false);
    unblock();
    await first;
    expect(f.calls.filter((url) => url.endsWith('external-skills.json'))).toHaveLength(1);
    await rm(f.canonical(), { recursive: true });
    await opSessionUpdate(f.ctx);
    expect(existsSync(f.canonical())).toBe(true);
  });
});

describe('external catalogue validation', () => {
  it.each(['../outside', '/absolute', 'skills/../../outside'])('rejects unsafe path %s before any installation', async (path) => {
    const f = await fixture();
    f.catalogue.skills[0].path = path;
    await expect(f.run()).rejects.toThrow(/invalid field|unsafe external path/);
    expect(existsSync(f.stateFile)).toBe(false);
    expect(f.calls.some((url) => url.includes('codeload'))).toBe(false);
  });

  it('rejects upstream fork duplicates even when other entries are valid', async () => {
    const f = await fixture();
    f.catalogue.skills[1].name = 'own-fork';
    await expect(f.run()).rejects.toThrow('duplicate or fork');
    expect(existsSync(f.canonical())).toBe(false);
  });

  it('accepts refs without a version label, but refuses mutable refs or newer schemas', async () => {
    const f = await fixture();
    const catalogue: Omit<typeof f.catalogue, 'pins'> & { pins: Record<string, { ref: string; version?: string }> } = structuredClone(f.catalogue);
    delete catalogue.pins[SOURCE].version;
    expect(parseCatalogue(catalogue)[0].version).toBeUndefined();
    catalogue.pins[SOURCE].ref = 'main';
    expect(() => parseCatalogue(catalogue)).toThrow('invalid field');
    catalogue.schemaVersion = 3;
    expect(() => parseCatalogue(catalogue)).toThrow('unsupported schemaVersion');
  });
});
