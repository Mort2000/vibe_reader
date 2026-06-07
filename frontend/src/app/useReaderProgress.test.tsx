import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  BookSummary,
  ChapterSummary,
  JobSummary,
  Paragraph,
  ProgressUpdateResponse,
  ReadingProgress,
} from '../types';
import { useReaderProgress } from './useReaderProgress';

const apiQueryMocks = vi.hoisted(() => ({
  useChapterDataQuery: vi.fn(),
  useChaptersQuery: vi.fn(),
  useCommentsQuery: vi.fn(),
  useCurrentWindowQuery: vi.fn(),
  useRetryWindowMutation: vi.fn(),
  useUpdateProgressMutation: vi.fn(),
}));
const backendEventsMock = vi.hoisted(() => vi.fn());
const retryWindowMock = vi.hoisted(() => vi.fn());
const updateProgressMock = vi.hoisted(() => vi.fn());

vi.mock('../lib/apiQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/apiQueries')>();
  return {
    ...actual,
    useChapterDataQuery: apiQueryMocks.useChapterDataQuery,
    useChaptersQuery: apiQueryMocks.useChaptersQuery,
    useCommentsQuery: apiQueryMocks.useCommentsQuery,
    useCurrentWindowQuery: apiQueryMocks.useCurrentWindowQuery,
    useRetryWindowMutation: apiQueryMocks.useRetryWindowMutation,
    useUpdateProgressMutation: apiQueryMocks.useUpdateProgressMutation,
  };
});

vi.mock('../hooks/useBackendEvents', () => ({
  useBackendEvents: backendEventsMock,
}));

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

function bookSummary(): BookSummary {
  return {
    id: 7,
    title: 'Reader Book',
    author: null,
    cover_url: null,
    total_chapters: 2,
    imported_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    last_progress: {
      book_id: 7,
      chapter_idx: 0,
      paragraph_idx: 1,
      scroll_pct: 0.25,
      updated_at: '2026-01-01T00:00:00Z',
    },
  };
}

function chapters(): ChapterSummary[] {
  return [
    {
      book_id: 7,
      idx: 0,
      title: 'Opening',
      paragraph_count: 4,
      token_estimate: 80,
      prev_chapter_idx: null,
      next_chapter_idx: 1,
    },
    {
      book_id: 7,
      idx: 1,
      title: 'Next',
      paragraph_count: 4,
      token_estimate: 90,
      prev_chapter_idx: 0,
      next_chapter_idx: null,
    },
  ];
}

function paragraphs(chapterIdx: number): Paragraph[] {
  return [0, 1, 2, 3].map((paragraphIdx) => ({
    book_id: 7,
    chapter_idx: chapterIdx,
    paragraph_idx: paragraphIdx,
    text: `chapter ${chapterIdx} paragraph ${paragraphIdx}`,
  }));
}

function progress(chapterIdx: number, paragraphIdx: number): ReadingProgress {
  return {
    book_id: 7,
    chapter_idx: chapterIdx,
    paragraph_idx: paragraphIdx,
    scroll_pct: paragraphIdx / 3,
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function queuedJob(): JobSummary {
  return {
    id: 99,
    job_type: 'window',
    book_id: 7,
    chapter_idx: 0,
    window_id: null,
    status: 'pending',
    attempt_count: 1,
    error: null,
    trace_id: null,
    created_at: '2026-01-01T00:00:00Z',
    started_at: null,
    completed_at: null,
  };
}

function setupQueries() {
  apiQueryMocks.useChaptersQuery.mockReturnValue({
    data: { items: chapters(), total: 2 },
    error: null,
    isError: false,
    isFetching: false,
  });
  apiQueryMocks.useChapterDataQuery.mockImplementation(
    (bookId: number | null, chapterIdx: number | null) => {
      const resolvedBookId = bookId ?? 0;
      const resolvedChapterIdx = chapterIdx ?? 0;
      return {
        data: {
          paragraphs: {
            book_id: resolvedBookId,
            chapter_idx: resolvedChapterIdx,
            items: paragraphs(resolvedChapterIdx),
            total: 4,
          },
          progress: progress(resolvedChapterIdx, resolvedChapterIdx === 0 ? 1 : 0),
        },
        error: null,
        isError: false,
        isFetching: false,
      };
    },
  );
  apiQueryMocks.useCommentsQuery.mockReturnValue({
    data: { items: [], total: 0 },
    error: null,
    isError: false,
  });
  apiQueryMocks.useCurrentWindowQuery.mockReturnValue({
    data: undefined,
    error: null,
    isError: false,
  });
  apiQueryMocks.useRetryWindowMutation.mockReturnValue({
    mutateAsync: retryWindowMock,
  });
  apiQueryMocks.useUpdateProgressMutation.mockReturnValue({
    mutateAsync: updateProgressMock,
  });
  backendEventsMock.mockReturnValue({
    connection: 'open',
    events: [],
    lastEvent: null,
  });
}

describe('useReaderProgress', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateProgressMock.mockResolvedValue({
      assistant_frontier_paragraph_idx: 2,
      current_window: null,
      jobs: [queuedJob()],
      progress: progress(0, 2),
    } satisfies ProgressUpdateResponse);
    setupQueries();
  });

  afterEach(() => {
    cleanup();
  });

  it('keeps manual paragraph selection scoped to the active context', () => {
    const queryClient = createQueryClient();
    const selectedBook = bookSummary();

    const { result, rerender } = renderHook(
      ({ selectedChapter }: { selectedChapter: number | null }) =>
        useReaderProgress({
          selectedBookId: selectedBook.id,
          selectedBook,
          selectedChapter,
          setRequest: vi.fn(),
          pushRequestError: vi.fn(),
        }),
      {
        initialProps: { selectedChapter: 0 },
        wrapper: createWrapper(queryClient),
      },
    );

    expect(result.current.activeContext).toEqual({ bookId: 7, chapterIdx: 0 });
    expect(result.current.selectedParagraph).toBe(1);

    act(() => {
      result.current.setSelectedParagraph(3);
    });

    expect(result.current.selectedParagraph).toBe(3);

    rerender({ selectedChapter: 1 });

    expect(result.current.activeContext).toEqual({ bookId: 7, chapterIdx: 1 });
    expect(result.current.selectedParagraph).toBe(0);
  });

  it('saves the current paragraph progress for the active context', async () => {
    const queryClient = createQueryClient();
    const setRequest = vi.fn();

    const { result } = renderHook(
      () =>
        useReaderProgress({
          selectedBookId: 7,
          selectedBook: bookSummary(),
          selectedChapter: 0,
          setRequest,
          pushRequestError: vi.fn(),
        }),
      { wrapper: createWrapper(queryClient) },
    );

    act(() => {
      result.current.setSelectedParagraph(2);
    });
    act(() => {
      result.current.saveCurrentProgress();
    });

    await waitFor(() => {
      expect(updateProgressMock).toHaveBeenCalledWith({
        bookId: 7,
        chapterIdx: 0,
        paragraphIdx: 2,
        scrollPct: 2 / 3,
      });
    });
    await waitFor(() => {
      expect(result.current.progressSync).toBe('success');
    });

    expect(result.current.jobs).toEqual([queuedJob()]);
    expect(setRequest).toHaveBeenLastCalledWith({
      status: 'success',
      label: '进度已保存 · P3',
      detail: '当前窗口等待生成',
    });
  });
});
