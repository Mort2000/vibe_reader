import type {
  BookSummary,
  ChapterSummary,
  ImportResult,
  JobInfo,
  ListResponse,
  ParagraphComment,
  ParagraphsResponse,
  ProgressUpdateResponse,
  ReadingProgress,
  WindowInfo,
} from '../types';

const BASE = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw body?.error || { code: 'unknown', message: res.statusText };
  }
  return res.json();
}

export async function getHealth(): Promise<{ status: string }> {
  return request('/health');
}

export async function getBooks(q?: string): Promise<ListResponse<BookSummary>> {
  const params = q ? `?q=${encodeURIComponent(q)}` : '';
  return request(`/books${params}`);
}

export async function getBook(bookId: number): Promise<BookSummary> {
  return request(`/books/${bookId}`);
}

export async function importEpub(file: File): Promise<ImportResult> {
  const form = new FormData();
  form.append('file', file);
  return request('/books/import', { method: 'POST', body: form });
}

export async function deleteBook(bookId: number): Promise<void> {
  await request(`/books/${bookId}`, { method: 'DELETE' });
}

export async function getChapters(bookId: number): Promise<ListResponse<ChapterSummary>> {
  return request(`/books/${bookId}/chapters`);
}

export async function getChapter(bookId: number, chapterIdx: number): Promise<ChapterSummary> {
  return request(`/books/${bookId}/chapters/${chapterIdx}`);
}

export async function getParagraphs(
  bookId: number,
  chapterIdx: number,
  includeComments = true,
): Promise<ParagraphsResponse> {
  const params = includeComments ? '?include_comments=true' : '';
  return request(`/books/${bookId}/chapters/${chapterIdx}/paragraphs${params}`);
}

export async function getProgress(bookId: number): Promise<ReadingProgress> {
  return request(`/books/${bookId}/progress`);
}

export async function updateProgress(
  bookId: number,
  chapterIdx: number,
  paragraphIdx: number,
  scrollPct: number,
): Promise<ProgressUpdateResponse> {
  return request(`/books/${bookId}/progress`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chapter_idx: chapterIdx,
      paragraph_idx: paragraphIdx,
      scroll_pct: scrollPct,
    }),
  });
}

export async function getCurrentWindow(
  bookId: number,
  chapterIdx: number,
): Promise<{ window: WindowInfo; comments_ready_count: number; comments_target_count: number }> {
  return request(`/books/${bookId}/chapters/${chapterIdx}/windows/current`);
}

export async function getChapterComments(
  bookId: number,
  chapterIdx: number,
  start?: number,
  end?: number,
): Promise<ListResponse<ParagraphComment>> {
  const params = new URLSearchParams();
  if (start !== undefined) params.set('start', String(start));
  if (end !== undefined) params.set('end', String(end));
  const qs = params.toString() ? `?${params.toString()}` : '';
  return request(`/books/${bookId}/chapters/${chapterIdx}/comments${qs}`);
}

export async function retryWindow(
  windowId: number,
): Promise<{ window: WindowInfo; job: JobInfo }> {
  return request(`/windows/${windowId}/retry`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: 'manual_retry' }),
  });
}

export function createEventSource(
  bookId: number,
  chapterIdx: number,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): EventSource {
  const params = new URLSearchParams({
    book_id: String(bookId),
    chapter_idx: String(chapterIdx),
  });
  const es = new EventSource(`${BASE}/events?${params.toString()}`);
  const handler = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data as string);
      onEvent(e.type || data.event || 'message', data);
    } catch {
      // ignore malformed events
    }
  };
  es.addEventListener('window.queued', handler);
  es.addEventListener('window.running', handler);
  es.addEventListener('window.done', handler);
  es.addEventListener('window.failed', handler);
  es.addEventListener('comment.created', handler);
  es.addEventListener('job.failed', handler);
  return es;
}
