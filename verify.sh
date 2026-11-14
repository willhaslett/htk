#!/bin/bash
set -euo pipefail
portable_root=$(cd -P "$(dirname "$0")" && pwd)
if [ -n "${HTK_PORTABLE_PYTHON:-}" ]; then
  exec "$HTK_PORTABLE_PYTHON" "$portable_root/verify.py"
fi
export HTK_RUNTIME="${HTK_RUNTIME:-${XDG_DATA_HOME:-$HOME/.local/share}/htk-portable/runtime}"
exec bash "$portable_root/htk/libexec/python.sh" "$portable_root/verify.py"
