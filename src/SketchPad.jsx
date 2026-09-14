import React, { useLayoutEffect, useRef, useState } from 'react';
import { Button, Dialog, IconButton } from '@radix-ui/themes';
import {
  X,
  ArrowCounterClockwise,
  ArrowClockwise,
  Eraser,
  PencilSimple,
  Trash,
} from '@phosphor-icons/react';
import './sketch.css';

const COLORS = [
  ['墨黑', '#253329'],
  ['灰色', '#89918a'],
  ['红色', '#e14d4d'],
  ['橙色', '#ed973c'],
  ['黄色', '#eacb45'],
  ['绿色', '#47916b'],
  ['蓝色', '#437bc7'],
  ['紫色', '#9661ba'],
];
const WIDTHS = [
  ['细', 3],
  ['中', 8],
  ['粗', 18],
];

// Keep unfinished drawings per conversation, including while another dialog is open.
export function SketchPad({ open, scope, onClose, onAttach, disabled }) {
  const drafts = useRef(new Map());
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
    >
      <Dialog.Content
        className="sketch-dialog"
        onInteractOutside={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          requestAnimationFrame(() => {
            // Closing can finish after a tap has focused the parent editor.
            // Do not dismiss/reopen the software keyboard by stealing focus.
            if (document.activeElement !== document.body) return;
            document
              .querySelector(
                '.task-dialog[data-state="open"] button, .composer [aria-label="指令菜单"]',
              )
              ?.focus({ preventScroll: true });
          });
        }}
      >
        {open && (
          <DrawingBoard
            key={scope}
            initial={drafts.current.get(scope)}
            save={(draft) => drafts.current.set(scope, draft)}
            onClose={onClose}
            onAttach={onAttach}
            disabled={disabled}
          />
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}

function DrawingBoard({ initial, save, onClose, onAttach, disabled }) {
  const canvas = useRef(null),
    stage = useRef(null),
    active = useRef(null);
  const drawing = useRef(initial || { width: 2000, height: 1500, strokes: [], redo: [] });
  const [color, setColor] = useState(COLORS[0][1]),
    [width, setWidth] = useState(8),
    [eraser, setEraser] = useState(false),
    [, setRevision] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const data = drawing.current;
  function paintStroke(ctx, stroke) {
    ctx.strokeStyle = stroke.color;
    ctx.fillStyle = stroke.color;
    ctx.lineWidth = stroke.width;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const first = stroke.points[0];
    if (!first) return;
    if (stroke.points.length === 1) {
      ctx.beginPath();
      ctx.arc(first.x, first.y, stroke.width / 2, 0, 2 * Math.PI);
      ctx.fill();
      return;
    }
    ctx.beginPath();
    ctx.moveTo(first.x, first.y);
    for (const p of stroke.points.slice(1)) ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }
  function render() {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, el.width, el.height);
    data.strokes.forEach((s) => paintStroke(ctx, s));
  }
  function changed() {
    save(data);
    setRevision((v) => v + 1);
  }
  function fit() {
    const { width: w, height: h } = stage.current.getBoundingClientRect();
    const scale = Math.min(w / data.width, h / data.height);
    canvas.current.style.width = `${data.width * scale}px`;
    canvas.current.style.height = `${data.height * scale}px`;
  }
  function sizeBlank() {
    const box = stage.current.getBoundingClientRect();
    data.height = Math.max(1000, Math.min(3000, Math.round((data.width * box.height) / box.width)));
    canvas.current.width = data.width;
    canvas.current.height = data.height;
  }
  useLayoutEffect(() => {
    if (!initial) sizeBlank();
    canvas.current.width = data.width;
    canvas.current.height = data.height;
    render();
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(stage.current);
    return () => {
      observer.disconnect();
      save(data);
    };
  }, []);
  function point(e) {
    const r = canvas.current.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(data.width, ((e.clientX - r.left) * data.width) / r.width)),
      y: Math.max(0, Math.min(data.height, ((e.clientY - r.top) * data.height) / r.height)),
    };
  }
  function start(e) {
    if (busy || active.current !== null || e.button !== 0) return;
    e.preventDefault();
    canvas.current.setPointerCapture(e.pointerId);
    active.current = e.pointerId;
    // Brush sizes are measured on screen, then exported at the canvas resolution.
    const scale = data.width / canvas.current.getBoundingClientRect().width;
    data.strokes.push({
      color: eraser ? '#ffffff' : color,
      width: width * scale * (eraser ? 2 : 1),
      points: [point(e)],
    });
    data.redo = [];
    paintStroke(canvas.current.getContext('2d'), data.strokes.at(-1));
    changed();
  }
  function move(e) {
    if (active.current !== e.pointerId) return;
    e.preventDefault();
    const stroke = data.strokes.at(-1);
    const events = e.nativeEvent.getCoalescedEvents?.() || [e];
    const ctx = canvas.current.getContext('2d');
    for (const event of events) {
      const p = point(event);
      paintStroke(ctx, { ...stroke, points: [stroke.points.at(-1), p] });
      stroke.points.push(p);
    }
  }
  function stop(e) {
    if (active.current !== e.pointerId) return;
    active.current = null;
    if (canvas.current.hasPointerCapture(e.pointerId))
      canvas.current.releasePointerCapture(e.pointerId);
    render();
    changed();
  }
  function undo() {
    if (active.current !== null) return;
    const stroke = data.strokes.pop();
    if (stroke) data.redo.push(stroke);
    render();
    changed();
  }
  function redo() {
    if (active.current !== null) return;
    const stroke = data.redo.pop();
    if (stroke) data.strokes.push(stroke);
    render();
    changed();
  }
  function clear() {
    if (active.current !== null) return;
    data.strokes = [];
    data.redo = [];
    sizeBlank();
    fit();
    render();
    changed();
  }
  async function attach() {
    if (busy || disabled || !data.strokes.length) return;
    setBusy(true);
    setError('');
    try {
      const blob = await new Promise((resolve, reject) =>
        canvas.current.toBlob(
          (value) => (value ? resolve(value) : reject(new Error('图片导出失败，请重试'))),
          'image/png',
        ),
      );
      const file = new File(
        [blob],
        `sketch-${new Date().toISOString().replace(/[:.]/g, '-')}.png`,
        { type: 'image/png' },
      );
      if (onAttach(file) === false)
        throw new Error('每条消息最多 6 个附件，请先移除附件再添加绘画');
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <header className="sketch-header">
        <Dialog.Title>绘画参考</Dialog.Title>
        <IconButton
          type="button"
          variant="soft"
          aria-label="关闭画板"
          disabled={busy}
          onClick={onClose}
        >
          <X />
        </IconButton>
      </header>
      <Dialog.Description className="sr-only">
        选择颜色和三档笔粗，用鼠标、触控笔或手指绘画。完成后添加到消息作为参考图片。
      </Dialog.Description>
      <div className="sketch-tools">
        <div className="sketch-colors" role="group" aria-label="画笔颜色">
          {COLORS.map(([name, value]) => (
            <button
              type="button"
              key={value}
              aria-label={name}
              aria-pressed={!eraser && color === value}
              style={{ '--swatch': value }}
              onClick={() => {
                setColor(value);
                setEraser(false);
              }}
            >
              <span />
            </button>
          ))}
        </div>
        <div className="sketch-brushes" role="group" aria-label="画笔粗细">
          {WIDTHS.map(([name, value]) => (
            <button
              type="button"
              key={value}
              aria-label={name + '笔'}
              aria-pressed={width === value}
              onClick={() => setWidth(value)}
            >
              <span style={{ width: value, height: value }} />
              <small>{name}</small>
            </button>
          ))}
        </div>
        <div className="sketch-edits">
          <IconButton
            type="button"
            variant={eraser ? 'soft' : 'solid'}
            aria-label="画笔"
            aria-pressed={!eraser}
            onClick={() => setEraser(false)}
          >
            <PencilSimple />
          </IconButton>
          <IconButton
            type="button"
            variant={eraser ? 'solid' : 'soft'}
            aria-label="橡皮擦"
            aria-pressed={eraser}
            onClick={() => setEraser(true)}
          >
            <Eraser />
          </IconButton>
          <IconButton
            type="button"
            variant="soft"
            aria-label="撤销笔画"
            disabled={!data.strokes.length || busy}
            onClick={undo}
          >
            <ArrowCounterClockwise />
          </IconButton>
          <IconButton
            type="button"
            variant="soft"
            aria-label="重做笔画"
            disabled={!data.redo.length || busy}
            onClick={redo}
          >
            <ArrowClockwise />
          </IconButton>
          <IconButton
            type="button"
            variant="soft"
            aria-label="清空画布"
            disabled={!data.strokes.length || busy}
            onClick={clear}
          >
            <Trash />
          </IconButton>
        </div>
      </div>
      <div className="sketch-stage" ref={stage}>
        <canvas
          ref={canvas}
          aria-label="绘画画布"
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={stop}
          onPointerCancel={stop}
          onLostPointerCapture={stop}
        />
      </div>
      {error && (
        <p className="attachment-error" role="alert">
          {error}
        </p>
      )}
      <footer className="sketch-footer">
        <span>草稿自动保留至本页关闭</span>
        <Button type="button" disabled={busy || disabled || !data.strokes.length} onClick={attach}>
          {busy ? '正在导出…' : '完成并添加图片'}
        </Button>
      </footer>
    </>
  );
}
