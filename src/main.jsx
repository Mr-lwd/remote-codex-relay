import {
  ModelPicker,
  DEFAULT_CONFIG,
  normalizePreference as normalizeModelPreference,
  validPreference as validModelPreference,
} from './ModelPicker';
import { savedPreference } from './preferences';
import React, { useState, useEffect, useLayoutEffect, useRef, useMemo, memo } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Theme,
  Button,
  Dialog,
  TextField,
  Select,
  Tooltip,
  IconButton,
  Popover,
  DropdownMenu,
} from '@radix-ui/themes';
import {
  TerminalWindow,
  Plus,
  ArrowUp,
  ArrowRight,
  ChatCircle,
  Pulse,
  Folder,
  Clock,
  Circle,
  CheckCircle,
  Command,
  MagnifyingGlass,
  SignOut,
  List,
  X,
  Stop,
  Copy,
  CaretRight,
  ArrowClockwise,
  LockKey,
  Lightning,
  Broadcast,
  DotsThree,
  PencilSimple,
  Archive,
} from '@phosphor-icons/react';
import { MessageBody } from './MessageBody';
import {
  useAttachments,
  AttachmentInput,
  AttachmentTray,
  attachmentEvents,
  ImageCard,
  ImagePreview,
  FileCard,
} from './Images';
import '@radix-ui/themes/styles.css';
import '@fontsource/geist/400.css';
import '@fontsource/geist/500.css';
import '@fontsource/geist/600.css';
import '@fontsource/noto-sans-sc/400.css';
import '@fontsource/noto-sans-sc/500.css';
import '@fontsource/noto-sans-sc/600.css';
import {
  COMMANDS,
  commandCandidates,
  commandInfo,
  commandProgress,
  operationState,
  commandNames,
} from './commands';
import { SketchPad } from './SketchPad';
import { DirectoryPicker } from './DirectoryPicker';
import { GoalBar } from './GoalBar';
import { MessageQueue } from './MessageQueue';
import { useSessionState } from './sessionState';
import './styles.css';
import './responsive.css';

function useMedia(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return matches;
}

function useVisibleViewport() {
  useEffect(() => {
    const viewport = window.visualViewport;
    let frame;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const root = document.documentElement;
        root.style.setProperty('--viewport-height', `${viewport?.height || window.innerHeight}px`);
        root.style.setProperty('--viewport-top', `${viewport?.offsetTop || 0}px`);
        root.classList.toggle(
          'keyboard-open',
          Boolean(viewport && window.innerHeight - viewport.height > 140),
        );
      });
    };
    update();
    window.addEventListener('resize', update);
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', update);
      viewport?.removeEventListener('resize', update);
      viewport?.removeEventListener('scroll', update);
    };
  }, []);
}

function ResponsivePanel({ drawer, open, onOpenChange, side, title, children }) {
  if (!drawer) return children;
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        className={`responsive-drawer drawer-${side}`}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          requestAnimationFrame(() => {
            if (!document.querySelector('[role="dialog"]'))
              document
                .querySelector(
                  side === 'left' ? '.mobile-menu' : '.conversation-header .activity-toggle',
                )
                ?.focus();
          });
        }}
      >
        <Dialog.Title className="sr-only">{title}</Dialog.Title>
        <Dialog.Description className="sr-only">
          {side === 'left' ? '选择工作会话或新建任务' : '查看当前任务的状态与执行记录'}
        </Dialog.Description>
        {children}
      </Dialog.Content>
    </Dialog.Root>
  );
}

const statuses = {
  awaiting: '等待回复',
  running: '执行中',
  starting: '启动中',
  completed: '已完成',
  idle: '待命',
  interrupted: '已中断',
  unknown: '状态待确认',
  failed: '执行失败',
  detached: '查看会话状态',
};
const when = (v) =>
  v
    ? new Date(typeof v === 'number' ? v * 1000 : v).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';
const number = (n) =>
  new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(n || 0);
async function api(path, body) {
  const r = await fetch('/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Relay-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401) {
    const e = new Error('请使用访问口令登录');
    e.auth = true;
    throw e;
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `请求失败 (${r.status})`);
  return data;
}
function Mark({ small = false }) {
  return (
    <div className={'mark ' + (small ? 'small' : '')}>
      <TerminalWindow weight="bold" size={small ? 21 : 26} />
    </div>
  );
}
function Status({ value, label }) {
  return (
    <span className={'status ' + value}>
      <span />
      {label || statuses[value] || value}
    </span>
  );
}

const defaultControls = { permission: 'workspace', collaboration: 'default' };
const emptyDraft = { text: '', commandToken: null };
function normalizeControls(value) {
  return {
    permission: ['read-only', 'workspace', 'full'].includes(value?.permission)
      ? value.permission
      : 'workspace',
    collaboration: value?.collaboration === 'plan' ? 'plan' : 'default',
  };
}
function PermissionPicker({ value, onChange, disabled = false }) {
  return (
    <>
      <Select.Root
        value={value.permission}
        onValueChange={(permission) => onChange({ ...value, permission })}
        disabled={disabled}
      >
        <Select.Trigger
          aria-label="操作权限"
          title={
            value.permission === 'full'
              ? '可访问服务器文件与网络，不逐项询问批准'
              : value.permission === 'read-only'
                ? '仅查看，不允许修改文件或提升权限'
                : '工作目录内可修改，额外权限需在对话中批准'
          }
        />
        <Select.Content>
          <Select.Item value="read-only">只读</Select.Item>
          <Select.Item value="workspace">按需批准</Select.Item>
          <Select.Item value="full">完全访问</Select.Item>
        </Select.Content>
      </Select.Root>
    </>
  );
}
function CommandRecord({ record }) {
  const info = commandInfo(record.input),
    progress = commandProgress(record);
  return (
    <article
      className={'command-record ' + progress.status}
      aria-label={`${info?.title || '会话指令'} · ${progress.label}`}
    >
      <div className="command-record-heading">
        <Command size={16} />
        <strong>{info?.title || '会话指令'}</strong>
        <span className="command-progress">{progress.label}</span>
        <time>{when(record.at)}</time>
      </div>
      <div className="command-input">{record.input}</div>
      <p>{progress.message}</p>
    </article>
  );
}

const MessageList = memo(function MessageList({ messages, onPreview }) {
  return messages.map((m) =>
    m.kind === 'command-record' ? (
      <CommandRecord key={m.id} record={m} />
    ) : (
      <article
        data-message-id={m.id}
        key={m.id + '-' + m.role}
        className={'message ' + m.role + (m.kind === 'status-answer' ? ' status-answer' : '')}
      >
        <div className="message-avatar">
          {m.role === 'user' ? (
            'U'
          ) : m.role === 'system' ? (
            <Command size={15} />
          ) : (
            <TerminalWindow size={17} />
          )}
        </div>
        <div className="message-main">
          <div className="message-label">
            <strong>{m.role === 'user' ? '你' : m.role === 'system' ? '工作台' : 'Codex'}</strong>
            {((m.role === 'user' && commandInfo(m.text)) ||
              ['compact', 'goal'].includes(m.kind)) && (
              <span className="record-label command-label">
                {commandInfo(m.text)?.title || commandNames[m.kind]}
              </span>
            )}
            {m.kind === 'status-answer' && <span className="record-label">进度快照</span>}
            <time>{when(m.at)}</time>
          </div>
          <MessageBody message={m} onPreview={onPreview} />
        </div>
      </article>
    ),
  );
});

