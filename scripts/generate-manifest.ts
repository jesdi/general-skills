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
