import {
  Activity,
  AlertCircle,
  BookOpen,
  Bot,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Clock3,
  Database,
  Eye,
  Gauge,
  Import,
  Library,
  Layers,
  Loader2,
  MessageSquareText,
  RefreshCcw,
  Search,
  Settings,
  Signal,
  Sparkles,
  UploadCloud,
  WifiOff,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useBackendEvents } from './hooks/useBackendEvents';
import { api, describeError, streamChat } from './lib/api';
import type {
  ActivityItem,
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
} from './types';

interface RequestState {
  status: LoadStatus;
  label: string;
  detail?: string;
  requestId?: string | null;
}

interface ReaderContext {
  bookId: number;
  chapterIdx: number;
}

const initialRequest: RequestState = {
  status: 'idle',
  label: '等待连接',
};

const emptyParagraphs: Paragraph[] = [];

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '0';
  return new Intl.NumberFormat('zh-CN').format(value);
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '未记录';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function compactModelName(value: string | null | undefined): string {
  if (!value) return '未知';
  return value.split('/').pop() || value;
}

function compactDataDir(value: string | null | undefined): string {
  if (!value) return '未连接';
  const marker = '/.vibe_reader';
  const idx = value.indexOf(marker);
  return idx >= 0 ? `~${value.slice(idx)}` : value;
}

function statusCopy(status: LoadStatus): string {
  if (status === 'loading') return '进行中';
  if (status === 'success') return '已完成';
  if (status === 'error') return '需处理';
  return '空闲';
}

function chapterDisplayTitle(chapter: ChapterSummary | null): string {
  if (!chapter) return '未选择章节';
  return chapter.title || `第 ${chapter.idx + 1} 章`;
}

function bookAuthorLabel(book: BookSummary): string | null {
  const author = book.author?.trim();
  if (!author) return '未知作者';
  return author === book.title.trim() ? null : author;
}

function StatusPill({
  status,
  children,
}: {
  status: LoadStatus | 'open' | 'connecting' | 'closed' | 'bad' | 'good';
  children: React.ReactNode;
}) {
  return <span className={`status-pill status-${status}`}>{children}</span>;
}

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <BookOpen size={24} />
    </div>
  );
}

