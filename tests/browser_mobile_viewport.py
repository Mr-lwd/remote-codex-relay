"""Mobile viewport events with synthetic APIs; not a physical keyboard/GPU test."""

from urllib.parse import urlsplit

from playwright.sync_api import expect


def check_mobile_viewport(page):
    detail = {
        "id": "viewport",
        "title": "手机视口验收",
        "cwd": "/tmp/mobile",
        "status": "idle",
        "messages": [
            {
                "id": "answer",
                "role": "assistant",
                "at": "2026-01-01T00:00:00Z",
                "text": "手机上的回复内容。\n\n" * 30,
            }
        ],
        "requests": [],
        "notes": [],
        "activities": [],
        "history": {"mode": "recent", "hasMore": False, "total": 1},
    }

    def route_api(route):
        path = urlsplit(route.request.url).path
        if path == "/api/threads":
            route.fulfill(json={"threads": [detail], "defaultCwd": "/tmp"})
        elif path == "/api/threads/viewport":
            route.fulfill(json=detail)
        else:
            route.fulfill(status=404, json={"detail": "fixture"})

    page.route("**/api/**", route_api)
    page.add_init_script("""(() => {
      localStorage.setItem('relay-thread', 'viewport');
      window.EventSource=class {constructor(){window.viewportStream=this;} addEventListener(){} close(){}};
      const viewport = new EventTarget();
      Object.assign(viewport, {height:innerHeight, offsetTop:0, scale:1});
      Object.defineProperty(window, 'visualViewport', {value:viewport});
      window.resizeVisualViewport = async values => {
        Object.assign(viewport, values);
        viewport.dispatchEvent(new Event('resize'));
        viewport.dispatchEvent(new Event('scroll'));
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      };
    })();""")
    page.reload()
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    expect(field).to_be_visible()
    # Mobile WebKit parses viewport metadata after init scripts; refresh the
    # synthetic dimensions once the document has its device-width viewport.
    page.evaluate("resizeVisualViewport({height:innerHeight})")
    original_height = page.locator(".app-shell").bounding_box()["height"]
    # Pinch zoom must magnify the existing layout, rather than shrink the app
    # and misidentify the zoom as an opened keyboard.
    page.evaluate("resizeVisualViewport({scale:2,height:innerHeight/2,offsetTop:50})")
    assert page.locator(".app-shell").bounding_box()["height"] == original_height
    assert not page.locator("html").evaluate("el => el.classList.contains('keyboard-open')")
    page.evaluate("resizeVisualViewport({scale:1,height:innerHeight,offsetTop:0})")
    field.focus()
    page.evaluate("resizeVisualViewport({height:430,offsetTop:20})")
    expect(page.locator("html")).to_have_class("keyboard-open")
    bounds = page.locator(".app-shell").bounding_box()
    assert bounds["height"] == 430 and bounds["y"] == 20
    page.evaluate("""() => {
      window.viewportMutations = 0;
      window.viewportObserver = new MutationObserver(records => viewportMutations += records.length);
      viewportObserver.observe(document.documentElement, {attributes:true});
    }""")
    # Fractional viewport noise during keyboard animation must not resize the
    # entire page on each edit. Real changes still take effect immediately.
    for index in range(12):
        field.press_sequentially("n")
        field.press("Backspace")
        page.evaluate(
            "height => resizeVisualViewport({height,offsetTop:20.1})", 430 + (index % 2) * 0.2
        )
    assert page.evaluate("viewportMutations") == 0
    expect(field).to_be_focused()
    expect(field).to_have_value("")
    assert page.locator(".messages").bounding_box()["height"] > 40
    assert page.get_by_role("dialog").count() == 0
    page.evaluate("viewportObserver.disconnect()")
    page.evaluate("resizeVisualViewport({height:390,offsetTop:0})")
    assert page.locator(".app-shell").bounding_box()["height"] == 390
    page.evaluate("resizeVisualViewport({height:innerHeight,offsetTop:0})")
    assert page.locator(".app-shell").bounding_box()["height"] == original_height
    assert not page.locator("html").evaluate("el => el.classList.contains('keyboard-open')")
    return {"zoomPreservesLayout": True, "keyboardEditingStable": True}
