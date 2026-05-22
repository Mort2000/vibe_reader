import type {
  BookSummary,
  ChapterSummary,
  ImportResult,
  ListResponse,
  ParagraphsResponse,
  ProgressUpdateResponse,
  ReadingProgress,
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
