"""Real deadlines against an isolated server, fake external queue and owned RPC turn."""

import argparse
import fcntl
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/input-smoke")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="relay-input-smoke-") as directory:
        work = Path(directory)
        home = work / "codex"
        sessions = home / "sessions"
        sessions.mkdir(parents=True)
        locks = home / "thread-writer-locks"
        locks.mkdir()
        tid = str(uuid.uuid4())
        writer = (locks / f"{tid}.lock").open("w")
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rollout = sessions / "questions.jsonl"
        rollout.write_text("")
        with sqlite3.connect(home / "state_5.sqlite") as db:
            db.execute(
                "CREATE TABLE threads(id TEXT,name TEXT,title TEXT,cwd TEXT,model TEXT,source TEXT,updated_at INTEGER,rollout_path TEXT,tokens_used INTEGER,archived INTEGER)"
            )
            db.execute(
                "INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    "测试问答",
                    "测试问答",
                    str(work),
                    "gpt-6-astra",
                    "cli",
                    1,
                    str(rollout),
                    0,
                    0,
                ),
            )
        queue = work / "queue.jsonl"
        binary = work / "codex-fixture"
        binary.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\nfrom pathlib import Path\n"
            "assert sys.argv[1] == 'queue'\n"
            "with Path(os.environ['QUESTION_QUEUE_CAPTURE']).open('a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "print('accepted')\n"
        )
        binary.chmod(0o700)
        config = work / "relay.toml"
        config.write_text("")
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("RELAY_") and k not in ("CODEX_HOME", "CODEX_BIN")
        }
        env.update(
            RELAY_CONFIG_FILE=str(config),
            RELAY_DATA_DIR=str(work / "data"),
            RELAY_DIST_DIR=str(args.dist.resolve()),
            RELAY_WORKSPACE_ROOT=str(work),
            RELAY_DEFAULT_CWD=str(work),
            CODEX_HOME=str(home),
            CODEX_BIN=str(binary),
            QUESTION_QUEUE_CAPTURE=str(queue),
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (args.output / "server.log").open("w") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "server", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                with httpx.Client(
                    base_url=base, headers={"X-Relay-Request": "1"}, timeout=10
                ) as api:
                    for _ in range(100):
                        if server.poll() is not None:
                            raise RuntimeError("Fixture server exited")
                        try:
                            if api.get("/api/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.1)
                    assert (
                        api.post(
                            "/api/login",
                            json={"password": (work / "data/access.txt").read_text().strip()},
                        ).status_code
                        == 200
                    )

                    def add_question(ident):
                        events = [
                            {"type": "event_msg", "payload": {"type": "task_started"}},
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "function_call",
                                    "name": "request_user_input_async",
                                    "call_id": ident,
                                    "arguments": json.dumps(
                                        {
                                            "questions": [
                                                {
                                                    "title": "选择方案",
                                                    "options": ["A", "B（推荐）"],
                                                },
                                                {"title": "补充信息"},
                                            ]
                                        }
                                    ),
                                },
                            },
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "function_call_output",
                                    "call_id": ident,
                                    "output": '{"accepted":true}',
                                },
                            },
                        ]
                        with rollout.open("a") as output:
                            for event in events:
                                output.write(
                                    json.dumps({"timestamp": "2026-01-01T00:00:00Z", **event})
                                    + "\n"
                                )
                        value = api.get(f"/api/threads/{tid}").json()["requests"]
                        assert len(value) == 1
                        return value[0]

                    manual = add_question("manual")
                    path = f"/api/threads/{tid}/requests/{manual['id']}"
                    answers = {q["id"]: ["手动回答"] for q in manual["questions"]}
                    assert api.post(path, json={"answers": answers}).status_code == 200
                    assert api.post(path, json={"answers": answers}).status_code == 409
                    assert len(queue.read_text().splitlines()) == 1

                    automatic = add_question("automatic")
                    path = f"/api/threads/{tid}/requests/{automatic['id']}"
                    assert 29 <= automatic["deadline"] - automatic["serverNow"] <= 30
                    assert (
                        api.post(
                            path + "/draft",
                            json={"answers": {automatic["questions"][0]["id"]: ["A"]}},
                        ).status_code
                        == 200
                    )
                    # No browser or polling is needed for the deadline itself.
                    started = time.monotonic()
                    while time.monotonic() - started < 36:
                        lines = queue.read_text().splitlines()
                        if len(lines) == 2:
                            break
                        time.sleep(0.25)
                    assert len(lines) == 2
                    elapsed = time.monotonic() - started
                    assert elapsed >= 28, elapsed
                    command = json.loads(lines[-1])
                    assert command[:3] == ["queue", "--thread", tid]
                    envelope = command[-1]
                    decoded = json.loads(envelope.split("\n", 1)[1].rsplit("\n", 1)[0])
                    assert decoded[0]["answer"] == "（30 秒未提交，系统自动回复）A"
                    assert "用户未填写" in decoded[1]["answer"]
                    assert (
                        api.post(
                            path,
                            json={"answers": {q["id"]: ["重复"] for q in automatic["questions"]}},
                        ).status_code
                        == 409
                    )
                    assert len(queue.read_text().splitlines()) == 2
                    assert not api.get(f"/api/threads/{tid}").json()["requests"]

                    # Exercise the managed task path over an actual JSON-RPC
                    # subprocess. A successful CLI enqueue is not evidence that
                    # an app-server turn received its answer.
                    writer.close()
                    rpc_capture = work / "rpc.jsonl"
                    binary.write_text(
                        "#!" + sys.executable + "\n"
                        "import json, os, sys\nfrom pathlib import Path\n"
                        "assert sys.argv[1:] == ['app-server', '--stdio']\n"
                        "home = Path(os.environ['CODEX_HOME'])\n"
                        "capture = Path(os.environ['QUESTION_QUEUE_CAPTURE']).with_name('rpc.jsonl')\n"
                        "for line in sys.stdin:\n"
                        "    request = json.loads(line)\n"
                        "    if 'id' not in request: continue\n"
                        "    method, params = request['method'], request.get('params', {})\n"
                        "    with capture.open('a') as f: f.write(json.dumps(request) + '\\n')\n"
                        "    result = {}\n"
                        "    if method == 'thread/resume':\n"
                        "        result = {'thread': {'id':params['threadId']}, 'model':'gpt-6-astra'}\n"
                        "    elif method == 'turn/start':\n"
                        "        result = {'turn': {'id':'fixture-turn'}}\n"
                        "    elif method == 'turn/steer':\n"
                        "        assert params['expectedTurnId'] == 'fixture-turn'\n"
                        "        text = params['input'][0]['text']\n"
                        "        event = {'type':'event_msg','payload':{'type':'user_message','message':text}}\n"
                        "        with (home/'sessions/questions.jsonl').open('a') as f:\n"
                        "            f.write(json.dumps(event,ensure_ascii=False) + '\\n')\n"
                        "        result = {'turnId':'fixture-turn'}\n"
                        "    elif method != 'initialize': raise RuntimeError(method)\n"
                        "    print(json.dumps({'id':request['id'],'result':result}),flush=True)\n"
                    )
                    started_job = api.post(
                        f"/api/threads/{tid}/messages", json={"text": "启动隔离问答测试"}
                    )
                    assert started_job.status_code == 200, started_job.text
                    for _ in range(100):
                        if rpc_capture.exists() and '"turn/start"' in rpc_capture.read_text():
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Managed fixture turn did not start")
                    managed_manual = add_question("managed-manual")
                    path = f"/api/threads/{tid}/requests/{managed_manual['id']}"
                    answers = {q["id"]: ["手动选择"] for q in managed_manual["questions"]}
                    assert api.post(path, json={"answers": answers}).status_code == 200
                    assert api.post(path, json={"answers": answers}).status_code == 409
                    assert "手动选择" in rollout.read_text()
                    managed_auto = add_question("managed-auto")
                    path = f"/api/threads/{tid}/requests/{managed_auto['id']}"
                    assert (
                        api.post(
                            path + "/draft",
                            json={"answers": {managed_auto["questions"][0]["id"]: ["A"]}},
                        ).status_code
                        == 200
                    )
                    managed_started = time.monotonic()
                    while time.monotonic() - managed_started < 36:
                        calls = [json.loads(line) for line in rpc_capture.read_text().splitlines()]
                        steers = [call for call in calls if call["method"] == "turn/steer"]
                        if len(steers) == 2 and "系统自动回复" in rollout.read_text():
                            break
                        time.sleep(0.25)
                    assert len(steers) == 2
                    managed_elapsed = time.monotonic() - managed_started
                    assert managed_elapsed >= 28
                    managed_text = steers[-1]["params"]["input"][0]["text"]
                    assert "系统自动回复）A" in managed_text
                    assert "用户未填写" in managed_text
                    assert any(
                        json.loads(line).get("payload", {}).get("message") == managed_text
                        for line in rollout.read_text().splitlines()
                    )
                    assert len(queue.read_text().splitlines()) == 2
                    assert not api.get(f"/api/threads/{tid}").json()["requests"]
                    result = {
                        "passed": True,
                        "manualSubmittedOnce": True,
                        "automaticSubmittedOnce": True,
                        "withoutBrowser": True,
                        "elapsedSeconds": round(elapsed, 2),
                        "managedManualReceived": True,
                        "managedAutomaticReceived": True,
                        "managedReplyEvents": len(steers),
                        "managedElapsedSeconds": round(managed_elapsed, 2),
                    }
                    (args.output / "results.json").write_text(json.dumps(result, indent=2))
                    print(json.dumps(result), flush=True)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                writer.close()


if __name__ == "__main__":
    main()
