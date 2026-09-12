"""Check slash-path editing and request payloads without starting any Codex task."""

import argparse
import io
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def check_slash_composer(page):
    submitted = []

    def capture(route):
        submitted.append(route.request.post_data_json)
        route.fulfill(json={"kind": "queued"})

    pattern = "**/api/threads/*/messages"
    page.route(pattern, capture)
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    readiness = page.get_by_role("button", name="本条指令模型", exact=True)

    def wait_for_send():
        expect(field).to_have_value("")
        # The draft clears before the subsequent thread refresh finishes. Wait for
        # the send lock to release before typing another message.
        expect(readiness).to_be_enabled(timeout=20000)

    try:
        expect(readiness).to_be_enabled(timeout=20000)
        paths = [
            "/src 请参考附件",
            "/tmp/project/report.png",
            "/goal/report.md",
            "/plan/project",
            "/go",
            "/",
            "//server/share/file.pdf",
            "/constructor",
            "/tmp/报告 文件.png",
        ]
        for text in paths:
            field.fill("")
            # Real per-character input catches premature conversion at /goal or /plan.
            field.press_sequentially(text)
            expect(field).to_have_value(text)
            expect(field).to_be_focused()
            expect(page.locator(".command-token")).to_have_count(0)
            field.press("Enter")
            wait_for_send()
            assert submitted[-1]["text"] == text, {
                "expected": text,
                "actual": submitted[-1]["text"],
            }
            assert submitted[-1]["collaboration"] == "default"
        # Manual command selection wraps an existing path without discarding it.
        field.fill("/srv/report.md")
        page.get_by_role("button", name="指令菜单", exact=True).click()
        page.get_by_role("option").filter(has_text="/goal").click()
        expect(page.locator(".command-token")).to_have_count(1)
        expect(field).to_have_value("/srv/report.md")
        field.focus()
        field.evaluate("(el)=>el.setSelectionRange(0,0)")
        field.press("Backspace")
        expect(page.locator(".command-token")).to_have_count(0)
        expect(field).to_have_value("/srv/report.md")
        # Recognized commands still become atomic tokens on space or confirmation.
        field.fill("/goal ")
        expect(page.locator(".command-token")).to_have_count(1)
        field.fill("检查路径支持")
        field.press("Enter")
        wait_for_send()
        assert submitted[-1]["text"] == "/goal 检查路径支持"
        field.fill("/compact")
        field.press("Enter")
        expect(page.locator(".command-token")).to_have_count(1)
        field.press("Enter")
        wait_for_send()
        assert submitted[-1]["text"] == "/compact"
        return {
            "plainPaths": len(paths),
            "atomicCommands": True,
            "pathPreservedByMenu": True,
            "capturedRequests": len(submitted),
        }
    finally:
        page.unroute(pattern, capture)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--password-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        for width, height in [(1440, 900), (390, 844)]:
            context = browser.new_context(viewport={"width": width, "height": height})
            try:
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(args.url)
                page.get_by_label("访问口令", exact=True).fill(
                    args.password_file.read_text().strip()
                )
                page.get_by_role("button", name="进入工作台").click()
                expect(page.locator(".composer")).to_be_visible()
                raw = io.BytesIO()
                Image.new("RGB", (40, 30), "green").save(raw, "PNG")
                page.locator(".composer input[type=file]").set_input_files(
                    {"name": "slash-check.png", "mimeType": "image/png", "buffer": raw.getvalue()}
                )
                expect(page.locator(".composer-attachments")).to_contain_text("已就绪")
                checks = check_slash_composer(page)
                assert not errors, errors
                results.append({"viewport": [width, height], **checks, "pageErrors": errors})
                page.screenshot(path=str(args.output / f"{width}x{height}.png"))
            finally:
                context.close()
    (args.output / "results.json").write_text(
        json.dumps({"passed": True, "results": results}, ensure_ascii=False, indent=2)
    )
    print("Slash-path browser checks passed; no model task was submitted.")


if __name__ == "__main__":
    main()
