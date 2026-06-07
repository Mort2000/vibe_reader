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

import { streamChat } from '../lib/api';
import {
  queryKeys,
  useChatSessionQuery,
  useChatTurnsQuery,
} from '../lib/apiQueries';
import { formatNumber } from '../lib/formatters';
import type { BookSummary, ChatSession, ChatTurn, LoadStatus } from '../types';
import type { ReaderContext, RequestState } from './types';
import { requestErrorState, sameContext } from './controllerShared';

interface UseChatOptions {
  selectedBook: BookSummary | null;
  activeContext: ReaderContext | null;
  contextReady: boolean;
  selectedParagraph: number;
  setRequest: Dispatch<SetStateAction<RequestState>>;
}

export function useChat({
  selectedBook,
  activeContext,
  contextReady,
  selectedParagraph,
  setRequest,
}: UseChatOptions) {
  const [localChatSession, setLocalChatSession] = useState<ChatSession | null>(null);
  const [localChatTurns, setLocalChatTurns] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatStatus, setChatStatus] = useState<LoadStatus>('idle');
  const [streamingTurn, setStreamingTurn] = useState<ChatTurn | null>(null);
  const queryClient = useQueryClient();
  const chatAbortRef = useRef<AbortController | null>(null);
  // Mirrors the activeContext produced by useReaderProgress; async stream
  // callbacks rely on reader and chat refs sharing the same source.
  const activeContextRef = useRef<ReaderContext | null>(null);
  const selectedBookId = activeContext?.bookId ?? null;
  const activeChapterIdx = activeContext?.chapterIdx ?? null;

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
  const requestState = useMemo<RequestState | null>(() => {
    if (contextReady && (chatSessionQuery.isError || chatTurnsQuery.isError)) {
      return requestErrorState(
        chatSessionQuery.error || chatTurnsQuery.error,
        '聊天历史加载失败',
      );
    }
    return null;
  }, [
    chatSessionQuery.error,
    chatSessionQuery.isError,
    chatTurnsQuery.error,
    chatTurnsQuery.isError,
    contextReady,
  ]);

  useEffect(() => {
    activeContextRef.current = activeContext;
  }, [activeContext]);

  const resetChatState = useCallback(() => {
    activeContextRef.current = null;
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    setLocalChatSession(null);
    setLocalChatTurns([]);
    setChatInput('');
    setChatStatus('idle');
    setStreamingTurn(null);
  }, []);

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
    setRequest,
  ]);

  const abortChat = useCallback(() => {
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    setChatStatus('idle');
    setStreamingTurn(null);
    setRequest({ status: 'idle', label: '聊天已停止' });
  }, [setRequest]);

  return {
    chatTurns,
    chatInput,
    setChatInput,
    chatStatus: effectiveChatStatus,
    streamingTurn,
    requestState,
    resetChatState,
    sendChat,
    abortChat,
  };
}
