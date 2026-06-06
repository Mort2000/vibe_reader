import { useEffect, useState } from 'react';

import { createBackendEventSource } from '../lib/api';
import type { ActivityItem, BackendEvent } from '../types';

function eventTone(event: string): ActivityItem['tone'] {
  if (event.endsWith('.failed') || event.endsWith('.error')) return 'bad';
  if (
    event.endsWith('.done') ||
    event === 'comment.created' ||
    event === 'context.compacted'
  ) {
    return 'good';
  }
  if (
    event.endsWith('.running') ||
    event.endsWith('.queued') ||
    event === 'context.compacting' ||
    event === 'chat.started'
  ) {
    return 'info';
  }
  return 'neutral';
}

function eventTitle(event: BackendEvent): string {
  const name = event.event;
  if (name === 'window.queued') return '评论窗口已排队';
  if (name === 'window.running') return 'AI 正在分析窗口';
  if (name === 'window.done') return '评论窗口完成';
  if (name === 'window.failed') return '评论窗口失败';
  if (name === 'comment.created') return '新段落评论';
  if (name === 'context.queued') return '上下文压缩已排队';
  if (name === 'context.compacting') return '上下文压缩中';
  if (name === 'context.compacted') return '上下文已压缩';
  if (name === 'context.failed') return '上下文压缩失败';
  if (name === 'job.failed') return '后台任务失败';
  if (name === 'chat.started') return '对话开始';
  if (name === 'chat.done') return '对话完成';
  if (name === 'chat.error') return '对话失败';
  return name || '后台事件';
}

export function useBackendEvents(bookId: number | null, chapterIdx: number | null) {
  const [connection, setConnection] = useState<
    'idle' | 'connecting' | 'open' | 'error' | 'closed'
  >('idle');
  const [events, setEvents] = useState<ActivityItem[]>([]);
  const [lastEvent, setLastEvent] = useState<BackendEvent | null>(null);

  useEffect(() => {
    if (!bookId || chapterIdx === null) {
      window.queueMicrotask(() => setConnection('idle'));
      return;
    }

    window.queueMicrotask(() => setConnection('connecting'));
    const source = createBackendEventSource(
      bookId,
      chapterIdx,
      (event) => {
        setLastEvent(event);
        setEvents((current) => {
          const item: ActivityItem = {
            id: event.event_id || `${event.event}-${Date.now()}`,
            event: event.event,
            title: eventTitle(event),
            detail: [
              event.paragraph_idx !== undefined ? `P${event.paragraph_idx + 1}` : null,
              event.window_id !== undefined ? `窗口 ${event.window_id}` : null,
              event.job_id !== undefined ? `任务 ${event.job_id}` : null,
            ]
              .filter(Boolean)
              .join(' · '),
            tone: eventTone(event.event),
            createdAt: event.created_at || new Date().toISOString(),
            traceId: event.trace_id || null,
            requestId: event.request_id || null,
          };
          return [item, ...current].slice(0, 40);
        });
      },
      setConnection,
    );

    return () => {
      source.close();
      window.queueMicrotask(() => setConnection('closed'));
    };
  }, [bookId, chapterIdx]);

  return { connection, events, lastEvent };
}
