"""Measure real keystrokes on rich chat history using synthetic API data only."""

import argparse
import base64
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from PIL import Image


def check_typing(page, enforce=True):
    messages = [
        {
            "id": f"rich-{i}",
            "role": "assistant",
            "at": "2026-01-01T00:00:00Z",
            "text": f"记录 {i}\n\n"
            + (
                "| 项目 | 数值 |\n| --- | --- |\n| 测试 | $x^2+y^2=z^2$ |\n\n"
                "$$\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}$$\n\n"
            )
            * 5,
        }
        for i in range(60)
    ]
    full = {"value": False}

    def detail():
        return {
            "id": "typing",
            "title": "输入性能验收",
            "cwd": "/tmp/typing",
            "status": "running",
            "model": "gpt-6-astra",
            "reasoningEffort": "high",
            "modelSwitchAllowed": True,
            "tokens": 0,
            "goal": None,
            "messages": messages if full["value"] else messages[-10:],
            "notes": [],
            "activities": [],
            "requests": [],
            "commandRecords": [],
            "pendingMessages": [],
            "pendingCount": 0,
            "history": {
                "mode": "all" if full["value"] else "recent",
                "hasMore": not full["value"],
                "total": len(messages),
            },
        }

    def route_api(route):
        url = urlsplit(route.request.url)
        if url.path == "/api/threads":
            route.fulfill(json={"threads": [detail()], "defaultCwd": "/tmp"})
        elif url.path == "/api/threads/typing":
            if parse_qs(url.query).get("history") == ["all"]:
                full["value"] = True
            route.fulfill(json=detail())
        else:
            route.fulfill(status=404, json={"detail": "Unexpected fixture request"})

    page.route("**/api/**", route_api)
    page.add_init_script("""localStorage.setItem('relay-thread', 'typing');
      window.EventSource = class {
        constructor() { window.typingStream = this; }
        addEventListener() {} close() {}
      };
    """)
    page.reload()
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    expect(page.locator("article.message")).to_have_count(10)
    result = {}
    page.typing_probe = result
    metrics = page.context.new_cdp_session(page)
    metrics.send("Performance.enable")
    for mode in ["recent", "all"]:
        if mode == "all":
            page.get_by_role("button", name="向上滚动加载更早记录", exact=True).click()
            expect(page.locator("article.message")).to_have_count(60)
        field.fill("")
        field.focus()
        page.evaluate("""() => {
          window.typingSamples = [];
          window.originalTable = document.querySelector('.message table');
          const field = document.querySelector('.composer textarea');
          if (window.typingListener) field.removeEventListener('input', window.typingListener, true);
          window.typingListener = () => {
            const start = performance.now();
            requestAnimationFrame(() => requestAnimationFrame(() => window.typingSamples.push(performance.now() - start)));
          };
          field.addEventListener('input', window.typingListener, true);
        }""")
        # Actual trusted keyboard input; do not bypass React with fill() for measurements.
        before = {m["name"]: m["value"] for m in metrics.send("Performance.getMetrics")["metrics"]}
        field.press_sequentially("typingtest12", delay=25)
        page.wait_for_function("window.typingSamples.length === 12")
        expect(field).to_have_value("typingtest12")
        samples = page.evaluate("window.typingSamples")
        stable = page.evaluate("window.originalTable === document.querySelector('.message table')")
        result[mode] = {
            "medianMs": sorted(samples)[6],
            "maxMs": max(samples),
            "unchangedHistoryDom": stable,
            "keys": len(samples),
        }
        after = {m["name"]: m["value"] for m in metrics.send("Performance.getMetrics")["metrics"]}
        result[mode]["workMs"] = {
            name: (after[name] - before[name]) * 1000
            for name in ["ScriptDuration", "LayoutDuration", "RecalcStyleDuration"]
        }
        if enforce:
            assert stable, "Typing recreated the existing rich history DOM"
            assert result[mode]["medianMs"] < 200, result[mode]

    # Identical SSE snapshots keep rich history intact, but real content changes still render.
    original = page.locator(".message table").first.element_handle()
    page.evaluate(
        "payload => window.typingStream.onmessage({data:JSON.stringify(payload)})",
        {"threads": [detail()], "selected": detail()},
    )
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    if enforce:
        assert original.evaluate("el => el === document.querySelector('.message table')")
    messages[-1]["text"] = "更新后的回复\n\n| 新值 | 结果 |\n| --- | --- |\n| 公式 | $a^3$ |"
    page.evaluate(
        "payload => window.typingStream.onmessage({data:JSON.stringify(payload)})",
        {"threads": [detail()], "selected": detail()},
    )
    expect(page.locator("article.message").last).to_contain_text("更新后的回复")
    expect(page.locator("article.message").last.locator("table")).to_contain_text("新值")
    expect(page.locator("article.message").last.locator(".katex")).to_be_visible()
    expect(field).to_have_value("typingtest12")
    result["streamUpdatesPreserved"] = True
    raw = io.BytesIO()
    Image.new("RGB", (40, 30), "green").save(raw, "PNG")
    messages[-1]["images"] = [
        {
            "id": "preview",
            "name": "old.png",
            "url": "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode(),
        }
    ]
    messages[-1]["files"] = [
        {"id": "document", "name": "old.txt", "url": "/api/media/document", "size": 12}
    ]
    page.evaluate(
        "payload => window.typingStream.onmessage({data:JSON.stringify(payload)})",
        {"threads": [detail()], "selected": detail()},
    )
    expect(page.get_by_role("button", name="预览 old.png", exact=True)).to_be_visible()
    messages[-1]["images"][0]["name"] = "updated.png"
    messages[-1]["files"][0].update(
        name="updated.txt", downloadUrl="/api/media/document?download=1"
    )
    page.evaluate(
        "payload => window.typingStream.onmessage({data:JSON.stringify(payload)})",
        {"threads": [detail()], "selected": detail()},
    )
    expect(page.get_by_role("link", name="下载 updated.txt", exact=True)).to_have_attribute(
        "href", "/api/media/document?download=1"
    )
    page.get_by_role("button", name="预览 updated.png", exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("updated.png")
    page.get_by_role("button", name="关闭图片预览", exact=True).click()
    expect(field).to_have_value("typingtest12")
    result["attachmentUpdatesAndPreviewPreserved"] = True
    result["stability"] = check_typing_stability(page, detail(), enforce=enforce)
    result["inputFocus"] = check_input_focus(page)
    metrics.detach()
    return result


def check_input_focus(page):
    """Keep typing out of the global shortcut and deferred drawer-focus paths."""
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    dialog = page.locator(".task-dialog")
    field.fill("")
    field.press_sequentially("next message", delay=15)
    expect(field).to_have_value("next message")
    expect(dialog).to_have_count(0)

    page.locator(".composer .model-trigger").click()
    page.keyboard.press("n")
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(dialog).to_have_count(0)
    expect(page.locator(".model-menu")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".model-menu")).to_have_count(0)

    # The shortcut must still work outside inputs, menus and composition.
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("n")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    page.evaluate("""() => {
      document.activeElement.blur();
      for (const options of [{isComposing: true}, {keyCode: 229}, {handled: true}]) {
        const event = new KeyboardEvent('keydown', {key: 'n', bubbles: true, cancelable: true, ...options});
        if (options.handled) event.preventDefault();
        document.body.dispatchEvent(event);
      }
    }""")
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(dialog).to_have_count(0)

    drawers = 0
    for trigger, close in [
        (".mobile-menu", "关闭会话列表"),
        (".conversation-header .activity-toggle", "关闭执行详情"),
    ]:
        opener = page.locator(trigger)
        if not opener.is_visible():
            continue
        opener.click()
        # Focus as soon as the drawer is removed, before its deferred close
        # callback. This deterministically exercises a fast click into the
        # composer rather than relying on the machine's event-loop timing.
        page.evaluate("""() => {
          const drawer = document.querySelector('.responsive-drawer');
          const input = document.querySelector('.composer textarea');
          window.inputFocusProbe = {done: false, losses: 0};
          new MutationObserver((_, observer) => {
            if (drawer.isConnected) return;
            observer.disconnect();
            input.focus();
            const end = performance.now() + 250;
            const sample = () => {
              if (document.activeElement !== input) inputFocusProbe.losses++;
              if (performance.now() < end) requestAnimationFrame(sample);
              else inputFocusProbe.done = true;
            };
            requestAnimationFrame(sample);
          }).observe(document.body, {childList: true, subtree: true});
        }""")
        page.get_by_role("button", name=close, exact=True).click()
        page.wait_for_function("window.inputFocusProbe.done")
        assert page.evaluate("inputFocusProbe.losses") == 0, trigger
        expect(field).to_be_focused()
        field.fill("")
        page.keyboard.type("next message")
        expect(field).to_have_value("next message")
        expect(dialog).to_have_count(0)

        # Without a new focus choice, keyboard users still return to the opener.
        opener.click()
        page.keyboard.press("Escape")
        expect(opener).to_be_focused()
        drawers += 1
    return {"shortcutIsolation": True, "drawerFocusChecks": drawers}


