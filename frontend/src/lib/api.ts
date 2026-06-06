import type {
  ApiErrorBody,
  BackendEvent,
  BookSummary,
  ChapterSummary,
  ChatSession,
  ChatTurn,
  ImportResult,
  JobSummary,
  ListResponse,
  ParagraphComment,
  ParagraphsResponse,
  ProgressUpdateResponse,
  ReadingProgress,
  RuntimeInfo,
  SettingsSummary,
  WindowResponse,
} from '../types';

const BASE = '/api';

export class ApiError extends Error {
  code: string;
  details: Record<string, unknown>;
  requestId: string | null;
  status: number;

  constructor(error: ApiErrorBody, status: number, fallbackRequestId: string | null = null) {
    super(error.message || error.code);
    this.name = 'ApiError';
    this.code = error.code || 'unknown';
    this.details = error.details || {};
    this.requestId = error.request_id || fallbackRequestId;
    this.status = status;
  }
}

function apiPath(path: string): string {
  return `${BASE}${path}`;
}

async function parseError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const fallbackRequestId =
    res.headers.get('x-request-id') ||
    res.headers.get('x-correlation-id') ||
    res.headers.get('traceparent');
  const error = body?.error || {
    code: body?.detail ? 'validation_error' : 'request_failed',
    message:
      typeof body?.detail === 'string'
        ? body.detail
        : res.statusText || 'Request failed',
    details: Array.isArray(body?.detail) ? { validation: body.detail } : {},
  };
  return new ApiError(error, res.status, fallbackRequestId);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(apiPath(path), options);
  if (!res.ok) {
    throw await parseError(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function describeError(error: unknown): {
  title: string;
  detail: string;
  requestId?: string | null;
} {
  if (error instanceof ApiError) {
    return {
      title: error.code,
      detail: error.message,
      requestId: error.requestId,
    };
  }
  if (error instanceof Error) {
    return { title: 'network_error', detail: error.message };
  }
  return { title: 'unknown_error', detail: 'Unknown failure' };
}

export const api = {
  health: () => request<{ status: string; time: string }>('/health'),
  runtime: () => request<RuntimeInfo>('/runtime'),
  settings: () => request<SettingsSummary>('/settings'),
  books: (q?: string) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    const qs = params.toString();
    return request<ListResponse<BookSummary>>(`/books${qs ? `?${qs}` : ''}`);
  },
  book: (bookId: number) => request<BookSummary>(`/books/${bookId}`),
  importBook: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<ImportResult>('/books/import', {
      method: 'POST',
      body: form,
    });
  },
  deleteBook: (bookId: number) =>
    request<{ deleted: boolean; book_id: number }>(`/books/${bookId}`, {
      method: 'DELETE',
    }),
  chapters: (bookId: number) =>
    request<ListResponse<ChapterSummary>>(`/books/${bookId}/chapters`),
  chapter: (bookId: number, chapterIdx: number) =>
    request<ChapterSummary>(`/books/${bookId}/chapters/${chapterIdx}`),
  paragraphs: (bookId: number, chapterIdx: number, includeComments = true) => {
    const params = new URLSearchParams();
    if (includeComments) params.set('include_comments', 'true');
    const qs = params.toString();
    return request<ParagraphsResponse>(
      `/books/${bookId}/chapters/${chapterIdx}/paragraphs${qs ? `?${qs}` : ''}`,
    );
  },
  progress: (bookId: number) =>
    request<ReadingProgress>(`/books/${bookId}/progress`),
  updateProgress: (
    bookId: number,
    chapterIdx: number,
    paragraphIdx: number,
    scrollPct: number,
  ) =>
    request<ProgressUpdateResponse>(`/books/${bookId}/progress`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chapter_idx: chapterIdx,
        paragraph_idx: paragraphIdx,
        scroll_pct: scrollPct,
      }),
    }),
  currentWindow: (
    bookId: number,
    chapterIdx: number,
    paragraphIdx?: number,
  ) => {
    const params = new URLSearchParams();
    if (paragraphIdx !== undefined) params.set('paragraph_idx', String(paragraphIdx));
    const qs = params.toString();
    return request<WindowResponse>(
      `/books/${bookId}/chapters/${chapterIdx}/windows/current${qs ? `?${qs}` : ''}`,
    );
  },
  comments: (bookId: number, chapterIdx: number, start?: number, end?: number) => {
    const params = new URLSearchParams();
    if (start !== undefined) params.set('start', String(start));
    if (end !== undefined) params.set('end', String(end));
    const qs = params.toString();
    return request<ListResponse<ParagraphComment>>(
      `/books/${bookId}/chapters/${chapterIdx}/comments${qs ? `?${qs}` : ''}`,
    );
  },
  retryWindow: (windowId: number) =>
    request<{ window: WindowResponse['window']; job: JobSummary }>(
      `/windows/${windowId}/retry`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'manual_retry' }),
      },
    ),
  chatSession: (bookId: number, chapterIdx: number) =>
    request<{ session: ChatSession }>(
      `/books/${bookId}/chat/session?chapter_idx=${chapterIdx}`,
    ),
  chatTurns: (sessionId: number, limit = 80, offset = 0) =>
    request<ListResponse<ChatTurn>>(
      `/chat/sessions/${sessionId}/turns?limit=${limit}&offset=${offset}`,
    ),
};

