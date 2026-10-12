#!/usr/bin/env bash
set -euo pipefail
repo='https://github.com/Reza2kn/persianvlm-local.git'
dest="${PERSIANVLM_HOME:-$HOME/persianvlm-local}"
command -v git >/dev/null || { echo 'Install Git and Docker Desktop first.' >&2; exit 1; }
if [[ -e "$dest" ]]; then
  [[ -f "$dest/local.sh" && -f "$dest/compose.yaml" ]] || { echo "Refusing to overwrite $dest" >&2; exit 1; }
  echo "Using existing installation: $dest"
else
  git clone --branch v0.1.0 --depth 1 "$repo" "$dest"
fi
exec bash "$dest/local.sh" start
