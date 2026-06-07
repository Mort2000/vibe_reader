import { Layers } from 'lucide-react';

import { chapterDisplayTitle, formatNumber } from '../../lib/formatters';
import type { ChapterSummary } from '../../types';
import styles from './ChapterNavigator.module.css';

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
    <section className={styles.chapterStrip}>
      <div className={styles.sectionHeading}>
        <Layers size={18} />
        <strong>章节</strong>
      </div>
      <div className={styles.chapterList}>
        {chapters.map((chapter) => (
          <button
            key={chapter.idx}
            className={activeChapter?.idx === chapter.idx ? styles.active : ''}
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
    <section className={styles.chapterStrip}>
      <div className={styles.sectionHeading}>
        <Layers size={18} />
        <strong>章节</strong>
      </div>
      <div className={styles.emptyFeed}>
        <Layers size={18} />
        <span>选择书籍后显示章节。</span>
      </div>
    </section>
  );
}
