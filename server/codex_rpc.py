"""Bidirectional local Codex app-server connection; never approves on behalf of a user."""

import asyncio
import contextlib
import json
import os
import uuid
from server.config import settings
from server import __version__


class CodexRPC:
    def __init__(self, binary, on_request):
        self.binary, self.on_request = binary, on_request
        self.proc = None
        self.pending = {}
        self.events = asyncio.Queue()
        self.inbox = {}
        self.items = {}
        self.next_id = 0
        self.thread_id = None
        self.turn_id = None
        self.reader = None
        self.write_lock = asyncio.Lock()

    async def start(self):
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("CODEX_THREAD", "CODEX_TURN"))
        }
        env["CODEX_HOME"] = str(settings.codex_home)
        self.proc = await asyncio.create_subprocess_exec(
            self.binary,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
            limit=192 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self.read())
        await self.call(
            "initialize",
            {
                "clientInfo": {"name": "codex_relay", "version": __version__},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.write({"method": "initialized", "params": {}})
        return self

    async def write(self, value):
        async with self.write_lock:
            self.proc.stdin.write((json.dumps(value) + "\n").encode())
            await self.proc.stdin.drain()

    async def call(self, method, params, timeout=40):
        self.next_id += 1
        ident = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.write({"id": ident, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Codex {method} 响应超时，请检查会话状态后重试") from e
        finally:
            self.pending.pop(ident, None)

    async def read(self):
        failure = RuntimeError("Codex 连接已关闭，请检查任务状态后重试")
        try:
            async for line in self.proc.stdout:
                value = json.loads(line)
                if "method" not in value:
                    future = self.pending.get(value.get("id"))
                    if future and not future.done():
                        if "error" in value:
                            future.set_exception(
                                RuntimeError(value["error"].get("message", "Codex 请求失败"))
                            )
                        else:
                            future.set_result(value.get("result", {}))
                    continue
                method, params = value["method"], value.get("params", {})
                if "id" in value:
                    await self.on_request(self, value)
                else:
                    if method == "item/started":
                        item = params.get("item", {})
                        self.items[item.get("id")] = item
                    if method == "turn/started":
                        self.turn_id = params["turn"]["id"]
                    if method == "serverRequest/resolved":
                        for key, request in list(self.inbox.items()):
                            if request["rpcId"] == params.get("requestId"):
                                self.inbox.pop(key, None)
                    await self.events.put(value)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            failure = e
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            await self.events.put({"method": "relay/disconnected", "error": str(failure)})
            self.inbox.clear()

    async def wait_event(self, names, turn_id=None):
        while True:
            event = await self.events.get()
            if event["method"] == "relay/disconnected":
                raise RuntimeError(event["error"])
            if event["method"] in names:
                params = event.get("params", {})
                if turn_id and params.get("turn", {}).get("id", params.get("turnId")) != turn_id:
                    continue
                return event

    async def close(self):
        self.inbox.clear()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 4)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader


REQUEST_METHODS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "files",
    "item/permissions/requestApproval": "permissions",
    "item/tool/requestUserInput": "input",
}


async def receive_request(client, value):
    method, params = value["method"], value.get("params", {})
    if method not in REQUEST_METHODS:
        await client.write(
            {
                "id": value["id"],
                "error": {"code": -32601, "message": "此控制台尚不支持该交互，请使用原客户端"},
            }
        )
        return
    key = uuid.uuid4().hex
    item = client.items.get(params.get("itemId"), {})
    client.inbox[key] = {
        "id": key,
        "rpcId": value["id"],
        "type": REQUEST_METHODS[method],
        "threadId": params.get("threadId"),
        "turnId": params.get("turnId"),
        "reason": params.get("reason"),
        "command": params.get("command"),
        "cwd": params.get("cwd"),
        "changes": item.get("changes", []),
        "grantRoot": params.get("grantRoot"),
        "permissions": params.get("permissions", {}),
        "questions": params.get("questions", []),
        "responding": False,
    }


def permission_settings(permission):
    return {
        "read-only": {"approvalPolicy": "never", "sandbox": "read-only"},
        "workspace": {"approvalPolicy": "on-request", "sandbox": "workspace-write"},
        "full": {"approvalPolicy": "never", "sandbox": "danger-full-access"},
    }[permission]
