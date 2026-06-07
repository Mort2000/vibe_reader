// @vitest-environment jsdom
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChatStreamCallbacks, streamChat } from '../lib/api';
import type { BookSummary, ChatSession, ChatTurn } from '../types';
import { useChat } from './useChat';

type StreamChatInput = Parameters<typeof streamChat>[0];

const apiMocks = vi.hoisted(() => ({
  streamChat: vi.fn(),
}));
const apiQueryMocks = vi.hoisted(() => ({
  useChatSessionQuery: vi.fn(),
  useChatTurnsQuery: vi.fn(),
}));

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return {
    ...actual,
    streamChat: apiMocks.streamChat,
  };
});

vi.mock('../lib/apiQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/apiQueries')>();
  return {
    ...actual,
    useChatSessionQuery: apiQueryMocks.useChatSessionQuery,
    useChatTurnsQuery: apiQueryMocks.useChatTurnsQuery,
  };
});

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
}

function bookSummary(): BookSummary {
  return {
    id: 7,
    title: 'Chat Book',
    author: null,
    cover_url: null,
    total_chapters: 3,
    imported_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    last_progress: null,
  };
}

function chatSession(): ChatSession {
  return {
    id: 30,
    book_id: 7,
    chapter_idx: 2,
    title: null,
    last_paragraph_idx: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function chatTurn(id: number): ChatTurn {
  return {
    id,
    session_id: 30,
    book_id: 7,
    chapter_idx: 2,
    paragraph_idx: 1,
    user_msg: `question ${id}`,
    ai_msg: `answer ${id}`,
    status: 'done',
    tokens_in: 4,
    tokens_out: 5,
    trace_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

describe('useChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiQueryMocks.useChatSessionQuery.mockReturnValue({
      data: { session: chatSession() },
      error: null,
      isError: false,
    });
    apiQueryMocks.useChatTurnsQuery.mockReturnValue({
      data: { items: [chatTurn(1)], total: 1 },
      error: null,
      isError: false,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('streams a chat turn for the active reader context', () => {
    const queryClient = createQueryClient();
    const setRequest = vi.fn();
    const abortController = new AbortController();
    let capturedCallbacks: ChatStreamCallbacks | null = null;
    let capturedInput: StreamChatInput | null = null;

    apiMocks.streamChat.mockImplementation(
      (input: StreamChatInput, callbacks: ChatStreamCallbacks) => {
        capturedInput = input;
        capturedCallbacks = callbacks;
        return abortController;
      },
    );

    const { result } = renderHook(
      () =>
        useChat({
          selectedBook: bookSummary(),
          activeContext: { bookId: 7, chapterIdx: 2 },
          contextReady: true,
          selectedParagraph: 4,
          setRequest,
        }),
      { wrapper: createWrapper(queryClient) },
    );

    expect(result.current.chatTurns).toEqual([chatTurn(1)]);

    act(() => {
      result.current.setChatInput(' explain this ');
    });
    act(() => {
      result.current.sendChat();
    });

    expect(capturedInput).toEqual({
      bookId: 7,
      chapterIdx: 2,
      paragraphIdx: 4,
      sessionId: 30,
      userMsg: 'explain this',
    });
    expect(result.current.chatStatus).toBe('loading');
    expect(setRequest).toHaveBeenLastCalledWith({
      status: 'loading',
      label: 'AI 正在回答 · P5',
    });

    act(() => {
      capturedCallbacks?.onStarted({
        session_id: 30,
        turn_id: 42,
        trace_id: 'trace-42',
      });
      capturedCallbacks?.onDelta({
        turn_id: 42,
        delta: 'partial ',
      });
    });

    expect(result.current.streamingTurn).toMatchObject({
      id: 42,
      ai_msg: 'partial ',
      trace_id: 'trace-42',
    });

    act(() => {
      capturedCallbacks?.onDone({
        session_id: 30,
        turn_id: 42,
        ai_msg: 'final answer',
        tokens_in: 12,
        tokens_out: 34,
        trace_id: 'trace-42',
      });
    });

    expect(result.current.streamingTurn).toBeNull();
    expect(result.current.chatStatus).toBe('success');
    expect(result.current.chatTurns).toEqual([
      chatTurn(1),
      expect.objectContaining({
        id: 42,
        session_id: 30,
        book_id: 7,
        chapter_idx: 2,
        paragraph_idx: 4,
        user_msg: 'explain this',
        ai_msg: 'final answer',
        status: 'done',
        tokens_in: 12,
        tokens_out: 34,
        trace_id: 'trace-42',
      }),
    ]);
    expect(setRequest).toHaveBeenLastCalledWith({
      status: 'success',
      label: 'AI 回答完成',
      detail: 'tokens 12 in / 34 out',
    });
  });
});