function App() {
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
  const [windowCounts, setWindowCounts] = useState({ ready: 0, target: 0 });
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

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <strong>Vibe Reader Mini</strong>
            <span>{brandSubtitle}</span>
          </div>
        </div>
        <div className="topbar-actions">
          <StatusPill status={request.status}>
            {request.status === 'loading' && <Loader2 size={14} className="spin" />}
            {request.status === 'success' && <CheckCircle2 size={14} />}
            {request.status === 'error' && <AlertCircle size={14} />}
            {statusCopy(request.status)}
          </StatusPill>
          <button className="icon-button" onClick={() => void loadBootstrap()} title="刷新运行状态">
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      <main className="workspace" data-mode={mode}>
        <aside className={`library-panel nav-section ${libraryCollapsed ? 'is-collapsed' : ''}`}>
          <NavSectionToggle
            icon={<Library size={18} />}
            label="书库"
            current={selectedBook?.title || '未选择书籍'}
            collapsed={libraryCollapsed}
            onToggle={() => setLibraryCollapsed((value) => !value)}
          />
          <div className="nav-section-body">
            <LibraryHeader
              query={query}
              setQuery={setQuery}
              onRefresh={refreshBooks}
              onImport={handleImport}
              importProgress={importProgress}
            />
            <BookList
              books={books}
              selectedBookId={selectedBook?.id ?? null}
              onSelect={selectBook}
            />
            {importResult && <ImportReceipt result={importResult} />}
          </div>
        </aside>

        <aside className={`chapter-panel nav-section ${chaptersCollapsed ? 'is-collapsed' : ''}`}>
          <NavSectionToggle
            icon={<Layers size={18} />}
            label="章节"
            current={selectedBook ? chapterDisplayTitle(activeChapter) : '先选择书籍'}
            collapsed={chaptersCollapsed}
            onToggle={() => setChaptersCollapsed((value) => !value)}
          />
          <div className="nav-section-body">
            {selectedBook ? (
              <ChapterNavigator
                activeChapter={activeChapter}
                chapters={chapters}
                onSelectChapter={(idx) => {
                  resetReadingState(false);
                  setSelectedChapter(idx);
                  setMode('reader');
                }}
              />
            ) : (
              <EmptyChapterPanel />
            )}
          </div>
        </aside>

        <section className="reader-stage">
          {selectedBook ? (
            <ReaderPreview
              activeChapter={activeChapter}
              paragraphs={paragraphs}
              chapterStatus={chapterStatus}
              progress={progress}
              progressSync={progressSync}
              selectedParagraph={selectedParagraph}
              restorePending={restorePending}
              currentWindow={currentWindow}
              onVisibleParagraph={setSelectedParagraph}
              onSaveProgress={() => void saveProgress(selectedParagraph, true)}
              onRestoreSettled={settleRestore}
            />
          ) : (
            <EmptyReader onImport={handleImport} importProgress={importProgress} />
          )}
        </section>

        <aside className="assistant-panel">
          <ChatCompanion
            chatTurns={chatTurns}
            streamingTurn={streamingTurn}
            chatInput={chatInput}
            chatStatus={chatStatus}
            selectedBook={selectedBook}
            activeChapter={activeChapter}
            selectedParagraph={selectedParagraph}
            onInputChange={setChatInput}
            onSend={sendChat}
            onAbort={abortChat}
          />
          <StatusCenter
            request={request}
            runtime={runtime}
            settings={settings}
            connection={connection}
            events={events}
            selectedBook={selectedBook}
            activeChapter={activeChapter}
            currentWindow={currentWindow}
            windowCounts={windowCounts}
            jobs={jobs}
            onRetryWindow={() => void retryCurrentWindow()}
          />
        </aside>
      </main>

      <nav className="mobile-nav" aria-label="移动端导航">
        <button
          className={mode === 'library' ? 'active' : ''}
          onClick={() => setMode('library')}
        >
          <Library size={18} />
          书库
        </button>
        <button
          className={mode === 'reader' ? 'active' : ''}
          onClick={() => setMode('reader')}
        >
          <BookOpen size={18} />
          阅读
        </button>
        <button
          className={mode === 'chapters' ? 'active' : ''}
          onClick={() => setMode('chapters')}
        >
          <Layers size={18} />
          章节
        </button>
        <button
          className={mode === 'assistant' ? 'active' : ''}
          onClick={() => setMode('assistant')}
        >
          <Bot size={18} />
          AI
        </button>
        <button
          className={mode === 'status' ? 'active' : ''}
          onClick={() => setMode('status')}
        >
          <Activity size={18} />
          状态
        </button>
      </nav>
    </div>
  );
}

function NavSectionToggle({
  icon,
  label,
  current,
  collapsed,
  onToggle,
}: {
  icon: React.ReactNode;
  label: string;
  current: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className="nav-section-toggle"
      type="button"
      aria-expanded={!collapsed}
      onClick={onToggle}
    >
      <span className="nav-section-title">
        {icon}
        <span>{label}</span>
      </span>
      <span className="nav-section-current">{current}</span>
      {collapsed ? <ChevronRight size={17} /> : <ChevronDown size={17} />}
    </button>
  );
}

function LibraryHeader({
  query,
  setQuery,
  onRefresh,
  onImport,
  importProgress,
}: {
  query: string;
  setQuery: (value: string) => void;
  onRefresh: () => Promise<void>;
  onImport: (file: File) => Promise<void>;
  importProgress: LoadStatus;
}) {
  return (
    <div className="library-header">
      <div className="panel-title">
        <Library size={18} />
        <span>书库</span>
      </div>
      <label className="search-box">
        <Search size={16} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void onRefresh();
          }}
          placeholder="搜索书名或作者"
        />
      </label>
      <ImportButton onImport={onImport} importProgress={importProgress} />
      <button className="soft-button" onClick={() => void onRefresh()}>
        <RefreshCcw size={16} />
        刷新书库
      </button>
    </div>
  );
}

function ImportButton({
  onImport,
  importProgress,
}: {
  onImport: (file: File) => Promise<void>;
  importProgress: LoadStatus;
}) {
  return (
    <label className={`import-button import-${importProgress}`}>
      {importProgress === 'loading' ? (
        <Loader2 size={18} className="spin" />
      ) : (
        <UploadCloud size={18} />
      )}
      <span>{importProgress === 'loading' ? '导入中' : '导入 EPUB'}</span>
      <input
        type="file"
        accept=".epub,application/epub+zip"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) void onImport(file);
          event.currentTarget.value = '';
        }}
      />
    </label>
  );
}

