import { useCallback, useMemo, useState } from 'react';

import { useBookQuery } from '../lib/apiQueries';
import { chapterDisplayTitle } from '../lib/formatters';
import type { BookSummary } from '../types';
import { initialRequest, requestErrorState } from './controllerShared';
import type { ReaderContext, RequestState } from './types';
import { useChat } from './useChat';
import { useLibrary } from './useLibrary';
import { useReaderProgress } from './useReaderProgress';

export interface AppControllerOptions {
  routeBookId: number | null;
  routeChapterIdx: number | null;
  onNavigateToBook: (book: BookSummary, context?: ReaderContext | null) => void;
  onNavigateToChapter: (chapterIdx: number) => void;
}

export function resolveBookSelectionContext(
  selectedBookId: number | null,
  activeContext: ReaderContext | null,
  bookId: number,
): ReaderContext | null {
  return selectedBookId === bookId ? activeContext : null;
}

export function useAppController({
  routeBookId,
  routeChapterIdx,
  onNavigateToBook,
  onNavigateToChapter,
}: AppControllerOptions) {
  const [libraryCollapsed, setLibraryCollapsed] = useState(true);
  const [chaptersCollapsed, setChaptersCollapsed] = useState(false);
  const [request, setRequest] = useState<RequestState>(initialRequest);
  const selectedBookId = routeBookId;
  const bookQuery = useBookQuery(selectedBookId);
  const selectedBook = selectedBookId !== null ? bookQuery.data ?? null : null;
  const selectedBookTitle =
    selectedBook?.title ??
    (selectedBookId !== null ? `书籍 #${selectedBookId}` : null);

  const pushRequestError = useCallback((error: unknown, label: string) => {
    setRequest(requestErrorState(error, label));
  }, []);

  const reader = useReaderProgress({
    selectedBookId,
    selectedBook,
    selectedChapter: routeChapterIdx,
    setRequest,
    pushRequestError,
  });
  const chat = useChat({
    selectedBook,
    activeContext: reader.activeContext,
    contextReady: reader.contextReady,
    selectedParagraph: reader.selectedParagraph,
    setRequest,
  });
  const { resetReaderState } = reader;
  const { resetChatState } = chat;
  const resetReadingState = useCallback(() => {
    resetReaderState();
    resetChatState();
  }, [resetChatState, resetReaderState]);
  const library = useLibrary({
    selectedBookId,
    activeContext: reader.activeContext,
    onNavigateToBook,
    setRequest,
    pushRequestError,
    resetReadingState,
  });

  const bookRequestState = useMemo<RequestState | null>(() => {
    if (selectedBookId !== null && bookQuery.isFetching && !bookQuery.data) {
      return { status: 'loading', label: `加载${selectedBookTitle}` };
    }
    if (selectedBookId !== null && bookQuery.isError) {
      return requestErrorState(bookQuery.error, '书籍加载失败');
    }
    return null;
  }, [
    bookQuery.data,
    bookQuery.error,
    bookQuery.isError,
    bookQuery.isFetching,
    selectedBookId,
    selectedBookTitle,
  ]);
  const derivedRequest = useMemo(
    () => bookRequestState ?? reader.requestState ?? chat.requestState ?? request,
    [bookRequestState, chat.requestState, reader.requestState, request],
  );
  const brandSubtitle = selectedBookTitle
    ? reader.activeChapter
      ? `${chapterDisplayTitle(reader.activeChapter)} · ${selectedBookTitle}`
      : selectedBookTitle
    : '本地小说阅读器 · AI 伴读';

  const selectChapter = useCallback(
    (idx: number) => {
      onNavigateToChapter(idx);
    },
    [onNavigateToChapter],
  );

  const toggleLibraryCollapsed = useCallback(() => {
    setLibraryCollapsed((value) => !value);
  }, []);

  const toggleChaptersCollapsed = useCallback(() => {
    setChaptersCollapsed((value) => !value);
  }, []);

  return {
    runtime: library.runtime,
    settings: library.settings,
    books: library.books,
    query: library.query,
    setQuery: library.setQuery,
    selectedBookId,
    selectedBook,
    activeContext: reader.activeContext,
    chapters: reader.chapters,
    libraryCollapsed,
    chaptersCollapsed,
    request: derivedRequest,
    importResult: library.importResult,
    importProgress: library.importProgress,
    paragraphs: reader.paragraphs,
    chapterStatus: reader.chapterStatus,
    progress: reader.progress,
    progressSync: reader.progressSync,
    selectedParagraph: reader.selectedParagraph,
    setSelectedParagraph: reader.setSelectedParagraph,
    currentWindow: reader.currentWindow,
    windowCounts: reader.windowCounts,
    jobs: reader.jobs,
    chatTurns: chat.chatTurns,
    chatInput: chat.chatInput,
    setChatInput: chat.setChatInput,
    chatStatus: chat.chatStatus,
    streamingTurn: chat.streamingTurn,
    restorePending: reader.restorePending,
    connection: reader.connection,
    events: reader.events,
    activeChapter: reader.activeChapter,
    brandSubtitle,
    loadBootstrap: library.loadBootstrap,
    refreshBooks: library.refreshBooks,
    handleImport: library.handleImport,
    selectBook: library.selectBook,
    selectChapter,
    saveCurrentProgress: reader.saveCurrentProgress,
    settleRestore: reader.settleRestore,
    retryCurrentWindow: reader.retryCurrentWindow,
    sendChat: chat.sendChat,
    abortChat: chat.abortChat,
    toggleLibraryCollapsed,
    toggleChaptersCollapsed,
  };
}
