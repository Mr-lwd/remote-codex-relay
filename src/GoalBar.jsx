import React, { useEffect, useRef, useState } from 'react';
import { Button, Dialog, IconButton, Tooltip } from '@radix-ui/themes';
import { ArrowsOutSimple, PauseCircle, PlayCircle, Target, Trash, X } from '@phosphor-icons/react';

const labels = {
  active: '进行中的目标',
  paused: '已暂停的目标',
  blocked: '受阻的目标',
  usageLimited: '用量受限的目标',
  budgetLimited: '预算受限的目标',
  complete: '已完成的目标',
  completed: '已完成的目标',
};
function duration(seconds = 0) {
  const n = Math.max(0, Math.floor(seconds));
  return n < 60
    ? `${n}s`
    : n < 3600
      ? `${Math.floor(n / 60)}m ${n % 60}s`
      : `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
}

export function GoalBar({
  goal: currentGoal,
  locked,
  api,
  threadId,
  settings,
  onRefresh,
  onToast,
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [dialog, setDialog] = useState(null),
    [draft, setDraft] = useState(''),
    [original, setOriginal] = useState('');
  // Keep the dialog alive through its closed state even when SSE removes the
  // goal, so its portal can release focus and scroll locks normally.
  const lastGoal = useRef(currentGoal);
  if (currentGoal) lastGoal.current = currentGoal;
  const goal = currentGoal || lastGoal.current;
  useEffect(() => {
    if (!currentGoal) {
      setDialog(null);
      setError('');
    }
  }, [currentGoal]);
  if (!goal) return null;
  const active = goal.status === 'active';
  const label = labels[goal.status] || '当前目标';
  function edit() {
    setDraft(goal.objective);
    setOriginal(goal.objective);
    setError('');
    setDialog('edit');
  }
  async function act(action) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await api('/threads/' + threadId + '/goal', {
        action,
        ...settings,
        ...(action === 'edit' ? { objective: draft, expectedObjective: original } : {}),
      });
      setDialog(null);
      onToast(
        result.message ||
          (result.kind === 'queued' ? '继续目标已排队，将使用所选模型与强度' : '正在继续目标'),
      );
      await onRefresh(threadId);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="goal-control" aria-label="当前目标" hidden={!currentGoal}>
      <div className={'goal-bar ' + goal.status}>
        <Target size={19} className="goal-symbol" />
        <button
          type="button"
          className="goal-summary"
          onClick={edit}
          aria-label="编辑目标"
          title={goal.objective}
        >
          <strong>{label}</strong>
          <span>{goal.objective}</span>
        </button>
        <span className="goal-duration" title="目标累计执行时间">
          {duration(goal.time_used_seconds)}
        </span>
        <div className="goal-actions">
          <Tooltip content="清除目标">
            <IconButton
              type="button"
              variant="ghost"
              color="gray"
              aria-label="清除目标"
              disabled={busy || locked}
              onClick={() => {
                setError('');
                setDialog('clear');
              }}
            >
              <Trash size={18} />
            </IconButton>
          </Tooltip>
          <Tooltip content={active ? '暂停目标并停止当前回复' : '继续目标（使用所选模型与强度）'}>
            <IconButton
              type="button"
              variant="ghost"
              color="gray"
              aria-label={active ? '暂停目标' : '继续目标'}
              disabled={busy || locked}
              onClick={() => act(active ? 'pause' : 'resume')}
            >
              {active ? <PauseCircle size={21} /> : <PlayCircle size={21} />}
            </IconButton>
          </Tooltip>
          <Tooltip content="展开并编辑目标">
            <IconButton
              type="button"
              variant="ghost"
              color="gray"
              aria-label="展开目标"
              onClick={edit}
              disabled={busy}
            >
              <ArrowsOutSimple size={18} />
            </IconButton>
          </Tooltip>
        </div>
      </div>
      {error && !dialog && (
        <p className="goal-error" role="alert">
          {error}
        </p>
      )}
      <Dialog.Root
        open={Boolean(currentGoal && dialog)}
        onOpenChange={(open) => {
          if (!open && !busy) {
            setDialog(null);
            setError('');
          }
        }}
      >
        <Dialog.Content className="task-dialog goal-dialog" maxWidth="620px">
          <div className="directory-heading">
            <Dialog.Title>{dialog === 'clear' ? '清除当前目标' : '编辑目标'}</Dialog.Title>
            <Dialog.Close>
              <IconButton type="button" variant="ghost" aria-label="关闭目标详情" disabled={busy}>
                <X size={20} />
              </IconButton>
            </Dialog.Close>
          </div>
          <Dialog.Description size="2" mb="4">
            {dialog === 'clear'
              ? '清除目标并停止当前回复，聊天记录保留。'
              : `${label} · 累计执行 ${duration(goal.time_used_seconds)}。保存后保留当前状态。`}
          </Dialog.Description>
          {dialog === 'edit' ? (
            <textarea
              className="goal-editor"
              aria-label="目标内容"
              value={draft}
              maxLength={30000}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy || locked}
              rows={8}
            />
          ) : (
            <p className="goal-clear-preview">{goal.objective}</p>
          )}
          {locked && <p className="goal-error">此会话由其他客户端控制，请等待本轮结束后操作。</p>}
          {error && (
            <p role="alert" className="goal-error">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <Dialog.Close>
              <Button type="button" variant="soft" color="gray" disabled={busy}>
                取消
              </Button>
            </Dialog.Close>
            <Button
              type="button"
              color={dialog === 'clear' ? 'red' : undefined}
              disabled={
                busy ||
                locked ||
                (dialog === 'edit' && (!draft.trim() || draft.trim() === original))
              }
              onClick={() => act(dialog === 'clear' ? 'clear' : 'edit')}
            >
              {busy ? '正在保存…' : dialog === 'clear' ? '确认清除' : '保存目标'}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Root>
    </section>
  );
}
