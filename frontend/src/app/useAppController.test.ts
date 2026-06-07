import { describe, expect, it } from 'vitest';

import { resolveBookSelectionContext } from './useAppController';

describe('book selection routing context', () => {
  it('keeps the active context when selecting the current book again', () => {
    const context = { bookId: 12, chapterIdx: 5 };

    expect(resolveBookSelectionContext(12, context, 12)).toBe(context);
  });

  it('drops the active context when selecting a different book', () => {
    const context = { bookId: 12, chapterIdx: 5 };

    expect(resolveBookSelectionContext(12, context, 13)).toBeNull();
    expect(resolveBookSelectionContext(null, null, 13)).toBeNull();
  });
});
