import type { PaneMode } from '../types';
import type { ReaderContext } from './types';

const topLevelRouteModes: Record<string, PaneMode> = {
  library: 'library',
  reader: 'reader',
  chapters: 'chapters',
  assistant: 'assistant',
  status: 'status',
  config: 'config',
};

const chapterRouteModes: Record<string, PaneMode> = {
  library: 'library',
  chapters: 'chapters',
  assistant: 'assistant',
  status: 'status',
  config: 'config',
};

export interface ParsedAppRoute {
  mode: PaneMode;
  routeBookId: number | null;
  routeChapterIdx: number | null;
}

export function parseRouteNumber(value: string | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function parseAppRoutePath(pathname: string): ParsedAppRoute | null {
  const segments = pathname.split('/').filter(Boolean);
  if (segments.length === 0) return null;

  if (segments.length === 1) {
    const mode = topLevelRouteModes[segments[0]];
    return mode ? { mode, routeBookId: null, routeChapterIdx: null } : null;
  }

  if (segments[0] !== 'books') return null;
  const routeBookId = parseRouteNumber(segments[1]);
  if (routeBookId === null) return null;

  if (segments.length === 2) {
    return { mode: 'reader', routeBookId, routeChapterIdx: null };
  }

  if (segments[2] !== 'chapters') return null;
  if (segments.length === 3) {
    return { mode: 'chapters', routeBookId, routeChapterIdx: null };
  }

  const routeChapterIdx = parseRouteNumber(segments[3]);
  if (routeChapterIdx === null) return null;

  if (segments.length === 4) {
    return { mode: 'reader', routeBookId, routeChapterIdx };
  }

  if (segments.length === 5) {
    const mode = chapterRouteModes[segments[4]];
    return mode ? { mode, routeBookId, routeChapterIdx } : null;
  }

  return null;
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
  if (mode === 'library') {
    return context ? `${readerRoutePath(context)}/library` : '/library';
  }
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
  if (mode === 'status') {
    return context ? `${readerRoutePath(context)}/status` : '/status';
  }
  return context ? `${readerRoutePath(context)}/config` : '/config';
}
