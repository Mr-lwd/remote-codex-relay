"""Unit tests always use a disposable Relay/Codex home, never the operator's data."""

import os
from pathlib import Path
import tempfile

_test_home = tempfile.TemporaryDirectory(prefix="codex-relay-tests-")
_root = Path(_test_home.name)
for _key in tuple(os.environ):
    if _key.startswith("RELAY_") or _key in ("CODEX_BIN", "CODEX_HOME"):
        os.environ.pop(_key)
os.environ.update(
    RELAY_CONFIG_FILE=str(_root / "relay.toml"),
    RELAY_DATA_DIR=str(_root / "runtime"),
    RELAY_WORKSPACE_ROOT=str(_root),
    RELAY_DEFAULT_CWD=str(_root),
    CODEX_HOME=str(_root / "codex"),
    CODEX_BIN="codex-test-unavailable",
)
(_root / "relay.toml").write_text("")


def pytest_unconfigure(config):
    _test_home.cleanup()


pytest_plugins = ["relay_fixtures"]
