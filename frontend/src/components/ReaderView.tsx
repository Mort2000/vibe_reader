import { useEffect, useRef, useCallback, useImperativeHandle, forwardRef } from 'react';
import type { Paragraph, ParagraphComment } from '../types';

interface Props {
  chapterIdx: number;
  paragraphs: Paragraph[];
  initialParagraphIdx: number | null;
  onProgressChange: (chapterIdx: number, paragraphIdx: number, scrollPct: number) => void;
}

export interface ReaderViewHandle {
  flush: () => { chapterIdx: number; paragraphIdx: number; scrollPct: number } | null;
}

const DEBOUNCE_MS = 800;
const SCROLL_THRESHOLD = 0.01;

function findCenterParagraph(container: HTMLElement): { idx: number; scrollPct: number } | null {
  const rect = container.getBoundingClientRect();
  const centerY = rect.top + rect.height / 2;

  const elements = container.querySelectorAll('[data-paragraph-idx]');
  let bestIdx = -1;
  let bestDist = Infinity;
  let bestTop = 0;
  let bestHeight = 0;

  elements.forEach((el) => {
    const elRect = el.getBoundingClientRect();
    const elCenter = elRect.top + elRect.height / 2;
    const dist = Math.abs(elCenter - centerY);
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = parseInt(el.getAttribute('data-paragraph-idx') || '0', 10);
      bestTop = elRect.top;
      bestHeight = elRect.height;
    }
  });

  if (bestIdx < 0) return null;

  const scrollPct = Math.max(0, Math.min(1, (centerY - bestTop) / bestHeight));

  return { idx: bestIdx, scrollPct };
}

const ReaderView = forwardRef<ReaderViewHandle, Props>(function ReaderView(
  { chapterIdx, paragraphs, initialParagraphIdx, onProgressChange },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const lastReported = useRef<{ chapterIdx: number; paragraphIdx: number; scrollPct: number } | null>(null);
  const pendingReport = useRef<{ chapterIdx: number; paragraphIdx: number; scrollPct: number } | null>(null);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chapterIdxRef = useRef(chapterIdx);
  chapterIdxRef.current = chapterIdx;

  const reportProgress = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const result = findCenterParagraph(container);
    if (!result) return;

    const chIdx = chapterIdxRef.current;
    const current = { chapterIdx: chIdx, paragraphIdx: result.idx, scrollPct: result.scrollPct };
    pendingReport.current = current;

    const last = lastReported.current;
    if (last && last.chapterIdx === current.chapterIdx && last.paragraphIdx === current.paragraphIdx) {
      if (Math.abs(last.scrollPct - current.scrollPct) < SCROLL_THRESHOLD) {
        return;
      }
    }

    lastReported.current = current;
    onProgressChange(chIdx, result.idx, result.scrollPct);
  }, [onProgressChange]);

  const flush = useCallback((): { chapterIdx: number; paragraphIdx: number; scrollPct: number } | null => {
    if (debounceTimer.current) {
      clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    const container = containerRef.current;
    const result = container ? findCenterParagraph(container) : null;
    if (!result) return pendingReport.current;

    const chIdx = chapterIdxRef.current;
    const state = { chapterIdx: chIdx, paragraphIdx: result.idx, scrollPct: result.scrollPct };

    const last = lastReported.current;
    const changed = !last
      || last.chapterIdx !== state.chapterIdx
      || last.paragraphIdx !== state.paragraphIdx
      || Math.abs(last.scrollPct - state.scrollPct) >= SCROLL_THRESHOLD;

    if (changed) {
      lastReported.current = state;
      onProgressChange(chIdx, result.idx, result.scrollPct);
    }
    return state;
  }, [onProgressChange]);

  useImperativeHandle(ref, () => ({ flush }), [flush]);

  const handleScroll = useCallback(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(reportProgress, DEBOUNCE_MS);
  }, [reportProgress]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);

  useEffect(() => {
    lastReported.current = null;
    pendingReport.current = null;
  }, [chapterIdx]);

  useEffect(() => {
    if (initialParagraphIdx === null || !containerRef.current) return;
    const el = containerRef.current.querySelector(
      `[data-paragraph-idx="${initialParagraphIdx}"]`
    );
    if (el) {
      el.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
    }
  }, [initialParagraphIdx, paragraphs]);

  return (
    <div className="reader-view" ref={containerRef}>
      {paragraphs.map((p) => (
        <div
          key={p.paragraph_idx}
          className="paragraph"
          data-chapter-idx={chapterIdx}
          data-paragraph-idx={p.paragraph_idx}
        >
          <p className="paragraph-text">{p.text}</p>
          {p.comments && p.comments.length > 0 && (
            <div className="paragraph-comments">
              {p.comments.slice(0, 1).map((c) => (
                <CommentBubble key={c.id} comment={c} />
              ))}
              {p.comments.length > 1 && (
                <details className="more-comments">
                  <summary>{p.comments.length - 1} more</summary>
                  {p.comments.slice(1).map((c) => (
                    <CommentBubble key={c.id} comment={c} />
                  ))}
                </details>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
});

export default ReaderView;

function CommentBubble({ comment }: { comment: ParagraphComment }) {
  return (
    <div className={`comment-bubble type-${comment.comment_type}`}>
      <span className="comment-type-badge">{comment.comment_type}</span>
      <span className="comment-text">{comment.comment}</span>
    </div>
  );
}
