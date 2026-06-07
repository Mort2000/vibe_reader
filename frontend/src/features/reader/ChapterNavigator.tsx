import { Layers } from 'lucide-react';

import { chapterDisplayTitle, formatNumber } from '../../lib/formatters';
import type { ChapterSummary } from '../../types';

export function ChapterNavigator({
  activeChapter,
  chapters,
  onSelectChapter,
}: {
  activeChapter: ChapterSummary | null;
  chapters: ChapterSummary[];
  onSelectChapter: (idx: number) => void;
}) {
  return (
    <section className="chapter-strip">
      <div className="section-heading">
        <Layers size={18} />
        <strong>章节</strong>
      </div>
      <div className="chapter-list">
        {chapters.map((chapter) => (
          <button
            key={chapter.idx}
            className={activeChapter?.idx === chapter.idx ? 'active' : ''}
            onClick={() => onSelectChapter(chapter.idx)}
          >
            <span>{chapterDisplayTitle(chapter)}</span>
            <small>
              {formatNumber(chapter.paragraph_count)} 段 ·{' '}
              {formatNumber(chapter.token_estimate)} tokens
            </small>
          </button>
        ))}
      </div>
    </section>
  );
}

export function EmptyChapterPanel() {
  return (
    <section className="chapter-strip empty-chapter">
      <div className="section-heading">
        <Layers size={18} />
        <strong>章节</strong>
      </div>
      <div className="empty-feed">
        <Layers size={18} />
        <span>选择书籍后显示章节。</span>
      </div>
    </section>
  );
}
