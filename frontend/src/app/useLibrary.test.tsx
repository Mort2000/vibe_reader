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

  it('selects a different book and resets reader state', () => {
    const queryClient = createQueryClient();
    const resetReadingState = vi.fn();
    const setSelectedBook = vi.fn();
    const setSelectedChapter = vi.fn();
    const setMode = vi.fn();
    const selectedBook = bookSummary(1);
    const nextBook = bookSummary(2);

    const { result } = renderHook(
      () =>
        useLibrary({
          selectedBook,
          setSelectedBook,
          setSelectedChapter,
          setMode,
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
    expect(setSelectedBook).toHaveBeenCalledWith(nextBook);
    expect(setSelectedChapter).toHaveBeenCalledWith(null);
    expect(setMode).toHaveBeenCalledWith('reader');
  });

  it('does not reset reader state when re-selecting the active book', () => {
    const queryClient = createQueryClient();
    const resetReadingState = vi.fn();
    const setSelectedBook = vi.fn();
    const setSelectedChapter = vi.fn();
    const setMode = vi.fn();
    const selectedBook = bookSummary(1);

    const { result } = renderHook(
      () =>
        useLibrary({
          selectedBook,
          setSelectedBook,
          setSelectedChapter,
          setMode,
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
    expect(setSelectedBook).not.toHaveBeenCalled();
    expect(setSelectedChapter).not.toHaveBeenCalled();
    expect(setMode).toHaveBeenCalledWith('reader');
  });
});
