import type {
  ApiErrorBody,
  BackendEvent,
  BookSummary,
  ChapterSummary,
  ChatSession,
  ChatTurn,
  ConfigDocument,
  ImportResult,
  JobSummary,
  ListResponse,
  ModelConfigSummary,
  ModelPingResult,
  ParagraphComment,
  ParagraphsResponse,
  ProgressUpdateResponse,
  ReadingProgress,
  RuntimeInfo,
  SettingsSummary,
  WindowResponse,
} from '../types';
import {
  backendEventSchema,
  chatDeltaEventSchema,
  chatDoneEventSchema,
  chatErrorEventSchema,
  chatFirstDeltaEventSchema,
  chatStartedEventSchema,
  type ChatDeltaEvent,
  type ChatDoneEvent,
  type ChatErrorEvent,
  type ChatFirstDeltaEvent,
  type ChatStartedEvent,
} from './apiSchemas';
import { API_BASE_PATH, CHAT_TURN_HISTORY_LIMIT } from './constants';

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
  return `${API_BASE_PATH}${path}`;
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
  health: (options?: RequestInit) =>
    request<{ status: string; time: string }>('/health', options),
  runtime: (options?: RequestInit) => request<RuntimeInfo>('/runtime', options),
  settings: (options?: RequestInit) => request<SettingsSummary>('/settings', options),
  config: (options?: RequestInit) => request<ConfigDocument>('/config', options),
  saveConfig: (
    config: ConfigDocument['config'],
    options?: RequestInit & { resetEnvOverridePaths?: string[] },
  ) =>
    request<ConfigDocument>('/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
      body: JSON.stringify({
        config,
        reset_env_override_paths: options?.resetEnvOverridePaths ?? [],
      }),
    }),
  resetConfig: (
    body:
      | { scope: 'field'; path: string }
      | { scope: 'group'; group: string }
      | { scope: 'preset' | 'common'; preset: string },
    options?: RequestInit,
  ) =>
    request<ConfigDocument>('/config/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
      body: JSON.stringify(body),
    }),
  createModel: (model: Partial<ModelConfigSummary>, options?: RequestInit) =>
    request<ConfigDocument>('/config/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
      body: JSON.stringify(model),
    }),
  updateModel: (
    modelId: string,
    model: Partial<ModelConfigSummary>,
    options?: RequestInit,
  ) =>
    request<ConfigDocument>(`/config/models/${encodeURIComponent(modelId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
      body: JSON.stringify(model),
    }),
  deleteModel: (modelId: string, options?: RequestInit) =>
    request<ConfigDocument>(`/config/models/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
      signal: options?.signal,
    }),
  pingModel: (
    body:
      | { model_id: string; model?: Partial<ModelConfigSummary> }
      | { model: Partial<ModelConfigSummary> },
    options?: RequestInit,
  ) =>
    request<ModelPingResult>('/config/models/ping', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
      body: JSON.stringify(body),
    }),
  switchActiveModel: (
    scope: 'global' | 'chat' | 'comment' | 'compaction',
    modelId: string,
    options?: RequestInit,
  ) =>
    request<ConfigDocument>('/config/active', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
      body: JSON.stringify({ scope, model_id: modelId }),
    }),
  books: (q?: string, options?: RequestInit) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    const qs = params.toString();
    return request<ListResponse<BookSummary>>(
      `/books${qs ? `?${qs}` : ''}`,
      options,
    );
  },
  book: (bookId: number, options?: RequestInit) =>
    request<BookSummary>(`/books/${bookId}`, options),
  importBook: (file: File, options?: RequestInit) => {
    const form = new FormData();
    form.append('file', file);
    return request<ImportResult>('/books/import', {
      method: 'POST',
      body: form,
      signal: options?.signal,
    });
  },
  deleteBook: (bookId: number, options?: RequestInit) =>
    request<{ deleted: boolean; book_id: number }>(`/books/${bookId}`, {
      method: 'DELETE',
      signal: options?.signal,
    }),
  chapters: (bookId: number, options?: RequestInit) =>
    request<ListResponse<ChapterSummary>>(`/books/${bookId}/chapters`, options),
  chapter: (bookId: number, chapterIdx: number, options?: RequestInit) =>
    request<ChapterSummary>(
      `/books/${bookId}/chapters/${chapterIdx}`,
      options,
    ),
  paragraphs: (
    bookId: number,
    chapterIdx: number,
    includeComments = true,
    options?: RequestInit,
  ) => {
    const params = new URLSearchParams();
    if (includeComments) params.set('include_comments', 'true');
    const qs = params.toString();
    return request<ParagraphsResponse>(
      `/books/${bookId}/chapters/${chapterIdx}/paragraphs${qs ? `?${qs}` : ''}`,
      options,
    );
  },
  progress: (bookId: number, options?: RequestInit) =>
    request<ReadingProgress>(`/books/${bookId}/progress`, options),
  updateProgress: (
    bookId: number,
    chapterIdx: number,
    paragraphIdx: number,
    scrollPct: number,
    options?: RequestInit,
  ) =>
    request<ProgressUpdateResponse>(`/books/${bookId}/progress`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      signal: options?.signal,
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
    options?: RequestInit,
  ) => {
    const params = new URLSearchParams();
    if (paragraphIdx !== undefined) params.set('paragraph_idx', String(paragraphIdx));
    const qs = params.toString();
    return request<WindowResponse>(
      `/books/${bookId}/chapters/${chapterIdx}/windows/current${qs ? `?${qs}` : ''}`,
      options,
    );
  },
  comments: (
    bookId: number,
    chapterIdx: number,
    start?: number,
    end?: number,
    options?: RequestInit,
  ) => {
    const params = new URLSearchParams();
    if (start !== undefined) params.set('start', String(start));
    if (end !== undefined) params.set('end', String(end));
    const qs = params.toString();
    return request<ListResponse<ParagraphComment>>(
      `/books/${bookId}/chapters/${chapterIdx}/comments${qs ? `?${qs}` : ''}`,
      options,
    );
  },
  retryWindow: (windowId: number, options?: RequestInit) =>
    request<{ window: WindowResponse['window']; job: JobSummary }>(
      `/windows/${windowId}/retry`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: options?.signal,
        body: JSON.stringify({ reason: 'manual_retry' }),
      },
    ),
  chatSession: (bookId: number, chapterIdx: number, options?: RequestInit) =>
    request<{ session: ChatSession }>(
      `/books/${bookId}/chat/session?chapter_idx=${chapterIdx}`,
      options,
    ),
  chatTurns: (
    sessionId: number,
    limit = CHAT_TURN_HISTORY_LIMIT,
    offset = 0,
    options?: RequestInit,
  ) =>
    request<ListResponse<ChatTurn>>(
      `/chat/sessions/${sessionId}/turns?limit=${limit}&offset=${offset}`,
      options,
    ),
};

