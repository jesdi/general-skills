#!/bin/sh
# Wrapper: configures the shared launcher (launcher.sh is a byte-identical copy
# of shared/launcher.sh).
set -eu
# Snapshot the caller's environment: the launcher restores it before exec, so
# its settings never overwrite an inherited variable of the same name.
inherited=$(export -p)

# Resolve file symlinks as well as symlinked skill directories.
entry=$0
while [ -L "$entry" ]; do
    parent=$(CDPATH= cd -- "$(dirname -- "$entry")" && pwd)
    entry=$(readlink "$entry")
    case "$entry" in /*) ;; *) entry=$parent/$entry ;; esac
done
skill_dir=$(CDPATH= cd -- "$(dirname -- "$entry")" && pwd)

name=test-weigh-in env_prefix=TEST_WEIGH_IN prefix=test-weigh-in fail_code=2 engine=weigh.py
# No pinned venv: ignore any inherited pins/venv_key.
pins= venv_key=
. "$skill_dir/launcher.sh"
