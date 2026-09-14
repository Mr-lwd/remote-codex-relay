import React, { useEffect, useRef, useState } from 'react';
import { Button } from '@radix-ui/themes';

export function InteractionCard({ request, onReply, onDraft }) {
  const [answers, setAnswers] = useState(() =>
      Object.fromEntries(
        (request.questions || []).map((question) => {
          const options = question.options || [];
          const option =
            options.find((option) => /推荐|recommended/i.test(option.label)) || options[0];
          return [question.id, request.savedAnswers?.[question.id]?.[0] ?? option?.label ?? ''];
        }),
      ),
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const clockOffset = useRef({ serverNow: null, value: 0 });
  if (request.serverNow && request.serverNow !== clockOffset.current.serverNow) {
    clockOffset.current = {
      serverNow: request.serverNow,
      value: request.serverNow * 1000 - Date.now(),
    };
  }
  const remaining = () =>
    request.deadline == null
      ? null
      : Math.max(
          0,
          Math.ceil((request.deadline * 1000 - Date.now() - clockOffset.current.value) / 1000),
        );
  const [seconds, setSeconds] = useState(remaining);
  const draftQueue = useRef(Promise.resolve());
  const submitting = useRef(false);
  const waiting = busy || request.replyStatus === 'sending';
  useEffect(() => {
    setSeconds(remaining());
    if (request.type !== 'input' || request.deadline == null) return;
    const interval = setInterval(() => setSeconds(remaining()), 250);
    return () => clearInterval(interval);
  }, [request.type, request.deadline]);
  function changeAnswer(id, value) {
    const next = { ...answers, [id]: value };
    setAnswers(next);
    if (!onDraft) return;
    const snapshot = Object.fromEntries(Object.entries(next).map(([id, value]) => [id, [value]]));
    // Serialize draft writes so an older network response cannot overwrite the
    // most recent choice before the server's deadline.
    draftQueue.current = draftQueue.current
      .catch(() => {})
      .then(() => onDraft(request.id, snapshot));
    draftQueue.current.then(
      () => setError(''),
      () => setError('选择尚未同步，请点击提交回答重试。'),
    );
  }
  async function respond(body) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      await onReply(request.id, body);
    } catch (e) {
      setError(e.message);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <section
      className="interaction-card"
      data-question-card={request.type === 'input' ? '' : undefined}
      aria-label={request.type === 'input' ? 'Codex 等待你的回答' : 'Codex 等待批准'}
    >
      <div className="interaction-title">
        <strong>{request.type === 'input' ? 'Codex 需要你的回答' : 'Codex 请求操作批准'}</strong>
        <span aria-live="off">
          {request.replyStatus === 'sending'
            ? '正在提交…'
            : request.replyStatus === 'failed'
              ? '提交失败，请重试'
              : request.type === 'input' && seconds !== null
                ? seconds > 0
                  ? `${seconds} 秒后自动回复`
                  : '正在自动回复…'
                : '等待你处理'}
        </span>
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
            <fieldset key={q.id} disabled={waiting}>
              <legend>{q.question}</legend>
              {q.options?.map((o) => (
                <label className="answer-option" key={o.label}>
                  <input
                    type="radio"
                    name={request.id + q.id}
                    checked={answers[q.id] === o.label}
                    onChange={() => changeAnswer(q.id, o.label)}
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
                onChange={(e) => changeAnswer(q.id, e.target.value)}
                required
                autoComplete="off"
              />
            </fieldset>
          ))}
          {request.deadline != null && (
            <p className="question-timeout-hint">
              30 秒未提交时，将使用当前选择；未填写的题使用推荐项或第一项。文字题会反馈未填写。
            </p>
          )}
          <Button
            type="submit"
            disabled={waiting || request.questions.some((q) => !answers[q.id]?.trim())}
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
