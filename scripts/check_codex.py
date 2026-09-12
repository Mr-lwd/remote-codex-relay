"""Probe the installed CLI protocol without creating a thread or running a model."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.codex_rpc import CodexRPC, receive_request
from server.config import settings

REQUIRED = (
    "thread/start",
    "thread/resume",
    "turn/start",
    "turn/interrupt",
    "thread/compact/start",
    "thread/goal/get",
    "thread/goal/set",
    "thread/goal/clear",
    "thread/name/set",
    "thread/archive",
)


async def probe():
    env = {**os.environ, "CODEX_HOME": str(settings.codex_home)}
    with tempfile.TemporaryDirectory(prefix="relay-protocol-") as directory:
        result = subprocess.run(
            [
                settings.codex_bin,
                "app-server",
                "generate-json-schema",
                "--experimental",
                "--out",
                directory,
            ],
            env=env,
            capture_output=True,
            timeout=40,
        )
        if result.returncode:
            raise RuntimeError("Codex could not export the experimental schema; verify CLI version")
        methods = set()

        def walk(value):
            if isinstance(value, dict):
                method = value.get("properties", {}).get("method", {})
                if "const" in method:
                    methods.add(method["const"])
                methods.update(method.get("enum", []))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for path in Path(directory).rglob("*.json"):
            walk(json.loads(path.read_text()))
        missing = set(REQUIRED) - methods
        if missing:
            raise RuntimeError("Unsupported native methods: " + ", ".join(sorted(missing)))
    client = CodexRPC(settings.codex_bin, receive_request)
    try:
        await client.start()
        result = await client.call("model/list", {"limit": 100})
        models = {item["model"] for item in result.get("data", [])}
        print(f"OK: Native initialize and model/list ({len(models)} models in first page)")
        for model in settings.models:
            status = (
                "listed" if model["id"] in models else "not in first page; verify provider access"
            )
            print(f"INFO: {model['id']}: {status}")
    finally:
        await client.close()
    print("OK: Required experimental methods present. No thread or inference was created.")


if __name__ == "__main__":
    try:
        asyncio.run(probe())
    except Exception as error:
        # Do not dump native/provider errors that might include private details.
        print(
            f"FAIL: {type(error).__name__}. Check CLI version/configuration and retry.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
