import React, { memo } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { ImageCard, FileCard } from './Images';
import remarkRelayMath from './remarkRelayMath';

export const MessageBody = memo(function MessageBody({ message: m, onPreview }) {
  return (
    <div className="message-body">
      {m.role === 'user' ? (
        <div className="message-user-text">{m.text}</div>
      ) : (
        <Markdown
          remarkPlugins={[remarkGfm, remarkMath, remarkRelayMath]}
          rehypePlugins={[[rehypeKatex, { strict: false, trust: false }]]}
          components={{
            table: ({ children }) => (
              <div
                className="markdown-table-scroll"
                tabIndex={0}
                role="region"
                aria-label="表格，可横向滚动"
              >
                <table>{children}</table>
              </div>
            ),
            img: ({ src, alt }) => (
              <ImageCard
                asset={{
                  url: src,
                  name: alt || '图片',
                  downloadUrl: src?.startsWith('/api/media/') ? src + '?download=1' : src,
                }}
                onPreview={onPreview}
              />
            ),
            a: ({ href, children }) => {
              const asset = m.images?.find((a) => a.url === href?.split('?')[0]);
              return asset ? (
                <button type="button" className="image-text-link" onClick={() => onPreview(asset)}>
                  {children}
                </button>
              ) : (
                <a
                  href={href}
                  download={href?.startsWith('/api/media/') ? true : undefined}
                  target={href?.startsWith('/api/media/') ? undefined : '_blank'}
                  rel="noreferrer"
                >
                  {children}
                </a>
              );
            },
          }}
        >
          {m.text || ''}
        </Markdown>
      )}
      {m.images?.length > 0 && (
        <div className="message-images">
          {m.images.map((asset) => (
            <ImageCard key={asset.id} asset={asset} onPreview={onPreview} />
          ))}
        </div>
      )}
      {m.files?.length > 0 && (
        <div className="message-files">
          {m.files.map((asset) => (
            <FileCard key={asset.id} asset={asset} />
          ))}
        </div>
      )}
    </div>
  );
}, sameBody);

function sameBody(previous, next) {
  // SSE sends fresh objects even for unchanged history. Compare only the inputs
  // rendered here so typing and status updates do not reparse Markdown/KaTeX.
  const a = previous.message;
  const b = next.message;
  return (
    previous.onPreview === next.onPreview &&
    a.role === b.role &&
    a.text === b.text &&
    sameAssets(a.images, b.images) &&
    sameAssets(a.files, b.files)
  );
}

function sameAssets(a, b) {
  return a === b || JSON.stringify(a || []) === JSON.stringify(b || []);
}