export interface ChatStreamCallbacks {
  onStarted: (data: ChatStartedEvent) => void;
  onFirstDelta?: (data: ChatFirstDeltaEvent) => void;
  onDelta: (data: ChatDeltaEvent) => void;
  onDone: (data: ChatDoneEvent) => void;
  onError: (data: ChatErrorEvent) => void;
}

function reportInvalidStreamEvent(
  eventName: string,
  callbacks: ChatStreamCallbacks,
) {
  callbacks.onError({
    code: 'stream_event_invalid',
    message: `聊天流事件格式无效: ${eventName}`,
  });
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

  let data: unknown;
  try {
    data = JSON.parse(dataLines.join('\n'));
  } catch {
    callbacks.onError({
      code: 'stream_parse_failed',
      message: '无法解析聊天流事件',
    });
    return;
  }

  if (eventName === 'chat.started') {
    const parsed = chatStartedEventSchema.safeParse(data);
    if (!parsed.success) {
      reportInvalidStreamEvent(eventName, callbacks);
      return;
    }
    callbacks.onStarted(parsed.data);
  } else if (eventName === 'chat.first_delta') {
    const parsed = chatFirstDeltaEventSchema.safeParse(data);
    if (!parsed.success) {
      reportInvalidStreamEvent(eventName, callbacks);
      return;
    }
    callbacks.onFirstDelta?.(parsed.data);
  } else if (eventName === 'chat.delta') {
    const parsed = chatDeltaEventSchema.safeParse(data);
    if (!parsed.success) {
      reportInvalidStreamEvent(eventName, callbacks);
      return;
    }
    callbacks.onDelta(parsed.data);
  } else if (eventName === 'chat.done') {
    const parsed = chatDoneEventSchema.safeParse(data);
    if (!parsed.success) {
      reportInvalidStreamEvent(eventName, callbacks);
      return;
    }
    callbacks.onDone(parsed.data);
  } else if (eventName === 'chat.error') {
    const parsed = chatErrorEventSchema.safeParse(data);
    if (!parsed.success) {
      reportInvalidStreamEvent(eventName, callbacks);
      return;
    }
    callbacks.onError(parsed.data);
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
      const data: unknown = JSON.parse(String(message.data || '{}'));
      const parsed = backendEventSchema.safeParse(data);
      if (!parsed.success) {
        throw new Error('Invalid backend event payload');
      }
      onEvent({ ...parsed.data, event: parsed.data.event || message.type });
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
