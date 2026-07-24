import { mkdtemp, mkdir, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { fetchLatest, fetchVersion } from '../src/registry.js';

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
    if (u.endsWith('/latest') || /\/\d+\.\d+\.\d+$/.test(u)) {
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

describe('fetchVersion', () => {
  it('downloads the exact requested version', async () => {
    const cache = await mkdtemp(join(tmpdir(), 'cache-'));
    const fetchImpl = await buildFixture('1.2.3');
    const result = await fetchVersion('1.2.3', { cacheDir: cache, fetchImpl });
    expect(result.packageVersion).toBe('1.2.3');
    expect(result.manifest.skills[0].name).toBe('hello-world');
  });

  it('serves a cached version without touching the network', async () => {
    const cache = await mkdtemp(join(tmpdir(), 'cache-'));
    const fetchImpl = await buildFixture('1.2.3');
    await fetchVersion('1.2.3', { cacheDir: cache, fetchImpl });
    const failing = (async () => {
      throw new Error('offline');
    }) as unknown as typeof fetch;
    const result = await fetchVersion('1.2.3', { cacheDir: cache, fetchImpl: failing });
    expect(result.packageVersion).toBe('1.2.3');
  });

  it('fails loudly when the version does not exist and is not cached', async () => {
    const cache = await mkdtemp(join(tmpdir(), 'cache-'));
    const fetchImpl = (async () =>
      new Response('not found', { status: 404 })) as unknown as typeof fetch;
    await expect(fetchVersion('9.9.9', { cacheDir: cache, fetchImpl })).rejects.toThrow(
      /9\.9\.9/,
    );
  });
});
