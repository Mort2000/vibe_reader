import type { PaneMode } from '../types';
import type { ReaderContext } from './types';

export function parseRouteNumber(value: string | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function bookRoutePath(bookId: number): string {
  return `/books/${bookId}`;
}

export function readerRoutePath(context: ReaderContext): string {
  return `/books/${context.bookId}/chapters/${context.chapterIdx}`;
}

export function modeRoutePath(
  mode: PaneMode,
  context: ReaderContext | null,
  selectedBookId: number | null,
): string {
  if (mode === 'library') return '/library';
  if (mode === 'reader') {
    if (context) return readerRoutePath(context);
    return selectedBookId !== null ? bookRoutePath(selectedBookId) : '/reader';
  }
  if (mode === 'chapters') {
    if (context) return `${readerRoutePath(context)}/chapters`;
    return selectedBookId !== null ? `${bookRoutePath(selectedBookId)}/chapters` : '/chapters';
  }
  if (mode === 'assistant') {
    return context ? `${readerRoutePath(context)}/assistant` : '/assistant';
  }
  return context ? `${readerRoutePath(context)}/status` : '/status';
}
