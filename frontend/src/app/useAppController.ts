import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { useBackendEvents } from '../hooks/useBackendEvents';
import { describeError, streamChat } from '../lib/api';
import {
  booksQueryOptions,
  type ChapterDataResult,
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
  ListResponse,
  LoadStatus,
  PaneMode,
  Paragraph,
  ParagraphComment,
  ProgressUpdateResponse,
  WindowResponse,
} from '../types';
import type { ReaderContext, RequestState, WindowCounts } from './types';

const initialRequest: RequestState = {
  status: 'idle',
  label: '等待连接',
};

const emptyChapters: ChapterSummary[] = [];
const emptyParagraphs: Paragraph[] = [];
const emptyJobs: JobSummary[] = [];

interface ParagraphSelection {
  context: ReaderContext;
  paragraphIdx: number;
}

interface JobSnapshot {
  context: ReaderContext;
  jobs: JobSummary[];
}

function sameContext(
  left: ReaderContext | null | undefined,
  right: ReaderContext | null | undefined,
): boolean {
  return Boolean(
    left &&
      right &&
      left.bookId === right.bookId &&
      left.chapterIdx === right.chapterIdx,
  );
}

function requestErrorState(error: unknown, label: string): RequestState {
  const info = describeError(error);
  return {
    status: 'error',
    label,
    detail: `${info.title}: ${info.detail}`,
    requestId: info.requestId,
  };
}

function attachComments(
  paragraphs: Paragraph[],
  comments: ParagraphComment[],
): Paragraph[] {
  const commentsByParagraph = new Map<number, ParagraphComment[]>();
  for (const comment of comments) {
    const existing = commentsByParagraph.get(comment.paragraph_idx) || [];
    existing.push(comment);
    commentsByParagraph.set(comment.paragraph_idx, existing);
  }

  return paragraphs.map((paragraph) => ({
    ...paragraph,
    comments: commentsByParagraph.get(paragraph.paragraph_idx) || [],
  }));
}

function windowTargetCount(window: WindowResponse['window']): number {
  return window.focus_end_paragraph_idx - window.focus_start_paragraph_idx + 1;
}

function upsertBook(
  current: ListResponse<BookSummary> | undefined,
  book: BookSummary,
): ListResponse<BookSummary> {
  if (!current) {
    return { items: [book], total: 1 };
  }

  const exists = current.items.some((item) => item.id === book.id);
  return {
    ...current,
    items: [book, ...current.items.filter((item) => item.id !== book.id)],
    total: exists ? current.total : current.total + 1,
  };
}

