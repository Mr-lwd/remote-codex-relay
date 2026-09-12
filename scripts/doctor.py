"""Check prerequisites without reading credentials or starting model inference."""

import argparse
import importlib.metadata
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.config import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-codex", action="store_true", help="Check build/runtime only (CI)")
    args = parser.parse_args()
    failures = []

    def check(ok, label):
        print(f"{'OK' if ok else 'FAIL'}: {label}")
        if not ok:
            failures.append(label)

    check(sys.version_info >= (3, 11), "Python 3.11+")
    for dependency in (
        "fastapi",
        "uvicorn",
        "httpx",
        "Pillow",
        "pypdf",
        "python-docx",
        "python-pptx",
        "openpyxl",
        "reportlab",
    ):
        try:
            version = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            version = None
        check(version is not None, f"{dependency}: {version or 'run scripts/setup.sh'}")
    check((settings.dist_dir / "index.html").is_file(), "Frontend built (npm ci && npm run build)")
    check(settings.workspace_root.is_dir(), "Workspace root exists")
    check(settings.default_cwd.is_dir(), "Default working directory exists")
    print(f"INFO: Relay data: {settings.data_dir}")
    print(f"INFO: Login password after first startup: {settings.data_dir / 'access.txt'}")
    if not args.skip_codex:
        binary = shutil.which(settings.codex_bin)
        check(bool(binary), "Codex CLI is executable (PATH or CODEX_BIN)")
        if binary:
            try:
                result = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, timeout=10
                )
                check(result.returncode == 0, "Codex responds to --version")
                if result.returncode == 0:
                    print(f"INFO: {result.stdout.strip()}; adapter tested with 0.154.0")
                result = subprocess.run(
                    [binary, "app-server", "--help"], capture_output=True, text=True, timeout=10
                )
                check(result.returncode == 0, "Codex app-server subcommand available")
            except (OSError, subprocess.TimeoutExpired):
                check(False, "Codex executable responds within 10 seconds")
        state = settings.codex_home / "state_5.sqlite"
        if state.exists():
            try:
                with sqlite3.connect(f"file:{state}?mode=ro", uri=True) as db:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
                check(
                    {
                        "id",
                        "name",
                        "title",
                        "cwd",
                        "model",
                        "source",
                        "updated_at",
                        "rollout_path",
                        "tokens_used",
                        "archived",
                    }
                    <= columns,
                    "Codex state_5.sqlite schema is compatible",
                )
            except sqlite3.Error:
                check(False, "Codex session database is readable")
        else:
            print("INFO: No Codex session database yet; the web session list starts empty.")
        print(
            "INFO: CLI login/provider and model access must be configured by the operator. No credentials were read or inference run."
        )
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
