import { describeError } from '../lib/api';
import type {
  BookSummary,
  ChapterSummary,
  JobSummary,
  ListResponse,
  Paragraph,
  ParagraphComment,
  WindowResponse,
} from '../types';
import type { ReaderContext, RequestState } from './types';

export const initialRequest: RequestState = {
  status: 'idle',
  label: '等待连接',
};

export const emptyChapters: ChapterSummary[] = [];
export const emptyParagraphs: Paragraph[] = [];
export const emptyJobs: JobSummary[] = [];

export interface ParagraphSelection {
  context: ReaderContext;
  paragraphIdx: number;
}

export interface JobSnapshot {
  context: ReaderContext;
  jobs: JobSummary[];
}

export function sameContext(
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

export function sameOptionalContext(
  left: ReaderContext | null | undefined,
  right: ReaderContext | null | undefined,
): boolean {
  if (!left || !right) return left === right;
  return sameContext(left, right);
}

export function requestErrorState(error: unknown, label: string): RequestState {
  const info = describeError(error);
  return {
    status: 'error',
    label,
    detail: `${info.title}: ${info.detail}`,
    requestId: info.requestId,
  };
}

export function attachComments(
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

export function windowTargetCount(window: WindowResponse['window']): number {
  return window.focus_end_paragraph_idx - window.focus_start_paragraph_idx + 1;
}

export function upsertBook(
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
