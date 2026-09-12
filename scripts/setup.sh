#!/usr/bin/env bash
# Compatibility entrypoint: share preparation, backups and checks with the manager.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ${1:-} != '' && ${1:-} != '--dev' ]]; then
  printf 'Usage: bash scripts/setup.sh [--dev]\n' >&2
  exit 2
fi
exec bash scripts/relay.sh setup "$@"
