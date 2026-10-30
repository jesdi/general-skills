// Copies shared/launcher.sh into every skill whose run.sh sources launcher.sh.
// `--check` only reports skills whose copy is missing or differs, and exits 1.
import { copyFileSync, existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const shared = readFileSync(join(root, 'shared/launcher.sh'));
const check = process.argv.includes('--check');

const bad: string[] = [];
for (const skill of readdirSync(join(root, 'skills'))) {
  const wrapper = join(root, 'skills', skill, 'run.sh');
  if (!existsSync(wrapper) || !readFileSync(wrapper, 'utf8').includes('launcher.sh')) continue;
  const copy = join(root, 'skills', skill, 'launcher.sh');
  const state = !existsSync(copy) ? 'missing' : readFileSync(copy).equals(shared) ? null : 'differs from shared/launcher.sh';
  if (!state) continue;
  if (check) bad.push(`skills/${skill}/launcher.sh ${state} (run pnpm sync-launchers)`);
  else copyFileSync(join(root, 'shared/launcher.sh'), copy);
}
if (bad.length) {
  console.error(bad.join('\n'));
  process.exit(1);
}
