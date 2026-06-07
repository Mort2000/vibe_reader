import { describe, expect, it } from 'vitest';

import {
  bookRoutePath,
  modeRoutePath,
  parseAppRoutePath,
  parseRouteNumber,
} from './routes';

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
    expect(modeRoutePath('library', context, 7)).toBe(
      '/books/7/chapters/3/library',
    );
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

  it('parses app routes into pane mode and reader params', () => {
    expect(parseAppRoutePath('/library')).toEqual({
      mode: 'library',
      routeBookId: null,
      routeChapterIdx: null,
    });
    expect(parseAppRoutePath('/books/12')).toEqual({
      mode: 'reader',
      routeBookId: 12,
      routeChapterIdx: null,
    });
    expect(parseAppRoutePath('/books/12/chapters')).toEqual({
      mode: 'chapters',
      routeBookId: 12,
      routeChapterIdx: null,
    });
    expect(parseAppRoutePath('/books/12/chapters/5')).toEqual({
      mode: 'reader',
      routeBookId: 12,
      routeChapterIdx: 5,
    });
    expect(parseAppRoutePath('/books/12/chapters/5/assistant')).toEqual({
      mode: 'assistant',
      routeBookId: 12,
      routeChapterIdx: 5,
    });
    expect(parseAppRoutePath('/books/12/chapters/5/library')).toEqual({
      mode: 'library',
      routeBookId: 12,
      routeChapterIdx: 5,
    });
  });

  it('rejects invalid app routes', () => {
    expect(parseAppRoutePath('/')).toBeNull();
    expect(parseAppRoutePath('/books/not-a-number')).toBeNull();
    expect(parseAppRoutePath('/books/12/pages/5')).toBeNull();
    expect(parseAppRoutePath('/books/12/chapters/5/reader')).toBeNull();
  });

  it('falls back to pane routes when no book is selected', () => {
    expect(modeRoutePath('library', null, null)).toBe('/library');
    expect(modeRoutePath('reader', null, null)).toBe('/reader');
    expect(modeRoutePath('chapters', null, null)).toBe('/chapters');
    expect(modeRoutePath('assistant', null, null)).toBe('/assistant');
    expect(modeRoutePath('status', null, null)).toBe('/status');
  });
});
