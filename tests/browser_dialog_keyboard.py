"""Repeated mobile dialog/keyboard transitions using synthetic API and viewport events."""

from playwright.sync_api import expect

from browser_mobile_viewport import check_mobile_viewport


def check_dialog_keyboard(page, *, enforce=True):
    # Reuse the isolated conversation and VisualViewport event fixture.
    check_mobile_viewport(page)
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    page.route(
        "**/api/directories?**",
        lambda route: route.fulfill(
            json={
                "path": "/tmp",
                "entries": [],
                "breadcrumbs": [],
                "shortcuts": [],
                "parent": None,
                "limit": 50,
                "total": 0,
            }
        ),
    )
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.evaluate("""async () => {
      const snapshot = await (await fetch('/api/threads/viewport')).json();
      window.dialogStreamInterval = setInterval(() => {
        snapshot.messages[0].text += '\\n连续输出内容';
        viewportStream.onmessage({data:JSON.stringify({threads:[snapshot],selected:snapshot})});
      }, 100);
      window.dialogProbe = {closedOverlayFrames:0, emptyFrames:0, focusLosses:0};
      window.dialogSampling = true;
      function sample() {
        if (!dialogSampling) return;
        if (document.querySelector('.rt-DialogOverlay[data-state="closed"]'))
          dialogProbe.closedOverlayFrames++;
        if (document.querySelector('.app-shell').clientHeight < 100) dialogProbe.emptyFrames++;
        requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    }""")
    for cycle in range(5):
        field.focus()
        page.evaluate("resizeVisualViewport({height:430,offsetTop:25})")
        field.press_sequentially("输入n")
        field.press("Backspace")
        page.evaluate("resizeVisualViewport({height:innerHeight,offsetTop:0})")
        # Open the new-task dialog while the software keyboard is closing.
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("n")
        task = page.get_by_role("dialog", name="开始一个新任务")
        expect(task).to_be_visible()
        task_field = task.get_by_label("任务内容", exact=True)
        task_field.fill("临时草稿")
        page.evaluate("resizeVisualViewport({height:430,offsetTop:35})")
        task.get_by_role("button", name="绘画参考", exact=True).click()
        sketch = page.get_by_role("dialog", name="绘画参考", exact=True)
        expect(sketch).to_be_visible()
        # Fast tap into the parent text field when the drawing dialog leaves
        # the DOM, before its deferred focus-restoration callback executes.
        page.evaluate("""() => {
          const closing = document.querySelector('.sketch-dialog');
          const field = document.querySelector('#task-text');
          window.focusCheckDone = false;
          new MutationObserver((_, observer) => {
            if (closing.isConnected) return;
            observer.disconnect();
            field.focus();
            const until = performance.now() + 250;
            const sample = () => {
              if (document.activeElement !== field) dialogProbe.focusLosses++;
              if (performance.now() < until) requestAnimationFrame(sample);
              else window.focusCheckDone = true;
            };
            requestAnimationFrame(sample);
          }).observe(document.body, {childList:true,subtree:true});
        }""")
        sketch.get_by_role("button", name="关闭画板", exact=True).click()
        page.wait_for_function("focusCheckDone")
        if enforce:
            expect(task_field).to_be_focused()
        task_field.focus()
        task_field.press_sequentially("n删除")
        task_field.press("Backspace")
        task.get_by_role("button", name="选择工作目录", exact=True).click()
        directory = page.get_by_role("dialog", name="选择工作目录", exact=True)
        expect(directory).to_be_visible()
        directory.get_by_label("跳转到目录路径", exact=True).fill("/tmp")
        page.evaluate("resizeVisualViewport({height:430,offsetTop:10})")
        directory.get_by_role("button", name="关闭目录选择", exact=True).click()
        expect(directory).to_have_count(0)
        task_field.focus()
        task_field.press_sequentially("恢复输入")
        task_field.press("Backspace")
        page.evaluate("resizeVisualViewport({height:570,offsetTop:0})")
        task.get_by_role("button", name="取消", exact=True).click()
        expect(task).to_have_count(0)
        page.evaluate("resizeVisualViewport({height:innerHeight,offsetTop:0})")
        expect(page.get_by_role("dialog")).to_have_count(0)
        field.focus()
        field.fill(f"返回输入框 {cycle}")
        field.press("Backspace")
        expect(field).to_be_focused()
        assert page.locator("body").evaluate("el => getComputedStyle(el).pointerEvents") != "none"
        assert not page.locator(".app-shell").get_attribute("aria-hidden")
    # Keep viewport events arriving throughout rapid dialog reentry, rather
    # than waiting for each simulated keyboard transition before interacting.
    page.evaluate("""() => {
      window.keyboardAnimating = true;
      let frame = 0;
      const animate = () => {
        if (!keyboardAnimating) return;
        const heights = [innerHeight, 570, 430, 390, 430, 570];
        resizeVisualViewport({height:heights[frame++ % heights.length],offsetTop:0});
        requestAnimationFrame(animate);
      };
      requestAnimationFrame(animate);
    }""")
    try:
        for cycle in range(8):
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("n")
            task = page.get_by_role("dialog", name="开始一个新任务")
            expect(task).to_be_visible()
            task.get_by_label("任务内容", exact=True).fill(f"快速开关草稿 {cycle}")
            # Avoid Playwright's stable-position wait while the keyboard is
            # moving the dialog; dispatch the close immediately.
            task.get_by_role("button", name="取消", exact=True).evaluate("el => el.click()")
            expect(task).to_have_count(0)
            field.focus()
            field.fill(f"继续输入 {cycle}")
            field.press("Backspace")
            expect(field).to_be_focused()
            expect(field).to_have_value("继续输入 ")
            assert (
                page.locator("body").evaluate("el => getComputedStyle(el).pointerEvents") != "none"
            )
            assert not page.locator(".app-shell").get_attribute("aria-hidden")
    finally:
        page.evaluate("keyboardAnimating = false")
        page.evaluate("resizeVisualViewport({height:innerHeight,offsetTop:0})")
    page.evaluate("dialogSampling = false; clearInterval(dialogStreamInterval)")
    expect(page.locator(".message.assistant")).to_contain_text("连续输出内容")
    result = page.evaluate("dialogProbe")
    result["pageErrors"] = page_errors
    if enforce:
        assert not any(result.values()), result
    return result
