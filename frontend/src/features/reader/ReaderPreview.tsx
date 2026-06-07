import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Eye,
  Loader2,
  RefreshCcw,
  Sparkles,
} from 'lucide-react';
import { useCallback, useEffect, useRef } from 'react';

import { StatusPill } from '../../components/StatusPill';
import { ImportButton } from '../library/LibraryPanel';
import {
  chapterDisplayTitle,
  formatDate,
  statusCopy,
} from '../../lib/formatters';
import type {
  ChapterSummary,
  LoadStatus,
  Paragraph,
  ReadingProgress,
  ReadingWindow,
} from '../../types';

export function ReaderPreview({
  activeChapter,
  paragraphs,
  chapterStatus,
  progress,
  progressSync,
  selectedParagraph,
  restorePending,
  currentWindow,
  onVisibleParagraph,
  onSaveProgress,
  onRestoreSettled,
}: {
  activeChapter: ChapterSummary | null;
  paragraphs: Paragraph[];
  chapterStatus: LoadStatus;
  progress: ReadingProgress | null;
  progressSync: LoadStatus;
  selectedParagraph: number;
  restorePending: boolean;
  currentWindow: ReadingWindow | null;
  onVisibleParagraph: (idx: number) => void;
  onSaveProgress: () => void;
  onRestoreSettled: () => void;
}) {
  const progressPct =
    paragraphs.length > 1
      ? Math.round((selectedParagraph / Math.max(paragraphs.length - 1, 1)) * 100)
      : paragraphs.length
        ? 100
        : 0;
  const progressWidth = `${Math.max(0, Math.min(100, progressPct))}%`;

  return (
    <div className="reader-preview">
      <section className="reading-surface">
        <div className="reader-toolbar">
          <div>
            <span className="eyebrow">{chapterDisplayTitle(activeChapter)}</span>
            <strong>
              P{selectedParagraph + 1}
              {paragraphs.length ? ` / ${paragraphs.length} · ${progressPct}%` : ''}
            </strong>
            <small>
              {progress?.updated_at
                ? `自动保存 ${formatDate(progress.updated_at)}`
                : '自动保存待同步'}
            </small>
          </div>
          <div className="toolbar-actions">
            <StatusPill status={progressSync}>
              {progressSync === 'loading' && <Loader2 size={14} className="spin" />}
              {progressSync === 'success' && <CheckCircle2 size={14} />}
              {progressSync === 'error' && <AlertCircle size={14} />}
              {progressSync === 'idle' ? '进度待同步' : statusCopy(progressSync)}
            </StatusPill>
            <button className="soft-inline-button" onClick={onSaveProgress}>
              <RefreshCcw size={16} />
              同步进度
            </button>
          </div>
          <div className="reader-progress-meter" aria-label="阅读进度">
            <span style={{ width: progressWidth }} />
          </div>
        </div>

        <ReaderDocument
          paragraphs={paragraphs}
          chapterStatus={chapterStatus}
          selectedParagraph={selectedParagraph}
          restorePending={restorePending}
          currentWindow={currentWindow}
          onVisibleParagraph={onVisibleParagraph}
          onRestoreSettled={onRestoreSettled}
        />
      </section>
    </div>
  );
}

