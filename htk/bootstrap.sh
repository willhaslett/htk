#!/bin/bash
# macOS provides bash, curl, shasum, tar, uname and mktemp; no Python/Homebrew needed.
set -euo pipefail
root=$(cd -P "$(dirname "$0")" && pwd)
runtime=${HTK_RUNTIME:-${DAILY_TOOLKIT_RUNTIME:-${XDG_DATA_HOME:-$HOME/.local/share}/htk-portable/runtime}}
uv_version=0.11.2
python_version=3.12.13
case "$(/usr/bin/uname -s)-$(/usr/bin/uname -m)" in
  Darwin-arm64) target=aarch64-apple-darwin; checksum=4beaa9550f93ef7f0fc02f7c28c9c48cd61fe30db00f5ac8947e0a425c3fb282 ;;
  Darwin-x86_64) target=x86_64-apple-darwin; checksum=a9c3653245031304c50dd60ac0301bf6c112e12c38c32302a71d4fa6a63ba2cb ;;
  *) printf 'This bootstrap supports Apple Silicon and Intel macOS.\n' >&2; exit 1 ;;
esac
mkdir -p "$runtime/bin"
# Reuse a compatible existing UV before considering provisioning. An explicit
# path is useful when the user manages UV independently.
uv_binary=${DAILY_TOOLKIT_UV:-$(command -v uv || true)}
if [ -n "$uv_binary" ]; then
  installed_version=$("$uv_binary" --version)
  case "$installed_version" in
    "uv $uv_version"|"uv $uv_version "*) ;;
    *) printf 'UV %s required; set DAILY_TOOLKIT_UV to a compatible existing binary.\n' "$uv_version" >&2; exit 1 ;;
  esac
else
  uv_binary="$runtime/bin/uv-$uv_version"
fi
if [ ! -x "$uv_binary" ]; then
  download=$(/usr/bin/mktemp -d)
  trap 'rm -rf "$download"' EXIT
  archive="uv-$target.tar.gz"
  /usr/bin/curl --fail --location --proto '=https' --tlsv1.2 \
    "https://github.com/astral-sh/uv/releases/download/$uv_version/$archive" -o "$download/$archive"
  printf '%s  %s\n' "$checksum" "$download/$archive" | /usr/bin/shasum -a 256 -c -
  /usr/bin/tar -xzf "$download/$archive" -C "$download"
  /bin/cp "$download/uv-$target/uv" "$uv_binary"
  /bin/chmod 755 "$uv_binary"
fi
export UV_PYTHON_INSTALL_DIR="$runtime/python-managed"
export UV_CACHE_DIR="$runtime/uv-cache"
"$uv_binary" python install "$python_version"
python_binary=$("$uv_binary" python find --managed-python "$python_version")
"$python_binary" -c 'import ssl, curses, tomllib; print("Managed Python ready")'
/bin/ln -sfn "$python_binary" "$runtime/python"
printf 'Runtime ready at %s\n' "$runtime"
if [ "$#" -gt 0 ]; then
  exec "$root/bin/htk" "$@"
fi
printf 'Next: %s/bin/htk install\n' "$root"
