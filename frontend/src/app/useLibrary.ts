import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import {
  booksQueryOptions,
  queryKeys,
  runtimeQueryOptions,
  settingsQueryOptions,
  useImportBookMutation,
} from '../lib/apiQueries';
import { BOOTSTRAP_DELAY_MS } from '../lib/constants';
import { formatNumber } from '../lib/formatters';
import type {
  BookSummary,
  ImportResult,
  ListResponse,
  LoadStatus,
  PaneMode,
} from '../types';
import type { RequestState } from './types';
import { upsertBook } from './controllerShared';

interface UseLibraryOptions {
  selectedBook: BookSummary | null;
  setSelectedBook: Dispatch<SetStateAction<BookSummary | null>>;
  setSelectedChapter: Dispatch<SetStateAction<number | null>>;
  setMode: Dispatch<SetStateAction<PaneMode>>;
  setRequest: Dispatch<SetStateAction<RequestState>>;
  pushRequestError: (error: unknown, label: string) => void;
  resetReadingState: () => void;
}

export function useLibrary({
  selectedBook,
  setSelectedBook,
  setSelectedChapter,
  setMode,
  setRequest,
  pushRequestError,
  resetReadingState,
}: UseLibraryOptions) {
  const [query, setQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importProgress, setImportProgress] = useState<LoadStatus>('idle');
  const queryClient = useQueryClient();
  const { mutateAsync: importBook } = useImportBookMutation();

  const runtimeQuery = useQuery({ ...runtimeQueryOptions(), enabled: false });
  const settingsQuery = useQuery({ ...settingsQueryOptions(), enabled: false });
  const booksQuery = useQuery({
    ...booksQueryOptions(submittedQuery),
    enabled: false,
  });

  const loadBootstrap = useCallback(
    async (bookQuery = '', autoSelect = false) => {
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
    },
    [pushRequestError, queryClient, setRequest, setSelectedBook],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadBootstrap('', true);
    }, BOOTSTRAP_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [loadBootstrap]);

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
  }, [pushRequestError, query, queryClient, setRequest]);

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
      setMode,
      setRequest,
      setSelectedBook,
      setSelectedChapter,
      submittedQuery,
    ],
  );

  const selectBook = useCallback(
    (book: BookSummary) => {
      if (selectedBook?.id === book.id) {
        setMode('reader');
        return;
      }

      resetReadingState();
      setSelectedBook(book);
      setSelectedChapter(null);
      setMode('reader');
    },
    [
      resetReadingState,
      selectedBook?.id,
      setMode,
      setSelectedBook,
      setSelectedChapter,
    ],
  );

  return {
    runtime: runtimeQuery.data ?? null,
    settings: settingsQuery.data ?? null,
    books: booksQuery.data?.items ?? [],
    query,
    setQuery,
    importResult,
    importProgress,
    loadBootstrap,
    refreshBooks,
    handleImport,
    selectBook,
  };
}
