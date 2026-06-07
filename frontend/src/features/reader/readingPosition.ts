import type { ReadingProgress } from '../../types';

export function resolveProgressParagraph(
  progress: ReadingProgress,
  chapterIdx: number,
  paragraphCount: number,
): number {
  if (progress.chapter_idx !== chapterIdx || paragraphCount <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(progress.paragraph_idx, paragraphCount - 1));
}

export function readingProgressPercent(
  paragraphCount: number,
  selectedParagraph: number,
): number {
  if (paragraphCount <= 0) {
    return 0;
  }
  if (paragraphCount === 1) {
    return 100;
  }
  const clampedParagraph = Math.max(
    0,
    Math.min(selectedParagraph, paragraphCount - 1),
  );
  return Math.round((clampedParagraph / (paragraphCount - 1)) * 100);
}
