#!/bin/sh
# Keep bootstrap dependencies to POSIX tools, curl, tar, and SHA-256.
set -eu

json=false
for arg in "$@"; do
    [ "$arg" != --json ] || json=true
done
fail() {
    printf 'crap: %s\n' "$1" >&2
    if "$json"; then
        printf '%s\n' '{"error":{"kind":"tooling","message":"Runtime setup failed; see stderr. Run the launcher again after resolving the setup error."}}'
    fi
    exit 2
}

# Resolve file symlinks as well as symlinked skill directories.
entry=$0
while [ -L "$entry" ]; do
    parent=$(CDPATH= cd -- "$(dirname -- "$entry")" && pwd)
    entry=$(readlink "$entry")
    case "$entry" in /*) ;; *) entry=$parent/$entry ;; esac
done
skill_dir=$(CDPATH= cd -- "$(dirname -- "$entry")" && pwd)
cache=${CRAP_GATE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/crap-gate}
case "$cache" in /*) ;; *) fail 'CRAP_GATE_CACHE_DIR must be an absolute path' ;; esac
uv_version=0.12.13
python_version=3.13.7
case "$(uname -s):$(uname -m)" in
    Darwin:arm64|Darwin:aarch64)
        target=aarch64-apple-darwin
        digest=7e6ddb9316acc00f2296c82ff4d99977870ee34b2f0ddcae9444d714db9364ed ;;
    Darwin:x86_64)
        target=x86_64-apple-darwin
        digest=5e287ef61cb6a9b61b3a83fef124fd143e400468a7dac794230147a810e17119 ;;
    Linux:aarch64|Linux:arm64)
        target=aarch64-unknown-linux-musl
        digest=f44bc1037a17889fe562fffd2002d4ed108e499fbe68b4f022af244dc7b8244f ;;
    Linux:x86_64)
        target=x86_64-unknown-linux-musl
        digest=4e2bfd0c9007b1032a50e539e965fd0a6037d87ad93ae1580d220a92d4c94098 ;;
    *) fail 'Unsupported platform: use macOS or Linux on x86_64 or ARM64 (Windows: WSL)' ;;
esac
uv_dir=$cache/uv/$uv_version/$target
uv=$uv_dir/uv
if [ ! -x "$uv" ]; then
    [ "${CRAP_GATE_OFFLINE:-0}" != 1 ] || fail 'uv is not cached; provision this cache online first'
    mkdir -p "$uv_dir" || fail 'Cannot create runtime cache'
    stage=$(mktemp -d "$uv_dir/.download.XXXXXX") || fail 'Cannot create download directory'
    trap 'rm -rf "$stage"' EXIT
    trap 'exit 2' HUP INT TERM
    printf 'crap: downloading uv %s\n' "$uv_version" >&2
    curl --fail --location --silent --show-error --connect-timeout 15 --max-time 300 \
        "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-$target.tar.gz" \
        -o "$stage/uv.tar.gz" || fail 'Cannot download uv; check network access and retry'
    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$stage/uv.tar.gz")
    elif command -v shasum >/dev/null 2>&1; then
        actual=$(shasum -a 256 "$stage/uv.tar.gz")
    else
        fail 'SHA-256 verification requires sha256sum or shasum'
    fi
    [ "${actual%% *}" = "$digest" ] || fail 'uv archive checksum mismatch'
    tar -xzf "$stage/uv.tar.gz" -C "$stage" || fail 'Cannot extract uv'
    # Atomic publication: concurrent first runs never see a partial executable.
    mv -f "$stage/uv-$target/uv" "$uv" || fail 'Cannot install cached uv'
    rm -rf "$stage"
    trap - EXIT HUP INT TERM
fi

# Limit uv settings to this subprocess. Do not activate a venv or change PATH:
# coverage commands inherit the caller's project environment (the engine adds
# only the repo's own .venv/bin, when no virtualenv is active).
managed_uv() (
    unset UV_NO_MANAGED_PYTHON UV_PYTHON UV_PYTHON_DOWNLOADS UV_PYTHON_BIN_DIR
    export UV_PYTHON_INSTALL_DIR="$cache/python"
    export UV_CACHE_DIR="$cache/uv-cache"
    export UV_MANAGED_PYTHON=true
    "$uv" --no-config "$@"
)
python=$(managed_uv python find --no-project --system --managed-python --no-python-downloads "$python_version" 2>/dev/null) || {
    [ "${CRAP_GATE_OFFLINE:-0}" != 1 ] || fail 'Python is not cached; provision this cache online first'
    managed_uv python install --no-bin "$python_version" >&2 || fail 'Cannot provision Python; check network access and retry'
    python=$(managed_uv python find --no-project --system --managed-python --no-python-downloads "$python_version") || fail 'Cannot locate managed Python'
}
# Ignore Python-specific ambient settings for the engine, but preserve them for
# child coverage processes. -I would also remove the sibling module directory.
exec "$python" -E -s "$skill_dir/crap.py" "$@"
