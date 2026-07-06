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
