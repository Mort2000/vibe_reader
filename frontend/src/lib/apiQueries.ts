import { useMutation, useQuery } from '@tanstack/react-query';

import type {
  ParagraphsResponse,
  ProgressUpdateResponse,
  ReadingProgress,
} from '../types';
import { api } from './api';
import { CHAT_TURN_HISTORY_LIMIT } from './constants';

export interface ChapterDataResult {
  paragraphs: ParagraphsResponse;
  progress: ReadingProgress;
}

export const queryKeys = {
  runtime: () => ['runtime'] as const,
  settings: () => ['settings'] as const,
  books: (query = '') => ['books', query] as const,
  chapters: (bookId: number | null) => ['books', bookId, 'chapters'] as const,
  chapterData: (bookId: number | null, chapterIdx: number | null) =>
    ['books', bookId, 'chapters', chapterIdx, 'data'] as const,
  comments: (bookId: number | null, chapterIdx: number | null) =>
    ['books', bookId, 'chapters', chapterIdx, 'comments'] as const,
  currentWindow: (
    bookId: number | null,
    chapterIdx: number | null,
    paragraphIdx: number | null,
  ) => ['books', bookId, 'chapters', chapterIdx, 'window', paragraphIdx] as const,
  chatSession: (bookId: number | null, chapterIdx: number | null) =>
    ['books', bookId, 'chapters', chapterIdx, 'chat-session'] as const,
  chatTurns: (sessionId: number | null) => ['chat-sessions', sessionId, 'turns'] as const,
};

export function runtimeQueryOptions() {
  return {
    queryKey: queryKeys.runtime(),
    queryFn: ({ signal }: { signal?: AbortSignal }) => api.runtime({ signal }),
  };
}

export function settingsQueryOptions() {
  return {
    queryKey: queryKeys.settings(),
    queryFn: ({ signal }: { signal?: AbortSignal }) => api.settings({ signal }),
  };
}

export function booksQueryOptions(query = '') {
  return {
    queryKey: queryKeys.books(query),
    queryFn: ({ signal }: { signal?: AbortSignal }) =>
      api.books(query || undefined, { signal }),
  };
}

export function useChaptersQuery(bookId: number | null) {
  return useQuery({
    queryKey: queryKeys.chapters(bookId),
    queryFn: ({ signal }) => api.chapters(bookId!, { signal }),
    enabled: bookId !== null,
  });
}

export function useChapterDataQuery(
  bookId: number | null,
  chapterIdx: number | null,
) {
  return useQuery({
    queryKey: queryKeys.chapterData(bookId, chapterIdx),
    queryFn: async ({ signal }): Promise<ChapterDataResult> => {
      const [paragraphs, progress] = await Promise.all([
        api.paragraphs(bookId!, chapterIdx!, true, { signal }),
        api.progress(bookId!, { signal }),
      ]);
      return { paragraphs, progress };
    },
    enabled: bookId !== null && chapterIdx !== null,
  });
}

export function useCommentsQuery(
  bookId: number | null,
  chapterIdx: number | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.comments(bookId, chapterIdx),
    queryFn: ({ signal }) =>
      api.comments(bookId!, chapterIdx!, undefined, undefined, { signal }),
    enabled: enabled && bookId !== null && chapterIdx !== null,
  });
}

export function useCurrentWindowQuery(
  bookId: number | null,
  chapterIdx: number | null,
  paragraphIdx: number | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.currentWindow(bookId, chapterIdx, paragraphIdx),
    queryFn: ({ signal }) =>
      api.currentWindow(bookId!, chapterIdx!, paragraphIdx ?? undefined, { signal }),
    enabled:
      enabled && bookId !== null && chapterIdx !== null && paragraphIdx !== null,
  });
}

export function useChatSessionQuery(
  bookId: number | null,
  chapterIdx: number | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.chatSession(bookId, chapterIdx),
    queryFn: ({ signal }) => api.chatSession(bookId!, chapterIdx!, { signal }),
    enabled: enabled && bookId !== null && chapterIdx !== null,
  });
}

export function useChatTurnsQuery(sessionId: number | null) {
  return useQuery({
    queryKey: queryKeys.chatTurns(sessionId),
    queryFn: ({ signal }) =>
      api.chatTurns(sessionId!, CHAT_TURN_HISTORY_LIMIT, 0, { signal }),
    enabled: sessionId !== null,
  });
}

export function useImportBookMutation() {
  return useMutation({
    mutationFn: (file: File) => api.importBook(file),
  });
}

export function useUpdateProgressMutation() {
  return useMutation({
    mutationFn: ({
      bookId,
      chapterIdx,
      paragraphIdx,
      scrollPct,
    }: {
      bookId: number;
      chapterIdx: number;
      paragraphIdx: number;
      scrollPct: number;
    }): Promise<ProgressUpdateResponse> =>
      api.updateProgress(bookId, chapterIdx, paragraphIdx, scrollPct),
  });
}

export function useRetryWindowMutation() {
  return useMutation({
    mutationFn: (windowId: number) => api.retryWindow(windowId),
  });
}
