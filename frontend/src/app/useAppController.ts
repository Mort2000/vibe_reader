import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useBackendEvents } from '../hooks/useBackendEvents';
import { api, describeError, streamChat } from '../lib/api';
import { chapterDisplayTitle, formatNumber } from '../lib/formatters';
import type {
  BookSummary,
  ChapterSummary,
  ChatSession,
  ChatTurn,
  ImportResult,
  JobSummary,
  LoadStatus,
  PaneMode,
  Paragraph,
  ParagraphComment,
  ProgressUpdateResponse,
  ReadingProgress,
  ReadingWindow,
  RuntimeInfo,
  SettingsSummary,
} from '../types';
import type { ReaderContext, RequestState, WindowCounts } from './types';

const initialRequest: RequestState = {
  status: 'idle',
  label: '等待连接',
};

const emptyParagraphs: Paragraph[] = [];

export function useAppController() {
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [settings, setSettings] = useState<SettingsSummary | null>(null);
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [query, setQuery] = useState('');
  const [selectedBook, setSelectedBook] = useState<BookSummary | null>(null);
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [libraryCollapsed, setLibraryCollapsed] = useState(true);
  const [chaptersCollapsed, setChaptersCollapsed] = useState(false);
  const [mode, setMode] = useState<PaneMode>('library');
  const [request, setRequest] = useState<RequestState>(initialRequest);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importProgress, setImportProgress] = useState<LoadStatus>('idle');
  const [paragraphs, setParagraphs] = useState<Paragraph[]>(emptyParagraphs);
  const [chapterStatus, setChapterStatus] = useState<LoadStatus>('idle');
  const [progress, setProgress] = useState<ReadingProgress | null>(null);
  const [progressSync, setProgressSync] = useState<LoadStatus>('idle');
  const [selectedParagraph, setSelectedParagraph] = useState(0);
  const [currentWindow, setCurrentWindow] = useState<ReadingWindow | null>(null);
  const [windowCounts, setWindowCounts] = useState<WindowCounts>({ ready: 0, target: 0 });
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [chatSession, setChatSession] = useState<ChatSession | null>(null);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatStatus, setChatStatus] = useState<LoadStatus>('idle');
  const [streamingTurn, setStreamingTurn] = useState<ChatTurn | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  const chapterLoadSeqRef = useRef(0);
  const chatStreamSeqRef = useRef(0);
  const progressSaveSeqRef = useRef(0);
  const activeContextRef = useRef<ReaderContext | null>(null);
  const [loadedContext, setLoadedContext] = useState<ReaderContext | null>(null);
  const [restorePending, setRestorePending] = useState(false);

  const { connection, events, lastEvent } = useBackendEvents(
    selectedBook?.id ?? null,
    selectedChapter,
  );

  const pushRequestError = useCallback((error: unknown, label: string) => {
    const info = describeError(error);
    setRequest({
      status: 'error',
      label,
      detail: `${info.title}: ${info.detail}`,
      requestId: info.requestId,
    });
  }, []);

  const mergeComments = useCallback((comments: ParagraphComment[]) => {
    setParagraphs((current) => {
      const commentsByParagraph = new Map<number, ParagraphComment[]>();
      for (const comment of comments) {
        const existing = commentsByParagraph.get(comment.paragraph_idx) || [];
        existing.push(comment);
        commentsByParagraph.set(comment.paragraph_idx, existing);
      }
      return current.map((paragraph) => ({
        ...paragraph,
        comments: commentsByParagraph.get(paragraph.paragraph_idx) || [],
      }));
    });
  }, []);

  const applyProgressUpdate = useCallback((result: ProgressUpdateResponse) => {
    setProgress(result.progress);
    setCurrentWindow(result.current_window);
    setJobs(result.jobs);
    if (result.current_window) {
      setWindowCounts((current) => ({
        ready: current.ready,
        target:
          result.current_window!.focus_end_paragraph_idx -
          result.current_window!.focus_start_paragraph_idx +
          1,
      }));
    }
  }, []);

  const resetReadingState = useCallback((clearChapters = false) => {
    chapterLoadSeqRef.current += 1;
    chatStreamSeqRef.current += 1;
    progressSaveSeqRef.current += 1;
    activeContextRef.current = null;
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;

    if (clearChapters) setChapters([]);
    setLoadedContext(null);
    setRestorePending(false);
    setParagraphs(emptyParagraphs);
    setChapterStatus('idle');
    setProgressSync('idle');
    setSelectedParagraph(0);
    setCurrentWindow(null);
    setWindowCounts({ ready: 0, target: 0 });
    setJobs([]);
    setChatSession(null);
    setChatTurns([]);
    setChatInput('');
    setChatStatus('idle');
    setStreamingTurn(null);
  }, []);

  useEffect(() => {
    activeContextRef.current =
      selectedBook && selectedChapter !== null
        ? { bookId: selectedBook.id, chapterIdx: selectedChapter }
        : null;
  }, [selectedBook, selectedChapter]);

  const loadBootstrap = useCallback(async (bookQuery = '', autoSelect = false) => {
    setRequest({ status: 'loading', label: '连接本地服务' });
    try {
      const [runtimeInfo, settingsInfo, bookList] = await Promise.all([
        api.runtime(),
        api.settings(),
        api.books(bookQuery || undefined),
      ]);
      setRuntime(runtimeInfo);
      setSettings(settingsInfo);
      setBooks(bookList.items);
      setRequest({
        status: 'success',
        label: `服务已连接 · ${bookList.total} 本书`,
      });
      if (autoSelect && bookList.items[0]) {
        setSelectedBook(bookList.items[0]);
      }
    } catch (error) {
      pushRequestError(error, '本地服务连接失败');
    }
  }, [pushRequestError]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadBootstrap('', true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadBootstrap]);

  useEffect(() => {
    if (!selectedBook) {
      return;
    }
    let cancelled = false;
    window.queueMicrotask(() => {
      setRequest({ status: 'loading', label: `加载《${selectedBook.title}》目录` });
    });
    api
      .chapters(selectedBook.id)
      .then((res) => {
        if (cancelled) return;
        setChapters(res.items);
        const progressChapter = selectedBook.last_progress?.chapter_idx;
        const nextChapter = progressChapter ?? res.items[0]?.idx ?? 0;
        setSelectedChapter(nextChapter);
        setRequest({
          status: 'success',
          label: `目录已就绪 · ${res.total} 章`,
        });
      })
      .catch((error) => {
        if (!cancelled) pushRequestError(error, '目录加载失败');
      });
    return () => {
      cancelled = true;
    };
  }, [pushRequestError, selectedBook]);

  const loadChapterData = useCallback(
    async (book: BookSummary, chapterIdx: number) => {
      const loadSeq = ++chapterLoadSeqRef.current;
      const isStale = () => {
        const active = activeContextRef.current;
        return (
          chapterLoadSeqRef.current !== loadSeq ||
          !active ||
          active.bookId !== book.id ||
          active.chapterIdx !== chapterIdx
        );
      };

      setLoadedContext(null);
      setRestorePending(false);
      setParagraphs(emptyParagraphs);
      setCurrentWindow(null);
      setWindowCounts({ ready: 0, target: 0 });
      setJobs([]);
      setChatSession(null);
      setChatTurns([]);
      setStreamingTurn(null);
      setChatStatus('idle');
      setProgressSync('idle');
      setChapterStatus('loading');
      setRequest({
        status: 'loading',
        label: `加载第 ${chapterIdx + 1} 章正文`,
      });

      try {
        const [paragraphResult, progressResult] = await Promise.all([
          api.paragraphs(book.id, chapterIdx, true),
          api.progress(book.id),
        ]);
        if (isStale()) return;

        const progressParagraph =
          progressResult.chapter_idx === chapterIdx
            ? progressResult.paragraph_idx
            : 0;

        setParagraphs(paragraphResult.items);
        setProgress(progressResult);
        setSelectedParagraph(progressParagraph);
        setLoadedContext({ bookId: book.id, chapterIdx });
        setRestorePending(true);
        setChapterStatus('success');
        setRequest({
          status: 'success',
          label: `正文已就绪 · ${formatNumber(paragraphResult.total)} 段`,
        });

        api
          .currentWindow(book.id, chapterIdx, progressParagraph)
          .then((windowResult) => {
            if (isStale()) return;
            setCurrentWindow(windowResult.window);
            setWindowCounts({
              ready: windowResult.comments_ready_count,
              target: windowResult.comments_target_count,
            });
          })
          .catch((error) => {
            if (isStale()) return;
            const info = describeError(error);
            if (info.title !== 'window_not_found') {
              pushRequestError(error, '窗口状态加载失败');
            } else {
              setCurrentWindow(null);
              setWindowCounts({ ready: 0, target: 0 });
            }
          });

        api
          .chatSession(book.id, chapterIdx)
          .then(async ({ session }) => {
            if (isStale()) return;
            setChatSession(session);
            const turnList = await api.chatTurns(session.id);
            if (isStale()) return;
            setChatTurns(turnList.items);
            setChatStatus('success');
          })
          .catch((error) => {
            if (isStale()) return;
            setChatSession(null);
            setChatTurns([]);
            setChatStatus('error');
            pushRequestError(error, '聊天历史加载失败');
          });
      } catch (error) {
        if (isStale()) return;
        setChapterStatus('error');
        setParagraphs(emptyParagraphs);
        pushRequestError(error, '章节正文加载失败');
      }
    },
    [pushRequestError],
  );

  useEffect(() => {
    if (!selectedBook || selectedChapter === null) return;
    const timer = window.setTimeout(() => {
      void loadChapterData(selectedBook, selectedChapter);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadChapterData, selectedBook, selectedChapter]);

  const activeChapter = useMemo(
    () => chapters.find((chapter) => chapter.idx === selectedChapter) ?? null,
    [chapters, selectedChapter],
  );
  const brandSubtitle = selectedBook
    ? activeChapter
      ? `${chapterDisplayTitle(activeChapter)} · ${selectedBook.title}`
      : selectedBook.title
    : '本地小说阅读器 · AI 伴读';

  const refreshCommentsAndWindow = useCallback(async () => {
    if (!selectedBook || selectedChapter === null || !loadedContext) return;
    const context = { bookId: selectedBook.id, chapterIdx: selectedChapter };
    if (
      loadedContext.bookId !== context.bookId ||
      loadedContext.chapterIdx !== context.chapterIdx
    ) {
      return;
    }

    const isStale = () => {
      const active = activeContextRef.current;
      return (
        !active ||
        active.bookId !== context.bookId ||
        active.chapterIdx !== context.chapterIdx
      );
    };

    try {
      const commentsResult = await api.comments(context.bookId, context.chapterIdx);
      if (isStale()) return;
      mergeComments(commentsResult.items);
      try {
        const windowResult = await api.currentWindow(
          context.bookId,
          context.chapterIdx,
          selectedParagraph,
        );
        if (isStale()) return;
        setCurrentWindow(windowResult.window);
        setWindowCounts({
          ready: windowResult.comments_ready_count,
          target: windowResult.comments_target_count,
        });
      } catch (error) {
        if (isStale()) return;
        const info = describeError(error);
        if (info.title !== 'window_not_found') {
          pushRequestError(error, '窗口刷新失败');
        }
      }
    } catch (error) {
      if (isStale()) return;
      pushRequestError(error, '评论刷新失败');
    }
  }, [
    loadedContext,
    mergeComments,
    pushRequestError,
    selectedBook,
    selectedChapter,
    selectedParagraph,
  ]);

  useEffect(() => {
    if (!lastEvent) return;
    if (
      [
        'comment.created',
        'window.queued',
        'window.running',
        'window.done',
        'window.failed',
        'context.queued',
        'context.compacting',
        'context.compacted',
        'context.failed',
        'job.failed',
      ].includes(lastEvent.event)
    ) {
      const timer = window.setTimeout(() => {
        void refreshCommentsAndWindow();
      }, 120);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [lastEvent, refreshCommentsAndWindow]);

  const saveProgress = useCallback(
    async (paragraphIdx: number, force = false) => {
      if (
        !selectedBook ||
        selectedChapter === null ||
        !paragraphs.length ||
        chapterStatus !== 'success' ||
        !loadedContext ||
        loadedContext.bookId !== selectedBook.id ||
        loadedContext.chapterIdx !== selectedChapter ||
        paragraphIdx < 0 ||
        paragraphIdx >= paragraphs.length ||
        (restorePending && !force)
      ) {
        return;
      }
      if (
        !force &&
        progress?.book_id === selectedBook.id &&
        progress?.chapter_idx === selectedChapter &&
        progress.paragraph_idx === paragraphIdx &&
        progress.updated_at
      ) {
        return;
      }

      const scrollPct =
        paragraphs.length <= 1 ? 0 : paragraphIdx / Math.max(paragraphs.length - 1, 1);

      setProgressSync('loading');
      const saveSeq = ++progressSaveSeqRef.current;
      try {
        const context = { bookId: selectedBook.id, chapterIdx: selectedChapter };
        const isCurrent = () => {
          const active = activeContextRef.current;
          return (
            progressSaveSeqRef.current === saveSeq &&
            active?.bookId === context.bookId &&
            active.chapterIdx === context.chapterIdx
          );
        };
        const result = await api.updateProgress(
          context.bookId,
          context.chapterIdx,
          paragraphIdx,
          Math.max(0, Math.min(1, scrollPct)),
        );
        if (!isCurrent()) return;
        applyProgressUpdate(result);
        setProgressSync('success');
        setRequest({
          status: 'success',
          label: `进度已保存 · P${paragraphIdx + 1}`,
          detail: result.current_window
            ? `AI frontier P${result.assistant_frontier_paragraph_idx + 1}`
            : '当前窗口等待生成',
        });
      } catch (error) {
        const active = activeContextRef.current;
        if (
          progressSaveSeqRef.current !== saveSeq ||
          !active ||
          active.bookId !== selectedBook.id ||
          active.chapterIdx !== selectedChapter
        ) {
          return;
        }
        setProgressSync('error');
        pushRequestError(error, '阅读进度保存失败');
      }
    },
    [
      applyProgressUpdate,
      chapterStatus,
      loadedContext,
      paragraphs.length,
      progress,
      pushRequestError,
      restorePending,
      selectedBook,
      selectedChapter,
    ],
  );

  useEffect(() => {
    if (!selectedBook || selectedChapter === null || !paragraphs.length) return;
    if (restorePending || chapterStatus !== 'success') return;
    const timer = window.setTimeout(() => {
      void saveProgress(selectedParagraph);
    }, 850);
    return () => window.clearTimeout(timer);
  }, [
    chapterStatus,
    paragraphs.length,
    restorePending,
    saveProgress,
    selectedBook,
    selectedChapter,
    selectedParagraph,
  ]);

  const retryCurrentWindow = useCallback(async () => {
    if (!currentWindow || !selectedBook || selectedChapter === null) return;
    const context = { bookId: selectedBook.id, chapterIdx: selectedChapter };
    setRequest({ status: 'loading', label: `重试窗口 ${currentWindow.id}` });
    try {
      const result = await api.retryWindow(currentWindow.id);
      const active = activeContextRef.current;
      if (
        !active ||
        active.bookId !== context.bookId ||
        active.chapterIdx !== context.chapterIdx
      ) {
        return;
      }
      setCurrentWindow(result.window);
      setJobs((current) => [result.job, ...current.filter((job) => job.id !== result.job.id)]);
      setRequest({
        status: 'success',
        label: '重试任务已提交',
        detail: `任务 #${result.job.id} 已加入队列`,
      });
    } catch (error) {
      const active = activeContextRef.current;
      if (
        !active ||
        active.bookId !== context.bookId ||
        active.chapterIdx !== context.chapterIdx
      ) {
        return;
      }
      pushRequestError(error, '窗口重试失败');
    }
  }, [currentWindow, pushRequestError, selectedBook, selectedChapter]);

  const sendChat = useCallback(() => {
    if (
      !selectedBook ||
      selectedChapter === null ||
      !chatInput.trim() ||
      chatStatus === 'loading'
    ) {
      return;
    }

    const userMsg = chatInput.trim();
    const paragraphIdx = selectedParagraph;
    const streamSeq = ++chatStreamSeqRef.current;
    const context = { bookId: selectedBook.id, chapterIdx: selectedChapter };
    const isCurrentStream = () => {
      const active = activeContextRef.current;
      return (
        chatStreamSeqRef.current === streamSeq &&
        active?.bookId === context.bookId &&
        active.chapterIdx === context.chapterIdx
      );
    };
    const provisional: ChatTurn = {
      id: -Date.now(),
      session_id: chatSession?.id ?? -1,
      book_id: context.bookId,
      chapter_idx: context.chapterIdx,
      paragraph_idx: paragraphIdx,
      user_msg: userMsg,
      ai_msg: '',
      status: 'streaming',
      tokens_in: null,
      tokens_out: null,
      trace_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setChatInput('');
    setChatStatus('loading');
    setStreamingTurn(provisional);
    setRequest({
      status: 'loading',
      label: `AI 正在回答 · P${paragraphIdx + 1}`,
    });

    chatAbortRef.current?.abort();
    chatAbortRef.current = streamChat(
      {
        bookId: selectedBook.id,
        chapterIdx: selectedChapter,
        paragraphIdx,
        sessionId: chatSession?.id ?? null,
        userMsg,
      },
      {
        onStarted: (data) => {
          if (!isCurrentStream()) return;
          setChatSession((current) =>
            current && current.id === data.session_id
              ? current
              : {
                  id: data.session_id,
                  book_id: context.bookId,
                  chapter_idx: context.chapterIdx,
                  title: null,
                  last_paragraph_idx: paragraphIdx,
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                },
          );
          setStreamingTurn((current) =>
            current
              ? {
                  ...current,
                  id: data.turn_id,
                  session_id: data.session_id,
                  trace_id: data.trace_id,
                }
              : current,
          );
        },
        onDelta: (data) => {
          if (!isCurrentStream()) return;
          setStreamingTurn((current) =>
            current
              ? {
                  ...current,
                  id: data.turn_id || current.id,
                  ai_msg: `${current.ai_msg || ''}${data.delta || ''}`,
                  updated_at: new Date().toISOString(),
                }
              : current,
          );
        },
        onDone: (data) => {
          if (!isCurrentStream()) return;
          const doneTurn: ChatTurn = {
            id: data.turn_id,
            session_id: data.session_id,
            book_id: context.bookId,
            chapter_idx: context.chapterIdx,
            paragraph_idx: paragraphIdx,
            user_msg: userMsg,
            ai_msg: data.ai_msg,
            status: 'done',
            tokens_in: data.tokens_in,
            tokens_out: data.tokens_out,
            trace_id: data.trace_id,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
          setChatTurns((current) => [
            ...current.filter((turn) => turn.id !== data.turn_id),
            doneTurn,
          ]);
          setStreamingTurn(null);
          setChatStatus('success');
          chatAbortRef.current = null;
          setRequest({
            status: 'success',
            label: 'AI 回答完成',
            detail: `tokens ${formatNumber(data.tokens_in)} in / ${formatNumber(
              data.tokens_out,
            )} out`,
          });
        },
        onError: (data) => {
          if (!isCurrentStream()) return;
          const failedTurn: ChatTurn = {
            ...provisional,
            id: data.turn_id ?? provisional.id,
            session_id: data.session_id ?? provisional.session_id,
            status: 'failed',
            ai_msg: data.message,
            trace_id: data.trace_id ?? provisional.trace_id,
            updated_at: new Date().toISOString(),
          };
          setChatTurns((current) => [
            ...current.filter((turn) => turn.id !== failedTurn.id),
            failedTurn,
          ]);
          setStreamingTurn(null);
          setChatStatus('error');
          chatAbortRef.current = null;
          setRequest({
            status: 'error',
            label: data.code,
            detail: data.message,
            requestId: data.request_id,
          });
        },
      },
    );
  }, [
    chatInput,
    chatSession,
    chatStatus,
    selectedBook,
    selectedChapter,
    selectedParagraph,
  ]);

  const abortChat = useCallback(() => {
    chatStreamSeqRef.current += 1;
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    setChatStatus('idle');
    setStreamingTurn(null);
    setRequest({ status: 'idle', label: '聊天已停止' });
  }, []);

  const settleRestore = useCallback(() => {
    setRestorePending(false);
  }, []);

  const refreshBooks = useCallback(async () => {
    setRequest({ status: 'loading', label: '刷新书库' });
    try {
      const bookList = await api.books(query || undefined);
      setBooks(bookList.items);
      setRequest({ status: 'success', label: `书库已更新 · ${bookList.total} 本书` });
    } catch (error) {
      pushRequestError(error, '书库刷新失败');
    }
  }, [pushRequestError, query]);

  const handleImport = useCallback(
    async (file: File) => {
      setImportProgress('loading');
      setRequest({ status: 'loading', label: `导入 ${file.name}` });
      try {
        const result = await api.importBook(file);
        setImportResult(result);
        resetReadingState(true);
        setSelectedChapter(null);
        setSelectedBook(result.book);
        setBooks((current) => [
          result.book,
          ...current.filter((item) => item.id !== result.book.id),
        ]);
        setMode('reader');
        setImportProgress('success');
        setRequest({
          status: 'success',
          label: '导入完成',
          detail: `${result.import_stats.chapter_count} 章 · ${formatNumber(
            result.import_stats.paragraph_count,
          )} 段 · ${result.import_stats.duration_ms}ms`,
        });
      } catch (error) {
        setImportProgress('error');
        pushRequestError(error, '导入失败');
      }
    },
    [pushRequestError, resetReadingState],
  );

  const selectBook = useCallback((book: BookSummary) => {
    if (selectedBook?.id === book.id) {
      setMode('reader');
      return;
    }

    resetReadingState(true);
    setSelectedBook(book);
    setSelectedChapter(null);
    setMode('reader');
  }, [resetReadingState, selectedBook?.id]);

  const selectChapter = useCallback(
    (idx: number) => {
      resetReadingState(false);
      setSelectedChapter(idx);
      setMode('reader');
    },
    [resetReadingState],
  );

  const saveCurrentProgress = useCallback(() => {
    void saveProgress(selectedParagraph, true);
  }, [saveProgress, selectedParagraph]);

  const toggleLibraryCollapsed = useCallback(() => {
    setLibraryCollapsed((value) => !value);
  }, []);

  const toggleChaptersCollapsed = useCallback(() => {
    setChaptersCollapsed((value) => !value);
  }, []);

  return {
    runtime,
    settings,
    books,
    query,
    setQuery,
    selectedBook,
    chapters,
    libraryCollapsed,
    chaptersCollapsed,
    mode,
    setMode,
    request,
    importResult,
    importProgress,
    paragraphs,
    chapterStatus,
    progress,
    progressSync,
    selectedParagraph,
    setSelectedParagraph,
    currentWindow,
    windowCounts,
    jobs,
    chatTurns,
    chatInput,
    setChatInput,
    chatStatus,
    streamingTurn,
    restorePending,
    connection,
    events,
    activeChapter,
    brandSubtitle,
    loadBootstrap,
    refreshBooks,
    handleImport,
    selectBook,
    selectChapter,
    saveCurrentProgress,
    settleRestore,
    retryCurrentWindow,
    sendChat,
    abortChat,
    toggleLibraryCollapsed,
    toggleChaptersCollapsed,
  };
}