function ReaderDocument({
  paragraphs,
  chapterStatus,
  selectedParagraph,
  restorePending,
  currentWindow,
  onVisibleParagraph,
  onRestoreSettled,
}: {
  paragraphs: Paragraph[];
  chapterStatus: LoadStatus;
  selectedParagraph: number;
  restorePending: boolean;
  currentWindow: ReadingWindow | null;
  onVisibleParagraph: (idx: number) => void;
  onRestoreSettled: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const paragraphRefs = useRef(new Map<number, HTMLElement>());
  const manualSelectionUntilRef = useRef(0);

  const selectParagraph = useCallback(
    (idx: number) => {
      manualSelectionUntilRef.current = Date.now() + 1200;
      onVisibleParagraph(idx);
    },
    [onVisibleParagraph],
  );

  useEffect(() => {
    const root = rootRef.current;
    if (!root || !paragraphs.length) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort(
            (a, b) =>
              a.boundingClientRect.top - b.boundingClientRect.top ||
              b.intersectionRatio - a.intersectionRatio,
          )[0];
        if (!visible) return;
        if (restorePending) return;
        if (Date.now() < manualSelectionUntilRef.current) return;
        const idx = Number((visible.target as HTMLElement).dataset.paragraphIdx);
        if (Number.isFinite(idx)) onVisibleParagraph(idx);
      },
      {
        root,
        rootMargin: '-18% 0px -58% 0px',
        threshold: [0.1, 0.35, 0.6],
      },
    );

    paragraphRefs.current.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [onVisibleParagraph, paragraphs, restorePending]);

  useEffect(() => {
    if (!restorePending || !paragraphs.length) return undefined;
    const root = rootRef.current;
    const target = paragraphRefs.current.get(selectedParagraph);
    let settleTimer: number | undefined;

    const scrollTimer = window.setTimeout(() => {
      if (root && target) {
        target.scrollIntoView({ block: 'start' });
      }
      settleTimer = window.setTimeout(onRestoreSettled, 260);
    }, 0);

    return () => {
      window.clearTimeout(scrollTimer);
      if (settleTimer !== undefined) window.clearTimeout(settleTimer);
    };
  }, [onRestoreSettled, paragraphs.length, restorePending, selectedParagraph]);

  if (chapterStatus === 'loading') {
    return (
      <div className="reader-loading">
        <Loader2 size={24} className="spin" />
        <strong>正在加载正文</strong>
      </div>
    );
  }

  if (chapterStatus === 'error') {
    return (
      <div className="reader-loading error-state">
        <AlertCircle size={24} />
        <strong>正文加载失败</strong>
      </div>
    );
  }

  if (!paragraphs.length) {
    return (
      <div className="reader-loading">
        <Eye size={24} />
        <strong>暂无正文</strong>
      </div>
    );
  }

  return (
    <div className="reader-document" ref={rootRef}>
      {paragraphs.map((paragraph) => {
        const idx = paragraph.paragraph_idx;
        const isCovered = Boolean(
          currentWindow &&
            idx >= currentWindow.start_paragraph_idx &&
            idx <= currentWindow.end_paragraph_idx,
        );
        const isFocused = Boolean(
          currentWindow &&
            idx >= currentWindow.focus_start_paragraph_idx &&
            idx <= currentWindow.focus_end_paragraph_idx,
        );
        const isFrontier = currentWindow?.assistant_frontier_paragraph_idx === idx;
        const paragraphClass = [
          'paragraph-block',
          idx === selectedParagraph ? 'active' : '',
          isCovered ? 'window-covered' : '',
          isFocused ? 'window-focused' : '',
          isFrontier ? 'window-frontier' : '',
        ]
          .filter(Boolean)
          .join(' ');

        return (
          <article
            className={paragraphClass}
            data-paragraph-idx={idx}
            key={idx}
            tabIndex={0}
            aria-current={idx === selectedParagraph ? 'location' : undefined}
            onClick={() => selectParagraph(idx)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectParagraph(idx);
              }
            }}
            ref={(node) => {
              if (node) {
                paragraphRefs.current.set(idx, node);
              } else {
                paragraphRefs.current.delete(idx);
              }
            }}
          >
            <div className="paragraph-index">P{idx + 1}</div>
            <p>{paragraph.text}</p>
            {paragraph.comments?.length ? (
              <div className="comment-stack">
                {paragraph.comments.map((comment) => (
                  <aside
                    className={`comment-bubble comment-${comment.comment_type}`}
                    key={comment.id}
                  >
                    <span>{comment.comment_type}</span>
                    <p>{comment.comment}</p>
                  </aside>
                ))}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

export function EmptyReader({
  onImport,
  importProgress,
}: {
  onImport: (file: File) => Promise<void>;
  importProgress: LoadStatus;
}) {
  return (
    <div className="empty-reader">
      <div className="empty-visual">
        <BookOpen size={52} />
        <Sparkles size={24} />
      </div>
      <h1>书库为空</h1>
      <p>等待第一本 EPUB。</p>
      <ImportButton onImport={onImport} importProgress={importProgress} />
    </div>
  );
}
