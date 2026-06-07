import { describe, expect, it } from 'vitest';

import type { ReadingProgress } from '../../types';
import {
  readingProgressPercent,
  resolveProgressParagraph,
} from './readingPosition';

const baseProgress: ReadingProgress = {
  book_id: 1,
  chapter_idx: 2,
  paragraph_idx: 4,
  scroll_pct: 0.5,
  updated_at: '2026-06-07T00:00:00Z',
};

describe('resolveProgressParagraph', () => {
  it('restores the saved paragraph for the active chapter', () => {
    expect(resolveProgressParagraph(baseProgress, 2, 10)).toBe(4);
  });

  it('starts at the first paragraph when progress belongs to another chapter', () => {
    expect(resolveProgressParagraph(baseProgress, 3, 10)).toBe(0);
  });

  it('clamps stale backend progress to the current chapter bounds', () => {
    expect(
      resolveProgressParagraph({ ...baseProgress, paragraph_idx: 99 }, 2, 10),
    ).toBe(9);
    expect(
      resolveProgressParagraph({ ...baseProgress, paragraph_idx: -3 }, 2, 10),
    ).toBe(0);
  });

  it('uses the first paragraph for empty chapters', () => {
    expect(resolveProgressParagraph(baseProgress, 2, 0)).toBe(0);
  });
});

describe('readingProgressPercent', () => {
  it('returns zero for empty chapters', () => {
    expect(readingProgressPercent(0, 0)).toBe(0);
  });

  it('returns complete progress for single-paragraph chapters', () => {
    expect(readingProgressPercent(1, 0)).toBe(100);
  });

  it('calculates rounded progress across paragraph indexes', () => {
    expect(readingProgressPercent(5, 2)).toBe(50);
    expect(readingProgressPercent(4, 1)).toBe(33);
  });

  it('clamps selected paragraphs before calculating progress', () => {
    expect(readingProgressPercent(5, -1)).toBe(0);
    expect(readingProgressPercent(5, 99)).toBe(100);
  });
});
