#!/bin/sh
set -eu

# Match the CRAP gate's coverage environment: use the project's prepared venv
# when the caller has not explicitly activated another one.
python=python3
if [ -z "${VIRTUAL_ENV:-}" ] && [ -x .venv/bin/python ]; then
    python=.venv/bin/python
fi

mkdir -p .crap
"$python" -m coverage erase
"$python" -m coverage run -m pytest skills/backlog skills/crap-gate skills/pr-risk -q
"$python" -m coverage run -a -m pytest skills/implement-spec -q
"$python" -m coverage run -a -m pytest skills/babysit-pr -q
COVERAGE_SUBPROCESS_PATCH=subprocess "$python" -m coverage run -m pytest skills/test-prune -q
"$python" -m coverage combine --append -q
"$python" -m coverage json -o .crap/python.json
