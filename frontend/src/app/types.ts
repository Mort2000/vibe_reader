import type { LoadStatus } from '../types';

export interface RequestState {
  status: LoadStatus;
  label: string;
  detail?: string;
  requestId?: string | null;
}

export interface ReaderContext {
  bookId: number;
  chapterIdx: number;
}

export interface WindowCounts {
  ready: number;
  target: number;
}

export type BackendConnection = 'idle' | 'connecting' | 'open' | 'error' | 'closed';
