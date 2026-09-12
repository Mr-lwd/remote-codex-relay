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
    metrics.detach()
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