export function useAppController() {
  const [query, setQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [selectedBook, setSelectedBook] = useState<BookSummary | null>(null);
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [libraryCollapsed, setLibraryCollapsed] = useState(true);
  const [chaptersCollapsed, setChaptersCollapsed] = useState(false);
  const [mode, setMode] = useState<PaneMode>('library');
  const [request, setRequest] = useState<RequestState>(initialRequest);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importProgress, setImportProgress] = useState<LoadStatus>('idle');
  const [progressSync, setProgressSync] = useState<LoadStatus>('idle');
  const [paragraphSelection, setParagraphSelection] =
    useState<ParagraphSelection | null>(null);
  const [jobSnapshot, setJobSnapshot] = useState<JobSnapshot | null>(null);
  const [localChatSession, setLocalChatSession] = useState<ChatSession | null>(null);
  const [localChatTurns, setLocalChatTurns] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatStatus, setChatStatus] = useState<LoadStatus>('idle');
  const [streamingTurn, setStreamingTurn] = useState<ChatTurn | null>(null);
  const [restoreSettledContext, setRestoreSettledContext] =
    useState<ReaderContext | null>(null);
  const queryClient = useQueryClient();
  const chatAbortRef = useRef<AbortController | null>(null);
  const activeContextRef = useRef<ReaderContext | null>(null);
  const selectedBookId = selectedBook?.id ?? null;

  const runtimeQuery = useQuery({ ...runtimeQueryOptions(), enabled: false });
  const settingsQuery = useQuery({ ...settingsQueryOptions(), enabled: false });
  const booksQuery = useQuery({
    ...booksQueryOptions(submittedQuery),
    enabled: false,
  });
  const chaptersQuery = useChaptersQuery(selectedBookId);
  const chapters = useMemo(
    () => chaptersQuery.data?.items ?? emptyChapters,
    [chaptersQuery.data],
  );
  const activeChapterIdx = selectedBook
    ? selectedChapter ?? selectedBook.last_progress?.chapter_idx ?? chapters[0]?.idx ?? null
    : null;
  const activeContext = useMemo<ReaderContext | null>(
    () =>
      selectedBookId !== null && activeChapterIdx !== null
        ? { bookId: selectedBookId, chapterIdx: activeChapterIdx }
        : null,
    [activeChapterIdx, selectedBookId],
  );
  const chapterDataQuery = useChapterDataQuery(selectedBookId, activeChapterIdx);
  const baseParagraphs = chapterDataQuery.data?.paragraphs.items ?? emptyParagraphs;
  const progress = chapterDataQuery.data?.progress ?? null;
  const progressParagraph = chapterDataQuery.data
    ? resolveProgressParagraph(
        chapterDataQuery.data.progress,
        activeChapterIdx ?? 0,
        chapterDataQuery.data.paragraphs.items.length,
      )
    : 0;
  const selectedParagraph = sameContext(paragraphSelection?.context, activeContext)
    ? paragraphSelection!.paragraphIdx
    : progressParagraph;
  const contextReady = Boolean(activeContext && chapterDataQuery.data);
  const commentsQuery = useCommentsQuery(
    selectedBookId,
    activeChapterIdx,
    contextReady,
  );
  const currentWindowQuery = useCurrentWindowQuery(
    selectedBookId,
    activeChapterIdx,
    selectedParagraph,
    contextReady,
  );
  const chatSessionQuery = useChatSessionQuery(
    selectedBookId,
    activeChapterIdx,
    contextReady,
  );
  const localChatSessionForContext = sameContext(
    localChatSession
      ? { bookId: localChatSession.book_id, chapterIdx: localChatSession.chapter_idx }
      : null,
    activeContext,
  )
    ? localChatSession
    : null;
  const chatSessionId =
    localChatSessionForContext?.id ?? chatSessionQuery.data?.session.id ?? null;
  const chatTurnsQuery = useChatTurnsQuery(contextReady ? chatSessionId : null);
  const { mutateAsync: importBook } = useImportBookMutation();
  const { mutateAsync: updateProgress } = useUpdateProgressMutation();
  const { mutateAsync: retryWindow } = useRetryWindowMutation();

  const runtime = runtimeQuery.data ?? null;
  const settings = settingsQuery.data ?? null;
  const books = booksQuery.data?.items ?? [];
  const chapterStatus = useMemo<LoadStatus>(() => {
    if (!selectedBook || activeChapterIdx === null) return 'idle';
    if (chapterDataQuery.isError) return 'error';
    if (chapterDataQuery.data) return 'success';
    if (chapterDataQuery.isFetching) return 'loading';
    return 'idle';
  }, [
    activeChapterIdx,
    chapterDataQuery.data,
    chapterDataQuery.isError,
    chapterDataQuery.isFetching,
    selectedBook,
  ]);
  const paragraphs = useMemo(() => {
    if (!contextReady || !commentsQuery.data) return baseParagraphs;
    return attachComments(baseParagraphs, commentsQuery.data.items);
  }, [baseParagraphs, commentsQuery.data, contextReady]);
  const currentWindow = contextReady ? currentWindowQuery.data?.window ?? null : null;
  const windowCounts = useMemo<WindowCounts>(() => {
    if (!contextReady || !currentWindowQuery.data) {
      return { ready: 0, target: 0 };
    }
    return {
      ready: currentWindowQuery.data.comments_ready_count,
      target: currentWindowQuery.data.comments_target_count,
    };
  }, [contextReady, currentWindowQuery.data]);
  const jobs = sameContext(jobSnapshot?.context, activeContext)
    ? jobSnapshot!.jobs
    : emptyJobs;
  const chatSession = contextReady
    ? localChatSessionForContext ?? chatSessionQuery.data?.session ?? null
    : null;
  const chatTurns = useMemo(() => {
    if (!contextReady || !activeContext) return [];
    const turnsById = new Map<number, ChatTurn>();
    for (const turn of chatTurnsQuery.data?.items ?? []) {
      turnsById.set(turn.id, turn);
    }
    for (const turn of localChatTurns) {
      if (turn.book_id === activeContext.bookId && turn.chapter_idx === activeContext.chapterIdx) {
        turnsById.set(turn.id, turn);
      }
    }
    return Array.from(turnsById.values());
  }, [activeContext, chatTurnsQuery.data, contextReady, localChatTurns]);
  const effectiveChatStatus = useMemo<LoadStatus>(() => {
    if (chatStatus === 'loading') return 'loading';
    if (contextReady && (chatSessionQuery.isError || chatTurnsQuery.isError)) {
      return 'error';
    }
    if (chatStatus === 'error') return 'error';
    if (contextReady && chatTurnsQuery.data) return 'success';
    return chatStatus;
  }, [
    chatSessionQuery.isError,
    chatStatus,
    chatTurnsQuery.data,
    chatTurnsQuery.isError,
    contextReady,
  ]);
  const restorePending = Boolean(
    contextReady && activeContext && !sameContext(restoreSettledContext, activeContext),
  );
  const activeChapter = useMemo(
    () => chapters.find((chapter) => chapter.idx === activeChapterIdx) ?? null,
    [activeChapterIdx, chapters],
  );
  const brandSubtitle = selectedBook
    ? activeChapter
      ? `${chapterDisplayTitle(activeChapter)} · ${selectedBook.title}`
      : selectedBook.title
    : '本地小说阅读器 · AI 伴读';

  const { connection, events, lastEvent } = useBackendEvents(
    selectedBookId,
    activeChapterIdx,
  );

  const derivedRequest = useMemo<RequestState>(() => {
    if (selectedBook && chaptersQuery.isFetching && !chaptersQuery.data) {
      return { status: 'loading', label: `加载《${selectedBook.title}》目录` };
    }
    if (selectedBook && chaptersQuery.isError) {
      return requestErrorState(chaptersQuery.error, '目录加载失败');
    }
    if (
      selectedBook &&
      activeChapterIdx !== null &&
      chapterDataQuery.isFetching &&
      !chapterDataQuery.data
    ) {
      return {
        status: 'loading',
        label: `加载第 ${activeChapterIdx + 1} 章正文`,
      };
    }
    if (selectedBook && activeChapterIdx !== null && chapterDataQuery.isError) {
      return requestErrorState(chapterDataQuery.error, '章节正文加载失败');
    }
    if (contextReady && commentsQuery.isError) {
      return requestErrorState(commentsQuery.error, '评论刷新失败');
    }
    if (contextReady && currentWindowQuery.isError) {
      const info = describeError(currentWindowQuery.error);
      if (info.title !== WINDOW_NOT_FOUND_CODE) {
        return requestErrorState(currentWindowQuery.error, '窗口状态加载失败');
      }
    }
    if (contextReady && (chatSessionQuery.isError || chatTurnsQuery.isError)) {
      return requestErrorState(
        chatSessionQuery.error || chatTurnsQuery.error,
        '聊天历史加载失败',
      );
    }
    return request;
  }, [
    activeChapterIdx,
    chapterDataQuery.data,
    chapterDataQuery.error,
    chapterDataQuery.isError,
    chapterDataQuery.isFetching,
    chaptersQuery.data,
    chaptersQuery.error,
    chaptersQuery.isError,
    chaptersQuery.isFetching,
    chatSessionQuery.error,
    chatSessionQuery.isError,
    chatTurnsQuery.error,
    chatTurnsQuery.isError,
    commentsQuery.error,
    commentsQuery.isError,
    contextReady,
    currentWindowQuery.error,
    currentWindowQuery.isError,
    request,
    selectedBook,
  ]);

  const setActiveSelectedParagraph = useCallback(
    (paragraphIdx: number) => {
      if (!activeContext) return;
      setParagraphSelection({ context: activeContext, paragraphIdx });
    },
    [activeContext],
  );

  const pushRequestError = useCallback((error: unknown, label: string) => {
    setRequest(requestErrorState(error, label));
  }, []);

  const applyProgressUpdate = useCallback(
    (
      result: ProgressUpdateResponse,
      context: ReaderContext,
      paragraphIdx: number,
    ) => {
      setJobSnapshot({ context, jobs: result.jobs });
      queryClient.setQueryData<ChapterDataResult>(
        queryKeys.chapterData(context.bookId, context.chapterIdx),
        (current) =>
          current
            ? {
                ...current,
                progress: result.progress,
              }
            : current,
      );

      const currentWindowKey = queryKeys.currentWindow(
        context.bookId,
        context.chapterIdx,
        paragraphIdx,
      );
      if (!result.current_window) {
        queryClient.removeQueries({ queryKey: currentWindowKey, exact: true });
        return;
      }

      queryClient.setQueryData<WindowResponse>(currentWindowKey, (current) => ({
        window: result.current_window!,
        comments_ready_count: current?.comments_ready_count ?? 0,
        comments_target_count:
          current?.comments_target_count ?? windowTargetCount(result.current_window!),
      }));
    },
    [queryClient],
  );

  const resetReadingState = useCallback(() => {
    activeContextRef.current = null;
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;

    setProgressSync('idle');
    setParagraphSelection(null);
    setJobSnapshot(null);
    setLocalChatSession(null);
    setLocalChatTurns([]);
    setChatInput('');
    setChatStatus('idle');
    setStreamingTurn(null);
    setRestoreSettledContext(null);
  }, []);

  useEffect(() => {
    activeContextRef.current = activeContext;
  }, [activeContext]);

  const loadBootstrap = useCallback(async (bookQuery = '', autoSelect = false) => {
    setSubmittedQuery(bookQuery);
    setRequest({ status: 'loading', label: '连接本地服务' });
    try {
      const [, , bookList] = await Promise.all([
        queryClient.fetchQuery({ ...runtimeQueryOptions(), staleTime: 0 }),
        queryClient.fetchQuery({ ...settingsQueryOptions(), staleTime: 0 }),
        queryClient.fetchQuery({
          ...booksQueryOptions(bookQuery),
          staleTime: 0,
        }),
      ]);
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
        if (!activeContext || !contextReady) return;
        void queryClient.invalidateQueries({
          queryKey: queryKeys.comments(activeContext.bookId, activeContext.chapterIdx),
        });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.currentWindow(
            activeContext.bookId,
            activeContext.chapterIdx,
            selectedParagraph,
          ),
        });
      }, BACKEND_EVENT_REFRESH_DELAY_MS);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [
    activeContext,
    contextReady,
    lastEvent,
    queryClient,
    selectedParagraph,
  ]);

  const saveProgress = useCallback(
    async (paragraphIdx: number, force = false) => {
      if (
        !activeContext ||
        !selectedBook ||
        activeChapterIdx === null ||
        !paragraphs.length ||
        chapterStatus !== 'success' ||
        paragraphIdx < 0 ||
        paragraphIdx >= paragraphs.length ||
        (restorePending && !force)
      ) {
        return;
      }
      if (
        !force &&
        progress?.book_id === selectedBook.id &&
        progress?.chapter_idx === activeChapterIdx &&
        progress.paragraph_idx === paragraphIdx &&
        progress.updated_at
      ) {
        return;
      }

      const scrollPct =
        paragraphs.length <= 1 ? 0 : paragraphIdx / Math.max(paragraphs.length - 1, 1);

      setProgressSync('loading');
      try {
        const context = activeContext;
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
        applyProgressUpdate(result, context, paragraphIdx);
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
        if (!active || !sameContext(active, activeContext)) {
          return;
        }
        setProgressSync('error');
        pushRequestError(error, '阅读进度保存失败');
      }
    },
    [
      activeChapterIdx,
      activeContext,
      applyProgressUpdate,
      chapterStatus,
      paragraphs.length,
      progress,
      pushRequestError,
      restorePending,
      selectedBook,
      updateProgress,
    ],
  );

  useEffect(() => {
    if (!selectedBook || activeChapterIdx === null || !paragraphs.length) return;
    if (restorePending || chapterStatus !== 'success') return;
    const timer = window.setTimeout(() => {
      void saveProgress(selectedParagraph);
    }, PROGRESS_AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [
    activeChapterIdx,
    chapterStatus,
    paragraphs.length,
    restorePending,
    saveProgress,
    selectedBook,
    selectedParagraph,
  ]);

  const retryCurrentWindow = useCallback(async () => {
    if (!currentWindow || !activeContext) return;
    const context = activeContext;
    setRequest({ status: 'loading', label: `重试窗口 ${currentWindow.id}` });
    try {
      const result = await retryWindow(currentWindow.id);
      const active = activeContextRef.current;
      if (!active || !sameContext(active, context)) {
        return;
      }
      setJobSnapshot((current) => ({
        context,
        jobs: [
          result.job,
          ...(sameContext(current?.context, context) ? current!.jobs : []).filter(
            (job) => job.id !== result.job.id,
          ),
        ],
      }));
      queryClient.setQueryData<WindowResponse>(
        queryKeys.currentWindow(context.bookId, context.chapterIdx, selectedParagraph),
        (current) => ({
          window: result.window,
          comments_ready_count: current?.comments_ready_count ?? 0,
          comments_target_count:
            current?.comments_target_count ?? windowTargetCount(result.window),
        }),
      );
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
      if (!active || !sameContext(active, context)) {
        return;
      }
      pushRequestError(error, '窗口重试失败');
    }
  }, [
    activeContext,
    currentWindow,
    pushRequestError,
    queryClient,
    retryWindow,
    selectedParagraph,
  ]);

  const sendChat = useCallback(() => {
    if (
      !selectedBook ||
      !activeContext ||
      !chatInput.trim() ||
      chatStatus === 'loading'
    ) {
      return;
    }

    const userMsg = chatInput.trim();
    const paragraphIdx = selectedParagraph;
    const context = activeContext;
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
        bookId: context.bookId,
        chapterIdx: context.chapterIdx,
        paragraphIdx,
        sessionId: chatSession?.id ?? null,
        userMsg,
      },
      {
        onStarted: (data) => {
          if (!isCurrentStream()) return;
          const session: ChatSession = {
            id: data.session_id,
            book_id: context.bookId,
            chapter_idx: context.chapterIdx,
            title: null,
            last_paragraph_idx: paragraphIdx,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
          setLocalChatSession((current) =>
            current && current.id === data.session_id ? current : session,
          );
          queryClient.setQueryData<{ session: ChatSession }>(
            queryKeys.chatSession(context.bookId, context.chapterIdx),
            { session },
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
          setLocalChatTurns((current) => [
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
          setLocalChatTurns((current) => [
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
    activeContext,
    chatInput,
    chatSession,
    chatStatus,
    queryClient,
    selectedBook,
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
    if (activeContext) {
      setRestoreSettledContext(activeContext);
    }
  }, [activeContext]);

  const refreshBooks = useCallback(async () => {
    setSubmittedQuery(query);
    setRequest({ status: 'loading', label: '刷新书库' });
    try {
      const bookList = await queryClient.fetchQuery({
        ...booksQueryOptions(query),
        staleTime: 0,
      });
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
        resetReadingState();
        setSelectedChapter(null);
        setSelectedBook(result.book);
        queryClient.setQueryData<ListResponse<BookSummary>>(
          queryKeys.books(submittedQuery),
          (current) => upsertBook(current, result.book),
        );
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

    resetReadingState();
    setSelectedBook(book);
    setSelectedChapter(null);
    setMode('reader');
  }, [resetReadingState, selectedBook?.id]);

  const selectChapter = useCallback(
    (idx: number) => {
      resetReadingState();
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
    request: derivedRequest,
    importResult,
    importProgress,
    paragraphs,
    chapterStatus,
    progress,
    progressSync,
    selectedParagraph,
    setSelectedParagraph: setActiveSelectedParagraph,
    currentWindow,
    windowCounts,
    jobs,
    chatTurns,
    chatInput,
    setChatInput,
    chatStatus: effectiveChatStatus,
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
