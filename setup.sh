#!/bin/bash
set -euo pipefail
portable_root=$(cd -P "$(dirname "$0")" && pwd)
if [ "${1:-}" = --help ]; then
  printf 'Usage: ./setup.sh [htk install options]
Installs portable commands and agent guidance; leaves application configs unchanged.
'
  exit 0
fi
export HTK_RUNTIME="${HTK_RUNTIME:-${XDG_DATA_HOME:-$HOME/.local/share}/htk-portable/runtime}"
bash "$portable_root/htk/bootstrap.sh"
exec "$portable_root/htk/bin/htk" install --config "$portable_root/personal/toolkit.toml" "$@"
