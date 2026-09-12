"""Opt-in real installation/rename acceptance in a disposable project, without systemd.

Requires network access (or package caches), Python venv and Node 22/24.
Does not read real Codex credentials or send model requests.
"""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.release import source_files  # noqa: E402
from scripts.relay_manager import dump_toml  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="Install a release ZIP instead of copying this checkout"
    )
    args = parser.parse_args()
    checks = []
    output = ROOT / "runtime/manager-smoke"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="relay-manager-smoke-") as directory:
        work = Path(directory)
        project = work / "初始安装 with spaces"
        project.mkdir()
        if args.archive:
            with zipfile.ZipFile(args.archive) as archive:
                prefixes = {Path(info.filename).parts[0] for info in archive.infolist()}
                if len(prefixes) != 1:
                    raise ValueError("Release archive must have one project root")
                for info in archive.infolist():
                    path = Path(info.filename)
                    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
                        raise ValueError("Unsafe archive path")
                    if info.is_dir():
                        continue
                    target = project.joinpath(*path.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(info))
            expected = set(source_files(project))
            actual = {path for path in project.rglob("*") if path.is_file()}
            if expected != actual:
                raise ValueError("Archive contains files outside the release allowlist")
        else:
            for source in source_files(ROOT):
                target = project / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        codex = work / "synthetic-codex"
        codex.mkdir()
        with sqlite3.connect(codex / "state_5.sqlite") as db:
            db.execute(
                "CREATE TABLE threads(id TEXT, name TEXT, title TEXT, cwd TEXT, model TEXT, source TEXT, updated_at INTEGER, rollout_path TEXT, tokens_used INTEGER, archived INTEGER)"
            )
            db.execute(
                "INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "migration-fixture",
                    "Migration test",
                    "Migration test",
                    str(work),
                    "gpt-5.6-sol",
                    "cli",
                    1,
                    str(work / "missing-rollout.jsonl"),
                    0,
                    0,
                ),
            )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        (project / "relay.toml").write_text(
            dump_toml(
                {
                    "server": {"port": port},
                    "paths": {
                        "data_dir": str(project / "runtime"),
                        "workspace_root": str(project),
                        "default_cwd": str(project),
                    },
                    "codex": {"binary": sys.executable, "home": str(codex)},
                }
            )
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("RELAY_") and k not in ("CODEX_BIN", "CODEX_HOME")
        }
        env.update(XDG_STATE_HOME=str(work / "state"), XDG_CONFIG_HOME=str(work / "config"))
        base = f"http://127.0.0.1:{port}"
        processes = []

        def command(action):
            return ["bash", str(project / "scripts/relay.sh"), action]

        def start(action):
            args = command(action) + (["--foreground"] if action == "start" else [])
            log = (output / f"{action}.log").open("w")
            process = subprocess.Popen(args, cwd=work, env=env, stdout=log, stderr=log)
            log.close()
            processes.append(process)
            client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{action} failed; see {output / (action + '.log')}")
                try:
                    with client.open(base + "/", timeout=2) as response:
                        record = project / "runtime/manager.json"
                        if (
                            response.status == 200
                            and record.exists()
                            and json.loads(record.read_text()).get("root") == str(project)
                        ):
                            return process, client
                except OSError:
                    pass
                time.sleep(0.2)
            raise RuntimeError("Installation/startup readiness timed out")

        def login(client, password):
            request = Request(
                base + "/api/login",
                data=json.dumps({"password": password}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with client.open(request) as response:
                assert json.load(response)["ok"]
            with client.open(base + "/api/threads") as response:
                assert json.load(response)["threads"][0]["id"] == "migration-fixture"
            with client.open(base + "/") as response:
                html = response.read().decode()
            for asset in re.findall(r'(?:src|href)="(/assets/[^"\s]+)"', html):
                with client.open(base + asset) as response:
                    assert response.status == 200

        def stop(process):
            subprocess.run(
                command("stop"), cwd=work, env=env, check=True, capture_output=True, timeout=30
            )
            process.wait(timeout=30)
            assert process.returncode == 0

        try:
            print(
                "Checking fresh installation, foreground startup and existing sessions...",
                flush=True,
            )
            process, client = start("start")
            password = (project / "runtime/access.txt").read_text().strip()
            login(client, password)
            request = Request(
                base + "/api/media?name=migration.txt",
                data=b"preserved attachment",
                headers={"X-Relay-Request": "1", "Content-Type": "application/octet-stream"},
            )
            with client.open(request) as response:
                asset = json.load(response)
            before = json.loads((project / "runtime/manager.json").read_text())["foreground"]
            result = subprocess.run(
                command("start"),
                cwd=work,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert "已经运行" in result.stdout
            assert (
                json.loads((project / "runtime/manager.json").read_text())["foreground"] == before
            )
            checks.extend(
                [
                    "fresh install from another directory",
                    "foreground startup and idempotent start",
                    "original session list and static assets",
                ]
            )
            stop(process)
            renamed = work / "迁移后的 新目录"
            project.rename(renamed)
            project = renamed
            # Force a rebuild as well as virtualenv/config relocation.
            (project / "dist/index.html").unlink()
            print(
                "Checking renamed directory, virtualenv rebuild, original password and attachment...",
                flush=True,
            )
            process, client = start("repair")
            assert (project / "runtime/access.txt").read_text().strip() == password
            login(client, password)
            with client.open(base + asset["downloadUrl"]) as response:
                assert response.read() == b"preserved attachment"
            status = subprocess.run(
                command("status"),
                cwd=work,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert "HTTP 就绪：是" in status.stdout and "虚拟环境：正常" in status.stdout
            stop(process)
            checks.extend(
                [
                    "rename to Chinese/space path",
                    "automatic virtualenv and frontend repair",
                    "original password, session and attachment preserved",
                    "status and stop after migration",
                ]
            )
        finally:
            for process in processes:
                if process.poll() is None:
                    process.send_signal(__import__("signal").SIGINT)
                    process.wait(timeout=30)
    (output / "results.json").write_text(
        json.dumps({"passed": True, "checks": checks}, indent=2) + "\n"
    )
    print(
        f"Passed {len(checks)} management checks; temporary services stopped. Report: {output / 'results.json'}"
    )


if __name__ == "__main__":
    main()
