import { mkdir, readFile, readdir, rename, writeFile, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import * as tar from 'tar';
import type { Manifest } from './manifest.js';

const PACKAGE = '@jesdi%2fskills';
const DEFAULT_REGISTRY = 'https://registry.npmjs.org';

const VERSION_RE = /^\d+\.\d+\.\d+(-[\w.-]+)?$/;

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

interface RegistryOpts {
  cacheDir: string;
  registryUrl?: string;
  fetchImpl?: typeof fetch;
}

async function download(
  meta: { version: string; dist: { tarball: string } },
  opts: RegistryOpts,
): Promise<void> {
  const f = opts.fetchImpl ?? fetch;
  const versionDir = join(opts.cacheDir, meta.version);
  if (existsSync(join(versionDir, 'package', 'skills-manifest.json'))) return;
  const res = await f(meta.dist.tarball);
  if (!res.ok) throw new Error(`tarball download failed: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const tmpDir = join(opts.cacheDir, `.tmp-${meta.version}`);
  await rm(tmpDir, { recursive: true, force: true });
  await mkdir(tmpDir, { recursive: true });
  const tmpTar = join(tmpDir, 'pkg.tgz');
  await writeFile(tmpTar, buf);
  await tar.extract({ file: tmpTar, cwd: tmpDir });
  await rm(tmpTar);
  await rm(versionDir, { recursive: true, force: true });
  await rename(tmpDir, versionDir);
}

export async function fetchVersion(
  version: string,
  opts: RegistryOpts,
): Promise<FetchLatestResult> {
  if (!VERSION_RE.test(version)) {
    throw new Error(`invalid version: ${version}`);
  }
  if (existsSync(join(opts.cacheDir, version, 'package', 'skills-manifest.json'))) {
    return loadFromCache(opts.cacheDir, version);
  }
  const f = opts.fetchImpl ?? fetch;
  const registry = opts.registryUrl ?? DEFAULT_REGISTRY;
  const res = await f(`${registry}/${PACKAGE}/${version}`);
  if (!res.ok) {
    throw new Error(`could not fetch @jesdi/skills@${version}: registry responded ${res.status}`);
  }
  const meta = (await res.json()) as { version: string; dist: { tarball: string } };
  if (meta.version !== version) {
    throw new Error(
      `registry returned version ${meta.version} but requested ${version} — refusing to cache`,
    );
  }
  await download(meta, opts);
  return loadFromCache(opts.cacheDir, version);
}

export async function fetchLatest(opts: RegistryOpts): Promise<FetchLatestResult> {
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
  await download(meta, opts);
  return loadFromCache(opts.cacheDir, meta.version);
}
