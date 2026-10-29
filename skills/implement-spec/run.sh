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

name=implement-spec env_prefix=IMPLEMENT_SPEC prefix=implement-spec fail_code=3 engine=driver.py
# The one dependency (design.md): jsonschema with its closure pinned.
venv_key=jsonschema-4.26.0
pins="jsonschema==4.26.0 attrs==26.1.0 referencing==0.37.0 rpds-py==2026.9.1 jsonschema-specifications==2025.9.1"
. "$skill_dir/launcher.sh"
