#!/bin/bash
set -euo pipefail
plugin_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$plugin_dir/../../../htk/libexec/python.sh" \
  "$plugin_dir/../../libexec/herdr_controls/keymap.py" "$@"
