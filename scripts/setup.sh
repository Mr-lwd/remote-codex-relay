#!/usr/bin/env bash
# Install locked dependencies; do not change the user's Codex login/configuration.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ${1:-} != '' && ${1:-} != '--dev' ]]; then
  printf 'Usage: bash scripts/setup.sh [--dev]\n' >&2
  exit 2
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
node -e 'const major=Number(process.versions.node.split(".")[0]); if(major<22 || major>=25) throw Error("Node.js 22 or 24 required")'
command -v npm >/dev/null
python3 -m venv .venv
requirements=requirements.txt
if [[ ${1:-} == '--dev' ]]; then requirements=requirements-dev.txt; fi
.venv/bin/python -m pip install -r "$requirements"
npm ci
npm run build
if [[ ! -f relay.toml ]]; then
  (umask 077; cp relay.example.toml relay.toml)
fi
printf '\nSetup complete. Check Codex with .venv/bin/python scripts/doctor.py, then run .venv/bin/python -m server\n'
