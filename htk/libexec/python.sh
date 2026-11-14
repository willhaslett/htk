#!/bin/bash
set -euo pipefail
runtime=${HTK_RUNTIME:-${DAILY_TOOLKIT_RUNTIME:-${XDG_DATA_HOME:-$HOME/.local/share}/htk-portable/runtime}}
if [ ! -x "$runtime/python" ]; then
  printf 'Managed runtime missing. Run the htk bootstrap.sh first.\n' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
exec "$runtime/python" "$@"
