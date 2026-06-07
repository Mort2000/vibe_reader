import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { useBackendEvents } from '../hooks/useBackendEvents';
import { describeError, streamChat } from '../lib/api';
import {
  booksQueryOptions,
  queryKeys,
  runtimeQueryOptions,
  settingsQueryOptions,
  useChapterDataQuery,
  useChaptersQuery,
  useChatSessionQuery,
  useChatTurnsQuery,
  useCommentsQuery,
  useCurrentWindowQuery,
  useImportBookMutation,
  useRetryWindowMutation,
  useUpdateProgressMutation,
} from '../lib/apiQueries';
import {
  BACKEND_EVENT_REFRESH_DELAY_MS,
  BOOTSTRAP_DELAY_MS,
  PROGRESS_AUTOSAVE_DELAY_MS,
  WINDOW_NOT_FOUND_CODE,
} from '../lib/constants';
import { resolveProgressParagraph } from '../features/reader/readingPosition';
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

function deferEffectStateUpdate(callback: () => void) {
  let cancelled = false;
  window.queueMicrotask(() => {
    if (!cancelled) callback();
  });
  return () => {
    cancelled = true;
  };
}

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
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [windowParagraph, setWindowParagraph] = useState<number | null>(null);
  const queryClient = useQueryClient();
  const chatAbortRef = useRef<AbortController | null>(null);
  const activeContextRef = useRef<ReaderContext | null>(null);
  const [loadedContext, setLoadedContext] = useState<ReaderContext | null>(null);
  const [restorePending, setRestorePending] = useState(false);
  const selectedBookId = selectedBook?.id ?? null;
  const contextReady = Boolean(
    selectedBookId !== null &&
      selectedChapter !== null &&
      loadedContext?.bookId === selectedBookId &&
      loadedContext.chapterIdx === selectedChapter,
  );
  const chaptersQuery = useChaptersQuery(selectedBookId);
  const chapterDataQuery = useChapterDataQuery(selectedBookId, selectedChapter);
  const commentsQuery = useCommentsQuery(
    selectedBookId,
    selectedChapter,
    contextReady,
  );
  const currentWindowQuery = useCurrentWindowQuery(
    selectedBookId,
    selectedChapter,
    windowParagraph,
    contextReady,
  );
  const chatSessionQuery = useChatSessionQuery(
    selectedBookId,
    selectedChapter,
    contextReady,
  );
  const chatTurnsQuery = useChatTurnsQuery(chatSessionQuery.data?.session.id ?? null);
  const { mutateAsync: importBook } = useImportBookMutation();
  const { mutateAsync: updateProgress } = useUpdateProgressMutation();
  const { mutateAsync: retryWindow } = useRetryWindowMutation();

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
    activeContextRef.current = null;
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;

    if (clearChapters) setChapters([]);
    setLoadedContext(null);
    setRestorePending(false);
    setWindowParagraph(null);
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
    setSubmittedQuery(bookQuery);
    setRequest({ status: 'loading', label: '连接本地服务' });
    try {
      const [runtimeInfo, settingsInfo, bookList] = await Promise.all([
        queryClient.fetchQuery({ ...runtimeQueryOptions(), staleTime: 0 }),
        queryClient.fetchQuery({ ...settingsQueryOptions(), staleTime: 0 }),
        queryClient.fetchQuery({
          ...booksQueryOptions(bookQuery),
          staleTime: 0,
        }),
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
  }, [pushRequestError, queryClient]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadBootstrap('', true);
    }, BOOTSTRAP_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [loadBootstrap]);

  useEffect(() => {
    if (!selectedBook) {
      return;
    }
    if (chaptersQuery.isFetching && !chaptersQuery.data) {
      return deferEffectStateUpdate(() => {
        setRequest({ status: 'loading', label: `加载《${selectedBook.title}》目录` });
      });
    }
    return undefined;
  }, [chaptersQuery.data, chaptersQuery.isFetching, selectedBook]);

  useEffect(() => {
    if (!selectedBook || !chaptersQuery.data) {
      return;
    }
    return deferEffectStateUpdate(() => {
      setChapters(chaptersQuery.data.items);
      if (selectedChapter === null) {
        const progressChapter = selectedBook.last_progress?.chapter_idx;
        const nextChapter = progressChapter ?? chaptersQuery.data.items[0]?.idx ?? 0;
        setSelectedChapter(nextChapter);
      }
      setRequest({
        status: 'success',
        label: `目录已就绪 · ${chaptersQuery.data.total} 章`,
      });
    });
  }, [chaptersQuery.data, selectedBook, selectedChapter]);

  useEffect(() => {
    if (selectedBook && chaptersQuery.isError) {
      return deferEffectStateUpdate(() => {
        pushRequestError(chaptersQuery.error, '目录加载失败');
      });
    }
    return undefined;
  }, [chaptersQuery.error, chaptersQuery.isError, pushRequestError, selectedBook]);

  useEffect(() => {
    if (!selectedBook || selectedChapter === null) return;
    if (chapterDataQuery.isFetching && !chapterDataQuery.data) {
      return deferEffectStateUpdate(() => {
        setLoadedContext(null);
        setRestorePending(false);
        setWindowParagraph(null);
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
          label: `加载第 ${selectedChapter + 1} 章正文`,
        });
      });
    }
    return undefined;
  }, [
    chapterDataQuery.data,
    chapterDataQuery.isFetching,
    selectedBook,
    selectedChapter,
  ]);

  useEffect(() => {
    if (!selectedBook || selectedChapter === null || !chapterDataQuery.data) return;
    const { paragraphs: paragraphResult, progress: progressResult } =
      chapterDataQuery.data;
    const progressParagraph = resolveProgressParagraph(
      progressResult,
      selectedChapter,
      paragraphResult.items.length,
    );

    return deferEffectStateUpdate(() => {
      setParagraphs(paragraphResult.items);
      setProgress(progressResult);
      setSelectedParagraph(progressParagraph);
      setLoadedContext({ bookId: selectedBook.id, chapterIdx: selectedChapter });
      setWindowParagraph(progressParagraph);
      setRestorePending(true);
      setChapterStatus('success');
      setRequest({
        status: 'success',
        label: `正文已就绪 · ${formatNumber(paragraphResult.total)} 段`,
      });
    });
  }, [chapterDataQuery.data, selectedBook, selectedChapter]);

  useEffect(() => {
    if (selectedBook && selectedChapter !== null && chapterDataQuery.isError) {
      return deferEffectStateUpdate(() => {
        setChapterStatus('error');
        setParagraphs(emptyParagraphs);
        pushRequestError(chapterDataQuery.error, '章节正文加载失败');
      });
    }
    return undefined;
  }, [
    chapterDataQuery.error,
    chapterDataQuery.isError,
    pushRequestError,
    selectedBook,
    selectedChapter,
  ]);

  const activeChapter = useMemo(
    () => chapters.find((chapter) => chapter.idx === selectedChapter) ?? null,
    [chapters, selectedChapter],
  );
  const brandSubtitle = selectedBook
    ? activeChapter
      ? `${chapterDisplayTitle(activeChapter)} · ${selectedBook.title}`
      : selectedBook.title
    : '本地小说阅读器 · AI 伴读';

  useEffect(() => {
    if (contextReady && commentsQuery.data) {
      return deferEffectStateUpdate(() => {
        mergeComments(commentsQuery.data.items);
      });
    }
    return undefined;
  }, [commentsQuery.data, contextReady, mergeComments]);

  useEffect(() => {
    if (contextReady && commentsQuery.isError) {
      return deferEffectStateUpdate(() => {
        pushRequestError(commentsQuery.error, '评论刷新失败');
      });
    }
    return undefined;
  }, [commentsQuery.error, commentsQuery.isError, contextReady, pushRequestError]);

  useEffect(() => {
    if (contextReady && currentWindowQuery.data) {
      return deferEffectStateUpdate(() => {
        setCurrentWindow(currentWindowQuery.data.window);
        setWindowCounts({
          ready: currentWindowQuery.data.comments_ready_count,
          target: currentWindowQuery.data.comments_target_count,
        });
      });
    }
    return undefined;
  }, [contextReady, currentWindowQuery.data]);

  useEffect(() => {
    if (!contextReady || !currentWindowQuery.isError) return;
    return deferEffectStateUpdate(() => {
      const info = describeError(currentWindowQuery.error);
      if (info.title !== WINDOW_NOT_FOUND_CODE) {
        pushRequestError(currentWindowQuery.error, '窗口状态加载失败');
        return;
      }
      setCurrentWindow(null);
      setWindowCounts({ ready: 0, target: 0 });
    });
  }, [
    contextReady,
    currentWindowQuery.error,
    currentWindowQuery.isError,
    pushRequestError,
  ]);

  useEffect(() => {
    if (contextReady && chatSessionQuery.data) {
      return deferEffectStateUpdate(() => {
        setChatSession(chatSessionQuery.data.session);
      });
    }
    return undefined;
  }, [chatSessionQuery.data, contextReady]);

  useEffect(() => {
    if (contextReady && chatTurnsQuery.data) {
      return deferEffectStateUpdate(() => {
        setChatTurns(chatTurnsQuery.data.items);
        setChatStatus('success');
      });
    }
    return undefined;
  }, [chatTurnsQuery.data, contextReady]);

  useEffect(() => {
    if (contextReady && (chatSessionQuery.isError || chatTurnsQuery.isError)) {
      const error = chatSessionQuery.error || chatTurnsQuery.error;
      return deferEffectStateUpdate(() => {
        setChatSession(null);
        setChatTurns([]);
        setChatStatus('error');
        pushRequestError(error, '聊天历史加载失败');
      });
    }
    return undefined;
  }, [
    chatSessionQuery.error,
    chatSessionQuery.isError,
    chatTurnsQuery.error,
    chatTurnsQuery.isError,
    contextReady,
    pushRequestError,
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
        if (!selectedBook || selectedChapter === null || !contextReady) return;
        setWindowParagraph(selectedParagraph);
        void queryClient.invalidateQueries({
          queryKey: queryKeys.comments(selectedBook.id, selectedChapter),
        });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.currentWindow(
            selectedBook.id,
            selectedChapter,
            selectedParagraph,
          ),
        });
      }, BACKEND_EVENT_REFRESH_DELAY_MS);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [
    contextReady,
    lastEvent,
    queryClient,
    selectedBook,
    selectedChapter,
    selectedParagraph,
  ]);

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
      try {
        const context = { bookId: selectedBook.id, chapterIdx: selectedChapter };
        const result = await updateProgress({
          bookId: context.bookId,
          chapterIdx: context.chapterIdx,
          paragraphIdx,
          scrollPct: Math.max(0, Math.min(1, scrollPct)),
        });
        const active = activeContextRef.current;
        if (
          !active ||
          active.bookId !== context.bookId ||
          active.chapterIdx !== context.chapterIdx
        ) {
          return;
        }
        applyProgressUpdate(result);
        setWindowParagraph(paragraphIdx);
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
      updateProgress,
    ],
  );

  useEffect(() => {
    if (!selectedBook || selectedChapter === null || !paragraphs.length) return;
    if (restorePending || chapterStatus !== 'success') return;
    const timer = window.setTimeout(() => {
      void saveProgress(selectedParagraph);
    }, PROGRESS_AUTOSAVE_DELAY_MS);
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
      const result = await retryWindow(currentWindow.id);
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
      void queryClient.invalidateQueries({
        queryKey: queryKeys.currentWindow(
          context.bookId,
          context.chapterIdx,
          selectedParagraph,
        ),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.comments(context.bookId, context.chapterIdx),
      });
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
  }, [
    currentWindow,
    pushRequestError,
    queryClient,
    retryWindow,
    selectedBook,
    selectedChapter,
    selectedParagraph,
  ]);

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
    const context = { bookId: selectedBook.id, chapterIdx: selectedChapter };
    let streamController: AbortController | null = null;
    const isCurrentStream = () => {
      const active = activeContextRef.current;
      return (
        chatAbortRef.current === streamController &&
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
    streamController = streamChat(
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
          void queryClient.invalidateQueries({
            queryKey: queryKeys.chatSession(context.bookId, context.chapterIdx),
          });
          void queryClient.invalidateQueries({
            queryKey: queryKeys.chatTurns(data.session_id),
          });
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
    chatAbortRef.current = streamController;
  }, [
    chatInput,
    chatSession,
    chatStatus,
    queryClient,
    selectedBook,
    selectedChapter,
    selectedParagraph,
  ]);

  const abortChat = useCallback(() => {
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
    setSubmittedQuery(query);
    setRequest({ status: 'loading', label: '刷新书库' });
    try {
      const bookList = await queryClient.fetchQuery({
        ...booksQueryOptions(query),
        staleTime: 0,
      });
      setBooks(bookList.items);
      setRequest({ status: 'success', label: `书库已更新 · ${bookList.total} 本书` });
    } catch (error) {
      pushRequestError(error, '书库刷新失败');
    }
  }, [pushRequestError, query, queryClient]);

  const handleImport = useCallback(
    async (file: File) => {
      setImportProgress('loading');
      setRequest({ status: 'loading', label: `导入 ${file.name}` });
      try {
        const result = await importBook(file);
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
        void queryClient.invalidateQueries({
          queryKey: queryKeys.books(submittedQuery),
        });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.chapters(result.book.id),
        });
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
    [
      importBook,
      pushRequestError,
      queryClient,
      resetReadingState,
      submittedQuery,
    ],
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
