import type { ChapterSummary } from '../types';

interface Props {
  chapters: ChapterSummary[];
  currentIdx: number | null;
  onSelect: (idx: number) => void;
}

export default function ChapterNav({ chapters, currentIdx, onSelect }: Props) {
  return (
    <nav className="chapter-nav">
      <h3>Chapters</h3>
      <ol className="chapter-list">
        {chapters.map((ch) => (
          <li
            key={ch.idx}
            className={`chapter-item ${ch.idx === currentIdx ? 'active' : ''}`}
            onClick={() => onSelect(ch.idx)}
          >
            <span className="chapter-title">{ch.title}</span>
            <span className="chapter-meta">{ch.paragraph_count} paragraphs</span>
          </li>
        ))}
      </ol>
    </nav>
  );
}
