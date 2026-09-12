import React, { useEffect, useRef, useState } from 'react';
import { IconButton } from '@radix-ui/themes';
import { X } from '@phosphor-icons/react';

export function MessageQueue({ threadId, items = [], api, onRefresh, onToast, onError }) {
  const [removing, setRemoving] = useState(new Set());
  const [removed, setRemoved] = useState(new Set());
  const requests = useRef(new Set());
  const list = useRef(null);
  const visible = items.filter((item) => !removed.has(item.id));
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [items.at(-1)?.id]);

  async function remove(item) {
    if (requests.current.has(item.id)) return;
    requests.current.add(item.id);
    setRemoving(new Set(requests.current));
    onError('');
    try {
      await api(`/threads/${threadId}/queue/${item.id}/cancel`, {});
      // Hide an acknowledged deletion even if an older SSE snapshot is in flight.
      setRemoved((current) => new Set([...current, item.id]));
      onToast('已删除排队消息');
    } catch (error) {
      onError(error.message);
    } finally {
      requests.current.delete(item.id);
      setRemoving(new Set(requests.current));
      onRefresh(threadId).catch((error) => onError(error.message));
    }
  }

  if (!visible.length) return null;
  return (
    <section className="message-queue" aria-label="待发送队列">
      <div className="message-queue-heading" role="status">
        <span>待发送 · {visible.length}</span>
        <span>本轮结束后依次执行</span>
      </div>
      <ol ref={list}>
        {visible.map((item) => (
          <li key={item.id}>
            <div className="message-queue-content">
              <p title={item.text}>{item.text}</p>
              {item.attachments?.length > 0 && <small>{item.attachments.join('、')}</small>}
            </div>
            <IconButton
              type="button"
              variant="ghost"
              color="gray"
              aria-label={`删除排队消息：${item.text}`}
              title="删除排队消息"
              disabled={removing.has(item.id)}
              onClick={() => remove(item)}
            >
              <X size={18} />
            </IconButton>
          </li>
        ))}
      </ol>
    </section>
  );
}