def check_typing_stability(page, detail, *, enforce=True):
    """Exercise typing and IME while identical snapshots and viewport resizes arrive."""
    page.add_init_script("""window.stabilityProbe = {writes: 0, redundantWrites: 0, errors: []};
      window.addEventListener('error', e => stabilityProbe.errors.push(e.message));
      const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop');
      Object.defineProperty(Element.prototype, 'scrollTop', {...descriptor, set(value) {
        if (this.classList.contains('messages')) {
          stabilityProbe.writes++;
          if (Math.abs(value - descriptor.get.call(this)) < 1) stabilityProbe.redundantWrites++;
        }
        descriptor.set.call(this, value);
      }});
    """)
    # Start with automatic reply positioning enabled, rather than the paused
    # reading state left by history loading and the attachment preview above.
    page.reload()
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    expect(page.locator("article.message")).to_have_count(len(detail["messages"]))
    field.focus()
    page.evaluate("async () => { await document.fonts.ready; }")
    page.evaluate(
        """payload => {
          window.stabilityPayload = payload;
          const app = document.querySelector('.app-shell');
          const messages = document.querySelector('.messages');
          const input = document.querySelector('.composer textarea');
          const table = document.querySelector('.message table');
          stabilityProbe.samples = 0;
          stabilityProbe.missingFrames = 0;
          stabilityProbe.domReplacements = 0;
          stabilityProbe.focusLosses = 0;
          stabilityProbe.writes = 0;
          stabilityProbe.redundantWrites = 0;
          window.stabilityActive = true;
          const sample = () => {
            if (!window.stabilityActive) return;
            stabilityProbe.samples++;
            if (!app.isConnected || !messages.isConnected || messages.clientHeight < 20)
              stabilityProbe.missingFrames++;
            if (input !== document.querySelector('.composer textarea') ||
                table !== document.querySelector('.message table')) stabilityProbe.domReplacements++;
            if (document.activeElement !== input) stabilityProbe.focusLosses++;
            requestAnimationFrame(sample);
          };
          requestAnimationFrame(sample);
          window.stabilityInterval = setInterval(() =>
            window.typingStream.onmessage({data: JSON.stringify(window.stabilityPayload)}), 60);
        }""",
        {"threads": [detail], "selected": detail},
    )
    cdp = page.context.new_cdp_session(page)
    frames = []

    def capture(event):
        frames.append(event["data"])
        cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    cdp.on("Page.screencastFrame", capture)
    cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 70, "everyNthFrame": 3})
    try:
        text = "continuous typing while receiving live updates " * 2
        field.press_sequentially(text, delay=20)
        expect(field).to_have_value(text)
        # Native composition, rather than replacing textarea text from JavaScript.
        cdp.send(
            "Input.imeSetComposition", {"text": "zhongwen", "selectionStart": 8, "selectionEnd": 8}
        )
        cdp.send(
            "Input.imeSetComposition", {"text": "中文", "selectionStart": 2, "selectionEnd": 2}
        )
        cdp.send("Input.insertText", {"text": "中文"})
        expect(field).to_have_value(text + "中文")
        size = page.viewport_size.copy()
        page.set_viewport_size({**size, "height": size["height"] - 160})
        field.press("Shift+Enter")
        field.press_sequentially("next line", delay=25)
        page.set_viewport_size(size)
        expect(field).to_have_value(text + "中文\nnext line")

        # Deleting to empty swaps send/stop controls while a reply is running.
        # Exercise native edits across line breaks, selection deletion, and IME
        # cancellation as well as insertion; fill() bypasses these event paths.
        field.press("Control+A")
        field.press("Delete")
        expect(field).to_have_value("")
        for _ in range(3):
            field.press_sequentially("edit next", delay=15)
            field.press("Shift+Enter")
            field.press_sequentially("line", delay=15)
            for _ in range(14):
                page.keyboard.press("Backspace")
            expect(field).to_have_value("")
        for composition in ["zhongwen", "中文", "中", ""]:
            cdp.send(
                "Input.imeSetComposition",
                {
                    "text": composition,
                    "selectionStart": len(composition),
                    "selectionEnd": len(composition),
                },
            )
        cdp.send("Input.insertText", {"text": "中文删除测试"})
        for _ in range(6):
            page.keyboard.press("Backspace")
        expect(field).to_have_value("")

        # A changing reply must remain visible while the draft is being erased.
        page.evaluate(r"""() => {
          clearInterval(window.stabilityInterval);
          window.stabilityInterval = setInterval(() => {
            stabilityPayload.selected.messages.at(-1).text += '\n\n新的回复片段';
            typingStream.onmessage({data: JSON.stringify(stabilityPayload)});
          }, 100);
        }""")
        field.press_sequentially("delete during streaming", delay=15)
        for _ in range(23):
            page.keyboard.press("Backspace")
        expect(field).to_have_value("")
        expect(page.locator("article.message").last).to_contain_text("新的回复片段")
        field.press_sequentially("/goal ", delay=20)
        expect(page.locator(".command-token")).to_have_count(1)
        field.press("Backspace")
        expect(page.locator(".command-token")).to_have_count(0)
        expect(field).to_have_value("")
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )
    finally:
        page.evaluate("window.stabilityActive = false; clearInterval(window.stabilityInterval)")
        cdp.send("Page.stopScreencast")
        cdp.detach()

    result = page.evaluate("stabilityProbe")
    # Inspect actual compositor frames as well as DOM/focus continuity. Crop to
    # the middle of the white conversation surface, avoiding the dark sidebar.
    brightness = []
    for frame in frames:
        with Image.open(io.BytesIO(base64.b64decode(frame))) as image:
            rgb = image.convert("RGB")
            box = (
                int(rgb.width * 0.4),
                int(rgb.height * 0.2),
                int(rgb.width * 0.85),
                int(rgb.height * 0.7),
            )
            pixels = rgb.crop(box).resize((1, 1)).getpixel((0, 0))
            brightness.append(sum(pixels) / 3)
    result.update(
        recordedFrames=len(frames),
        darkestFrame=min(brightness, default=0),
        deletionDuringStreaming=True,
        imeDeletionAndCancellation=True,
    )
    if enforce:
        assert result["samples"] >= 20 and result["recordedFrames"] >= 5, result
        assert result["redundantWrites"] == 0, result
        assert result["missingFrames"] == result["domReplacements"] == result["focusLosses"] == 0, (
            result
        )
        assert not result["errors"], result
        assert result["darkestFrame"] > 80, result
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cdp")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp) if args.cdp else p.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                errors = []
                try:
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(args.url)
                    result = check_typing(page, enforce=not args.baseline)
                    assert not errors, errors
                    results.append({"viewport": [width, height], "status": "passed", **result})
                except Exception as error:
                    results.append(
                        {
                            "viewport": [width, height],
                            "status": "failed",
                            "error": str(error),
                            "measurements": getattr(page, "typing_probe", {}),
                        }
                    )
                    raise
                finally:
                    context.close()
        finally:
            (args.output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
            if not args.cdp:
                browser.close()
    print("Typing benchmark passed.")


if __name__ == "__main__":
    main()
