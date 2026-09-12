"""Goal removal must release dialog input locks and leave the composer usable."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def check_goal_clear(page, scenario):
    state = {
        "goal": {"objective": "已经完成的测试目标", "status": "complete", "time_used_seconds": 12}
    }
    sent = []
    calls = []
    page.goal_probe = {"state": state, "calls": calls}

    def detail():
        return {
            "id": "goal-clear-fixture",
            "title": "目标清除测试",
            "cwd": "/tmp/goal-clear-fixture",
            "status": "completed",
            "managed": False,
            "modelSwitchAllowed": True,
            "model": "gpt-6-astra",
            "reasoningEffort": "high",
            "tokens": 0,
            "messages": [
                {
                    "id": "reply",
                    "role": "assistant",
                    "text": "目标已完成。",
                    "at": "2026-01-01T00:00:00Z",
                }
            ],
            "notes": [],
            "commandRecords": [],
            "activities": [],
            "requests": [],
            "pendingMessages": [],
            "pendingCount": 0,
            "executionSettings": {},
            "goal": state["goal"],
            "history": {"mode": "recent", "hasMore": False, "total": 1},
        }

    def route_api(route):
        path = urlsplit(route.request.url).path
        calls.append({"path": path, "method": route.request.method})
        if path == "/api/threads":
            route.fulfill(json={"threads": [detail()], "defaultCwd": "/tmp"})
        elif path == "/api/events":
            route.fulfill(
                content_type="text/event-stream",
                body="retry: 100\ndata: "
                + json.dumps({"threads": [detail()], "selected": detail()})
                + "\n\n",
            )
        elif path.endswith("/goal"):
            assert route.request.post_data_json["action"] == "clear"
            if state.pop("fail", False):
                route.fulfill(status=409, json={"detail": "测试删除失败，请重试"})
                return
            state["goal"] = None
            route.fulfill(json={"kind": "command", "message": "目标已清除，聊天记录保留。"})
        elif path.endswith("/messages"):
            sent.append(route.request.post_data_json)
            route.fulfill(json={"kind": "started"})
        elif path.startswith("/api/threads/"):
            route.fulfill(json=detail())
        else:
            route.fulfill(status=404, json={"detail": "Unexpected fixture request"})

    pattern = "**/api/**"
    page.route(pattern, route_api)
    try:
        page.reload()
        trash = page.get_by_role("button", name="清除目标", exact=True)
        expect(trash).to_be_enabled()
        trash.hover()
        expect(page.get_by_role("tooltip")).to_be_visible()
        trash.click()
        dialog = page.get_by_role("dialog", name="清除当前目标", exact=True)
        expect(dialog).to_be_visible()
        if scenario == "external":
            # Simulate another device clearing the goal while this modal is open.
            state["goal"] = None
        else:
            if scenario == "retry":
                state["fail"] = True
                dialog.get_by_role("button", name="确认清除", exact=True).click()
                expect(dialog.get_by_role("alert")).to_contain_text("测试删除失败")
                expect(dialog.get_by_role("button", name="确认清除", exact=True)).to_be_enabled()
            dialog.get_by_role("button", name="确认清除", exact=True).click()
        expect(page.locator(".goal-control")).not_to_be_visible()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(page.locator("body")).not_to_have_css("pointer-events", "none")
        expect(page.locator("body")).not_to_have_attribute("data-scroll-locked", "1")
        field = page.get_by_label("向 Codex 发送消息", exact=True)
        field.click(timeout=5000)
        field.press_sequentially("/tmp/删除目标后继续聊天")
        expect(field).to_have_value("/tmp/删除目标后继续聊天")
        field.press("Enter")
        expect(field).to_have_value("")
        assert len(sent) == 1 and sent[0]["text"] == "/tmp/删除目标后继续聊天"
        model = page.get_by_role("button", name="本条指令模型", exact=True)
        expect(model).to_be_enabled()
        model.click()
        expect(page.get_by_role("menu", name="选择模型", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_role("button", name="指令菜单", exact=True).click()
        expect(page.get_by_role("listbox", name="可用指令", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        return {
            "scenario": scenario,
            "composerUsable": True,
            "menusUsable": True,
            "modalLocksReleased": True,
        }
    finally:
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
            for width, height in [(1440, 900), (390, 844)]:
                for scenario in ["clear", "external", "retry"]:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    try:
                        page.goto(args.url)
                        result = check_goal_clear(page, scenario)
                        assert not errors, errors
                        results.append(
                            {"viewport": [width, height], **result, "pageErrors": errors}
                        )
                    except Exception:
                        page.screenshot(path=str(args.output / f"failure-{width}-{scenario}.png"))
                        (args.output / "failure.json").write_text(
                            json.dumps(
                                {
                                    "scenario": scenario,
                                    "viewport": [width, height],
                                    "pageErrors": errors,
                                    "probe": page.goal_probe,
                                    "locks": page.evaluate(
                                        "({pointerEvents:document.body.style.pointerEvents,scrollLock:document.body.getAttribute('data-scroll-locked'),dialogs:document.querySelectorAll('[role=dialog]').length})"
                                    ),
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        raise
                    finally:
                        context.close()
        finally:
            if not args.cdp:
                browser.close()
    (args.output / "results.json").write_text(
        json.dumps({"passed": True, "results": results}, ensure_ascii=False, indent=2)
    )
    print("Goal-clear browser checks passed without touching real goals or conversations.")


if __name__ == "__main__":
    main()
