#!/usr/bin/env bash
set -euo pipefail
batch_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$batch_dir/.venv/Scripts/python.exe" ]]; then
  exec "$batch_dir/.venv/Scripts/python.exe" "$(wslpath -w "$batch_dir/publish_assets.py")" "$@"
elif [[ -x "$batch_dir/.venv/bin/python" ]]; then
  exec "$batch_dir/.venv/bin/python" "$batch_dir/publish_assets.py" "$@"
else
  exec python3 "$batch_dir/publish_assets.py" "$@"
fi
