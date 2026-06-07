import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { useBackendEvents } from '../hooks/useBackendEvents';
import { describeError } from '../lib/api';
import {
  type ChapterDataResult,
  queryKeys,
  useChapterDataQuery,
  useChaptersQuery,
  useCommentsQuery,
  useCurrentWindowQuery,
  useRetryWindowMutation,
  useUpdateProgressMutation,
} from '../lib/apiQueries';
import {
  BACKEND_EVENT_REFRESH_DELAY_MS,
  PROGRESS_AUTOSAVE_DELAY_MS,
  WINDOW_NOT_FOUND_CODE,
} from '../lib/constants';
import { resolveProgressParagraph } from '../features/reader/readingPosition';
import type {
  BookSummary,
  LoadStatus,
  ProgressUpdateResponse,
  WindowResponse,
} from '../types';
import type { ReaderContext, RequestState, WindowCounts } from './types';
import {
  attachComments,
  emptyChapters,
  emptyJobs,
  emptyParagraphs,
  requestErrorState,
  sameContext,
  windowTargetCount,
  type JobSnapshot,
  type ParagraphSelection,
} from './controllerShared';

interface UseReaderProgressOptions {
  selectedBook: BookSummary | null;
  selectedChapter: number | null;
  setRequest: Dispatch<SetStateAction<RequestState>>;
  pushRequestError: (error: unknown, label: string) => void;
}

export function useReaderProgress({
  selectedBook,
  selectedChapter,
  setRequest,
  pushRequestError,
}: UseReaderProgressOptions) {
  const [progressSync, setProgressSync] = useState<LoadStatus>('idle');
  const [paragraphSelection, setParagraphSelection] =
    useState<ParagraphSelection | null>(null);
  const [jobSnapshot, setJobSnapshot] = useState<JobSnapshot | null>(null);
  const [restoreSettledContext, setRestoreSettledContext] =
    useState<ReaderContext | null>(null);
  const queryClient = useQueryClient();
  // Keep this synchronized with the activeContext also passed to useChat;
  // async callbacks rely on both refs sharing the same book/chapter source.
  const activeContextRef = useRef<ReaderContext | null>(null);
  const selectedBookId = selectedBook?.id ?? null;
  const { mutateAsync: updateProgress } = useUpdateProgressMutation();
  const { mutateAsync: retryWindow } = useRetryWindowMutation();

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
  const restorePending = Boolean(
    contextReady && activeContext && !sameContext(restoreSettledContext, activeContext),
  );
  const activeChapter = useMemo(
    () => chapters.find((chapter) => chapter.idx === activeChapterIdx) ?? null,
    [activeChapterIdx, chapters],
  );
  const { connection, events, lastEvent } = useBackendEvents(
    selectedBookId,
    activeChapterIdx,
  );

  const requestState = useMemo<RequestState | null>(() => {
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
    return null;
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
    commentsQuery.error,
    commentsQuery.isError,
    contextReady,
    currentWindowQuery.error,
    currentWindowQuery.isError,
    selectedBook,
  ]);

  const setActiveSelectedParagraph = useCallback(
    (paragraphIdx: number) => {
      if (!activeContext) return;
      setParagraphSelection({ context: activeContext, paragraphIdx });
    },
    [activeContext],
  );

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

  const resetReaderState = useCallback(() => {
    activeContextRef.current = null;
    setProgressSync('idle');
    setParagraphSelection(null);
    setJobSnapshot(null);
    setRestoreSettledContext(null);
  }, []);

  useEffect(() => {
    activeContextRef.current = activeContext;
  }, [activeContext]);

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
      setRequest,
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
    setRequest,
  ]);

  const saveCurrentProgress = useCallback(() => {
    void saveProgress(selectedParagraph, true);
  }, [saveProgress, selectedParagraph]);

  const settleRestore = useCallback(() => {
    if (activeContext) {
      setRestoreSettledContext(activeContext);
    }
  }, [activeContext]);

  return {
    selectedBookId,
    chapters,
    activeChapterIdx,
    activeContext,
    activeChapter,
    paragraphs,
    chapterStatus,
    progress,
    progressSync,
    selectedParagraph,
    setSelectedParagraph: setActiveSelectedParagraph,
    currentWindow,
    windowCounts,
    jobs,
    restorePending,
    contextReady,
    connection,
    events,
    requestState,
    resetReaderState,
    saveCurrentProgress,
    settleRestore,
    retryCurrentWindow,
  };
}