export interface ChatStreamCallbacks {
  onStarted: (data: {
    turn_id: number;
    session_id: number;
    trace_id: string;
  }) => void;
  onFirstDelta?: (data: Record<string, unknown>) => void;
  onDelta: (data: { turn_id: number; delta: string }) => void;
  onDone: (data: {
    turn_id: number;
    session_id: number;
    ai_msg: string;
    tokens_in: number | null;
    tokens_out: number | null;
    trace_id: string;
  }) => void;
  onError: (data: {
    turn_id?: number;
    session_id?: number;
    code: string;
    message: string;
    trace_id?: string;
    request_id?: string | null;
  }) => void;
}

function consumeSseBlock(block: string, callbacks: ChatStreamCallbacks) {
  const lines = block.split('\n');
  let eventName = 'message';
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (!dataLines.length) return;

  let data: Record<string, unknown>;
  try {
    data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;
  } catch {
    callbacks.onError({
      code: 'stream_parse_failed',
      message: '无法解析聊天流事件',
    });
    return;
  }

  if (eventName === 'chat.started') {
    callbacks.onStarted(data as Parameters<ChatStreamCallbacks['onStarted']>[0]);
  } else if (eventName === 'chat.first_delta') {
    callbacks.onFirstDelta?.(data);
  } else if (eventName === 'chat.delta') {
    callbacks.onDelta(data as Parameters<ChatStreamCallbacks['onDelta']>[0]);
  } else if (eventName === 'chat.done') {
    callbacks.onDone(data as Parameters<ChatStreamCallbacks['onDone']>[0]);
  } else if (eventName === 'chat.error') {
    callbacks.onError(data as Parameters<ChatStreamCallbacks['onError']>[0]);
  }
}

export function streamChat(
  input: {
    bookId: number;
    chapterIdx: number;
    paragraphIdx: number;
    sessionId: number | null;
    userMsg: string;
  },
  callbacks: ChatStreamCallbacks,
): AbortController {
  const controller = new AbortController();

  void fetch(apiPath('/chat/stream'), {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    signal: controller.signal,
    body: JSON.stringify({
      book_id: input.bookId,
      chapter_idx: input.chapterIdx,
      paragraph_idx: input.paragraphIdx,
      session_id: input.sessionId,
      user_msg: input.userMsg,
    }),
  })
    .then(async (res) => {
      if (!res.ok) {
        const error = await parseError(res);
        callbacks.onError({
          code: error.code,
          message: error.message,
          request_id: error.requestId,
        });
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        callbacks.onError({
          code: 'stream_unavailable',
          message: '浏览器无法读取聊天流',
        });
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || '';
        for (const block of blocks) {
          consumeSseBlock(block, callbacks);
        }
      }

      if (buffer.trim()) {
        consumeSseBlock(buffer, callbacks);
      }
    })
    .catch((error: unknown) => {
      if (error instanceof Error && error.name === 'AbortError') return;
      callbacks.onError({
        code: 'network_error',
        message: error instanceof Error ? error.message : '聊天流连接失败',
      });
    });

  return controller;
}

export function createBackendEventSource(
  bookId: number | null,
  chapterIdx: number | null,
  onEvent: (event: BackendEvent) => void,
  onConnectionChange: (state: 'connecting' | 'open' | 'error' | 'closed') => void,
): EventSource {
  const params = new URLSearchParams();
  if (bookId !== null) params.set('book_id', String(bookId));
  if (chapterIdx !== null) params.set('chapter_idx', String(chapterIdx));
  const url = apiPath(`/events${params.toString() ? `?${params.toString()}` : ''}`);
  const source = new EventSource(url);

  source.onopen = () => onConnectionChange('open');
  source.onerror = () => onConnectionChange('error');

  const handler = (message: MessageEvent) => {
    try {
      const data = JSON.parse(String(message.data || '{}')) as BackendEvent;
      onEvent({ ...data, event: data.event || message.type });
    } catch {
      onEvent({
        event: 'event.parse_failed',
        created_at: new Date().toISOString(),
      });
    }
  };

  [
    'window.queued',
    'window.running',
    'window.done',
    'window.failed',
    'comment.created',
    'context.queued',
    'context.compacting',
    'context.compacted',
    'context.failed',
    'job.failed',
    'chat.started',
    'chat.done',
    'chat.error',
  ].forEach((event) => source.addEventListener(event, handler));

  return source;
}
