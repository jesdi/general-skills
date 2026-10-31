#!/bin/sh
set -eu

# The fast gate: types, then the given test files. Without arguments it takes
# IMPLEMENT_SPEC_LOCKED, the space-separated locked files of implement-spec.
# Only test files run: a locked fixture (map_fixture.py) holds no test, and pytest
# exits 5 when it collects none. Fixtures still run through the tests that use them.
python=python3
if [ -z "${VIRTUAL_ENV:-}" ] && [ -x .venv/bin/python ]; then
    python=.venv/bin/python
fi
# shellcheck disable=SC2086 # IMPLEMENT_SPEC_LOCKED is a deliberate word list
[ "$#" -gt 0 ] || set -- ${IMPLEMENT_SPEC_LOCKED:-}

pnpm exec tsc --noEmit
ts=
for file in "$@"; do
    case ${file##*/} in
        # One pytest run per file: two test_*.py with the same basename collide in one run.
        test_*.py | *_test.py) "$python" -m pytest -q "$file" ;;
        *.test.ts) ts="$ts $file" ;;
    esac
done
# shellcheck disable=SC2086 # ts is a deliberate word list
[ -z "$ts" ] || pnpm exec vitest run $ts
