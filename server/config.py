"""Validated settings shared by the app, launcher and maintenance commands.

Precedence: environment > relay.toml > portable defaults. Importing this module
never creates files, reads credentials or starts a Codex process.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parent.parent
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODELS = (
    {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"},
    {"id": "gpt-6-astra", "name": "GPT-6-Astra"},
)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    dist_dir: Path
    codex_home: Path
    codex_bin: str
    workspace_root: Path
    default_cwd: Path
    host: str
    port: int
    secure_cookie: bool
    models: tuple
    default_model: str
    default_effort: str

    def client_config(self):
        return {
            "models": list(self.models),
            "defaultModel": self.default_model,
            "defaultEffort": self.default_effort,
            "efforts": list(EFFORTS),
        }


def load_settings(environ=None, root=ROOT, *, config=None, config_path=None):
    env = os.environ if environ is None else environ
    config_path = Path(
        config_path or env.get("RELAY_CONFIG_FILE", str(root / "relay.toml"))
    ).expanduser()
    if not config_path.is_absolute():
        config_path = root / config_path
    if config is None and "RELAY_CONFIG_FILE" in env and not config_path.is_file():
        raise ValueError("RELAY_CONFIG_FILE does not name an existing file")
    raw = (
        config
        if config is not None
        else (tomllib.loads(config_path.read_text()) if config_path.exists() else {})
    )
    allowed = {
        "server": {"host", "port", "secure_cookie"},
        "paths": {"data_dir", "dist_dir", "workspace_root", "default_cwd"},
        "codex": {"binary", "home"},
        "ui": {"models", "default_model", "default_effort"},
    }
    for section, values in raw.items():
        if section not in allowed or not isinstance(values, dict):
            raise ValueError(f"Unknown configuration section: {section}")
        extra = values.keys() - allowed[section]
        if extra:
            raise ValueError(f"Unknown keys in [{section}]: {', '.join(sorted(extra))}")

    def value(section, key, variable, default):
        result = env.get(variable, raw.get(section, {}).get(key, default))
        if isinstance(result, str) and not result.strip():
            raise ValueError(f"{variable} / {section}.{key} cannot be empty")
        return result

    def path(section, key, variable, default):
        result = Path(value(section, key, variable, default)).expanduser()
        return (result if result.is_absolute() else config_path.parent / result).resolve()

    workspace = path("paths", "workspace_root", "RELAY_WORKSPACE_ROOT", Path.home())
    default_cwd = path(
        "paths",
        "default_cwd",
        "RELAY_DEFAULT_CWD",
        root if root.is_relative_to(workspace) else workspace,
    )
    if (
        not workspace.is_dir()
        or not default_cwd.is_dir()
        or not default_cwd.is_relative_to(workspace)
    ):
        raise ValueError(
            "Workspace root and default directory must exist; default directory must be inside workspace root"
        )
    port = value("server", "port", "RELAY_PORT", 8000)
    if isinstance(port, bool) or not re.fullmatch(r"\d+", str(port)) or not 1 <= int(port) <= 65535:
        raise ValueError("RELAY_PORT / server.port must be an integer between 1 and 65535")
    secure = value("server", "secure_cookie", "RELAY_SECURE_COOKIE", False)
    if str(secure).lower() not in ("true", "false", "1", "0"):
        raise ValueError("RELAY_SECURE_COOKIE / server.secure_cookie must be true or false")
    models = raw.get("ui", {}).get("models", list(DEFAULT_MODELS))
    if not isinstance(models, list) or not models:
        raise ValueError("ui.models must be a nonempty array of {id, name} tables")
    ids = set()
    for model in models:
        if not isinstance(model, dict) or set(model) != {"id", "name"}:
            raise ValueError("Each ui.models entry must contain exactly id and name")
        if not isinstance(model["id"], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", model["id"]
        ):
            raise ValueError("Invalid model ID in ui.models")
        if (
            model["id"] in ids
            or not isinstance(model["name"], str)
            or not 1 <= len(model["name"].strip()) <= 80
        ):
            raise ValueError("Model IDs must be unique and names must contain 1–80 characters")
        ids.add(model["id"])
    model = value("ui", "default_model", "RELAY_DEFAULT_MODEL", models[0]["id"])
    effort = value("ui", "default_effort", "RELAY_DEFAULT_EFFORT", "medium")
    if model not in ids or effort not in EFFORTS:
        raise ValueError(
            "Default model must be in ui.models and effort must be low/medium/high/xhigh/max"
        )
    binary = value("codex", "binary", "CODEX_BIN", "codex")
    if "/" in binary or binary.startswith("~"):
        binary_path = Path(binary).expanduser()
        binary = str(
            (
                binary_path if binary_path.is_absolute() else config_path.parent / binary_path
            ).resolve()
        )
    return Settings(
        data_dir=path("paths", "data_dir", "RELAY_DATA_DIR", root / "runtime"),
        dist_dir=path("paths", "dist_dir", "RELAY_DIST_DIR", root / "dist"),
        codex_home=path("codex", "home", "CODEX_HOME", Path.home() / ".codex"),
        codex_bin=binary,
        workspace_root=workspace,
        default_cwd=default_cwd,
        host=str(value("server", "host", "RELAY_HOST", "127.0.0.1")),
        port=int(port),
        secure_cookie=str(secure).lower() in ("true", "1"),
        models=tuple(models),
        default_model=model,
        default_effort=effort,
    )


def __getattr__(name):
    # Management tools must be able to inspect/repair invalid or moved configuration
    # before loading runtime settings. Runtime imports still share one settings object.
    if name == "settings":
        result = load_settings()
        globals()[name] = result
        return result
    raise AttributeError(name)