function InteractionCard({ request, onReply }) {
  const [answers, setAnswers] = useState({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  async function respond(body) {
    setBusy(true);
    setError('');
    try {
      await onReply(request.id, body);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="interaction-card"
      aria-label={request.type === 'input' ? 'Codex 等待你的回答' : 'Codex 等待批准'}
    >
      <div className="interaction-title">
        <strong>{request.type === 'input' ? 'Codex 需要你的回答' : 'Codex 请求操作批准'}</strong>
        <span>等待你处理</span>
      </div>
      {request.reason && <p>{request.reason}</p>}
      {request.command && <pre>{request.command}</pre>}
      {request.cwd && <p className="interaction-cwd">目录：{request.cwd}</p>}
      {request.grantRoot && <p>申请写入目录：{request.grantRoot}</p>}
      {request.changes?.map((change, i) => (
        <div key={i}>
          <strong>{change.path}</strong>
          <pre>{change.diff || change.kind?.type}</pre>
        </div>
      ))}
      {request.type === 'permissions' && <pre>{JSON.stringify(request.permissions, null, 2)}</pre>}
      {request.type === 'input' ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            respond({
              answers: Object.fromEntries(
                request.questions.map((q) => [q.id, [answers[q.id] || '']]),
              ),
            });
          }}
        >
          {request.questions.map((q) => (
            <fieldset key={q.id}>
              <legend>{q.question}</legend>
              {q.options?.map((o) => (
                <label className="answer-option" key={o.label}>
                  <input
                    type="radio"
                    name={request.id + q.id}
                    checked={answers[q.id] === o.label}
                    onChange={() => setAnswers({ ...answers, [q.id]: o.label })}
                  />
                  <span>
                    {o.label}
                    {o.description && <small>{o.description}</small>}
                  </span>
                </label>
              ))}
              <input
                aria-label={q.header || q.question}
                type={q.isSecret ? 'password' : 'text'}
                placeholder="输入回答，也可补充自己的想法"
                value={answers[q.id] || ''}
                onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })}
                required
                autoComplete="off"
              />
            </fieldset>
          ))}
          <Button
            type="submit"
            disabled={busy || request.questions.some((q) => !answers[q.id]?.trim())}
          >
            提交回答
          </Button>
        </form>
      ) : (
        <div className="approval-actions">
          <Button type="button" disabled={busy} onClick={() => respond({ decision: 'accept' })}>
            批准本次
          </Button>
          <Button
            type="button"
            variant="soft"
            disabled={busy}
            onClick={() => respond({ decision: 'acceptForSession' })}
          >
            本次运行允许
          </Button>
          <Button
            type="button"
            color="red"
            variant="soft"
            disabled={busy}
            onClick={() => respond({ decision: 'decline' })}
          >
            拒绝
          </Button>
          {request.type !== 'permissions' && (
            <Button
              type="button"
              color="gray"
              variant="soft"
              disabled={busy}
              onClick={() => respond({ decision: 'cancel' })}
            >
              拒绝并中止
            </Button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}

function Login({ onLogin }) {
  const [password, setPassword] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('/login', { password });
      let snapshot;
      try {
        snapshot = await api('/threads');
      } catch (e) {
        if (e.auth) {
          throw new Error(
            '口令已验证，但浏览器未能保存或发送登录 Cookie。请允许此站点使用 Cookie，再重新登录。',
          );
        }
        throw e;
      }
      await onLogin(snapshot);
    } catch (e) {
      setError(e.auth ? '访问口令不正确' : e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <div className="login-brand">
        <Mark />
        <span>
          codex<span className="brand-light">relay</span>
        </span>
      </div>
      <div className="login-layout">
        <section className="login-intro">
          <div className="eyebrow">
            <Broadcast size={17} /> YOUR WORK, WITHIN REACH
          </div>
          <h1>
            离开桌面，
            <br />
            任务依然在手边。
          </h1>
          <p>
            连接你的 Codex 工作现场。查看进度、补充想法，
            <br className="desktop-only" />
            让下一步从一句话开始。
          </p>
          <div className="login-proof">
            <span className="live-dot" />
            本机 Codex 已接入 <span className="divider" /> 远程工作台
          </div>
        </section>
        <form className="login-form" onSubmit={submit}>
          <LockKey size={30} weight="duotone" />
          <h2>连接工作台</h2>
          <p>输入此服务器的访问口令</p>
          <label htmlFor="access">访问口令</label>
          <TextField.Root
            id="access"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="输入访问口令"
            size="3"
            autoComplete="current-password"
            required
          />
          <span className="form-hint">访问口令由服务器管理员提供</span>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <Button size="3" type="submit" disabled={busy || !password}>
            {busy ? '正在连接…' : '进入工作台'}
            <ArrowRight />
          </Button>
          <div className="login-foot">你的会话与任务，仅向授权访问者开放。</div>
        </form>
      </div>
      <footer className="login-footer">
        CODEX RELAY <span>把想法交给执行。</span>
      </footer>
    </main>
  );
}

function App() {
  const [uiConfig, setUiConfig] = useState(DEFAULT_CONFIG);
  const normalizePreference = (value) => normalizeModelPreference(value, uiConfig);
  const validPreference = (value) => validModelPreference(value, uiConfig);
  const resolvedModel = (value) => normalizePreference(value).id;
  useVisibleViewport();
  const navigationDrawer = useMedia('(max-width: 1023px)');
  const [modelPrefs, setModelPrefs] = useState(() => savedPreference('relay-model-prefs', {})),
    [newModel, setNewModel] = useState(() =>
      normalizePreference(savedPreference('relay-new-model', null)),
    );
  // Restore permissions, but require an explicit /plan in this page session.
  const [controlPrefs, setControlPrefs] = useState(() =>
      Object.fromEntries(
        Object.entries(savedPreference('relay-control-prefs', {}) || {}).map(([id, value]) => [
          id,
          normalizeControls({ ...value, collaboration: 'default' }),
        ]),
      ),
    ),
    [newControls, setNewControls] = useState(defaultControls),
    [commandOpen, setCommandOpen] = useState(false),
    [commandIndex, setCommandIndex] = useState(0);
  const [auth, setAuth] = useState(null),
    [threads, setThreads] = useState([]),
    [selected, setSelected] = useState(localStorage.getItem('relay-thread') || ''),
    [detailState, setDetail] = useState(null),
    [connected, setConnected] = useState(false),
    [search, setSearch] = useState(''),
    [newOpen, setNewOpen] = useState(false),
    [newText, setNewText] = useState(''),
    [newError, setNewError] = useState(''),
    [cwd, setCwd] = useState(''),
    [creating, setCreating] = useState(false),
    [pending, setPending] = useState(null),
    [mobile, setMobile] = useState(false),
    [activityOpen, setActivityOpen] = useState(false),
    [toast, setToast] = useState('');
  const detail = detailState?.id === selected ? detailState : null;
  const [draft, setDraft] = useSessionState(selected, emptyDraft);
  const { text, commandToken } = draft;
  const setText = (text) => setDraft((current) => ({ ...current, text }));
  const setCommandToken = (commandToken) => setDraft((current) => ({ ...current, commandToken }));
  const [sending, setSending] = useSessionState(selected, false);
  const [stopping, setStopping] = useSessionState(selected, false);
  const [error, setError] = useSessionState(selected, '');
  const attachments = useAttachments(selected),
    newAttachments = useAttachments('new');
  const fileInput = useRef(null),
    newFileInput = useRef(null);
  const [previewImage, setPreviewImage] = useState(null);
  const [sketchTarget, setSketchTarget] = useState(null);
  const [commandNavigated, setCommandNavigated] = useState(false);
  const [expandedHistory, setExpandedHistory] = useState(null),
    [historyLoading, setHistoryLoading] = useState(false);
  const selectedRef = useRef(selected),
    expandedRef = useRef(null),
    historyAnchor = useRef(null),
    historyRequest = useRef(null),
    previousScroll = useRef(0),
    touchStart = useRef(null);
  selectedRef.current = selected;
  const viewRef = useRef(null);
  if (viewRef.current?.id !== selected) viewRef.current = { id: selected, revision: 0 };
  const view = viewRef.current;
  const isCurrentView = () => viewRef.current === view;
  expandedRef.current = expandedHistory;
  const historyMode = expandedHistory === selected ? 'all' : 'recent';
  const [threadAction, setThreadAction] = useState(null),
    [threadName, setThreadName] = useState(''),
    [threadActionError, setThreadActionError] = useState(''),
    [threadActionBusy, setThreadActionBusy] = useState(false);
  const commandTokenButton = useRef(null);
  const openingThreadAction = useRef(false);
  const composedText = commandToken
    ? `/${commandToken}${text.trimStart() ? ' ' + text.trimStart() : ''}`
    : text;
  const end = useRef(null),
    input = useRef(null),
    scroll = useRef(null),
    stick = useRef(true);
  const preference = normalizePreference(
    modelPrefs[selected] || { id: detail?.model, effort: detail?.reasoningEffort },
  );
  const switchLocked = detail?.modelSwitchAllowed === false;
  const controls = normalizeControls(
    controlPrefs[selected] || { ...detail?.executionSettings, collaboration: 'default' },
  );
  const candidates = commandToken ? [] : commandCandidates(text);
  const matchingCommands = candidates.length ? candidates : COMMANDS;

  function selectControls(value) {
    setControlPrefs((current) => {
      const next = { ...current, [selected]: value };
      localStorage.setItem(
        'relay-control-prefs',
        JSON.stringify(
          Object.fromEntries(
            Object.entries(next).map(([id, settings]) => [id, { permission: settings.permission }]),
          ),
        ),
      );
      return next;
    });
  }
  function insertCommand(command) {
    setCommandToken(command.name);
    // Replacing a command preserves the argument; menu searches are discarded.
    if (!commandToken && commandCandidates(text).length) setText('');
    setCommandOpen(false);
    requestAnimationFrame(() => input.current?.focus());
  }
  function removeCommand() {
    setCommandToken(null);
    setCommandOpen(false);
    requestAnimationFrame(() => input.current?.focus());
  }
  function changeComposer(value, composing = false) {
    const info = !commandToken && !composing ? commandInfo(value) : null;
    // Wait for a separator or menu confirmation: /goal may still become /goal/file.
    if (info && /^\s*\/\w+\s/.test(value)) {
      setCommandToken(info.name);
      setText(info.argument);
      setCommandOpen(false);
    } else {
      setText(value);
      setCommandOpen(!commandToken && !composing && commandCandidates(value).length > 0);
    }
    setCommandIndex(0);
    setCommandNavigated(false);
  }
  function deleteCommandAtBoundary(element) {
    return commandToken && element.selectionStart === 0 && element.selectionEnd === 0;
  }
  useEffect(() => {
    const element = input.current;
    if (!element) return;
    // Mobile keyboards may send beforeinput without any Backspace keydown.
    const beforeInput = (event) => {
      if (event.isComposing) return;
      if (
        (event.inputType === 'deleteContentBackward' ||
          event.inputType === 'deleteWordBackward' ||
          event.inputType === 'deleteSoftLineBackward') &&
        deleteCommandAtBoundary(element)
      ) {
        event.preventDefault();
        removeCommand();
      } else if (event.inputType === 'deleteContentForward' && !element.value && commandToken) {
        event.preventDefault();
        removeCommand();
      }
    };
    element.addEventListener('beforeinput', beforeInput);
    return () => element.removeEventListener('beforeinput', beforeInput);
  }, [auth, commandToken, selected]);
  async function reply(requestId, body) {
    await api('/threads/' + selected + '/requests/' + requestId, body);
    await refreshGoal(selected);
  }
  function selectModel(value) {
    setModelPrefs((current) => {
      const next = { ...current, [selected]: value };
      localStorage.setItem('relay-model-prefs', JSON.stringify(next));
      return next;
    });
  }
  function selectNewModel(value) {
    setNewModel(value);
    localStorage.setItem('relay-new-model', JSON.stringify(value));
  }
  async function initialize(snapshot) {
    try {
      const d = snapshot || (await api('/threads'));
      setThreads(d.threads);
      setCwd(d.defaultCwd);
      const config = d.clientConfig || DEFAULT_CONFIG;
      setUiConfig(config);
      setNewModel(normalizeModelPreference(savedPreference('relay-new-model', null), config));
      setAuth(true);
      setError('');
      setSelected((s) => (d.threads.some((t) => t.id === s) ? s : d.threads[0]?.id || ''));
    } catch (e) {
      if (e.auth) setAuth(false);
      else {
        setAuth(false);
        setError(e.message);
      }
    }
  }
  useEffect(() => {
    initialize();
  }, []);
  useEffect(() => {
    const onKey = (e) => {
      if (
        e.key.toLowerCase() === 'n' &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) &&
        !e.target.isContentEditable
      ) {
        e.preventDefault();
        setNewOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (!auth) return;
    setDetail((current) => (current?.id === selected ? current : null));
    setConnected(false);
    localStorage.setItem('relay-thread', selected);
    let active = true;
    if (selected)
      refreshGoal(selected).catch((e) => {
        if (active && isCurrentView()) setError(e.message);
      });
    const stream = new EventSource(
      '/api/events' +
        (selected ? '?thread=' + encodeURIComponent(selected) + '&history=' + historyMode : ''),
    );
    stream.onopen = () => {
      if (active && isCurrentView()) setConnected(true);
    };
    stream.onmessage = (e) => {
      if (!active || !isCurrentView()) return;
      const d = JSON.parse(e.data);
      setThreads(d.threads);
      if (d.removedThread === selected) {
        setDetail(null);
        setSelected(d.threads[0]?.id || '');
      } else if (d.selected?.id === selected) {
        view.revision += 1;
        setDetail((current) =>
          current?.id === d.selected.id &&
          current?.history?.mode === 'all' &&
          d.selected.history?.mode === 'recent'
            ? current
            : d.selected,
        );
      }
      setConnected(true);
    };
    stream.addEventListener('backend-error', () => {
      if (active && isCurrentView()) setError('任务状态读取失败，正在自动重试');
    });
    stream.onerror = () => {
      if (!active || !isCurrentView()) return;
      setConnected(false);
      api('/threads').catch((e) => {
        if (e.auth) setAuth(false);
      });
    };
    return () => {
      active = false;
      stream.close();
    };
  }, [auth, selected, historyMode]);
  useLayoutEffect(() => {
    setCommandOpen(false);
    setCommandIndex(0);
    setCommandNavigated(false);
    setToast('');
    setHistoryLoading(false);
    historyRequest.current = null;
    historyAnchor.current = null;
    previousScroll.current = 0;
    touchStart.current = null;
    stick.current = true;
  }, [selected]);
  useLayoutEffect(() => {
    const el = scroll.current;
    if (!el) return;
    if (historyAnchor.current?.tid === selected && detail?.history?.mode === 'all') {
      const anchor = historyAnchor.current;
      el.scrollTop = el.scrollHeight - anchor.height + anchor.top;
      previousScroll.current = el.scrollTop;
      historyAnchor.current = null;
    } else if (stick.current) {
      el.scrollTop = el.scrollHeight;
      previousScroll.current = el.scrollTop;
    }
  }, [
    detail?.messages?.length,
    detail?.notes?.length,
    detail?.commandRecords,
    detail?.requests?.length,
    detail?.history?.mode,
    selected,
  ]);
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      if (stick.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
    });
    if (scroll.current) observer.observe(scroll.current);
    return () => observer.disconnect();
  }, [auth, selected]);
  useEffect(() => {
    setMobile(false);
  }, [navigationDrawer]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(''), 4200);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    if (!pending) return;
    let active = true;
    let polling = false;
    const t = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const job = await api('/jobs/' + pending.id);
        if (!active) return;
        if (job.thread_id) {
          if (viewRef.current === pending.view) choose(job.thread_id);
          setPending(null);
          setToast('新任务已启动');
        } else if (job.status === 'failed') {
          setNewError(job.error || '任务启动失败');
          setNewOpen(true);
          setPending(null);
        }
      } catch (e) {
        if (!active) return;
        setNewError(e.message);
        setNewOpen(true);
        setPending(null);
      } finally {
        polling = false;
      }
    }, 1000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [pending]);
  function choose(id) {
    if (id !== selected) setExpandedHistory(null);
    stick.current = true;
    previousScroll.current = 0;
    historyAnchor.current = null;
    setSelected(id);
    setMobile(false);
    setError('');
  }
  function openThreadAction(kind, thread) {
    openingThreadAction.current = true;
    setThreadName(thread.title || '');
    setThreadActionError('');
    requestAnimationFrame(() => {
      setMobile(false);
      setThreadAction({ kind, thread });
    });
  }
  async function submitThreadAction(e) {
    e.preventDefault();
    if (threadActionBusy) return;
    const { kind, thread } = threadAction;
    setThreadActionBusy(true);
    setThreadActionError('');
    try {
      const result = await api(
        '/threads/' + thread.id + '/' + kind,
        kind === 'rename' ? { name: threadName.trim() } : {},
      );
      if (kind === 'rename') {
        setThreads((all) =>
          all.map((t) => (t.id === thread.id ? { ...t, title: result.title } : t)),
        );
        setDetail((current) =>
          current?.id === thread.id ? { ...current, title: result.title } : current,
        );
      } else {
        setThreads((all) => all.filter((t) => t.id !== thread.id));
        if (selectedRef.current === thread.id) {
          setDetail(null);
          setSelected(threads.find((t) => t.id !== thread.id)?.id || '');
        }
      }
      setThreadAction(null);
      setToast(kind === 'rename' ? '会话已重命名' : '会话已归档，记录已保留');
    } catch (e) {
      setThreadActionError(e.message);
    } finally {
      setThreadActionBusy(false);
    }
  }
  async function send(value = composedText) {
    if (
      (!value.trim() && !attachments.items.length) ||
      !selected ||
      !detail ||
      sending ||
      stopping ||
      attachments.busy
    )
      return;
    if (attachments.items.length && switchLocked) {
      setError('此会话由其他客户端控制，暂不能接收附件；请等待本轮结束或新建任务');
      return;
    }
    const parsedCommand = commandInfo(value);
    let nextControls = controls;
    if (parsedCommand) {
      const { name: command, argument } = parsedCommand;
      if (
        attachments.items.length &&
        !(
          (['plan', 'default'].includes(command) && argument) ||
          (command === 'goal' && argument && !['pause', 'resume', 'clear'].includes(argument))
        )
      ) {
        setError('此指令不接受附件，请输入需求后发送');
        return;
      }
      if (['permissions', 'model', 'status', 'help'].includes(command) && !argument) {
        if (switchLocked && ['permissions', 'model'].includes(command)) {
          setError('此会话由原客户端控制，请在原客户端切换或新建任务');
          return;
        }
      }
      if (command === 'plan' || command === 'default') {
        if (switchLocked) {
          setError('该会话由原客户端控制，请在原客户端切换模式或新建任务');
          return;
        }
        nextControls = { ...controls, collaboration: command };
      }
    }
    if (!validPreference(preference)) {
      setError('请选择有效的模型和推理强度');
      return;
    }
    setSending(true);
    setCommandOpen(false);
    setError('');
    try {
      const result = await api('/threads/' + selected + '/messages', {
        text: value,
        attachments: attachments.ids,
        mode: 'task',
        model: switchLocked ? null : resolvedModel(preference),
        effort: switchLocked ? null : preference.effort,
        permission: switchLocked ? null : nextControls.permission,
        collaboration: switchLocked ? 'default' : nextControls.collaboration,
      });
      // Preserve edits made while this request was in flight, including A→B→A.
      setDraft((current) => (current === draft ? emptyDraft : current));
      attachments.clear();
      if (result.pendingMessage && isCurrentView()) {
        view.revision += 1;
        setDetail((current) => {
          if (current?.id !== selected) return current;
          const items = (current.pendingMessages || []).filter(
            (item) => item.id !== result.pendingMessage.id,
          );
          return {
            ...current,
            pendingMessages: [...items, result.pendingMessage],
            pendingCount: items.length + 1,
          };
        });
      }
      if (isCurrentView()) stick.current = true;
      const info = commandInfo(value);
      if (info && ['plan', 'default'].includes(info.name)) selectControls(nextControls);
      await refreshGoal(selected);
      setSending(false);
      if (!isCurrentView()) return;
      if (result.kind === 'command' && info) {
        if (info.name === 'help') setCommandOpen(true);
        if (info.name === 'status') setActivityOpen(true);
        if (['permissions', 'model'].includes(info.name))
          requestAnimationFrame(() => {
            if (!isCurrentView()) return;
            document
              .querySelector(
                info.name === 'permissions'
                  ? '.composer [aria-label="操作权限"]'
                  : '.composer [aria-label="本条指令模型"]',
              )
              ?.click();
          });
      }
      setToast(result.message || (info ? `${info.title}已提交` : '消息已提交'));
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  async function create(e) {
    e.preventDefault();
    if (newAttachments.busy) return;
    if (!validPreference(newModel)) {
      setNewError('请填写有效的模型 ID');
      return;
    }
    setCreating(true);
    setNewError('');
    try {
      const d = await api('/tasks', {
        text: newText,
        attachments: newAttachments.ids,
        cwd,
        model: resolvedModel(newModel),
        effort: newModel.effort,
        ...newControls,
      });
      setPending({ id: d.jobId, view });
      setNewOpen(false);
      setNewText('');
      newAttachments.clear();
      setToast('正在启动 Codex 新任务');
    } catch (e) {
      setNewError(e.message);
    } finally {
      setCreating(false);
    }
  }
  function fetchThread(tid) {
    return api('/threads/' + tid + '?history=' + (expandedRef.current === tid ? 'all' : 'recent'));
  }
  async function loadHistory() {
    const tid = selected;
    if (!detail?.history?.hasMore || historyRequest.current?.view === view) return;
    const request = { view, revision: ++view.revision };
    historyRequest.current = request;
    setHistoryLoading(true);
    try {
      const next = await api('/threads/' + tid + '?history=all');
      if (!isCurrentView() || next.id !== tid) return;
      const el = scroll.current;
      historyAnchor.current = { tid, height: el.scrollHeight, top: el.scrollTop };
      stick.current = false;
      setExpandedHistory(tid);
      if (view.revision === request.revision) setDetail(next);
    } catch (e) {
      if (isCurrentView()) setError(e.message);
    } finally {
      if (historyRequest.current === request) {
        historyRequest.current = null;
        setHistoryLoading(false);
      }
    }
  }
  function scrollHistory() {
    const el = scroll.current;
    const upward = el.scrollTop < previousScroll.current;
    previousScroll.current = el.scrollTop;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    if (upward && el.scrollTop < 80) loadHistory();
  }
  async function refreshGoal(tid) {
    if (!isCurrentView() || tid !== selected) return;
    const revision = ++view.revision;
    try {
      const next = await fetchThread(tid);
      if (isCurrentView() && view.revision === revision && next.id === tid) setDetail(next);
    } catch (e) {
      if (isCurrentView() && view.revision === revision) throw e;
    }
  }
  async function stop() {
    if (stopping) return;
    const tid = selected;
    setStopping(true);
    setError('');
    try {
      const result = await api('/threads/' + tid + '/stop', {});
      await refreshGoal(tid);
      if (isCurrentView()) setToast(result.message || '当前回复已停止');
    } catch (e) {
      setError(e.message);
    } finally {
      setStopping(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(selected);
      setToast('已复制会话 ID');
    } catch {
      setToast(selected);
    }
  }
  const messages = useMemo(
    () =>
      detail
        ? [
            ...detail.messages,
            ...detail.notes.map((n) => ({ ...n, at: new Date(n.created * 1000).toISOString() })),
            ...(detail.commandRecords || []).map((r) => ({
              ...r,
              kind: 'command-record',
              at: new Date(r.created * 1000).toISOString(),
            })),
          ].sort((a, b) => new Date(a.at) - new Date(b.at))
        : [],
    [detail?.messages, detail?.notes, detail?.commandRecords],
  );
  const workingState = operationState(detail);
  const replying = detail?.id === selected && ['starting', 'running'].includes(detail?.status);
  const hasDraft = Boolean(composedText.trim() || attachments.items.length);
  const draftCommand = commandToken ? commandInfo(composedText) : null;
  const visibleThreads = threads.filter((t) =>
    (t.title + ' ' + t.cwd).toLowerCase().includes(search.toLowerCase()),
  );
  if (auth === null)
    return (
      <div className="boot">
        <Mark />
        <p>正在连接你的工作台…</p>
        <div className="skeleton" />
      </div>
    );
  if (!auth) return <Login onLogin={initialize} />;
  return (
    <div className="app-shell">
      <ResponsivePanel
        drawer={navigationDrawer}
        open={mobile}
        onOpenChange={setMobile}
        side="left"
        title="工作会话"
      >
        <aside className={'sidebar ' + (mobile ? 'open' : '')}>
          <div className="sidebar-brand">
            <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
              <Mark small />
              <span>
                codex<span className="brand-light">relay</span>
              </span>
            </a>
            {navigationDrawer && (
              <IconButton
                variant="ghost"
                aria-label="关闭会话列表"
                onClick={() => setMobile(false)}
              >
                <X size={21} />
              </IconButton>
            )}
          </div>
          <div className="workspace-name">
            <div className="workspace-avatar">U</div>
            <div>
              Ubuntu 工作空间<small>个人远程工作台</small>
            </div>
            <span className="live-dot" />
          </div>
          <Button
            className="new-task"
            size="3"
            onClick={() => {
              setMobile(false);
              setNewOpen(true);
            }}
          >
            <Plus size={18} />
            新建任务<span className="keycap">N</span>
          </Button>
          <div className="sidebar-heading">
            <span>工作会话</span>
            <span>{threads.length.toString().padStart(2, '0')}</span>
          </div>
          <div className="sidebar-search">
            <MagnifyingGlass size={16} />
            <input
              aria-label="搜索会话"
              placeholder="搜索任务或目录"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <nav className="thread-list" aria-label="Codex 会话">
            {visibleThreads.map((t) => (
              <div key={t.id} className={'thread-row ' + (selected === t.id ? 'selected' : '')}>
                <button
                  className={'thread ' + (selected === t.id ? 'selected' : '')}
                  onClick={() => choose(t.id)}
                >
                  <span className={'thread-dot ' + t.status} />
                  <div>
                    <strong>{t.title || '未命名任务'}</strong>
                    <span>
                      {t.cwd.split('/').filter(Boolean).pop()}
                      <i>·</i>
                      {when(t.updated_at)}
                    </span>
                  </div>
                </button>
                <DropdownMenu.Root
                  onOpenChange={(open) => {
                    if (open) openingThreadAction.current = false;
                  }}
                >
                  <DropdownMenu.Trigger>
                    <IconButton
                      type="button"
                      variant="ghost"
                      className="thread-actions-trigger"
                      aria-label={'会话操作：' + (t.title || '未命名任务')}
                    >
                      <DotsThree size={22} />
                    </IconButton>
                  </DropdownMenu.Trigger>
                  <DropdownMenu.Content
                    align="end"
                    onCloseAutoFocus={(e) => {
                      if (openingThreadAction.current) {
                        e.preventDefault();
                        requestAnimationFrame(() =>
                          document
                            .querySelector(
                              '.thread-action-dialog input, .thread-action-dialog button',
                            )
                            ?.focus(),
                        );
                      }
                    }}
                  >
                    <DropdownMenu.Item onSelect={() => openThreadAction('rename', t)}>
                      <PencilSimple />
                      重命名
                    </DropdownMenu.Item>
                    <DropdownMenu.Separator />
                    <DropdownMenu.Item
                      color="red"
                      disabled={
                        t.managed ||
                        ['starting', 'running'].includes(t.status) ||
                        t.modelSwitchAllowed === false
                      }
                      onSelect={() => openThreadAction('archive', t)}
                    >
                      <Archive />
                      归档会话
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Root>
              </div>
            ))}
            {!visibleThreads.length && <p className="empty-search">没有匹配的会话</p>}
          </nav>
          <div className="sidebar-bottom">
            <div className="host-state">
              <div className="host-icon">
                <TerminalWindow size={19} />
              </div>
              <div>
                本机 Codex
                <small>
                  <span className={'live-dot ' + (!connected ? 'offline' : '')} />
                  {connected ? '实时连接正常' : '连接中，自动重试'}
                </small>
              </div>
            </div>
            <button
              className="logout"
              onClick={async () => {
                await api('/logout', {});
                setAuth(false);
              }}
            >
              <SignOut size={17} />
              退出工作台
            </button>
          </div>
        </aside>
      </ResponsivePanel>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <IconButton
              variant="ghost"
              className="mobile-menu"
              aria-label="打开会话列表"
              onClick={() => setMobile(true)}
            >
              <List size={22} />
            </IconButton>
            <span>工作空间</span>
            <CaretRight size={12} />
            <strong>任务控制台</strong>
          </div>
          <div className="topbar-right">
            <span className={'connection ' + (!connected ? 'offline' : '')}>
              <span className="live-dot" />
              {connected ? '实时同步' : '重新连接中'}
            </span>
            <div className="user-avatar">U</div>
          </div>
        </header>
        <main className="main-content">
          <div className="workbench">
            <section className="conversation">
              <div className="conversation-header">
                <div className="conversation-icon">
                  <ChatCircle size={22} />
                </div>
                <div className="conversation-title">
                  <h2>{detail?.title || '选择一个工作会话'}</h2>
                  <span>
                    <Folder size={12} />
                    {detail?.cwd || '开启一段新的工作'}
                  </span>
                </div>
                {detail && (
                  <Status
                    value={detail.requests?.length ? 'awaiting' : detail.status}
                    label={workingState?.label}
                  />
                )}
                <Tooltip content="执行现场">
                  <IconButton
                    className="activity-toggle"
                    variant="ghost"
                    aria-label="查看执行详情"
                    onClick={() => setActivityOpen(!activityOpen)}
                  >
                    <Pulse size={21} />
                  </IconButton>
                </Tooltip>
              </div>
              <div
                className="messages"
                ref={scroll}
                onScroll={scrollHistory}
                onWheel={(e) => {
                  if (e.deltaY < 0 && scroll.current.scrollTop < 80) loadHistory();
                }}
                onTouchStart={(e) => {
                  touchStart.current = e.touches[0]?.clientY;
                }}
                onTouchMove={(e) => {
                  if (
                    touchStart.current !== null &&
                    e.touches[0]?.clientY - touchStart.current > 20 &&
                    scroll.current.scrollTop < 80
                  )
                    loadHistory();
                }}
              >
                {pending && (
                  <div className="pending">
                    <span className="live-dot" />
                    正在为新任务连接 Codex…
                  </div>
                )}
                {!detail && selected && (
                  <div className="loading-messages">
                    <div className="skeleton" />
                    <div className="skeleton" />
                    <div className="skeleton" />
                  </div>
                )}
                {!selected && (
                  <div className="empty">
                    <TerminalWindow size={40} weight="duotone" />
                    <h2>下一件事，从这里开始。</h2>
                    <p>创建任务，或从左侧选择已有会话。</p>
                    <Button onClick={() => setNewOpen(true)}>
                      <Plus />
                      新建任务
                    </Button>
                  </div>
                )}
                {detail?.history?.hasMore && (
                  <button
                    type="button"
                    className="history-more"
                    disabled={historyLoading}
                    onClick={loadHistory}
                  >
                    {historyLoading ? '正在加载历史记录…' : '向上滚动加载更早记录'}
                  </button>
                )}
                {detail && (
                  <div className="timeline-date">
                    <span />
                    会话记录 ·{' '}
                    {messages.length
                      ? new Date(messages[0].at).toLocaleDateString('zh-CN')
                      : '今天'}
                    <span />
                  </div>
                )}
                <MessageList key={selected} messages={messages} onPreview={setPreviewImage} />
                {workingState && (
                  <div className="working" role="status">
                    <span className="working-bars">
                      <i />
                      <i />
                      <i />
                    </span>
                    {workingState.text}
                  </div>
                )}
                {detail?.requests?.map((request) => (
                  <InteractionCard
                    key={selected + ':' + request.id}
                    request={request}
                    onReply={reply}
                  />
                ))}
                <div ref={end} />
              </div>
              <div className="composer-area">
                <div className="composer-context">
                  {detail?.id === selected && (
                    <GoalBar
                      key={'goal:' + selected}
                      threadId={selected}
                      goal={detail.goal}
                      locked={switchLocked}
                      api={api}
                      settings={{
                        model: resolvedModel(preference),
                        effort: preference.effort,
                        ...controls,
                      }}
                      onRefresh={refreshGoal}
                      onToast={(message) => {
                        if (isCurrentView()) setToast(message);
                      }}
                    />
                  )}
                  {detail?.id === selected && (
                    <MessageQueue
                      key={'queue:' + selected}
                      threadId={selected}
                      items={detail.pendingMessages}
                      api={api}
                      onRefresh={refreshGoal}
                      onToast={(message) => {
                        if (isCurrentView()) setToast(message);
                      }}
                      onError={setError}
                    />
                  )}
                  {error && (
                    <div className="error" role="alert">
                      {error}
                      <button aria-label="关闭错误" onClick={() => setError('')}>
                        <X />
                      </button>
                    </div>
                  )}
                  {(attachments.items.length > 0 || attachments.error) && (
                    <div
                      className="composer-attachments"
                      {...attachmentEvents(attachments, sending || switchLocked || !selected)}
                    >
                      <AttachmentTray
                        attachments={attachments}
                        onPreview={setPreviewImage}
                        disabled={sending}
                      />
                    </div>
                  )}
                </div>
                <form
                  className="composer"
                  {...attachmentEvents(attachments, sending || switchLocked || !selected)}
                  onSubmit={(e) => {
                    e.preventDefault();
                    send();
                  }}
                >
                  <AttachmentInput
                    inputRef={fileInput}
                    attachments={attachments}
                    disabled={!detail || sending || switchLocked}
                  />
                  <>
                    {draftCommand && (
                      <div className="command-draft">
                        <button
                          ref={commandTokenButton}
                          type="button"
                          className="command-token"
                          contentEditable={false}
                          aria-label={`移除 /${draftCommand.name} 指令`}
                          title="点击或按 Delete / Backspace 整体移除"
                          onClick={removeCommand}
                          onKeyDown={(e) => {
                            if (e.key === 'Backspace' || e.key === 'Delete') {
                              e.preventDefault();
                              removeCommand();
                            } else if (e.key === 'ArrowRight') {
                              e.preventDefault();
                              input.current?.focus();
                              input.current?.setSelectionRange(0, 0);
                            }
                          }}
                        >
                          <code>/{draftCommand.name}</code>
                          <X size={14} />
                        </button>
                        <span>{draftCommand.title}</span>
                      </div>
                    )}
                  </>
                  <textarea
                    ref={input}
                    aria-label="向 Codex 发送消息"
                    placeholder={
                      selected
                        ? commandToken
                          ? {
                              goal: '输入目标，或 pause / resume / clear；留空查看目标',
                              plan: '输入需要规划的需求，或直接发送切换模式',
                              default: '输入执行需求，或直接发送切换模式',
                            }[commandToken] || '此指令无需参数，可直接发送'
                          : controls.collaboration === 'plan'
                            ? '计划模式：输入需求，或用 /default 返回执行'
                            : '发送消息，或输入 / 使用指令…'
                        : '先选择会话或新建任务'
                    }
                    value={text}
                    onChange={(e) => changeComposer(e.target.value, e.nativeEvent.isComposing)}
                    onCompositionEnd={(e) => changeComposer(e.currentTarget.value)}
                    disabled={!selected}
                    aria-controls={commandOpen ? 'slash-commands' : undefined}
                    aria-expanded={commandOpen}
                    aria-autocomplete="list"
                    onKeyDown={(e) => {
                      if (e.nativeEvent.isComposing) return;
                      if (
                        commandToken &&
                        ((e.key === 'Backspace' && deleteCommandAtBoundary(e.currentTarget)) ||
                          (e.key === 'Delete' && !text))
                      ) {
                        e.preventDefault();
                        removeCommand();
                        return;
                      }
                      if (e.key === 'ArrowLeft' && deleteCommandAtBoundary(e.currentTarget)) {
                        e.preventDefault();
                        commandTokenButton.current?.focus();
                        return;
                      }
                      if (commandOpen && matchingCommands.length) {
                        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                          e.preventDefault();
                          setCommandNavigated(true);
                          setCommandIndex(
                            (i) =>
                              (i + (e.key === 'ArrowDown' ? 1 : -1) + matchingCommands.length) %
                              matchingCommands.length,
                          );
                          return;
                        }
                        if (e.key === 'Escape') {
                          e.preventDefault();
                          setCommandOpen(false);
                          return;
                        }
                        if (
                          (e.key === 'Tab' ||
                            (e.key === 'Enter' && (commandNavigated || commandInfo(text)))) &&
                          !e.shiftKey
                        ) {
                          e.preventDefault();
                          insertCommand(matchingCommands[commandIndex % matchingCommands.length]);
                          return;
                        }
                      }
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        send();
                      }
                    }}
                  />
                  <div className="composer-toolbar">
                    <div className="composer-tools">
                      <Popover.Root open={commandOpen} onOpenChange={setCommandOpen}>
                        <Popover.Trigger>
                          <IconButton
                            type="button"
                            variant="soft"
                            aria-label="指令菜单"
                            disabled={!selected}
                          >
                            /
                          </IconButton>
                        </Popover.Trigger>
                        <Popover.Content
                          side="top"
                          align="start"
                          className="command-menu"
                          onOpenAutoFocus={(e) => e.preventDefault()}
                          onCloseAutoFocus={(e) => e.preventDefault()}
                        >
                          <button
                            type="button"
                            className="upload-menu-action"
                            disabled={!detail || sending || switchLocked}
                            onClick={() => {
                              setCommandOpen(false);
                              fileInput.current?.click();
                            }}
                          >
                            <Plus size={18} />
                            <span>上传文件</span>
                            <small>图片 / 文档</small>
                          </button>
                          <button
                            type="button"
                            className="upload-menu-action"
                            disabled={
                              !selected || sending || switchLocked || attachments.items.length >= 6
                            }
                            onClick={() => {
                              setCommandOpen(false);
                              setSketchTarget(selected);
                            }}
                          >
                            <Plus size={18} />
                            <span>绘画参考</span>
                            <small>画完添加图片</small>
                          </button>
                          <div className="command-menu-heading">会话指令</div>
                          <div id="slash-commands" role="listbox" aria-label="可用指令">
                            {matchingCommands.map((command, i) => (
                              <button
                                type="button"
                                role="option"
                                aria-selected={i === commandIndex}
                                key={command.name}
                                onClick={() => insertCommand(command)}
                              >
                                <strong>/{command.name}</strong>
                                <span>{command.hint}</span>
                              </button>
                            ))}
                            {!matchingCommands.length && <p>无匹配指令，输入 /help 查看列表</p>}
                          </div>
                          <small>/goal pause 暂停 · /goal resume 继续 · /goal clear 清除</small>
                        </Popover.Content>
                      </Popover.Root>
                      <PermissionPicker
                        key={'permission:' + selected}
                        value={controls}
                        onChange={selectControls}
                        disabled={!detail || sending || switchLocked}
                      />
                    </div>
                    <div className="composer-send">
                      <div
                        title={
                          switchLocked
                            ? '此会话由原客户端控制，需在原客户端切换或新建任务'
                            : '下一条指令生效'
                        }
                      >
                        <ModelPicker
                          key={'model:' + selected}
                          config={uiConfig}
                          label="本条指令模型"
                          preference={preference}
                          onChange={selectModel}
                          disabled={!detail || sending || switchLocked}
                        />
                      </div>
                      {replying && !hasDraft ? (
                        <IconButton
                          type="button"
                          size="3"
                          className="stop-reply"
                          aria-label={stopping ? '正在停止回复' : '停止回复'}
                          title={
                            switchLocked
                              ? '此会话由其他客户端控制，请在原客户端停止'
                              : '停止当前回复并暂停目标'
                          }
                          disabled={stopping || sending || switchLocked}
                          onClick={stop}
                        >
                          <Stop size={19} weight="fill" />
                        </IconButton>
                      ) : (
                        <IconButton
                          type="submit"
                          size="3"
                          aria-label="发送消息"
                          title={replying ? '发送并加入队列' : '发送消息'}
                          disabled={
                            !detail ||
                            !hasDraft ||
                            sending ||
                            stopping ||
                            attachments.busy ||
                            !validPreference(preference)
                          }
                        >
                          <ArrowUp size={21} />
                        </IconButton>
                      )}
                    </div>
                  </div>
                </form>
              </div>
            </section>
            <ResponsivePanel
              drawer={true}
              open={activityOpen}
              onOpenChange={setActivityOpen}
              side="right"
              title="执行现场"
            >
              <aside className={'inspector ' + (activityOpen ? 'expanded' : '')}>
                <div className="inspector-heading">
                  <h2>执行现场</h2>
                  <span>LIVE</span>
                  <IconButton
                    className="activity-toggle"
                    variant="ghost"
                    aria-label="关闭执行详情"
                    onClick={() => setActivityOpen(false)}
                  >
                    <X />
                  </IconButton>
                </div>
                <div className="current-state">
                  <div className="state-orbit">
                    <TerminalWindow size={30} weight="duotone" />
                  </div>
                  <strong>
                    {workingState?.label || (detail ? statuses[detail.status] : '等待选择会话')}
                  </strong>
                  <p>
                    {workingState
                      ? workingState.text
                      : detail?.status === 'completed'
                        ? '本轮执行结束，随时开始下一步。'
                        : '每一次操作，都会留下清晰记录。'}
                  </p>
                </div>
                <div className="inspector-details">
                  <div>
                    <span>运行模型</span>
                    <strong>{detail?.model || '待连接'}</strong>
                  </div>
                  <div>
                    <span>累计 Tokens</span>
                    <strong>{number(detail?.tokens)}</strong>
                  </div>
                  <div>
                    <span>会话来源</span>
                    <strong>
                      {detail?.source === 'vscode' ? 'VS Code' : detail?.source || 'Codex'}
                    </strong>
                  </div>
                  <button className="session-id" onClick={copy} disabled={!selected}>
                    <span>
                      {selected ? selected.slice(0, 8) + '…' + selected.slice(-4) : '暂无会话'}
                    </span>
                    <Copy size={13} />
                  </button>
                </div>
                {detail?.goal && (
                  <div className="goal">
                    <div>
                      <Lightning size={15} />
                      当前目标
                    </div>
                    <p>{detail.goal.objective}</p>
                  </div>
                )}
                <div className="activity-heading">
                  <h3>最近动态</h3>
                  <Clock size={15} />
                </div>
                <div className="activities">
                  {detail?.activities
                    ?.slice(-9)
                    .reverse()
                    .map((a, i) => (
                      <div className={'activity ' + (i === 0 ? 'latest' : '')} key={a.id}>
                        <div className="activity-point">
                          {a.type === 'complete' ? (
                            <CheckCircle size={15} />
                          ) : (
                            <Circle size={9} weight="fill" />
                          )}
                        </div>
                        <div>
                          <p>{a.label}</p>
                          <time>{when(a.at)}</time>
                        </div>
                      </div>
                    ))}
                  {!detail?.activities?.length && (
                    <p className="muted">任务开始后，执行动态会显示在这里。</p>
                  )}
                </div>
                <div className="inspector-foot">
                  <Broadcast size={15} />
                  自动同步，无需手动刷新
                </div>
              </aside>
            </ResponsivePanel>
          </div>
        </main>
      </div>
      <Dialog.Root
        open={Boolean(threadAction)}
        onOpenChange={(open) => {
          if (!open && !threadActionBusy) setThreadAction(null);
        }}
      >
        <Dialog.Content
          className="task-dialog thread-action-dialog"
          maxWidth="440px"
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            requestAnimationFrame(() =>
              document
                .querySelector('.thread-action-dialog input, .thread-action-dialog button')
                ?.focus(),
            );
          }}
        >
          <Dialog.Title>{threadAction?.kind === 'rename' ? '重命名会话' : '归档会话'}</Dialog.Title>
          <Dialog.Description size="2" mb="4">
            {threadAction?.kind === 'rename'
              ? '设置一个方便识别的名称，会同步到其他设备。'
              : '归档后将从工作会话列表移除，聊天记录和文件会保留。'}
          </Dialog.Description>
          <form onSubmit={submitThreadAction}>
            {threadAction?.kind === 'rename' ? (
              <TextField.Root
                aria-label="会话名称"
                value={threadName}
                onChange={(e) => setThreadName(e.target.value)}
                maxLength={120}
                disabled={threadActionBusy}
                autoFocus
                required
              />
            ) : (
              <p className="archive-thread-name">{threadAction?.thread.title || '未命名任务'}</p>
            )}
            {threadActionError && (
              <p role="alert" className="attachment-error">
                {threadActionError}
              </p>
            )}
            <div className="dialog-actions">
              <Button
                type="button"
                variant="soft"
                color="gray"
                disabled={threadActionBusy}
                onClick={() => setThreadAction(null)}
              >
                取消
              </Button>
              <Button
                type="submit"
                color={threadAction?.kind === 'archive' ? 'red' : undefined}
                disabled={
                  threadActionBusy || (threadAction?.kind === 'rename' && !threadName.trim())
                }
              >
                {threadActionBusy
                  ? '正在保存…'
                  : threadAction?.kind === 'rename'
                    ? '保存名称'
                    : '确认归档'}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Root>
      <SketchPad
        open={Boolean(sketchTarget)}
        scope={sketchTarget}
        onClose={() => setSketchTarget(null)}
        disabled={
          sketchTarget === 'new'
            ? creating
            : !selected || sending || switchLocked || sketchTarget !== selected
        }
        onAttach={(file) => (sketchTarget === 'new' ? newAttachments : attachments).add([file])}
      />
      <ImagePreview asset={previewImage} onClose={() => setPreviewImage(null)} />
      <Dialog.Root open={newOpen} onOpenChange={setNewOpen}>
        <Dialog.Content maxWidth="520px" className="task-dialog">
          <Dialog.Title>开始一个新任务</Dialog.Title>
          <Dialog.Description size="2" mb="5">
            告诉 Codex 你要做什么，任务将在服务器上执行。
          </Dialog.Description>
          <form
            onSubmit={create}
            className="new-form"
            {...attachmentEvents(newAttachments, creating)}
          >
            <label htmlFor="task-text">任务内容</label>
            <textarea
              id="task-text"
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              placeholder="例如：检查这个项目的构建状态，并修复发现的问题。"
              required={!newAttachments.items.length}
              rows={5}
            />
            <AttachmentInput
              inputRef={newFileInput}
              attachments={newAttachments}
              disabled={creating}
              label="新任务上传文件"
            />
            <div className="task-image-actions">
              <Button
                type="button"
                variant="soft"
                onClick={() => newFileInput.current?.click()}
                disabled={creating}
              >
                <Plus />
                添加文件
              </Button>
              <Button
                type="button"
                variant="soft"
                onClick={() => setSketchTarget('new')}
                disabled={creating || newAttachments.items.length >= 6}
              >
                绘画参考
              </Button>
            </div>
            <AttachmentTray
              attachments={newAttachments}
              onPreview={setPreviewImage}
              disabled={creating}
            />
            <ModelPicker
              config={uiConfig}
              label="任务模型"
              preference={newModel}
              onChange={selectNewModel}
              disabled={creating}
            />
            <div className="new-controls">
              <PermissionPicker value={newControls} onChange={setNewControls} disabled={creating} />
            </div>
            <p className="permission-hint">
              {newControls.permission === 'full'
                ? '完全访问：可访问服务器文件与网络，不逐项询问批准。'
                : newControls.permission === 'read-only'
                  ? '只读：允许查看资料，不授权修改文件或提升权限。'
                  : '按需批准：工作目录内可修改，额外权限由你在对话中批准。'}
            </p>
            <label htmlFor="task-cwd">工作目录</label>
            <DirectoryPicker value={cwd} onChange={setCwd} api={api} disabled={creating} />
            <p className="form-hint">使用现有 Codex 配置，以当前服务器用户权限执行。</p>
            {newError && <div className="error">{newError}</div>}
            <div className="dialog-actions">
              <Dialog.Close>
                <Button variant="soft" color="gray" type="button">
                  取消
                </Button>
              </Dialog.Close>
              <Button
                type="submit"
                disabled={
                  creating ||
                  (!newText.trim() && !newAttachments.items.length) ||
                  newAttachments.busy ||
                  !validPreference(newModel)
                }
              >
                {creating ? '正在创建…' : '启动任务'}
                <ArrowRight />
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Root>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle size={18} />
          {toast}
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')).render(
  <Theme accentColor="jade" grayColor="sage" radius="medium" scaling="100%">
    <App />
  </Theme>,
);
