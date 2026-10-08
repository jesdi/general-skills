#!/bin/sh
set -eu

pnpm test:coverage
pnpm --filter @jesdi/skills-cli build
pnpm exec tsc --noEmit
sh skills/crap-gate/run.sh --help > /dev/null
pnpm test:python
pnpm crap-gate --no-run "$@"
