"""Exercise the delivered composer against synthetic API/SSE events, never a real task."""

import argparse
import io
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def check_queue_composer(page, screenshot=None):
    items, submitted, stops = [], [], []
    state = {"status": "running", "failure": None}
    raw = io.BytesIO()
    Image.new("RGB", (40, 30), "green").save(raw, "PNG")
    asset = {
        "id": "a" * 32,
        "name": "reference.png",
        "kind": "image",
        "mime": "image/png",
        "width": 40,
        "height": 30,
        "size": len(raw.getvalue()),
        "url": "/api/media/" + "a" * 32,
        "thumbnailUrl": "/api/media/" + "a" * 32,
    }

    def detail():
        return {
            "id": "queue-fixture",
            "title": "追加消息测试",
            "cwd": "/tmp/queue-fixture",
            "status": state["status"],
            "managed": True,
            "modelSwitchAllowed": True,
            "model": "gpt-6-astra",
            "reasoningEffort": "high",
            "tokens": 0,
            "messages": [
                {
                    "id": "example",
                    "role": "assistant",
                    "text": "正在处理当前请求。",
                    "at": "2026-01-01T00:00:00Z",
                }
            ],
            "notes": [],
            "commandRecords": [],
            "activities": [],
            "requests": [],
            "pendingMessages": list(items),
            "pendingCount": len(items),
            "executionSettings": {"collaboration": "default", "permission": "workspace"},
            "goal": None,
            "history": {"mode": "recent", "hasMore": False, "total": 1},
        }

    def route_api(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == "/api/events":
            event = {"threads": [detail()], "selected": detail()}
            route.fulfill(
                content_type="text/event-stream",
                body="retry: 100\ndata: " + json.dumps(event) + "\n\n",
            )
        elif path == "/api/threads":
            route.fulfill(json={"threads": [detail()], "defaultCwd": "/tmp/queue-fixture"})
        elif path.endswith("/messages"):
            payload = request.post_data_json
            submitted.append(payload)
            item = {
                "id": str(len(submitted)),
                "text": payload["text"] or "请分析这些图片。",
                "created": time.time(),
                "model": payload["model"],
                "effort": payload["effort"],
                "attachments": [asset["name"]] if payload.get("attachments") else [],
            }
            items.append(item)
            route.fulfill(json={"kind": "queued", "queueId": item["id"], "pendingMessage": item})
        elif path.endswith("/cancel"):
            ident = path.split("/")[-2]
            failure = state.pop("failure", None)
            if failure == 500:
                route.fulfill(status=500, json={"detail": "删除失败，请重试"})
                return
            items[:] = [q for q in items if q["id"] != ident]
            route.fulfill(
                status=failure or 200,
                json={"detail": "该消息已开始执行，无法删除"} if failure else {"ok": True},
            )
        elif path.endswith("/stop"):
            stops.append(request.post_data_json)
            state["status"] = "interrupted"
            items.clear()
            route.fulfill(json={"message": "当前回复已停止"})
        elif path.startswith("/api/media"):
            if request.method == "POST":
                route.fulfill(json=asset)
            else:
                route.fulfill(content_type="image/png", body=raw.getvalue())
        elif path.startswith("/api/threads/"):
            route.fulfill(json=detail())
        else:
            route.fulfill(status=404, json={"detail": "Unexpected fixture request"})

    pattern = "**/api/**"
    page.route(pattern, route_api)
    other_context = page.context.browser.new_context(viewport={"width": 390, "height": 844})
    other = other_context.new_page()
    other.route(pattern, route_api)
    try:
        page.reload()
        field = page.get_by_label("向 Codex 发送消息", exact=True)
        stop = page.get_by_role("button", name="停止回复", exact=True)
        send = page.get_by_role("button", name="发送消息", exact=True)
        rows = page.locator(".message-queue li")
        ready = page.get_by_role("button", name="本条指令模型", exact=True)
        expect(stop).to_be_enabled()
        field.fill("  \n  ")
        expect(stop).to_be_visible()
        field.fill("第一条追加")
        expect(send).to_be_enabled()
        expect(send).to_have_attribute("title", "发送并加入队列")
        expect(stop).to_have_count(0)
        send.click()
        expect(rows).to_have_count(1)
        expect(stop).to_be_enabled()
        field.fill("第二条追加")
        field.press("Enter")
        expect(rows).to_have_count(2)
        expect(ready).to_be_enabled()
        assert submitted[0]["model"] == "gpt-6-astra"
        assert submitted[0]["effort"] in ("high", "max")
        assert not stops
        page.reload()
        expect(rows).to_have_count(2)
        other.goto(page.url)
        expect(other.locator(".message-queue li")).to_have_count(2)
        other.get_by_role("button", name="删除排队消息：第一条追加", exact=True).click()
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text("第二条追加")
        state["failure"] = 500
        rows.get_by_role("button").click()
        expect(page.get_by_role("alert")).to_contain_text("删除失败")
        # A reconnect must not dismiss an unrelated action error before it is read.
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/events"):
            pass
        expect(page.get_by_role("alert")).to_contain_text("删除失败")
        expect(rows).to_have_count(1)
        expect(rows.get_by_role("button")).to_be_enabled()
        rows.get_by_role("button").click()
        expect(rows).to_have_count(0)
        expect(page.get_by_role("alert")).to_have_count(0)
        field.fill("/goal ")
        expect(page.locator(".command-token")).to_have_count(1)
        expect(send).to_be_enabled()
        field.fill("检查排队目标")
        send.click()
        expect(rows).to_contain_text("/goal 检查排队目标")
        expect(ready).to_be_enabled()
        page.locator(".composer input[type=file]").set_input_files(
            {"name": asset["name"], "mimeType": "image/png", "buffer": raw.getvalue()}
        )
        expect(page.locator(".composer-attachments")).to_contain_text("已就绪")
        expect(send).to_be_enabled()
        send.click()
        expect(rows).to_have_count(2)
        expect(rows.last).to_contain_text("reference.png")
        assert submitted[-1]["attachments"] == [asset["id"]]
        assert submitted[-1]["text"] == ""
        expect(stop).to_be_enabled()
        stop.click()
        expect(rows).to_have_count(0)
        expect(send).to_be_disabled()
        assert len(stops) == 1
        state["status"] = "running"
        expect(stop).to_be_enabled()
        field.fill("恰好开始执行")
        send.click()
        expect(rows).to_have_count(1)
        state["failure"] = 409
        rows.get_by_role("button").click()
        expect(page.get_by_role("alert")).to_contain_text("已开始执行")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/events"):
            pass
        expect(page.get_by_role("alert")).to_contain_text("已开始执行")
        expect(rows).to_have_count(0)
        for i in range(4):
            expect(ready).to_be_enabled()
            field.fill(f"待发送 {i + 1}：" + "较长的追加需求内容。" * 8)
            send.click()
            expect(rows).to_have_count(i + 1)
        field.fill("还可以继续输入下一条消息")
        expect(send).to_be_enabled()
        box = page.locator(".message-queue ol").bounding_box()
        assert box["height"] <= 121
        button = rows.last.get_by_role("button").bounding_box()
        assert button["width"] >= 40 and button["height"] >= 40
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        send_box = send.bounding_box()
        assert send_box["y"] + send_box["height"] <= page.viewport_size["height"]
        assert send.evaluate(
            "el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.bottom - 2)); }"
        )
        if screenshot:
            page.screenshot(path=str(screenshot))
        return {
            "toggleAndEnter": True,
            "deleteAndRetry": True,
            "refreshAndCrossDevice": True,
            "commandAndAttachment": True,
            "dispatchConflict": True,
            "stopPreserved": True,
            "boundedQueue": True,
            "capturedMessages": len(submitted),
        }
    finally:
        other_context.close()
        page.unroute(pattern, route_api)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cdp")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp) if args.cdp else p.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844), (320, 568)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                try:
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(args.url)
                    result = check_queue_composer(page, args.output / f"{width}x{height}.png")
                    assert not errors, errors
                    results.append({"viewport": [width, height], **result, "pageErrors": errors})
                finally:
                    context.close()
        finally:
            if not args.cdp:
                browser.close()
    (args.output / "results.json").write_text(
        json.dumps({"passed": True, "results": results}, ensure_ascii=False, indent=2)
    )
    print("Queue browser checks passed using synthetic requests; no real task was submitted.")


if __name__ == "__main__":
    main()
