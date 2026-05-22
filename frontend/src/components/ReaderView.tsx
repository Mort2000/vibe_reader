import { useEffect, useRef, useCallback } from 'react';
import type { Paragraph, ParagraphComment } from '../types';
import * as api from '../api/client';

interface Props {
  bookId: number;
  chapterIdx: number;
  paragraphs: Paragraph[];
  initialParagraphIdx: number | null;
  onProgressChange: (paragraphIdx: number, scrollPct: number) => void;
}

const DEBOUNCE_MS = 800;

export default function ReaderView({
  bookId,
  chapterIdx,
  paragraphs,
  initialParagraphIdx,
  onProgressChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const lastReportedIdx = useRef<number | null>(null);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const getCurrentParagraph = useCallback((): { idx: number; scrollPct: number } | null => {
    const container = containerRef.current;
    if (!container) return null;

    const rect = container.getBoundingClientRect();
    const centerY = rect.top + rect.height / 2;

    const elements = container.querySelectorAll('[data-paragraph-idx]');
    let closest: Element | null = null;
    let closestDist = Infinity;

    elements.forEach((el) => {
      const elRect = el.getBoundingClientRect();
      const elCenter = elRect.top + elRect.height / 2;
      const dist = Math.abs(elCenter - centerY);
      if (dist < closestDist) {
        closestDist = dist;
        closest = el;
      }
    });

    if (!closest) return null;

    const idx = parseInt(closest.getAttribute('data-paragraph-idx') || '0', 10);
    const elRect = closest.getBoundingClientRect();
    const scrollPct = Math.max(
      0,
      Math.min(1, (centerY - elRect.top) / elRect.height)
    );

    return { idx, scrollPct };
  }, []);

  const reportProgress = useCallback(() => {
    const result = getCurrentParagraph();
    if (!result) return;
    if (result.idx === lastReportedIdx.current) return;

    lastReportedIdx.current = result.idx;
    onProgressChange(result.idx, result.scrollPct);
  }, [getCurrentParagraph, onProgressChange]);

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
}

function CommentBubble({ comment }: { comment: ParagraphComment }) {
  return (
    <div className={`comment-bubble type-${comment.comment_type}`}>
      <span className="comment-type-badge">{comment.comment_type}</span>
      <span className="comment-text">{comment.comment}</span>
    </div>
  );
}
