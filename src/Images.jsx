import React, { useEffect, useRef, useState } from 'react';
import { Button, Dialog, IconButton } from '@radix-ui/themes';
import {
  X,
  DownloadSimple,
  MagnifyingGlassPlus,
  ImageSquare,
  ArrowClockwise,
  FileText,
} from '@phosphor-icons/react';

const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'gif'];
const EXTENSIONS = [
  ...IMAGE_EXTENSIONS,
  'pdf',
  'doc',
  'docx',
  'ppt',
  'pptx',
  'xls',
  'xlsx',
  'odt',
  'ods',
  'odp',
  'txt',
  'md',
  'csv',
  'tsv',
  'json',
  'rtf',
  'zip',
];
const ACCEPT = EXTENSIONS.map((ext) => '.' + ext).join(',');
const extension = (name) => (name || '').split('.').at(-1).toLowerCase();
export const isImage = (asset) =>
  asset.kind
    ? asset.kind === 'image'
    : asset.mime
      ? asset.mime.startsWith('image/')
      : IMAGE_EXTENSIONS.includes(extension(asset.name));
export const fileSize = (size) =>
  size >= 1024 * 1024
    ? `${(size / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.ceil((size || 0) / 1024))} KB`;

export function useAttachments(scope) {
  const [drafts, setDrafts] = useState({});
  const resources = useRef(new Map());
  const update = (key, id, patch) =>
    setDrafts((all) => ({
      ...all,
      [key]: (all[key] || []).map((item) => (item.key === id ? { ...item, ...patch } : item)),
    }));
  async function start(key, item) {
    const controller = new AbortController();
    resources.current.get(item.key).controller = controller;
    update(key, item.key, { status: 'uploading', error: '' });
    try {
      if (!EXTENSIONS.includes(extension(item.file.name)))
        throw new Error('不支持此文件类型，请选择图片、PDF、Office、文本或 ZIP');
      const limit = isImage(item) ? 20 : 50;
      if (item.file.size > limit * 1024 * 1024) throw new Error(`文件超过 ${limit} MB`);
      const params = new URLSearchParams({ name: item.file.name });
      if (key !== 'new') params.set('thread', key);
      const r = await fetch('/api/media?' + params, {
        method: 'POST',
        headers: {
          'X-Relay-Request': '1',
          'Content-Type': item.file.type || 'application/octet-stream',
        },
        body: item.file,
        signal: controller.signal,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok)
        throw new Error(
          r.status === 401
            ? '请重新登录后重试'
            : typeof data.detail === 'string'
              ? data.detail
              : '上传失败，请重试',
        );
      update(key, item.key, { ...data, status: 'ready' });
    } catch (e) {
      if (e.name !== 'AbortError') update(key, item.key, { status: 'error', error: e.message });
    }
  }
  const items = drafts[scope] || [];
  function add(files) {
    const incoming = Array.from(files);
    if (!incoming.length) return false;
    const available = 6 - items.length;
    if (incoming.length > available) {
      setDrafts((all) => ({
        ...all,
        [scope + '-error']: '每条消息最多添加 6 个附件，请减少后重试',
      }));
      return false;
    }
    const added = incoming.map((file) => ({
      key: crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`,
      file,
      name: file.name,
      url: URL.createObjectURL(file),
      status: 'uploading',
    }));
    added.forEach((item) => resources.current.set(item.key, { url: item.url }));
    setDrafts((all) => ({
      ...all,
      [scope]: [...(all[scope] || []), ...added],
      [scope + '-error']: '',
    }));
    added.forEach((item) => start(scope, item));
    return true;
  }
  function release(item) {
    const resource = resources.current.get(item.key);
    resource?.controller?.abort();
    if (resource) URL.revokeObjectURL(resource.url);
    resources.current.delete(item.key);
  }
  function remove(id) {
    items.filter((i) => i.key === id).forEach(release);
    setDrafts((all) => ({
      ...all,
      [scope]: (all[scope] || []).filter((i) => i.key !== id),
      [scope + '-error']: '',
    }));
  }
  function clear() {
    items.forEach(release);
    const sent = new Set(items.map((item) => item.key));
    setDrafts((all) => ({
      ...all,
      [scope]: (all[scope] || []).filter((item) => !sent.has(item.key)),
      [scope + '-error']: '',
    }));
  }
  useEffect(
    () => () => {
      resources.current.forEach((r) => {
        r.controller?.abort();
        URL.revokeObjectURL(r.url);
      });
    },
    [],
  );
  return {
    items,
    add,
    remove,
    clear,
    retry: (item) => start(scope, item),
    error: drafts[scope + '-error'],
    busy: items.some((i) => i.status !== 'ready'),
    ids: items.filter((i) => i.status === 'ready').map((i) => i.id),
  };
}

export function AttachmentInput({ inputRef, attachments, disabled = false, label = '上传文件' }) {
  return (
    <input
      ref={inputRef}
      hidden
      type="file"
      tabIndex={-1}
      aria-label={label}
      accept={ACCEPT}
      multiple
      disabled={disabled}
      onChange={(e) => {
        attachments.add(e.target.files);
        e.target.value = '';
      }}
    />
  );
}
export function attachmentEvents(attachments, disabled = false) {
  return {
    onPaste: (e) => {
      const files = Array.from(e.clipboardData.files);
      if (files.length) {
        e.preventDefault();
        if (!disabled) attachments.add(files);
      }
    },
    onDragOver: (e) => {
      if (e.dataTransfer.types.includes('Files')) e.preventDefault();
    },
    onDrop: (e) => {
      if (e.dataTransfer.files.length) {
        e.preventDefault();
        if (!disabled) attachments.add(e.dataTransfer.files);
      }
    },
  };
}
export function AttachmentTray({ attachments, onPreview, disabled = false }) {
  return (
    <>
      {attachments.items.length > 0 && (
        <div className="attachment-tray" aria-label="待发送附件">
          {attachments.items.map((item) => (
            <div className="attachment-draft" key={item.key}>
              {isImage(item) ? (
                <button
                  type="button"
                  className="attachment-thumbnail"
                  aria-label={'预览 ' + item.name}
                  onClick={() => onPreview(item)}
                >
                  <img src={item.thumbnailUrl || item.url} alt={item.name} />
                </button>
              ) : (
                <a
                  className="attachment-file-icon"
                  href={item.downloadUrl || item.url}
                  download={item.name}
                  aria-label={'下载 ' + item.name}
                >
                  <FileText size={25} />
                  <small>{extension(item.name).toUpperCase()}</small>
                </a>
              )}
              <div className="attachment-state">
                <span>{item.name}</span>
                {item.status === 'uploading' ? (
                  <small role="status">上传中…</small>
                ) : item.status === 'error' ? (
                  <small role="alert">{item.error}</small>
                ) : (
                  <small>
                    <span>已就绪</span>
                    {!isImage(item) && ` · ${fileSize(item.size || item.file.size)}`}
                  </small>
                )}
              </div>
              <div className="attachment-actions">
                {item.status === 'error' && (
                  <IconButton
                    type="button"
                    variant="ghost"
                    aria-label={'重试 ' + item.name}
                    disabled={disabled}
                    onClick={() => attachments.retry(item)}
                  >
                    <ArrowClockwise />
                  </IconButton>
                )}
                <IconButton
                  type="button"
                  variant="ghost"
                  aria-label={'移除 ' + item.name}
                  disabled={disabled}
                  onClick={() => attachments.remove(item.key)}
                >
                  <X />
                </IconButton>
              </div>
            </div>
          ))}
        </div>
      )}
      {attachments.error && (
        <p role="alert" className="attachment-error">
          {attachments.error}
        </p>
      )}
    </>
  );
}
export function FileCard({ asset }) {
  return (
    <a
      className="file-card"
      href={asset.downloadUrl || asset.url}
      download={asset.name}
      aria-label={'下载 ' + asset.name}
    >
      <span className="file-card-icon">
        <FileText size={28} />
      </span>
      <span className="file-card-info">
        <strong>{asset.name}</strong>
        <small>
          {extension(asset.name).toUpperCase()} · {fileSize(asset.size)}
        </small>
      </span>
      <DownloadSimple size={20} />
    </a>
  );
}
export function ImageCard({ asset, onPreview }) {
  const [failed, setFailed] = useState(false);
  const name = asset.name || '图片';
  if (!asset.url) return <span className="image-failed">{name}：图片链接不可用</span>;
  return (
    <span className="image-card">
      <button
        type="button"
        className="image-open"
        onClick={() => onPreview(asset)}
        aria-label={'预览 ' + name}
      >
        {failed ? (
          <span className="image-failed">
            <ImageSquare />
            图片暂时无法加载，点击查看
          </span>
        ) : (
          <img
            src={asset.thumbnailUrl || asset.url}
            alt={name}
            width={asset.width}
            height={asset.height}
            loading="lazy"
            onError={() => setFailed(true)}
          />
        )}
        <span className="image-preview-label">
          <MagnifyingGlassPlus />
          预览
        </span>
      </button>
      <span className="image-caption">
        <span>{name}</span>
        <a
          href={
            asset.downloadUrl || asset.url + (asset.url.includes('?') ? '&' : '?') + 'download=1'
          }
          download={name}
          aria-label={'下载 ' + name}
        >
          <DownloadSimple size={18} />
        </a>
      </span>
    </span>
  );
}
export function ImagePreview({ asset, onClose }) {
  const [zoom, setZoom] = useState(false),
    [failed, setFailed] = useState(false);
  useEffect(() => {
    setZoom(false);
    setFailed(false);
  }, [asset?.url]);
  return (
    <Dialog.Root
      open={Boolean(asset)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Content className="image-preview" aria-describedby="image-preview-description">
        <div className="image-preview-header">
          <Dialog.Title>{asset?.name || '图片预览'}</Dialog.Title>
          <Dialog.Close>
            <IconButton variant="soft" aria-label="关闭图片预览">
              <X />
            </IconButton>
          </Dialog.Close>
        </div>
        <Dialog.Description id="image-preview-description" className="sr-only">
          点击图片切换原始尺寸与适应屏幕；手机可双指缩放。
        </Dialog.Description>
        {asset && (
          <>
            <div className={'image-preview-canvas' + (zoom ? ' original-size' : '')}>
              {failed ? (
                <p role="alert">图片加载失败，请关闭后重试，或下载原图。</p>
              ) : (
                <button
                  type="button"
                  onClick={() => setZoom((v) => !v)}
                  aria-label={zoom ? '适应屏幕' : '查看原始尺寸'}
                >
                  <img src={asset.url} alt={asset.name || '图片'} onError={() => setFailed(true)} />
                </button>
              )}
            </div>
            <div className="image-preview-footer">
              <span>
                {asset.width && `${asset.width} × ${asset.height}`}
                {asset.size && ` · ${(asset.size / 1024 / 1024).toFixed(1)} MB`}
              </span>
              <Button asChild>
                <a href={asset.downloadUrl || asset.url} download={asset.name || 'image'}>
                  <DownloadSimple />
                  下载原图
                </a>
              </Button>
            </div>
          </>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
