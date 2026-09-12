import React, { useEffect, useRef, useState } from 'react';
import { Button, Dialog, IconButton, TextField } from '@radix-ui/themes';
import { ArrowUp, ArrowRight, CaretRight, Folder, MagnifyingGlass, X } from '@phosphor-icons/react';

export function DirectoryPicker({ value, onChange, api, disabled }) {
  const [open, setOpen] = useState(false),
    [path, setPath] = useState(value),
    [typed, setTyped] = useState(value);
  const [data, setData] = useState(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [hidden, setHidden] = useState(false),
    [query, setQuery] = useState(''),
    [offset, setOffset] = useState(0),
    [reload, setReload] = useState(0);
  const request = useRef(0),
    list = useRef(null);
  function navigate(next) {
    setPath(next);
    setTyped(next);
    setQuery('');
    setOffset(0);
    setReload((n) => n + 1);
  }
  useEffect(() => {
    if (!open) return;
    const id = ++request.current;
    setBusy(true);
    setError('');
    api('/directories?' + new URLSearchParams({ path, hidden, q: query, offset }))
      .then((result) => {
        if (id !== request.current) return;
        setData(result);
        setTyped(result.path);
        list.current?.scrollTo(0, 0);
      })
      .catch((e) => {
        if (id === request.current) setError(e.message);
      })
      .finally(() => {
        if (id === request.current) setBusy(false);
      });
    return () => {
      request.current++;
    };
  }, [open, path, hidden, query, offset, reload]);
  return (
    <div className="directory-picker">
      <button
        id="task-cwd"
        type="button"
        className="directory-trigger"
        disabled={disabled}
        onClick={() => {
          navigate(value);
          setOpen(true);
        }}
        aria-label="选择工作目录"
      >
        <Folder size={20} />
        <span title={value}>{value || '选择服务器文件夹'}</span>
        <span className="directory-browse-label">浏览</span>
        <CaretRight size={16} />
      </button>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Content className="task-dialog directory-dialog" maxWidth="640px">
          <div className="directory-heading">
            <Dialog.Title>选择工作目录</Dialog.Title>
            <Dialog.Close>
              <IconButton type="button" variant="ghost" aria-label="关闭目录选择">
                <X size={20} />
              </IconButton>
            </Dialog.Close>
          </div>
          <Dialog.Description size="2">
            浏览服务器上的真实文件夹，选定后新任务将在该目录执行。
          </Dialog.Description>
          <div className="directory-shortcuts" aria-label="常用与最近目录">
            {(data?.shortcuts || []).map((item) => (
              <button
                type="button"
                key={item.path}
                title={item.path}
                onClick={() => navigate(item.path)}
              >
                {item.name}
              </button>
            ))}
          </div>
          <div className="directory-path">
            <TextField.Root
              aria-label="跳转到目录路径"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  e.stopPropagation();
                  navigate(typed);
                }
              }}
            />
            <Button
              type="button"
              variant="soft"
              disabled={!typed.trim()}
              onClick={() => navigate(typed)}
            >
              <ArrowRight size={16} />
              跳转
            </Button>
          </div>
          <nav className="directory-breadcrumbs" aria-label="目录层级">
            <IconButton
              type="button"
              variant="ghost"
              aria-label="返回上级目录"
              disabled={!data?.parent || busy || Boolean(error)}
              onClick={() => navigate(data.parent)}
            >
              <ArrowUp size={18} />
            </IconButton>
            {data?.breadcrumbs.map((item) => (
              <React.Fragment key={item.path}>
                <button type="button" title={item.path} onClick={() => navigate(item.path)}>
                  {item.name}
                </button>
                <CaretRight size={12} />
              </React.Fragment>
            ))}
          </nav>
          <div className="directory-filter">
            <TextField.Root
              aria-label="搜索当前文件夹"
              placeholder="搜索当前目录中的文件夹"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
            >
              <TextField.Slot>
                <MagnifyingGlass size={16} />
              </TextField.Slot>
            </TextField.Root>
            <label>
              <input
                type="checkbox"
                checked={hidden}
                onChange={(e) => {
                  setHidden(e.target.checked);
                  setOffset(0);
                }}
              />
              隐藏目录
            </label>
          </div>
          <div ref={list} className="directory-list" aria-label="文件夹列表" aria-busy={busy}>
            {error ? (
              <div className="directory-empty" role="alert">
                {error}
                <Button type="button" variant="soft" onClick={() => setReload((n) => n + 1)}>
                  重试
                </Button>
              </div>
            ) : busy ? (
              <div className="directory-empty" role="status">
                正在读取文件夹…
              </div>
            ) : data?.entries.length ? (
              data.entries.map((entry) => (
                <button
                  className="directory-entry"
                  type="button"
                  key={entry.path}
                  disabled={!entry.accessible}
                  title={entry.accessible ? entry.path : '无权访问此文件夹'}
                  onClick={() => navigate(entry.path)}
                >
                  <Folder size={20} />
                  <span>{entry.name}</span>
                  {!entry.accessible ? <small>无权访问</small> : <CaretRight size={16} />}
                </button>
              ))
            ) : (
              <p className="directory-empty">
                {query ? '没有匹配的文件夹' : '此目录没有子文件夹，可以直接选择当前目录。'}
              </p>
            )}
          </div>
          {data && data.total > data.limit && !error && (
            <div className="directory-pages">
              <Button
                type="button"
                variant="ghost"
                disabled={busy || offset === 0}
                onClick={() => setOffset((n) => Math.max(0, n - data.limit))}
              >
                上一页
              </Button>
              <span>
                {Math.floor(offset / data.limit) + 1} / {Math.ceil(data.total / data.limit)}
              </span>
              <Button
                type="button"
                variant="ghost"
                disabled={busy || offset + data.limit >= data.total}
                onClick={() => setOffset((n) => n + data.limit)}
              >
                下一页
              </Button>
            </div>
          )}
          <p className="directory-selection" title={data?.path}>
            已浏览到：{data?.path || path}
          </p>
          <div className="dialog-actions">
            <Dialog.Close>
              <Button type="button" variant="soft" color="gray">
                取消
              </Button>
            </Dialog.Close>
            <Button
              type="button"
              disabled={busy || Boolean(error) || !data}
              onClick={() => {
                onChange(data.path);
                setOpen(false);
              }}
            >
              选择此目录
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Root>
    </div>
  );
}