function BookList({
  books,
  selectedBookId,
  onSelect,
}: {
  books: BookSummary[];
  selectedBookId: number | null;
  onSelect: (book: BookSummary) => void;
}) {
  if (!books.length) {
    return (
      <div className="empty-list">
        <Import size={26} />
        <strong>还没有书</strong>
        <span>书库等待第一本 EPUB。</span>
      </div>
    );
  }

  return (
    <div className="book-list">
      {books.map((book) => {
        const authorLabel = bookAuthorLabel(book);
        return (
          <button
            className={`book-card ${selectedBookId === book.id ? 'active' : ''}`}
            key={book.id}
            onClick={() => onSelect(book)}
          >
            <BookCover book={book} />
            <span className="book-meta">
              <strong>{book.title}</strong>
              {authorLabel && <span>{authorLabel}</span>}
              <small>
                {book.total_chapters} 章
                {book.last_progress
                  ? ` · 读到 ${book.last_progress.chapter_idx + 1}-${book.last_progress.paragraph_idx + 1}`
                  : ' · 未开始'}
              </small>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function BookCover({ book }: { book: BookSummary }) {
  if (book.cover_url) {
    return <img className="book-cover" src={book.cover_url} alt="" />;
  }
  const initials = book.title.slice(0, 2).toUpperCase();
  return (
    <div className="book-cover fallback-cover" aria-hidden="true">
      <span>{initials}</span>
    </div>
  );
}

function ImportReceipt({ result }: { result: ImportResult }) {
  return (
    <div className="import-receipt">
      <div>
        <CheckCircle2 size={18} />
        <strong>最近导入完成</strong>
      </div>
      <span>{result.book.title}</span>
      <small>
        {result.import_stats.chapter_count} 章 · {formatNumber(result.import_stats.char_count)} 字符 ·{' '}
        {result.import_stats.duration_ms}ms
      </small>
    </div>
  );
}

function ChapterNavigator({
  activeChapter,
  chapters,
  onSelectChapter,
}: {
  activeChapter: ChapterSummary | null;
  chapters: ChapterSummary[];
  onSelectChapter: (idx: number) => void;
}) {
  return (
    <section className="chapter-strip">
      <div className="section-heading">
        <Layers size={18} />
        <strong>章节</strong>
      </div>
      <div className="chapter-list">
        {chapters.map((chapter) => (
          <button
            key={chapter.idx}
            className={activeChapter?.idx === chapter.idx ? 'active' : ''}
            onClick={() => onSelectChapter(chapter.idx)}
          >
            <span>{chapterDisplayTitle(chapter)}</span>
            <small>
              {formatNumber(chapter.paragraph_count)} 段 ·{' '}
              {formatNumber(chapter.token_estimate)} tokens
            </small>
          </button>
        ))}
      </div>
    </section>
  );
}

function EmptyChapterPanel() {
  return (
    <section className="chapter-strip empty-chapter">
      <div className="section-heading">
        <Layers size={18} />
        <strong>章节</strong>
      </div>
      <div className="empty-feed">
        <Layers size={18} />
        <span>选择书籍后显示章节。</span>
      </div>
    </section>
  );
}

function ReaderPreview({
  activeChapter,
  paragraphs,
  chapterStatus,
  progress,
  progressSync,
  selectedParagraph,
  restorePending,
  currentWindow,
  onVisibleParagraph,
  onSaveProgress,
  onRestoreSettled,
}: {
  activeChapter: ChapterSummary | null;
  paragraphs: Paragraph[];
  chapterStatus: LoadStatus;
  progress: ReadingProgress | null;
  progressSync: LoadStatus;
  selectedParagraph: number;
  restorePending: boolean;
  currentWindow: ReadingWindow | null;
  onVisibleParagraph: (idx: number) => void;
  onSaveProgress: () => void;
  onRestoreSettled: () => void;
}) {
  const progressPct =
    paragraphs.length > 1
      ? Math.round((selectedParagraph / Math.max(paragraphs.length - 1, 1)) * 100)
      : paragraphs.length
        ? 100
        : 0;
  const progressWidth = `${Math.max(0, Math.min(100, progressPct))}%`;

  return (
    <div className="reader-preview">
      <section className="reading-surface">
        <div className="reader-toolbar">
          <div>
            <span className="eyebrow">{chapterDisplayTitle(activeChapter)}</span>
            <strong>
              P{selectedParagraph + 1}
              {paragraphs.length ? ` / ${paragraphs.length} · ${progressPct}%` : ''}
            </strong>
            <small>
              {progress?.updated_at
                ? `自动保存 ${formatDate(progress.updated_at)}`
                : '自动保存待同步'}
            </small>
          </div>
          <div className="toolbar-actions">
            <StatusPill status={progressSync}>
              {progressSync === 'loading' && <Loader2 size={14} className="spin" />}
              {progressSync === 'success' && <CheckCircle2 size={14} />}
              {progressSync === 'error' && <AlertCircle size={14} />}
              {progressSync === 'idle' ? '进度待同步' : statusCopy(progressSync)}
            </StatusPill>
            <button className="soft-inline-button" onClick={onSaveProgress}>
              <RefreshCcw size={16} />
              同步进度
            </button>
          </div>
          <div className="reader-progress-meter" aria-label="阅读进度">
            <span style={{ width: progressWidth }} />
          </div>
        </div>

        <ReaderDocument
          paragraphs={paragraphs}
          chapterStatus={chapterStatus}
          selectedParagraph={selectedParagraph}
          restorePending={restorePending}
          currentWindow={currentWindow}
          onVisibleParagraph={onVisibleParagraph}
          onRestoreSettled={onRestoreSettled}
        />
      </section>
    </div>
  );
}

function WindowStatusCard({
  currentWindow,
  windowCounts,
  jobs,
  onRetryWindow,
}: {
  currentWindow: ReadingWindow | null;
  windowCounts: { ready: number; target: number };
  jobs: JobSummary[];
  onRetryWindow: () => void;
}) {
  if (!currentWindow) {
    return (
      <div className="window-card empty-window">
        <div>
          <Bot size={18} />
          <strong>AI 窗口等待触发</strong>
        </div>
        <span>暂无活动窗口。</span>
      </div>
    );
  }

  const readyPct =
    windowCounts.target > 0
      ? Math.round((windowCounts.ready / windowCounts.target) * 100)
      : 0;
  const retryable = currentWindow.status === 'failed' || currentWindow.status === 'done';

  return (
    <div className={`window-card window-${currentWindow.status}`}>
      <div className="window-card-header">
        <div>
          <Bot size={18} />
          <strong>窗口 {currentWindow.id}</strong>
        </div>
        <StatusPill
          status={
            currentWindow.status === 'done'
              ? 'success'
              : currentWindow.status === 'failed'
                ? 'error'
                : 'loading'
          }
        >
          {currentWindow.status}
        </StatusPill>
      </div>

      <div className="window-range">
        <span>
          覆盖 P{currentWindow.start_paragraph_idx + 1} - P
          {currentWindow.end_paragraph_idx + 1}
        </span>
        <span>
          焦点 P{currentWindow.focus_start_paragraph_idx + 1} - P
          {currentWindow.focus_end_paragraph_idx + 1}
        </span>
        <span>AI frontier P{currentWindow.assistant_frontier_paragraph_idx + 1}</span>
      </div>

      <div className="progress-meter" aria-label="评论准备进度">
        <span style={{ width: `${Math.min(100, readyPct)}%` }} />
      </div>
      <small>
        评论 {windowCounts.ready} / {windowCounts.target || '待估算'}
        {currentWindow.error ? ` · ${currentWindow.error}` : ''}
      </small>

      <div className="job-strip">
        {jobs.slice(0, 3).map((job) => (
          <span key={job.id} className={`job-chip job-${job.status}`}>
            #{job.id} {job.job_type} · {job.status}
          </span>
        ))}
      </div>

      {retryable && (
        <button className="soft-inline-button" onClick={onRetryWindow}>
          <RefreshCcw size={16} />
          重试 AI 窗口
        </button>
      )}
    </div>
  );
}

function ReaderDocument({
  paragraphs,
  chapterStatus,
  selectedParagraph,
  restorePending,
  currentWindow,
  onVisibleParagraph,
  onRestoreSettled,
}: {
  paragraphs: Paragraph[];
  chapterStatus: LoadStatus;
  selectedParagraph: number;
  restorePending: boolean;
  currentWindow: ReadingWindow | null;
  onVisibleParagraph: (idx: number) => void;
  onRestoreSettled: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const paragraphRefs = useRef(new Map<number, HTMLElement>());
  const manualSelectionUntilRef = useRef(0);

  const selectParagraph = useCallback(
    (idx: number) => {
      manualSelectionUntilRef.current = Date.now() + 1200;
      onVisibleParagraph(idx);
    },
    [onVisibleParagraph],
  );

  useEffect(() => {
    const root = rootRef.current;
    if (!root || !paragraphs.length) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort(
            (a, b) =>
              a.boundingClientRect.top - b.boundingClientRect.top ||
              b.intersectionRatio - a.intersectionRatio,
          )[0];
        if (!visible) return;
        if (restorePending) return;
        if (Date.now() < manualSelectionUntilRef.current) return;
        const idx = Number((visible.target as HTMLElement).dataset.paragraphIdx);
        if (Number.isFinite(idx)) onVisibleParagraph(idx);
      },
      {
        root,
        rootMargin: '-18% 0px -58% 0px',
        threshold: [0.1, 0.35, 0.6],
      },
    );

    paragraphRefs.current.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [onVisibleParagraph, paragraphs, restorePending]);

  useEffect(() => {
    if (!restorePending || !paragraphs.length) return undefined;
    const root = rootRef.current;
    const target = paragraphRefs.current.get(selectedParagraph);
    let settleTimer: number | undefined;

    const scrollTimer = window.setTimeout(() => {
      if (root && target) {
        target.scrollIntoView({ block: 'start' });
      }
      settleTimer = window.setTimeout(onRestoreSettled, 260);
    }, 0);

    return () => {
      window.clearTimeout(scrollTimer);
      if (settleTimer !== undefined) window.clearTimeout(settleTimer);
    };
  }, [onRestoreSettled, paragraphs.length, restorePending, selectedParagraph]);

  if (chapterStatus === 'loading') {
    return (
      <div className="reader-loading">
        <Loader2 size={24} className="spin" />
        <strong>正在加载正文</strong>
      </div>
    );
  }

  if (chapterStatus === 'error') {
    return (
      <div className="reader-loading error-state">
        <AlertCircle size={24} />
        <strong>正文加载失败</strong>
      </div>
    );
  }

  if (!paragraphs.length) {
    return (
      <div className="reader-loading">
        <Eye size={24} />
        <strong>暂无正文</strong>
      </div>
    );
  }

  return (
    <div className="reader-document" ref={rootRef}>
      {paragraphs.map((paragraph) => {
        const idx = paragraph.paragraph_idx;
        const isCovered = Boolean(
          currentWindow &&
            idx >= currentWindow.start_paragraph_idx &&
            idx <= currentWindow.end_paragraph_idx,
        );
        const isFocused = Boolean(
          currentWindow &&
            idx >= currentWindow.focus_start_paragraph_idx &&
            idx <= currentWindow.focus_end_paragraph_idx,
        );
        const isFrontier = currentWindow?.assistant_frontier_paragraph_idx === idx;
        const paragraphClass = [
          'paragraph-block',
          idx === selectedParagraph ? 'active' : '',
          isCovered ? 'window-covered' : '',
          isFocused ? 'window-focused' : '',
          isFrontier ? 'window-frontier' : '',
        ]
          .filter(Boolean)
          .join(' ');

        return (
          <article
            className={paragraphClass}
            data-paragraph-idx={idx}
            key={idx}
            tabIndex={0}
            aria-current={idx === selectedParagraph ? 'location' : undefined}
            onClick={() => selectParagraph(idx)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectParagraph(idx);
              }
            }}
            ref={(node) => {
              if (node) {
                paragraphRefs.current.set(idx, node);
              } else {
                paragraphRefs.current.delete(idx);
              }
            }}
          >
            <div className="paragraph-index">P{idx + 1}</div>
            <p>{paragraph.text}</p>
            {paragraph.comments?.length ? (
              <div className="comment-stack">
                {paragraph.comments.map((comment) => (
                  <aside className={`comment-bubble comment-${comment.comment_type}`} key={comment.id}>
                    <span>{comment.comment_type}</span>
                    <p>{comment.comment}</p>
                  </aside>
                ))}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function EmptyReader({
  onImport,
  importProgress,
}: {
  onImport: (file: File) => Promise<void>;
  importProgress: LoadStatus;
}) {
  return (
    <div className="empty-reader">
      <div className="empty-visual">
        <BookOpen size={52} />
        <Sparkles size={24} />
      </div>
      <h1>书库为空</h1>
      <p>等待第一本 EPUB。</p>
      <ImportButton onImport={onImport} importProgress={importProgress} />
    </div>
  );
}

function ChatCompanion({
  chatTurns,
  streamingTurn,
  chatInput,
  chatStatus,
  selectedBook,
  activeChapter,
  selectedParagraph,
  onInputChange,
  onSend,
  onAbort,
}: {
  chatTurns: ChatTurn[];
  streamingTurn: ChatTurn | null;
  chatInput: string;
  chatStatus: LoadStatus;
  selectedBook: BookSummary | null;
  activeChapter: ChapterSummary | null;
  selectedParagraph: number;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onAbort: () => void;
}) {
  const visibleTurns = useMemo(() => {
    const turns = [...chatTurns].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
    return streamingTurn ? [...turns, streamingTurn] : turns;
  }, [chatTurns, streamingTurn]);

  const canSend = Boolean(
    selectedBook && activeChapter && chatInput.trim() && chatStatus !== 'loading',
  );

  return (
    <section className="chat-panel companion-section">
      <div className="panel-title">
        <Bot size={18} />
        <span>AI Companion</span>
      </div>

      <div className="chat-context">
        <span>
          {selectedBook ? selectedBook.title : '未选择书籍'}
          {activeChapter ? ` · ${chapterDisplayTitle(activeChapter)}` : ''}
        </span>
        <StatusPill status={chatStatus}>
          {chatStatus === 'loading' && <Loader2 size={14} className="spin" />}
          {chatStatus === 'success' && <CheckCircle2 size={14} />}
          {chatStatus === 'error' && <AlertCircle size={14} />}
          P{selectedParagraph + 1}
        </StatusPill>
      </div>

      <div className="chat-turns">
        {visibleTurns.length ? (
          visibleTurns.map((turn) => <ChatTurnView key={`${turn.id}-${turn.status}`} turn={turn} />)
        ) : (
          <div className="empty-feed">
            <MessageSquareText size={18} />
            <span>暂无对话记录。</span>
          </div>
        )}
      </div>

      <div className="chat-composer">
        <textarea
          value={chatInput}
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              onSend();
            }
          }}
          placeholder="这里为什么有点奇怪？"
          rows={3}
        />
        <div>
          {chatStatus === 'loading' ? (
            <button className="soft-inline-button" onClick={onAbort}>
              <AlertCircle size={16} />
              停止
            </button>
          ) : (
            <span>当前 P{selectedParagraph + 1}</span>
          )}
          <button className="send-button" disabled={!canSend} onClick={onSend}>
            <MessageSquareText size={16} />
            发送
          </button>
        </div>
      </div>
    </section>
  );
}

function ChatTurnView({ turn }: { turn: ChatTurn }) {
  return (
    <article className={`chat-turn chat-${turn.status}`}>
      <div className="chat-user">
        <strong>你</strong>
        <p>{turn.user_msg}</p>
      </div>
      <div className="chat-ai">
        <strong>
          AI
          {turn.status === 'streaming' && <Loader2 size={14} className="spin" />}
          {turn.status === 'failed' && <AlertCircle size={14} />}
        </strong>
        <p>{turn.ai_msg || (turn.status === 'streaming' ? '正在组织回答...' : '暂无回答')}</p>
        <div className="chat-meta">
          <span>P{turn.paragraph_idx + 1}</span>
          {turn.tokens_in !== null && <span>{formatNumber(turn.tokens_in)} in</span>}
          {turn.tokens_out !== null && <span>{formatNumber(turn.tokens_out)} out</span>}
        </div>
      </div>
    </article>
  );
}

function StatusCenter({
  request,
  runtime,
  settings,
  connection,
  events,
  selectedBook,
  activeChapter,
  currentWindow,
  windowCounts,
  jobs,
  onRetryWindow,
}: {
  request: RequestState;
  runtime: RuntimeInfo | null;
  settings: SettingsSummary | null;
  connection: 'idle' | 'connecting' | 'open' | 'error' | 'closed';
  events: ActivityItem[];
  selectedBook: BookSummary | null;
  activeChapter: ChapterSummary | null;
  currentWindow: ReadingWindow | null;
  windowCounts: { ready: number; target: number };
  jobs: JobSummary[];
  onRetryWindow: () => void;
}) {
  const contextLimit =
    settings?.context?.provider_context_limit_tokens ??
    settings?.context?.effective_input_budget ??
    0;
  const targetTokens =
    settings?.context?.attention_target_input_tokens ??
    settings?.window_l1?.focus_target_tokens ??
    settings?.window?.target_window_tokens ??
    0;

  return (
    <div className="status-center observability-section">
      <div className="panel-title">
        <Activity size={18} />
        <span>状态中心</span>
      </div>

      <section className="status-card main-status">
        <div>
          <strong>{request.label}</strong>
          <span>{request.detail || '暂无异常。'}</span>
        </div>
        <StatusPill status={request.status}>{statusCopy(request.status)}</StatusPill>
        {request.requestId && <code>request_id: {request.requestId}</code>}
      </section>

      <section className="status-grid">
        <MetricCard
          icon={<Database size={18} />}
          label="本地数据"
          value={compactDataDir(runtime?.data_dir)}
          title={runtime?.data_dir || '未连接'}
          tone={runtime ? 'good' : 'warn'}
        />
        <MetricCard
          icon={<Bot size={18} />}
          label="模型"
          value={compactModelName(runtime?.llm.model || settings?.llm.model)}
          title={runtime?.llm.model || settings?.llm.model || '未知'}
          detail={runtime?.llm.api_key_configured ? 'Key 已配置' : 'Key 未配置'}
          tone={runtime?.llm.api_key_configured ? 'good' : 'warn'}
        />
        <MetricCard
          icon={<Gauge size={18} />}
          label="上下文预算"
          value={formatNumber(contextLimit || targetTokens)}
          detail={targetTokens ? `目标 ${formatNumber(targetTokens)}` : '等待配置'}
          tone="info"
        />
        <MetricCard
          icon={connection === 'error' ? <WifiOff size={18} /> : <Signal size={18} />}
          label="后台事件"
          value={connection === 'open' ? '已订阅' : connection}
          detail={
            selectedBook && activeChapter ? chapterDisplayTitle(activeChapter) : '选择章节后订阅'
          }
          tone={connection === 'open' ? 'good' : connection === 'error' ? 'bad' : 'warn'}
        />
      </section>

      <WindowStatusCard
        currentWindow={currentWindow}
        windowCounts={windowCounts}
        jobs={jobs}
        onRetryWindow={onRetryWindow}
      />

      <section className="status-card">
        <div className="section-heading">
          <Clock3 size={18} />
          <strong>活动流</strong>
        </div>
        <ActivityFeed events={events} />
      </section>

      <section className="status-card">
        <div className="section-heading">
          <Settings size={18} />
          <strong>运行摘要</strong>
        </div>
        <dl className="runtime-list">
          <div>
            <dt>版本</dt>
            <dd>{runtime?.version || '未连接'}</dd>
          </div>
          <div>
            <dt>可观测</dt>
            <dd>{runtime?.observability.enabled ? runtime.observability.provider : '关闭'}</dd>
          </div>
          <div>
            <dt>验证模式</dt>
            <dd>{runtime?.verify_mode ? '开启' : '关闭'}</dd>
          </div>
          <div>
            <dt>阅读设置</dt>
            <dd>
              {settings
                ? `${settings.reader.font_size}px / ${settings.reader.line_height}`
                : '等待加载'}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  title,
  detail,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  title?: string;
  detail?: string;
  tone: ActivityItem['tone'];
}) {
  return (
    <div className={`metric-card tone-${tone}`}>
      <span className="metric-icon">{icon}</span>
      <small>{label}</small>
      <strong title={title || value}>{value}</strong>
      {detail && <span>{detail}</span>}
    </div>
  );
}

function ActivityFeed({ events }: { events: ActivityItem[] }) {
  if (!events.length) {
    return (
      <div className="empty-feed">
        <MessageSquareText size={18} />
        <span>暂无活动。</span>
      </div>
    );
  }
  return (
    <div className="activity-feed">
      {events.map((item) => (
        <div className={`activity-item tone-${item.tone}`} key={item.id}>
          <span className="activity-dot" />
          <div>
            <strong>{item.title}</strong>
            {item.detail && <small>{item.detail}</small>}
            {item.traceId && <code>trace_id: {item.traceId}</code>}
            {item.requestId && <code>request_id: {item.requestId}</code>}
          </div>
          <time>{formatDate(item.createdAt)}</time>
        </div>
      ))}
    </div>
  );
}

export default App;
