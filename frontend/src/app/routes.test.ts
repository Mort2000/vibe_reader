import { describe, expect, it } from 'vitest';

import { bookRoutePath, modeRoutePath, parseRouteNumber } from './routes';

describe('route helpers', () => {
  it('parses only safe non-negative integer params', () => {
    expect(parseRouteNumber('0')).toBe(0);
    expect(parseRouteNumber('42')).toBe(42);
    expect(parseRouteNumber('-1')).toBeNull();
    expect(parseRouteNumber('1.5')).toBeNull();
    expect(parseRouteNumber('abc')).toBeNull();
    expect(parseRouteNumber('9007199254740992')).toBeNull();
  });

  it('builds reader-aware pane routes', () => {
    const context = { bookId: 7, chapterIdx: 3 };
    expect(bookRoutePath(7)).toBe('/books/7');
    expect(modeRoutePath('reader', context, 7)).toBe('/books/7/chapters/3');
    expect(modeRoutePath('chapters', context, 7)).toBe(
      '/books/7/chapters/3/chapters',
    );
    expect(modeRoutePath('assistant', context, 7)).toBe(
      '/books/7/chapters/3/assistant',
    );
    expect(modeRoutePath('status', context, 7)).toBe(
      '/books/7/chapters/3/status',
    );
  });

  it('falls back to pane routes when no book is selected', () => {
    expect(modeRoutePath('library', null, null)).toBe('/library');
    expect(modeRoutePath('reader', null, null)).toBe('/reader');
    expect(modeRoutePath('chapters', null, null)).toBe('/chapters');
    expect(modeRoutePath('assistant', null, null)).toBe('/assistant');
    expect(modeRoutePath('status', null, null)).toBe('/status');
  });
});
