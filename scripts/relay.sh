#!/usr/bin/env bash
# Bootstrap with system Python: a moved/broken virtualenv must not block repair.
set -euo pipefail
if ! command -v python3 >/dev/null; then
  printf '错误：缺少 Python 3.11+。Ubuntu 可运行 sudo apt install python3 python3-venv。\n' >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  printf '错误：需要 Python 3.11+，请先升级系统 Python。\n' >&2
  exit 1
fi
relay_script=$(readlink -f -- "${BASH_SOURCE[0]}")
exec python3 "$(dirname -- "$relay_script")/relay_manager.py" "$@"
