"""Isolated UI/API acceptance: creates fixtures, starts and stops its own server.

Never calls Codex or reads existing login/session data. Default launches a private
Chromium; --cdp reuses a browser with dedicated contexts and never closes it.
"""

import argparse
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
from PIL import Image
from browser_slash_paths import check_slash_composer, check_slash_upload_readiness
from browser_queue import check_queue_composer
from browser_goal_clear import check_goal_clear
from browser_sessions import check_sessions
from browser_typing import check_typing
from browser_user_inputs import check_user_inputs
from browser_dialog_keyboard import check_dialog_keyboard
from browser_message_rendering import check_message_rendering
from browser_reply_scroll import check_reply_scroll
from browser_login import check_login_cookies
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/browser-smoke")
    parser.add_argument("--cdp")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="relay-smoke-") as temporary:
        work = Path(temporary)
        home = work / "codex"
        home.mkdir()
        (work / "子目录").mkdir()
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
            # Only binary-presence validation is used; no inference request is sent.
            CODEX_BIN=sys.executable,
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (work / "server.log").open("w") as log:
            process = subprocess.Popen(
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
                    for _ in range(150):
                        if process.poll() is not None:
                            raise RuntimeError("Isolated server exited before startup")
                        try:
                            if api.get("/api/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("Isolated server startup timed out")
                    password = (work / "data/access.txt").read_text().strip()
                    assert api.get("/api/threads").status_code == 401
                    assert api.post("/api/login", json={"password": password}).status_code == 200
                    response = api.get("/api/threads")
                    response.raise_for_status()
                    assert response.json()["threads"] == []
                    assert [m["id"] for m in response.json()["clientConfig"]["models"]] == [
                        "gpt-5.6-sol",
                        "gpt-6-astra",
                    ]
                    assert api.get("/api/directories").json()["path"] == str(work)
                    assert api.get("/api/directories", params={"path": "/etc"}).status_code == 400
                    results.append(
                        "fresh install, login, default models, portable directory boundary"
                    )
                    tid = str(uuid.uuid4())
                    sessions = home / "sessions"
                    sessions.mkdir()
                    rollout = sessions / "smoke.jsonl"
                    messages = []
                    for i in range(16):
                        body = f"测试消息 {i}"
                        if i == 15:
                            body = (
                                "| 项目 | 数值 |\n| --- | --- |\n| 测试 | 42 |\n\n$$x^2+y^2=z^2$$"
                            )
                        messages.append(
                            {
                                "timestamp": f"2026-01-01T00:00:{i:02d}Z",
                                "type": "event_msg",
                                "payload": {
                                    "type": "agent_message" if i % 2 else "user_message",
                                    "message": body,
                                },
                            }
                        )
                    rollout.write_text(
                        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
                    )
                    with sqlite3.connect(home / "state_5.sqlite") as db:
                        db.execute(
                            "CREATE TABLE threads(id TEXT,name TEXT,title TEXT,cwd TEXT,model TEXT,source TEXT,updated_at INTEGER,rollout_path TEXT,tokens_used INTEGER,archived INTEGER)"
                        )
                        db.execute(
                            "INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (
                                tid,
                                "界面验收",
                                "界面验收",
                                str(work),
                                "gpt-6-astra",
                                "cli",
                                1,
                                str(rollout),
                                0,
                                0,
                            ),
                        )
                    assert len(api.get(f"/api/threads/{tid}").json()["messages"]) == 10
                    assert len(api.get(f"/api/threads/{tid}?history=all").json()["messages"]) == 16
                    raw = io.BytesIO()
                    Image.new("RGB", (60, 40), "#28764d").save(raw, "PNG")
                    for name, data in [
                        ("smoke.png", raw.getvalue()),
                        ("smoke.pdf", b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"),
                    ]:
                        response = api.post(
                            "/api/media", params={"name": name, "thread": tid}, content=data
                        )
                        response.raise_for_status()
                        asset = response.json()
                        download = api.get(asset["downloadUrl"])
                        assert download.content == data
                        assert download.headers["content-disposition"].startswith("attachment;")
                    results.append(
                        "recent/all history, authenticated PNG/PDF upload and original download"
                    )
                    with api.stream("GET", "/api/events", params={"thread": tid}) as stream:
                        stream.raise_for_status()
                        for line in stream.iter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
                                assert event["selected"]["id"] == tid
                                assert len(event["selected"]["messages"]) == 10
                                break
                        else:
                            raise AssertionError("No SSE snapshot received")
                    results.append("authenticated SSE delivers the current thread snapshot")
                    with sync_playwright() as playwright:
                        browser = (
                            playwright.chromium.connect_over_cdp(args.cdp)
                            if args.cdp
                            else playwright.chromium.launch()
                        )
                        try:
                            check_login_cookies(browser, base, password)
                            results.append(
                                "login: stale secure cookie, reload, logout, blocked cookie feedback"
                            )
                            for width, height in [
                                (1440, 900),
                                (768, 1024),
                                (390, 844),
                                (320, 568),
                                (568, 320),
                            ]:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height}
                                )
                                try:
                                    page = context.new_page()
                                    errors = []
                                    page.on("pageerror", lambda error: errors.append(str(error)))
                                    page.goto(base)
                                    page.get_by_label("访问口令", exact=True).fill(password)
                                    page.get_by_role("button", name="进入工作台").click()
                                    expect(
                                        page.get_by_role("button", name="发送消息", exact=True)
                                    ).to_be_visible()
                                    expect(page.locator("article.message")).to_have_count(10)
                                    expect(page.locator(".message table")).to_be_visible()
                                    expect(page.locator(".katex").first).to_be_visible()
                                    composer = page.locator(".composer textarea")
                                    assert "计划" not in (
                                        composer.get_attribute("placeholder") or ""
                                    )
                                    page.locator(".composer .model-trigger").click()
                                    page.get_by_role(
                                        "menuitem", name="GPT-6-Astra", exact=True
                                    ).click()
                                    page.get_by_role(
                                        "menuitemradio", name="max", exact=True
                                    ).click()
                                    expect(
                                        page.locator(".composer .model-trigger")
                                    ).to_contain_text("max")
                                    file_input = page.locator(".composer input[type=file]")
                                    file_input.set_input_files(
                                        {
                                            "name": "browser.png",
                                            "mimeType": "image/png",
                                            "buffer": raw.getvalue(),
                                        }
                                    )
                                    expect(page.locator(".composer-attachments")).to_be_visible()
                                    assert (
                                        page.locator(".composer .composer-attachments").count() == 0
                                    )
                                    assert page.evaluate(
                                        "document.documentElement.scrollWidth <= window.innerWidth"
                                    )
                                    assert not errors, errors
                                    check_slash_composer(page)
                                    page.screenshot(path=str(args.output / f"{width}x{height}.png"))
                                    check_queue_composer(
                                        page, args.output / f"queue-{width}x{height}.png"
                                    )
                                    check_goal_clear(page, "clear")
                                    assert not errors, errors
                                    if width in (1440, 390):
                                        check_sessions(page)
                                        results.append(
                                            f"session isolation {width}x{height}: switching, drafts, attachments, delayed replies, queues, goals, approvals, history and task creation"
                                        )
                                    results.append(
                                        f"browser {width}x{height}: login, math/table, model effort, attachment tray, no overflow/errors"
                                    )
                                except Exception:
                                    page.screenshot(
                                        path=str(args.output / f"failure-{width}x{height}.png")
                                    )
                                    (args.output / f"failure-{width}x{height}.json").write_text(
                                        json.dumps(
                                            {
                                                "viewport": [width, height],
                                                "pageErrors": errors,
                                                "composer": page.locator(
                                                    ".composer textarea"
                                                ).input_value()
                                                if page.locator(".composer textarea").count()
                                                else None,
                                                "attachments": page.locator(
                                                    ".attachment-state"
                                                ).all_text_contents(),
                                                "alerts": page.get_by_role(
                                                    "alert"
                                                ).all_text_contents(),
                                            },
                                            ensure_ascii=False,
                                            indent=2,
                                        )
                                    )
                                    raise
                                finally:
                                    context.close()
                            context = browser.new_context(viewport={"width": 1440, "height": 900})
                            try:
                                page = context.new_page()

                                def configured_threads(route):
                                    response = route.fetch()
                                    if response.status != 200:
                                        route.fulfill(response=response)
                                        return
                                    payload = response.json()
                                    payload["clientConfig"] = {
                                        "models": [{"id": "custom-model", "name": "Custom Model"}],
                                        "defaultModel": "custom-model",
                                        "defaultEffort": "high",
                                        "efforts": ["low", "medium", "high", "xhigh", "max"],
                                    }
                                    route.fulfill(response=response, json=payload)

                                page.route("**/api/threads", configured_threads)
                                page.goto(base)
                                page.get_by_label("访问口令", exact=True).fill(password)
                                page.get_by_role("button", name="进入工作台").click()
                                expect(page.locator(".composer .model-trigger")).to_contain_text(
                                    "Custom Model"
                                )
                                page.locator(".composer .model-trigger").click()
                                page.get_by_role(
                                    "menuitem", name="Custom Model", exact=True
                                ).click()
                                page.get_by_role("menuitemradio", name="high", exact=True).click()
                                submitted = []

                                def capture_message(route):
                                    submitted.append(route.request.post_data_json)
                                    route.fulfill(json={"kind": "queued"})

                                page.route("**/api/threads/*/messages", capture_message)
                                page.locator(".composer textarea").fill("验证自定义模型配置")
                                page.get_by_role("button", name="发送消息", exact=True).click()
                                expect(page.locator(".composer textarea")).to_have_value("")
                                assert (
                                    submitted[0]["model"] == "custom-model"
                                    and submitted[0]["effort"] == "high"
                                )
                                assert submitted[0]["collaboration"] == "default"
                                results.append(
                                    "custom model configuration reaches send payload with selected effort and default execution mode"
                                )
                            finally:
                                context.close()
                            for width, height in [(1440, 900), (390, 844)]:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height}
                                )
                                try:
                                    page = context.new_page()
                                    page.goto(base)
                                    check_slash_upload_readiness(page)
                                    results.append(
                                        f"delayed upload {width}x{height}: slash path waits for send readiness and preserves the attachment"
                                    )
                                finally:
                                    context.close()
                            for width, height in [(1440, 900), (390, 844)]:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height}
                                )
                                try:
                                    page = context.new_page()
                                    page.goto(base)
                                    check_message_rendering(page)
                                    results.append(
                                        f"message rendering {width}x{height}: literal user terminal text, assistant Markdown/math and attachments"
                                    )
                                finally:
                                    context.close()
                            for width, height in [(1440, 900), (390, 844)]:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height}
                                )
                                try:
                                    page = context.new_page()
                                    page.goto(base)
                                    check_reply_scroll(page)
                                    results.append(
                                        f"reply scroll {width}x{height}: answer start, stream, manual reading, resize, history and sessions"
                                    )
                                finally:
                                    context.close()
                            for width, height in [(1440, 900), (390, 844)]:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height}
                                )
                                try:
                                    page = context.new_page()
                                    page.goto(base)
                                    check_user_inputs(page)
                                    results.append(
                                        f"questions {width}x{height}: options, freeform answers, draft sync and countdown"
                                    )
                                finally:
                                    context.close()
                            for width, height in [(1440, 900), (390, 844)]:
                                context = browser.new_context(
                                    viewport={"width": width, "height": height}
                                )
                                try:
                                    page = context.new_page()
                                    page.goto(base)
                                    typing = check_typing(page)
                                    (args.output / f"typing-{width}.json").write_text(
                                        json.dumps(typing, ensure_ascii=False, indent=2)
                                    )
                                    results.append(
                                        f"typing {width}x{height}: rich recent/full history, unchanged snapshots, stream and attachment updates"
                                    )
                                finally:
                                    context.close()
                            context = browser.new_context(
                                viewport={"width": 390, "height": 844},
                                is_mobile=True,
                                has_touch=True,
                            )
                            try:
                                page = context.new_page()
                                page.goto(base)
                                dialogs = check_dialog_keyboard(page)
                                (args.output / "dialog-keyboard.json").write_text(
                                    json.dumps(dialogs, ensure_ascii=False, indent=2)
                                )
                                results.append(
                                    "mobile viewport: pinch zoom preserves layout, keyboard edits and fractional resize noise"
                                )
                                results.append(
                                    "mobile dialogs: repeated keyboard, nested dialogs, focus, overlays and streaming"
                                )
                            finally:
                                context.close()
                        finally:
                            if not args.cdp:
                                browser.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        (args.output / "results.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "checks": results,
                    "isolatedServerStopped": process.poll() is not None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            f"Passed {len(results)} checks; isolated service stopped. Report: {args.output / 'results.json'}"
        )


if __name__ == "__main__":
    main()
