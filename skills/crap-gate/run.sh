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

name=crap-gate env_prefix=CRAP_GATE prefix=crap fail_code=2 engine=crap.py
# No pinned venv: ignore any inherited pins/venv_key.
pins= venv_key=
json=false
for arg in "$@"; do
    [ "$arg" != --json ] || json=true
done
setup_failed() {
    ! "$json" || printf '%s\n' '{"error":{"kind":"tooling","message":"Runtime setup failed; see stderr. Run the launcher again after resolving the setup error."}}'
}
. "$skill_dir/launcher.sh"
