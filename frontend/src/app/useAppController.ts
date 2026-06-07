import { useCallback, useMemo, useState } from 'react';

import { chapterDisplayTitle } from '../lib/formatters';
import type { BookSummary, PaneMode } from '../types';
import { initialRequest, requestErrorState } from './controllerShared';
import type { RequestState } from './types';
import { useChat } from './useChat';
import { useLibrary } from './useLibrary';
import { useReaderProgress } from './useReaderProgress';

export function useAppController() {
  const [selectedBook, setSelectedBook] = useState<BookSummary | null>(null);
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [libraryCollapsed, setLibraryCollapsed] = useState(true);
  const [chaptersCollapsed, setChaptersCollapsed] = useState(false);
  const [mode, setMode] = useState<PaneMode>('library');
  const [request, setRequest] = useState<RequestState>(initialRequest);

  const pushRequestError = useCallback((error: unknown, label: string) => {
    setRequest(requestErrorState(error, label));
  }, []);

  const reader = useReaderProgress({
    selectedBook,
    selectedChapter,
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
    selectedBook,
    setSelectedBook,
    setSelectedChapter,
    setMode,
    setRequest,
    pushRequestError,
    resetReadingState,
  });

  const derivedRequest = useMemo(
    () => reader.requestState ?? chat.requestState ?? request,
    [chat.requestState, reader.requestState, request],
  );
  const brandSubtitle = selectedBook
    ? reader.activeChapter
      ? `${chapterDisplayTitle(reader.activeChapter)} · ${selectedBook.title}`
      : selectedBook.title
    : '本地小说阅读器 · AI 伴读';

  const selectChapter = useCallback(
    (idx: number) => {
      resetReadingState();
      setSelectedChapter(idx);
      setMode('reader');
    },
    [resetReadingState],
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
    selectedBook,
    chapters: reader.chapters,
    libraryCollapsed,
    chaptersCollapsed,
    mode,
    setMode,
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
