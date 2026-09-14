import { useLayoutEffect, useRef } from 'react';

// Follow the beginning of a reply until the reader scrolls. A trailing spacer
// lets even a short reply sit at the top without chasing its growing bottom.
export function useReplyScroll({ scope, enabled, detail, historyAnchor, previousScroll }) {
  const scroll = useRef(null);
  const timeline = useRef(null);
  const end = useRef(null);
  const reading = useRef({ paused: false, top: 0 });
  const sync = useRef(null);

  function pause() {
    reading.current.paused = true;
  }

  function resume() {
    reading.current.paused = false;
  }

  function noteScroll() {
    const top = scroll.current.scrollTop;
    // Programmatic positioning records its destination before the scroll event.
    if (Math.abs(top - reading.current.top) > 2) pause();
    reading.current.top = top;
  }

  sync.current = () => {
    const el = scroll.current;
    const content = timeline.current;
    const spacer = end.current;
    if (!el || !content || !spacer) return;
    const candidates = content.querySelectorAll(
      '.message.user, .message.assistant:not(.status-answer)',
    );
    const target = candidates[candidates.length - 1];
    const style = getComputedStyle(el);
    const viewportTop = el.getBoundingClientRect().top + el.clientTop;
    const targetTop = target
      ? target.getBoundingClientRect().top -
        viewportTop +
        el.scrollTop -
        parseFloat(style.paddingTop)
      : 0;
    const contentBottom = spacer.getBoundingClientRect().top - viewportTop + el.scrollTop;
    const space = target
      ? Math.max(0, targetTop + el.clientHeight - contentBottom - parseFloat(style.paddingBottom))
      : 0;
    const height = `${Math.ceil(space)}px`;
    if (spacer.style.height !== height) spacer.style.height = height;

    let top;
    if (historyAnchor.current?.tid === scope && detail?.history?.mode === 'all') {
      const anchor = historyAnchor.current;
      const message = [...content.querySelectorAll('[data-message-id]')].find(
        (node) => node.dataset.messageId === anchor.messageId,
      );
      top = message
        ? el.scrollTop + message.getBoundingClientRect().top - viewportTop - anchor.offset
        : el.scrollHeight - anchor.height + anchor.top;
      historyAnchor.current = null;
    } else if (!reading.current.paused && target) {
      top = targetTop;
    }
    if (top !== undefined) {
      // SSE sends new snapshot objects even when nothing has moved. Do not
      // invalidate the scroll layer for a no-op or subpixel rounding difference.
      const destination = Math.max(0, Math.min(top, el.scrollHeight - el.clientHeight));
      if (Math.abs(el.scrollTop - destination) >= 1) el.scrollTop = destination;
    }
    reading.current.top = el.scrollTop;
    previousScroll.current = el.scrollTop;
  };

  useLayoutEffect(() => {
    reading.current = { paused: false, top: 0 };
  }, [scope, enabled]);

  useLayoutEffect(() => {
    sync.current();
  }, [detail, scope, enabled]);

  useLayoutEffect(() => {
    let frame = null;
    const observer = new ResizeObserver(() => {
      // Resize notifications happen during layout. Coalesce them into a frame
      // instead of changing geometry from inside the observer delivery loop.
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        sync.current();
      });
    });
    if (scroll.current) observer.observe(scroll.current);
    // Text, images, formulas and task status can change height independently
    // of the viewport. The spacer is outside this observed content.
    if (timeline.current) observer.observe(timeline.current);
    return () => {
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [scope, enabled]);

  return { scroll, timeline, end, pause, resume, noteScroll };
}
