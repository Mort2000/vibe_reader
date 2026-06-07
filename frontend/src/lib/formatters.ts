import type { BookSummary, ChapterSummary, LoadStatus } from '../types';

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '0';
  return new Intl.NumberFormat('zh-CN').format(value);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '未记录';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

export function compactModelName(value: string | null | undefined): string {
  if (!value) return '未知';
  return value.split('/').pop() || value;
}

export function compactDataDir(value: string | null | undefined): string {
  if (!value) return '未连接';
  const marker = '/.vibe_reader';
  const idx = value.indexOf(marker);
  return idx >= 0 ? `~${value.slice(idx)}` : value;
}

export function statusCopy(status: LoadStatus): string {
  if (status === 'loading') return '进行中';
  if (status === 'success') return '已完成';
  if (status === 'error') return '需处理';
  return '空闲';
}

export function chapterDisplayTitle(chapter: ChapterSummary | null): string {
  if (!chapter) return '未选择章节';
  return chapter.title || `第 ${chapter.idx + 1} 章`;
}

export function bookAuthorLabel(book: BookSummary): string | null {
  const author = book.author?.trim();
  if (!author) return '未知作者';
  return author === book.title.trim() ? null : author;
}
