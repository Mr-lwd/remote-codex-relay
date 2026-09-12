"""Management tests use disposable projects, fake installers and no real user services."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

from scripts.relay_manager import (
    Manager,
    ManagerError,
    Transaction,
    atomic_write,
    dump_toml,
    frontend_ready,
    process_alive,
    remap,
    unit_quote,
)
from server.maintenance import maintenance_lock

SOURCE = Path(__file__).resolve().parent.parent
REAL_LOGGED = Manager.logged


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "原项目 with spaces"
    root.mkdir()
    for name in (
        "relay.example.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-browser.txt",
        "package.json",
        "package-lock.json",
        "index.html",
        "vite.config.js",
    ):
        shutil.copyfile(SOURCE / name, root / name)
    (root / "src").mkdir()
    (root / "src/main.jsx").write_text("export default 1;")
    monkeypatch.setattr(
        Manager,
        "inspect_service",
        lambda self: {"available": True, "ActiveState": "inactive", "text": ""},
    )
    monkeypatch.setattr(
        Manager, "logged", lambda *args, **kwargs: pytest.fail("unexpected real installation")
    )
    original_command = Manager.command

    def guarded_command(self, args, **kwargs):
        if str(args[0]) in ("systemctl", "journalctl"):
            pytest.fail("unit tests must not execute real service commands")
        return original_command(self, args, **kwargs)

    monkeypatch.setattr(Manager, "command", guarded_command)
    return root


def manager_for(root, tmp_path, **kwargs):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    return Manager(
        root,
        {"PATH": os.defpath, "CODEX_BIN": sys.executable, "RELAY_PORT": str(port)},
        state_dir=tmp_path / "state",
        unit_dir=tmp_path / "config",
        **kwargs,
    )


def build_dist(path, text="built"):
    (path / "assets").mkdir(parents=True, exist_ok=True)
    (path / "assets/app.js").write_text(text)
    (path / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>')


def fake_install(manager, monkeypatch, *, failure=None):
    calls = []
    monkeypatch.setattr(manager, "node_environment", lambda: "/fake/npm")
    monkeypatch.setattr(
        manager,
        "requirements",
        lambda dev=False: "requirements-dev.txt" if dev else "requirements.txt",
    )
    monkeypatch.setattr(
        manager, "venv_valid", lambda profile: (manager.root / ".venv/ready").exists()
    )
    monkeypatch.setattr(manager, "wait_ready", lambda process=None: None)
    monkeypatch.setattr(manager, "ready", lambda: True)

    def command(args, **kwargs):
        calls.append([str(a) for a in args])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def install(args, label):
        values = [str(a) for a in args]
        calls.append(values)
        if failure and failure in values:
            raise ManagerError("模拟依赖下载或构建失败")
        if "venv" in values:
            target = manager.root / ".venv"
            target.mkdir()
            (target / "pyvenv.cfg").write_text(f"command = {sys.executable} -m venv {target}\n")
        elif "pip" in values:
            (manager.root / ".venv/ready").write_text("installed")
        elif "ci" in values:
            target = manager.root / "node_modules/.bin"
            target.mkdir(parents=True)
            (target / "vite").touch()
        elif "build" in values:
            build_dist(Path(values[values.index("--outDir") + 1]))

    monkeypatch.setattr(manager, "command", command)
    monkeypatch.setattr(manager, "logged", install)
    return calls


def test_first_install_then_repeated_start_is_idempotent(project, tmp_path, monkeypatch):
    manager = manager_for(project, tmp_path)
    calls = fake_install(manager, monkeypatch)
    manager.run("start")
    assert frontend_ready(project / "dist")
    assert manager.record_path.exists()
    assert manager.config_path.stat().st_mode & 0o777 == 0o600
    assert (manager.unit_dir / "codex-relay.service").exists()
    assert not any("enable" in call for call in calls)
    manager.service["ActiveState"] = "active"
    before = len(calls)
    manager.run("start")
    assert len(calls) == before


@pytest.mark.parametrize("failure", ["pip", "ci", "build"])
def test_failed_preparation_restores_environment_build_and_data(
    project, tmp_path, monkeypatch, failure
):
    manager = manager_for(project, tmp_path)
    env = project / ".venv"
    env.mkdir()
    (env / "original").write_text("old environment")
    build_dist(project / "dist", "original frontend")
    (project / "node_modules").mkdir()
    (project / "node_modules/original").write_text("old packages")
    data = project / "runtime"
    data.mkdir()
    (data / "access.txt").write_text("original-password")
    (data / "attachment.pdf").write_bytes(b"%PDF-original")
    fake_install(manager, monkeypatch, failure=failure)
    with pytest.raises(ManagerError, match="模拟"):
        manager.run("start")
    assert (env / "original").read_text() == "old environment"
    assert (project / "dist/assets/app.js").read_text() == "original frontend"
    assert (project / "node_modules/original").read_text() == "old packages"
    assert (data / "access.txt").read_text() == "original-password"
    assert (data / "attachment.pdf").read_bytes() == b"%PDF-original"
    assert not manager.record_path.exists()


def test_rename_repairs_config_and_service_but_keeps_external_paths(project, tmp_path, monkeypatch):
    old = project
    (old / "work").mkdir()
    external = tmp_path / "external codex"
    external.mkdir()
    config = {
        "paths": {
            "data_dir": str(old / "runtime"),
            "workspace_root": str(old),
            "default_cwd": str(old / "work"),
        },
        "codex": {"home": str(external)},
    }
    (old / "relay.toml").write_text(dump_toml(config))
    first = manager_for(old, tmp_path)
    fake_install(first, monkeypatch)
    first.run("start")
    text = (first.unit_dir / "codex-relay.service").read_text()
    (old / "runtime/access.txt").write_text("same-password")
    new = old.with_name("新目录 moved")
    old.rename(new)
    monkeypatch.setattr(
        Manager,
        "inspect_service",
        lambda self: {
            "available": True,
            "ActiveState": "inactive",
            "text": text,
            "WorkingDirectory": str(old),
            "environment": {"RELAY_CONFIG_FILE": str(old / "relay.toml")},
        },
    )
    manager = manager_for(new, tmp_path)
    fake_install(manager, monkeypatch)
    manager.run("repair")
    assert manager.settings.data_dir == new / "runtime"
    assert manager.settings.default_cwd == new / "work"
    assert manager.settings.codex_home == external
    assert (new / "runtime/access.txt").read_text() == "same-password"
    assert str(old) not in (manager.unit_dir / "codex-relay.service").read_text()
    assert list(new.glob(".relay.toml.relay-backup-*"))


def test_conflicting_origins_refuse_migration(project, tmp_path):
    (project / "runtime").mkdir()
    (project / "runtime/manager.json").write_text(json.dumps({"root": str(tmp_path / "old-one")}))
    (project / ".venv").mkdir()
    (project / ".venv/pyvenv.cfg").write_text(
        f"command = python3 -m venv {tmp_path / 'old-two' / '.venv'}\n"
    )
    with pytest.raises(ManagerError, match="冲突"):
        manager_for(project, tmp_path)


def test_does_not_take_over_another_service(project, tmp_path, monkeypatch):
    monkeypatch.setattr(
        Manager,
        "inspect_service",
        lambda self: {
            "available": True,
            "text": "[Service]\nWorkingDirectory=/tmp/another-app\nExecStart=/bin/true\n",
            "WorkingDirectory": "/tmp/another-app",
        },
    )
    with pytest.raises(ManagerError, match="不属于此项目"):
        manager_for(project, tmp_path)


def test_does_not_take_over_copy_of_live_project(project, tmp_path):
    old = tmp_path / "still exists"
    old.mkdir()
    (project / ".venv").mkdir()
    (project / ".venv/pyvenv.cfg").write_text(f"command = python3 -m venv {old / '.venv'}\n")
    with pytest.raises(ManagerError, match="旧项目目录仍存在"):
        manager_for(project, tmp_path)


@pytest.mark.parametrize("action", ["repair", "restart", "stop", "start"])
@pytest.mark.parametrize(
    "table,status", [("jobs", "running"), ("pending", "queued"), ("command_records", "starting")]
)
def test_busy_tasks_and_queue_prevent_mutation(
    project, tmp_path, monkeypatch, action, table, status
):
    manager = manager_for(project, tmp_path)
    data = project / "runtime"
    data.mkdir()
    with sqlite3.connect(data / "relay.sqlite") as db:
        db.execute(f"CREATE TABLE {table}(status TEXT, job_id TEXT)")
        db.execute(f"INSERT INTO {table}(status) VALUES(?)", (status,))
    monkeypatch.setattr(
        manager, "stop_running", lambda: pytest.fail("must not stop a busy service")
    )
    with pytest.raises(ManagerError, match="本次未停止服务"):
        manager.run(action)
    assert not manager.config_path.exists()


def test_competing_manager_lock_is_rejected(project, tmp_path):
    first = manager_for(project, tmp_path)
    second = manager_for(project, tmp_path)
    with first.mutation_lock(), pytest.raises(ManagerError, match="另一个管理命令"):
        second.run("repair")


def test_http_write_gate_blocks_maintenance(project, tmp_path):
    manager = manager_for(project, tmp_path)
    data = project / "runtime"
    data.mkdir()
    with maintenance_lock(data), pytest.raises(ManagerError, match="正在处理写请求"):
        manager.run("repair")


def test_changed_data_dir_still_checks_previous_running_jobs(project, tmp_path, monkeypatch):
    previous = tmp_path / "previous-data"
    previous.mkdir()
    with sqlite3.connect(previous / "relay.sqlite") as db:
        db.execute("CREATE TABLE jobs(status TEXT)")
        db.execute("INSERT INTO jobs VALUES('running')")
    manager = manager_for(project, tmp_path)
    manager.state["data_dir"] = str(previous)
    monkeypatch.setattr(manager, "stop_running", lambda: pytest.fail("must not stop previous job"))
    with pytest.raises(ManagerError, match="本次未停止服务"):
        manager.run("restart")


def test_port_in_use_is_not_killed_or_changed(project, tmp_path):
    manager = manager_for(project, tmp_path)
    manager.configuration()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        manager.settings = replace(manager.settings, host="127.0.0.1", port=port)
        with pytest.raises(ManagerError, match="未结束任何外部进程"):
            manager.check_port()
        assert listener.getsockname()[1] == port


def test_unit_escapes_shell_and_systemd_characters(project, tmp_path):
    manager = manager_for(project, tmp_path)
    manager.configuration()
    manager.state["instance"] = "synthetic-instance"
    manager.root = project.with_name('路径 %n $USER "quoted"')
    text = manager.unit_text()
    assert "%%n" in text and "$$USER" in text and '\\"quoted\\"' in text
    assert f"WorkingDirectory={str(manager.root).replace('%', '%%')}\n" in text
    assert "Environment=" in text
    assert unit_quote("a%b", executable=True) == '"a%%b"'


def test_remap_uses_path_boundary_and_toml_roundtrips():
    import tomllib

    old = Path("/tmp/old")
    new = Path('/tmp/新路径 "quote"')
    assert remap("/tmp/older/work", old, new) == "/tmp/older/work"
    assert remap("runtime", old, new) == "runtime"
    raw = {
        "paths": {"data_dir": remap("/tmp/old/runtime", old, new)},
        "server": {"secure_cookie": False, "port": 8000},
        "ui": {"models": [{"id": "model", "name": "模型"}]},
    }
    assert tomllib.loads(dump_toml(raw)) == raw


def test_transaction_restores_without_deleting_failed_artifact(tmp_path):
    path = tmp_path / "dist"
    path.mkdir()
    (path / "original").touch()
    transaction = Transaction()
    transaction.reserve(path)
    path.mkdir()
    (path / "failed").touch()
    transaction.rollback()
    assert (path / "original").exists()
    assert list(tmp_path.glob(".dist.relay-failed-*/failed"))


def test_pid_reuse_is_not_treated_as_our_foreground_process():
    assert not process_alive({"pid": os.getpid(), "starttime": "not-this-process"})


def test_config_error_before_mutation(project, tmp_path):
    (project / "relay.toml").write_text('[server]\nport="invalid"\n')
    manager = manager_for(project, tmp_path)
    manager.env.pop("RELAY_PORT")
    with pytest.raises(ManagerError, match="配置无效"):
        manager.run("repair")
    assert not (project / "runtime").exists()


def test_node_search_uses_compatible_installed_service_path(project, tmp_path):
    manager = manager_for(project, tmp_path)
    dirs = []
    for major in (18, 22):
        directory = tmp_path / f"node{major}"
        directory.mkdir()
        for name, script in (
            ("node", f"#!/bin/sh\nprintf 'v{major}.1.0\\n'\n"),
            ("npm", "#!/bin/sh\nexit 0\n"),
        ):
            (directory / name).write_text(script)
            (directory / name).chmod(0o755)
        dirs.append(directory)
    manager.env["PATH"] = str(dirs[0])
    manager.service_env["PATH"] = str(dirs[1])
    assert manager.node_environment() == str(dirs[1] / "npm")
    assert manager.env["PATH"].split(os.pathsep)[0] == str(dirs[1])


def test_failed_start_restores_previous_unit(project, tmp_path, monkeypatch):
    manager = manager_for(project, tmp_path)
    calls = fake_install(manager, monkeypatch)
    target = manager.unit_dir / "codex-relay.service"
    atomic_write(target, "original unit")
    monkeypatch.setattr(
        manager, "wait_ready", lambda process=None: (_ for _ in ()).throw(ManagerError("启动失败"))
    )
    with pytest.raises(ManagerError, match="启动失败"):
        manager.run("start")
    assert target.read_text() == "original unit"
    assert any("stop" in call for call in calls)


def test_enable_is_explicit(project, tmp_path, monkeypatch):
    manager = manager_for(project, tmp_path)
    calls = fake_install(manager, monkeypatch)
    manager.run("start", enable=True)
    assert any("enable" in call for call in calls)


def test_launch_wrapper_from_other_directory(tmp_path):
    result = subprocess.run(
        ["bash", str(SOURCE / "scripts/relay.sh"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "repair" in result.stdout and "--foreground" in result.stdout


def test_missing_node_provides_install_command(project, tmp_path, monkeypatch):
    manager = manager_for(project, tmp_path)
    manager.env["PATH"] = ""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(ManagerError, match="nvm install 22"):
        manager.node_environment()


def test_missing_venv_does_not_replace_old_environment(project, tmp_path, monkeypatch):
    manager = manager_for(project, tmp_path)
    fake_install(manager, monkeypatch)
    (project / ".venv").mkdir()
    (project / ".venv/original").touch()
    monkeypatch.setattr(
        manager,
        "command",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr=""),
    )
    with pytest.raises(ManagerError, match="python3-venv"):
        manager.run("start")
    assert (project / ".venv/original").exists()


def test_timed_out_install_stops_its_children_before_rollback(project, tmp_path, monkeypatch):
    manager = manager_for(project, tmp_path)
    signals = []

    class Process:
        pid = 12345
        waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fake-install", timeout)
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    with pytest.raises(ManagerError, match="失败或超时"):
        REAL_LOGGED(manager, ["fake-install"], "安装测试")
    assert signals and signals[0][0] == 12345


def test_invalid_management_record_is_actionable(project, tmp_path):
    atomic_write(project / "runtime/manager.json", json.dumps({"root": 123}))
    with pytest.raises(ManagerError, match="有效绝对路径"):
        manager_for(project, tmp_path)
