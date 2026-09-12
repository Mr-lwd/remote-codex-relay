"""Portable, standard-library-only installation and service management for Linux/WSL."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.request import build_opener, ProxyHandler
from urllib.error import URLError
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.config import load_settings
from server.maintenance import busy_counts, maintenance_lock

SERVICE = "codex-relay.service"
PATH_KEYS = {
    "paths": {"data_dir", "dist_dir", "workspace_root", "default_cwd"},
    "codex": {"home", "binary"},
}
PATH_ENV = {
    "RELAY_CONFIG_FILE",
    "RELAY_DATA_DIR",
    "RELAY_DIST_DIR",
    "RELAY_WORKSPACE_ROOT",
    "RELAY_DEFAULT_CWD",
    "CODEX_HOME",
    "CODEX_BIN",
}


class ManagerError(Exception):
    pass


def say(message):
    print(message, flush=True)


def search_path(*values):
    return os.pathsep.join(
        dict.fromkeys(part for value in values for part in value.split(os.pathsep) if part)
    )


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".relay-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fingerprint(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def remap(value, old, new):
    if not isinstance(value, str) or old is None:
        return value
    path = Path(value).expanduser()
    if path.is_absolute() and path.is_relative_to(old):
        return str(new / path.relative_to(old))
    return value


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
        )
    raise ManagerError("配置含不支持的值，请先检查 relay.toml。")


def dump_toml(raw):
    return (
        "\n\n".join(
            f"[{json.dumps(section)}]\n"
            + "\n".join(f"{json.dumps(key)} = {toml_value(value)}" for key, value in values.items())
            for section, values in raw.items()
        )
        + "\n"
    )


def unit_quote(value, *, executable=False):
    value = str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("\n", "\\n").replace("\r", "\\r")
    if executable:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def unit_directory(value):
    # WorkingDirectory is a scalar, not an ExecStart-style argument list. Quoting
    # it makes the quote part of the path. Internal spaces and quotes are literal.
    value = str(value)
    if any(c in value for c in "\n\r\0") or value != value.strip():
        raise ManagerError("systemd 路径不能含换行或首尾空白，请调整目录名称后重试。")
    return value.replace("%", "%%")


def venv_origin(root):
    config = root / ".venv/pyvenv.cfg"
    if not config.is_file():
        return None
    for line in config.read_text().splitlines():
        if line.startswith("command = ") and " -m venv " in line:
            target = line.split(" -m venv ", 1)[1].strip()
            # CPython records paths containing spaces without shell quoting.
            if target.endswith("/.venv"):
                return Path(target).parent
    return None


def frontend_ready(dist):
    index = dist / "index.html"
    if not index.is_file():
        return False
    assets = re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', index.read_text())
    return bool(assets) and all((dist / name.lstrip("/")).is_file() for name in assets)


def process_alive(record):
    try:
        pid = int(record["pid"])
        # starttime protects against PID reuse; a moved directory doesn't change it.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] != "Z" and fields[19] == record["starttime"]
    except (KeyError, ValueError, OSError, IndexError):
        return False


class Transaction:
    """Restore replaced generated artifacts/configuration if preparation fails."""

    def __init__(self):
        self.entries = []

    def reserve(self, target):
        target = Path(target)
        backup = None
        if target.exists() or target.is_symlink():
            backup = target.with_name(f".{target.name}.relay-backup-{uuid.uuid4().hex[:8]}")
            target.rename(backup)
        self.entries.append((target, backup))

    def rollback(self):
        for target, backup in reversed(self.entries):
            if target.exists() or target.is_symlink():
                failed = target.with_name(f".{target.name}.relay-failed-{uuid.uuid4().hex[:8]}")
                target.rename(failed)
            if backup is not None:
                backup.rename(target)


class Manager:
    def __init__(self, root=ROOT, environ=None, *, state_dir=None, unit_dir=None, use_systemd=True):
        self.root = Path(root).resolve()
        self.env = dict(os.environ if environ is None else environ)
        self.state_dir = (
            Path(state_dir or self.env.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "codex-relay"
        )
        self.unit_dir = (
            Path(unit_dir or self.env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "systemd/user"
        )
        self.record_path = self.root / "runtime/manager.json"
        self.log_path = self.root / "runtime/manager.log"
        self.state = json.loads(self.record_path.read_text()) if self.record_path.exists() else {}
        if not isinstance(self.state, dict) or self.state.get("version", 1) != 1:
            raise ManagerError("管理记录无效，请检查 runtime/manager.json，勿删除运行数据。")
        for key in ("root", "data_dir"):
            if key in self.state and (
                not isinstance(self.state[key], str) or not Path(self.state[key]).is_absolute()
            ):
                raise ManagerError(
                    f"管理记录中的 {key} 不是有效绝对路径，请核对 runtime/manager.json。"
                )
        if "instance" in self.state and not re.fullmatch(
            r"[0-9a-f]{32}", str(self.state["instance"])
        ):
            raise ManagerError("管理记录中的实例标识无效，请核对 runtime/manager.json。")
        foreground = self.state.get("foreground")
        if foreground is not None and (
            not isinstance(foreground, dict)
            or not isinstance(foreground.get("pid"), int)
            or foreground["pid"] <= 0
            or not str(foreground.get("starttime", "")).isdigit()
        ):
            raise ManagerError("前台进程记录无效，请核对 runtime/manager.json。")
        self.service = (
            self.inspect_service()
            if use_systemd and self.state.get("mode") != "foreground"
            else {"available": False}
        )
        self.service_env = dict(self.service.get("environment", {}))
        if self.service_env.get("PATH"):
            self.env["PATH"] = search_path(self.env.get("PATH", ""), self.service_env["PATH"])
        self.old = self.identify_origin()
        for key, value in self.service_env.items():
            if key != "PATH":
                self.env.setdefault(key, value)
        for key in PATH_ENV:
            if key in self.env:
                self.env[key] = remap(self.env[key], self.old, self.root)
        self.config_path = Path(
            self.env.get("RELAY_CONFIG_FILE", self.root / "relay.toml")
        ).expanduser()
        if not self.config_path.is_absolute():
            self.config_path = self.root / self.config_path
        self.settings = None

    def config_fingerprint(self):
        return hashlib.sha256(
            json.dumps(asdict(self.settings), sort_keys=True, default=str).encode()
        ).hexdigest()

    def prepared(self):
        profile = self.requirements()
        return (
            not self.old
            and self.state.get("config") == self.config_fingerprint()
            and self.state.get("frontend") == self.source_fingerprint()
            and self.state.get("python") == fingerprint(self.dependency_files(profile))
            and self.venv_valid(profile)
            and frontend_ready(self.settings.dist_dir)
        )

    def command(self, args, *, check=True, timeout=20, capture=True):
        try:
            result = subprocess.run(
                [str(a) for a in args],
                env=self.env,
                cwd=self.root,
                capture_output=capture,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ManagerError(
                f"无法执行 {Path(str(args[0])).name}：{type(error).__name__}。"
            ) from None
        if check and result.returncode:
            raise ManagerError(
                f"{Path(str(args[0])).name} 执行失败，请运行 bash scripts/relay.sh logs 查看详情。"
            )
        return result

    def inspect_service(self):
        if not shutil.which("systemctl", path=self.env.get("PATH")):
            return {"available": False}
        result = self.command(
            [
                "systemctl",
                "--user",
                "show",
                SERVICE,
                "--property=LoadState,ActiveState,MainPID,FragmentPath,DropInPaths,WorkingDirectory,Environment,UnitFileState",
            ],
            check=False,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        fields["available"] = "LoadState" in fields
        fields["environment"] = dict(
            part.split("=", 1) for part in shlex.split(fields.get("Environment", "")) if "=" in part
        )
        fragment = Path(fields.get("FragmentPath") or self.unit_dir / SERVICE)
        fields["text"] = fragment.read_text() if fragment.is_file() else ""
        return fields

    def identify_origin(self):
        evidence = {}
        if self.state.get("root"):
            evidence["管理记录"] = Path(self.state["root"])
        origin = venv_origin(self.root)
        if origin:
            evidence["虚拟环境"] = origin
        roots = set(evidence.values())
        if len(roots) > 1:
            raise ManagerError(
                "旧路径信息冲突：" + "；".join(f"{k}={v}" for k, v in evidence.items())
            )
        old = next(iter(roots), self.root)
        if old != self.root and old.exists():
            raise ManagerError(
                f"旧项目目录仍存在：{old}。可能是复制的另一个实例，不能自动接管服务。"
            )
        text = self.service.get("text", "")
        if text:
            if self.service.get("DropInPaths"):
                raise ManagerError(
                    "服务含自定义 drop-in 配置，请先核对并合并路径设置，避免覆盖自定义启动行为。"
                )
            match = re.search(r"^WorkingDirectory=(.+)$", text, re.M)
            workdir = self.service.get("WorkingDirectory")
            if not workdir and match:
                workdir = match[1].replace("%%", "%")
            marker = re.search(r"^# Relay-Instance: (.+)$", text, re.M)
            if marker and marker[1] != self.state.get("instance"):
                raise ManagerError("同名服务属于另一个 Relay 实例，未覆盖。")
            # Adopt only the documented Python launcher, without custom executable arguments.
            exec_lines = re.findall(r"^ExecStart=(.+)$", text, re.M)
            expected = self.root if workdir == str(self.root) else old
            try:
                args = shlex.split(exec_lines[-1]) if exec_lines else []
                args = [a.replace("%%", "%").replace("$$", "$") for a in args]
                if args[:1] == ["/usr/bin/env"]:
                    args = args[1:]
            except ValueError:
                args = []
            if workdir not in (str(old), str(self.root)) or args != [
                str(expected / ".venv/bin/python"),
                "-m",
                "server",
            ]:
                raise ManagerError("同名服务的工作目录或启动命令不属于此项目，未覆盖。")
            if re.search(r"^EnvironmentFile=", text, re.M):
                raise ManagerError(
                    "服务使用 EnvironmentFile，请先核对环境文件；管理器不会自动移除它。"
                )
        return old if old != self.root else None

    @contextmanager
    def mutation_lock(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.state_dir / "manager.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ManagerError("另一个管理命令正在执行，请等待它结束后重试。") from None
            yield

    def read_config(self):
        if not self.config_path.exists():
            if "RELAY_CONFIG_FILE" in self.env:
                raise ManagerError(f"指定的配置文件不存在：{self.config_path}")
            return tomllib.loads((self.root / "relay.example.toml").read_text()), True
        try:
            return tomllib.loads(self.config_path.read_text()), False
        except tomllib.TOMLDecodeError as error:
            raise ManagerError(f"TOML 配置语法错误：{error}") from None

    def configuration(self):
        raw, create = self.read_config()
        changed = False
        for section, keys in PATH_KEYS.items():
            values = raw.get(section, {})
            if not isinstance(values, dict):
                raise ManagerError(f"配置节 [{section}] 应为表。")
            for key in keys & values.keys():
                value = remap(values[key], self.old, self.root)
                if value != values[key]:
                    values[key] = value
                    changed = True
        # Validate the prospective config at the same path base before writing anything.
        try:
            settings = load_settings(self.env, self.root, config=raw, config_path=self.config_path)
        except (ValueError, TypeError, OSError) as error:
            raise ManagerError(
                f"配置无效：{error}。请检查 {self.config_path} 及 RELAY_* 环境变量。"
            ) from None
        self.settings = settings
        return raw, create or changed

    def assert_idle(self):
        try:
            counts = {"jobs": 0, "pending": 0, "commands": 0}
            for directory in self.data_directories():
                for key, value in busy_counts(directory).items():
                    counts[key] += value
        except sqlite3.Error:
            raise ManagerError(
                "无法读取任务数据库，已停止维护；请检查数据目录及数据库状态。"
            ) from None
        if any(counts.values()):
            raise ManagerError(
                f"仍有执行任务 {counts['jobs']}、待处理队列 {counts['pending']}、执行指令 {counts['commands']}。"
                "请在网页等待任务结束并处理队列后重试；本次未停止服务。"
            )

    def data_directories(self):
        # A freshly edited config may point elsewhere while the live process still
        # owns the previous database. Check both before stopping that process.
        directories = {self.settings.data_dir.resolve(), (self.root / "runtime").resolve()}
        previous = self.state.get("data_dir") or self.service_env.get("RELAY_DATA_DIR")
        if previous:
            previous = Path(remap(previous, self.old, self.root)).expanduser()
            if not previous.is_absolute():
                previous = self.config_path.parent / previous
            directories.add(previous.resolve())
        return sorted(directories)

    def node_environment(self):
        candidates = []
        for path in (self.env.get("PATH", ""), self.service_env.get("PATH", "")):
            candidates.extend(Path(p) / "node" for p in path.split(os.pathsep) if p)
        for pattern in (
            ".local/share/node/*/bin/node",
            ".nvm/versions/node/*/bin/node",
            ".local/share/mise/installs/node/*/bin/node",
        ):
            candidates.extend(sorted(Path.home().glob(pattern), reverse=True))
        for node in dict.fromkeys(candidates):
            if not node.is_file() or not os.access(node, os.X_OK):
                continue
            result = self.command([node, "--version"], check=False, timeout=5)
            match = re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", result.stdout)
            if result.returncode or not match or int(match[1]) not in (22, 24):
                continue
            npm = node.parent / "npm"
            if npm.is_file():
                self.env["PATH"] = search_path(str(node.parent), self.env.get("PATH", ""))
                return str(npm)
        raise ManagerError(
            "未找到 Node.js 22/24 和 npm。已安装 nvm 时执行 nvm install 22 && nvm use 22；"
            "否则请从 https://nodejs.org 安装 Node.js 22/24 并加入 PATH，然后重试。"
        )

    def logged(self, args, label):
        say(label)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as log:
            log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {label}\n")
            log.flush()
            process = None
            try:
                process = subprocess.Popen(
                    [str(a) for a in args],
                    cwd=self.root,
                    env=self.env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                code = process.wait(timeout=900)
            except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                if process is not None:
                    # npm/build children must stop before restoring their directories.
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    except ProcessLookupError:
                        pass
                if isinstance(error, KeyboardInterrupt):
                    raise
                raise ManagerError(f"{label}失败或超时，详情见 {self.log_path}") from None
        if code:
            raise ManagerError(f"{label}失败（退出码 {code}），详情见 {self.log_path}")

    def requirements(self, dev=False):
        profile = self.state.get("profile", "requirements.txt")
        if profile not in ("requirements.txt", "requirements-dev.txt", "requirements-browser.txt"):
            raise ManagerError("管理记录中的依赖类型无效。")
        python = self.root / ".venv/bin/python"
        if python.exists():
            probe = self.command(
                [
                    python,
                    "-c",
                    "import importlib.util,json; print(json.dumps([bool(importlib.util.find_spec(n)) for n in ['pytest','playwright']]))",
                ],
                check=False,
            )
            if probe.returncode == 0:
                installed = json.loads(probe.stdout)
                if installed[1]:
                    profile = "requirements-browser.txt"
                elif installed[0] and profile == "requirements.txt":
                    profile = "requirements-dev.txt"
        if dev and profile == "requirements.txt":
            profile = "requirements-dev.txt"
        return profile

    def dependency_files(self, profile):
        paths = [self.root / profile]
        for line in paths[0].read_text().splitlines():
            if line.startswith("-r "):
                paths.extend(self.dependency_files(line[3:].strip()))
        return paths

    def venv_valid(self, profile):
        python = self.root / ".venv/bin/python"
        if not python.is_file() or venv_origin(self.root) != self.root:
            return False
        locks = {}
        for path in self.dependency_files(profile):
            for line in path.read_text().splitlines():
                if "==" in line and not line.startswith("#"):
                    name, version = line.strip().split("==", 1)
                    locks[name] = version
        code = (
            "import importlib.metadata as m,json,sys; expected=json.loads(sys.argv[1]); "
            "sys.exit(any(m.version(k)!=v for k,v in expected.items()))"
        )
        return self.command([python, "-c", code, json.dumps(locks)], check=False).returncode == 0

    def source_fingerprint(self):
        files = [
            self.root / name
            for name in ("package.json", "package-lock.json", "index.html", "vite.config.js")
        ]
        files.extend(p for p in (self.root / "src").rglob("*") if p.is_file())
        return fingerprint(files)

    def prepare(self, transaction, *, dev=False):
        npm = self.node_environment()
        profile = self.requirements(dev)
        if not self.venv_valid(profile):
            if self.command(
                [sys.executable, "-c", "import venv,ensurepip"], check=False
            ).returncode:
                raise ManagerError(
                    "缺少 Python venv/ensurepip。Ubuntu 请运行 sudo apt install python3-venv，然后重试。"
                )
            transaction.reserve(self.root / ".venv")
            self.logged(
                [sys.executable, "-m", "venv", self.root / ".venv"],
                "创建当前目录的 Python 虚拟环境",
            )
            self.logged(
                [
                    self.root / ".venv/bin/python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-r",
                    self.root / profile,
                ],
                "安装锁定的 Python 依赖",
            )
        if not self.venv_valid(profile):
            raise ManagerError("Python 环境校验失败，请查看管理日志。")
        dist = self.settings.dist_dir
        protected = [
            self.root,
            self.settings.data_dir,
            self.root / ".venv",
            self.root / "src",
            self.root / "scripts",
            self.root / "server",
        ]
        if any(
            p == dist or p.is_relative_to(dist) or dist.is_relative_to(p) for p in protected[1:]
        ) or self.root.is_relative_to(dist):
            raise ManagerError(
                "dist_dir 与项目源码、虚拟环境或运行数据重叠，请指定独立的前端构建目录。"
            )
        digest = self.source_fingerprint()
        if self.state.get("frontend") != digest or not frontend_ready(dist):
            packages = fingerprint([self.root / "package.json", self.root / "package-lock.json"])
            if (
                self.old
                or self.state.get("packages") != packages
                or not (self.root / "node_modules/.bin/vite").is_file()
            ):
                transaction.reserve(self.root / "node_modules")
                self.logged([npm, "ci"], "安装锁定的前端依赖")
            dist.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".relay-build-", dir=dist.parent) as temporary:
                stage = Path(temporary) / "dist"
                self.logged(
                    [npm, "run", "build", "--", "--outDir", stage], "构建前端（失败时保留上一版）"
                )
                if not frontend_ready(stage):
                    raise ManagerError("构建产物不完整，已保留上一版前端。")
                transaction.reserve(dist)
                stage.rename(dist)
            self.state["packages"] = packages
        self.state.update(
            profile=profile, python=fingerprint(self.dependency_files(profile)), frontend=digest
        )

    def is_running(self):
        return self.service.get("ActiveState") in ("active", "activating") or process_alive(
            self.state.get("foreground", {})
        )

    def stop_running(self):
        if self.service.get("ActiveState") in ("active", "activating"):
            self.command(["systemctl", "--user", "stop", SERVICE])
            self.service["ActiveState"] = "inactive"
        record = self.state.get("foreground", {})
        if process_alive(record):
            os.kill(record["pid"], signal.SIGINT)
            deadline = time.monotonic() + 15
            while process_alive(record) and time.monotonic() < deadline:
                time.sleep(0.1)
            if process_alive(record):
                raise ManagerError("前台服务尚未退出，请检查日志；未强制终止。")

    def check_port(self):
        try:
            addresses = socket.getaddrinfo(
                self.settings.host, self.settings.port, type=socket.SOCK_STREAM
            )
            for family, kind, proto, _, address in addresses:
                with socket.socket(family, kind, proto) as candidate:
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    candidate.bind(address)
        except OSError:
            raise ManagerError(
                f"无法监听 {self.settings.host}:{self.settings.port}，可能被其他程序占用或地址无效。未结束任何外部进程，也未更换端口。"
            ) from None

    def unit_text(self):
        environment = dict(self.service_env)
        environment.update(
            {
                k: v
                for k, v in self.env.items()
                if k.startswith("RELAY_") or k in ("CODEX_HOME", "CODEX_BIN")
            }
        )
        environment["PATH"] = self.env["PATH"]
        environment["RELAY_CONFIG_FILE"] = str(self.config_path)
        for key in PATH_ENV:
            if key in environment:
                environment[key] = remap(environment[key], self.old, self.root)
        lines = [
            "# Generated by scripts/relay.sh; local configuration, not project source.",
            f"# Relay-Instance: {self.state['instance']}",
            "[Unit]",
            "Description=Codex Relay web console",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={unit_directory(self.root)}",
            f"ExecStart=/usr/bin/env {unit_quote(self.root / '.venv/bin/python', executable=True)} -m server",
            *[f"Environment={unit_quote(k + '=' + v)}" for k, v in sorted(environment.items())],
            "Restart=on-failure",
            "RestartSec=3",
            "TimeoutStopSec=15",
            "UMask=0077",
            "KillMode=control-group",
            "StandardOutput=journal",
            "StandardError=journal",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
        return "\n".join(lines)

    def base_url(self):
        host = self.settings.host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.settings.port}"

    def ready(self):
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(self.base_url() + "/api/health", timeout=2) as response:
                if json.load(response).get("service") != "codex-relay":
                    return False
            with opener.open(self.base_url() + "/", timeout=2) as response:
                return response.status == 200 and b'id="root"' in response.read()
        except (OSError, ValueError, URLError):
            return False

    def wait_ready(self, process=None):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                break
            if self.ready():
                return
            time.sleep(0.2)
        raise ManagerError("服务未能正常就绪，请运行 bash scripts/relay.sh logs 查看启动日志。")

    def show_addresses(self):
        say(f"本机访问：{self.base_url()}/")
        if self.settings.host in ("0.0.0.0", "::"):
            say(
                f"监听范围：所有{' IPv4' if self.settings.host == '0.0.0.0' else ' IPv6'}网卡；请确认安全组和防火墙放行 TCP {self.settings.port}。"
            )
            try:
                addresses = {
                    item[4][0]
                    for item in socket.getaddrinfo(
                        socket.gethostname(), None, type=socket.SOCK_STREAM
                    )
                }
            except OSError:
                addresses = set()
            try:
                # UDP connect only selects the local route; no datagram is sent.
                family = socket.AF_INET if self.settings.host == "0.0.0.0" else socket.AF_INET6
                with socket.socket(family, socket.SOCK_DGRAM) as route:
                    route.connect(("192.0.2.1" if family == socket.AF_INET else "2001:db8::1", 9))
                    addresses.add(route.getsockname()[0])
            except OSError:
                pass
            for address in sorted(addresses):
                parsed = ipaddress.ip_address(address)
                if not parsed.is_loopback and parsed.version == (
                    4 if self.settings.host == "0.0.0.0" else 6
                ):
                    host = f"[{address}]" if ":" in address else address
                    say(f"网卡地址：http://{host}:{self.settings.port}/")
            say(
                f"公网访问：http://<服务器公网IP或域名>:{self.settings.port}/（云主机公网映射需自行确认）"
            )
        if self.settings.secure_cookie:
            say("Cookie 已设为 Secure：浏览器请使用 HTTPS 代理地址，HTTP 直连无法保持登录。")
        else:
            say("当前允许 HTTP 登录；使用 HTTPS 代理后请将 secure_cookie 设为 true。")
        say("读取原口令：" + shlex.join(["cat", str(self.settings.data_dir / "access.txt")]))
        say("服务日志：bash scripts/relay.sh logs")
        say(f"安装/修复日志：{self.log_path}")

    def save_state(self, transaction=None):
        self.state.update(
            version=1,
            root=str(self.root),
            data_dir=str(self.settings.data_dir),
            config=self.config_fingerprint(),
        )
        if transaction is not None:
            transaction.reserve(self.record_path)
        atomic_write(self.record_path, json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")

    def run(self, action, *, foreground=False, enable=False, dev=False):
        with self.mutation_lock(), ExitStack() as locks:
            raw, changed = self.configuration()
            self.settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                for directory in self.data_directories():
                    if directory.is_dir():
                        locks.enter_context(maintenance_lock(directory, exclusive=True))
            except BlockingIOError:
                raise ManagerError("服务正在处理写请求或其他维护操作，请稍后重试。") from None
            if action == "start" and self.is_running() and self.prepared() and self.ready():
                if enable and self.service.get("available"):
                    self.command(["systemctl", "--user", "enable", SERVICE])
                say("服务已经运行。")
                self.show_addresses()
                return
            self.assert_idle()
            if action == "stop":
                self.stop_running()
                say("服务已停止。")
                return
            if enable and (foreground or not self.service.get("available")):
                raise ManagerError("--enable 需要可用的用户 systemd，不能与前台运行同时使用。")
            was_running = self.is_running()
            if action == "setup" and was_running:
                raise ManagerError(
                    "服务正在运行。请使用 repair 更新运行环境，或先停止服务再执行 setup。"
                )
            self.stop_running()
            transaction = Transaction()
            old_state = dict(self.state)
            installed_unit = False
            process = None
            try:
                if action != "setup":
                    self.check_port()
                if changed:
                    transaction.reserve(self.config_path)
                    atomic_write(self.config_path, dump_toml(raw))
                self.state.setdefault("instance", uuid.uuid4().hex)
                self.prepare(transaction, dev=dev)
                binary = shutil.which(self.settings.codex_bin, path=self.env.get("PATH"))
                if action != "setup" and not binary:
                    raise ManagerError(
                        "找不到 Codex CLI，请安装或设置 codex.binary / CODEX_BIN；已有账号配置未修改。"
                    )
                if action == "setup":
                    self.save_state(transaction)
                    say("安装完成。运行 bash scripts/relay.sh start 启动。")
                    return
                if self.service.get("available") and not foreground:
                    target = self.unit_dir / SERVICE
                    if not target.exists() and self.service.get("text"):
                        atomic_write(target, self.service["text"])
                    transaction.reserve(target)
                    atomic_write(target, self.unit_text())
                    installed_unit = True
                    self.command(["systemctl", "--user", "daemon-reload"])
                    self.command(["systemctl", "--user", "start", SERVICE])
                    self.wait_ready()
                    if enable:
                        self.command(["systemctl", "--user", "enable", SERVICE])
                    self.state["mode"] = "systemd"
                    self.save_state(transaction)
                    say("服务已启动，首页及健康检查通过。")
                    self.show_addresses()
                else:
                    say("使用前台运行，按 Ctrl+C 停止。")
                    log_path = self.settings.data_dir / "server.log"
                    with log_path.open("ab") as log:
                        process = subprocess.Popen(
                            [str(self.root / ".venv/bin/python"), "-m", "server"],
                            cwd=self.root,
                            env=self.env,
                            stdout=log,
                            stderr=log,
                        )
                    fields = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
                    self.state["foreground"] = {"pid": process.pid, "starttime": fields[19]}
                    self.state["mode"] = "foreground"
                    self.wait_ready(process)
                    self.save_state(transaction)
                    self.show_addresses()
            except (Exception, KeyboardInterrupt):
                if process is not None and process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=15)
                if installed_unit:
                    self.command(["systemctl", "--user", "stop", SERVICE], check=False)
                transaction.rollback()
                self.state = old_state
                if installed_unit:
                    self.command(["systemctl", "--user", "daemon-reload"], check=False)
                if was_running and not self.old and self.service.get("available"):
                    self.command(["systemctl", "--user", "start", SERVICE], check=False)
                raise
        # Release both locks before waiting: stop/restart must work from another terminal.
        if process is not None:
            try:
                code = process.wait()
                if code:
                    raise ManagerError(f"前台服务退出（{code}），请查看日志。")
            except KeyboardInterrupt:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                process.wait(timeout=15)

    def status(self):
        self.configuration()
        say(f"项目目录：{self.root}")
        if self.old:
            say(f"检测到旧安装目录：{self.old}，请运行 bash scripts/relay.sh repair。")
        say(f"配置文件：{self.config_path}")
        say(f"服务状态：{'运行中' if self.is_running() else '未运行'}")
        say(
            f"进程 PID：{self.service.get('MainPID') or self.state.get('foreground', {}).get('pid', '无')}"
        )
        say(
            f"前端文件：{'完整' if frontend_ready(self.settings.dist_dir) else '缺失或不完整，请运行 bash scripts/relay.sh repair'}"
        )
        say(
            f"虚拟环境：{'正常' if self.venv_valid(self.requirements()) else '失效或依赖不匹配，请运行 bash scripts/relay.sh repair'}"
        )
        say(f"HTTP 就绪：{'是' if self.ready() else '否'}")
        try:
            npm = self.node_environment()
            say(f"Node/npm：{Path(npm).parent}")
        except ManagerError as error:
            say(f"依赖检查：{error}")
        if not self.is_running():
            try:
                self.check_port()
            except ManagerError as error:
                say(str(error))
        try:
            say("任务状态：" + json.dumps(busy_counts(self.settings.data_dir), ensure_ascii=False))
        except sqlite3.Error:
            say("任务数据库无法读取，请检查运行数据目录。")
        self.show_addresses()

    def logs(self):
        # Installation errors remain readable even if configuration cannot load.
        if self.log_path.exists():
            say("最近的安装/修复日志：")
            self.command(["tail", "-n", "30", self.log_path], capture=False)
        if (
            self.service.get("available")
            and self.service.get("text")
            and self.state.get("mode") != "foreground"
        ):
            self.command(
                ["journalctl", "--user", "-u", SERVICE, "-n", "60", "-f", "--no-pager"],
                capture=False,
                timeout=None,
            )
        else:
            self.configuration()
            path = self.settings.data_dir / "server.log"
            if not path.is_file():
                raise ManagerError("尚无服务日志。请先运行 start，或检查上方安装日志。")
            self.command(["tail", "-n", "60", "-F", path], capture=False, timeout=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex Relay 启动、迁移修复与故障诊断")
    parser.add_argument(
        "action", choices=("start", "repair", "stop", "restart", "status", "logs", "setup")
    )
    parser.add_argument("--foreground", action="store_true", help="前台运行（start）")
    parser.add_argument(
        "--enable", action="store_true", help="设置用户服务自启动（start/repair/restart）"
    )
    parser.add_argument("--dev", action="store_true", help="安装开发依赖（setup）")
    args = parser.parse_args(argv)
    if args.foreground and args.action != "start":
        parser.error("--foreground 只用于 start")
    if args.enable and args.action not in ("start", "repair", "restart"):
        parser.error("--enable 只用于 start/repair/restart")
    if args.dev and args.action != "setup":
        parser.error("--dev 只用于 setup")
    try:
        manager = Manager(use_systemd=not args.foreground)
        if args.action == "status":
            manager.status()
        elif args.action == "logs":
            manager.logs()
        else:
            manager.run(args.action, foreground=args.foreground, enable=args.enable, dev=args.dev)
    except KeyboardInterrupt:
        return 130
    except (ManagerError, OSError, ValueError, sqlite3.Error) as error:
        say(f"错误：{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
