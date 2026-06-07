import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BookSummary } from '../types';
import { useLibrary } from './useLibrary';

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
}

function bookSummary(id: number): BookSummary {
  return {
    id,
    title: `Book ${id}`,
    author: null,
    cover_url: null,
    total_chapters: 2,
    imported_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    last_progress: null,
  };
}

describe('useLibrary', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('navigates to a different book and resets reader state', () => {
    const queryClient = createQueryClient();
    const resetReadingState = vi.fn();
    const onNavigateToBook = vi.fn();
    const nextBook = bookSummary(2);

    const { result } = renderHook(
      () =>
        useLibrary({
          selectedBookId: 1,
          activeContext: { bookId: 1, chapterIdx: 3 },
          onNavigateToBook,
          setRequest: vi.fn(),
          pushRequestError: vi.fn(),
          resetReadingState,
        }),
      { wrapper: createWrapper(queryClient) },
    );

    act(() => {
      result.current.selectBook(nextBook);
    });

    expect(resetReadingState).toHaveBeenCalledTimes(1);
    expect(onNavigateToBook).toHaveBeenCalledWith(nextBook, null);
    expect(queryClient.getQueryData(['books', nextBook.id])).toEqual(nextBook);
  });

  it('keeps active context when re-selecting the routed book', () => {
    const queryClient = createQueryClient();
    const resetReadingState = vi.fn();
    const onNavigateToBook = vi.fn();
    const selectedBook = bookSummary(1);
    const activeContext = { bookId: 1, chapterIdx: 3 };

    const { result } = renderHook(
      () =>
        useLibrary({
          selectedBookId: 1,
          activeContext,
          onNavigateToBook,
          setRequest: vi.fn(),
          pushRequestError: vi.fn(),
          resetReadingState,
        }),
      { wrapper: createWrapper(queryClient) },
    );

    act(() => {
      result.current.selectBook(selectedBook);
    });

    expect(resetReadingState).not.toHaveBeenCalled();
    expect(onNavigateToBook).toHaveBeenCalledWith(selectedBook, activeContext);
    expect(queryClient.getQueryData(['books', selectedBook.id])).toEqual(selectedBook);
  });
});
