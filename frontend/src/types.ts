export type LoadStatus = 'idle' | 'loading' | 'success' | 'error';
export type PaneMode = 'library' | 'chapters' | 'reader' | 'assistant' | 'status';
export type ReaderTheme = 'light' | 'dark';
export type WindowStatus = 'pending' | 'running' | 'done' | 'failed';
export type JobStatus = WindowStatus | 'skipped';
export type ChatTurnStatus = 'streaming' | 'done' | 'failed';

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string | null;
}

export interface RuntimeInfo {
  app: string;
  version: string;
  data_dir: string;
  verify_mode: boolean;
  llm: {
    base_url_configured: boolean;
    api_key_configured: boolean;
    model: string;
  };
  observability: {
    enabled: boolean;
    provider: string;
  };
}

export interface SettingsSummary {
  reader: {
    font_size: number;
    line_height: number;
    theme: ReaderTheme;
  };
  llm: {
    base_url: string;
    api_key_configured: boolean;
    model: string;
  };
  context?: {
    provider_context_limit_tokens?: number;
    attention_target_input_tokens?: number;
    emergency_input_cap_tokens?: number;
    effective_input_budget?: number;
    hard_input_cap?: number;
  };
  window?: {
    lookahead_paragraphs?: number;
    target_window_tokens?: number;
    max_window_tokens?: number;
  };
  window_l1?: {
    lookahead_paragraphs?: number;
    focus_target_tokens?: number;
    focus_max_tokens?: number;
  };
}

export interface ReadingProgress {
  book_id: number;
  chapter_idx: number;
  paragraph_idx: number;
  scroll_pct: number;
  updated_at: string | null;
}

export interface BookSummary {
  id: number;
  title: string;
  author: string | null;
  cover_url: string | null;
  total_chapters: number;
  imported_at: string;
  updated_at: string;
  last_progress: ReadingProgress | null;
  paragraph_count?: number;
  token_estimate?: number;
}

export interface ChapterSummary {
  book_id: number;
  idx: number;
  title: string;
  paragraph_count: number;
  token_estimate: number;
  prev_chapter_idx?: number | null;
  next_chapter_idx?: number | null;
}

export interface ParagraphComment {
  id: number;
  book_id: number;
  chapter_idx: number;
  paragraph_idx: number;
  window_id: number;
  comment: string;
  comment_type: string;
  status: string;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Paragraph {
  book_id: number;
  chapter_idx: number;
  paragraph_idx: number;
  text: string;
  comments?: ParagraphComment[];
}

export interface ReadingWindow {
  id: number;
  book_id: number;
  chapter_idx: number;
  window_seq: number;
  start_paragraph_idx: number;
  end_paragraph_idx: number;
  focus_start_paragraph_idx: number;
  focus_end_paragraph_idx: number;
  assistant_frontier_paragraph_idx: number;
  status: WindowStatus;
  error: string | null;
  trace_id?: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  text_hash?: string;
  context_hash?: string;
}

export interface JobSummary {
  id: number;
  job_type: string;
  book_id: number;
  chapter_idx: number;
  window_id: number | null;
  status: JobStatus;
  attempt_count: number;
  error: string | null;
  trace_id: string | null;
  created_at: string;
  updated_at?: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface ImportStats {
  chapter_count: number;
  paragraph_count: number;
  char_count: number;
  token_estimate: number;
  duration_ms: number;
}

export interface ImportResult {
  book: BookSummary;
  first_chapter: ChapterSummary | null;
  import_stats: ImportStats;
}

export interface ListResponse<T> {
  items: T[];
  total: number;
}

export interface ParagraphsResponse {
  book_id: number;
  chapter_idx: number;
  items: Paragraph[];
  total: number;
}

export interface ProgressUpdateResponse {
  progress: ReadingProgress;
  assistant_frontier_paragraph_idx: number;
  current_window: ReadingWindow | null;
  jobs: JobSummary[];
}

export interface WindowResponse {
  window: ReadingWindow;
  comments_ready_count: number;
  comments_target_count: number;
}

export interface ChatSession {
  id: number;
  book_id: number;
  chapter_idx: number;
  title: string | null;
  last_paragraph_idx: number | null;
  created_at: string;
  updated_at: string;
}

export interface ChatTurn {
  id: number;
  session_id: number;
  book_id: number;
  chapter_idx: number;
  paragraph_idx: number;
  user_msg: string;
  ai_msg: string | null;
  status: ChatTurnStatus;
  tokens_in: number | null;
  tokens_out: number | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface BackendEvent {
  event_id?: string;
  event: string;
  request_id?: string | null;
  book_id?: number;
  chapter_idx?: number;
  paragraph_idx?: number;
  window_id?: number;
  job_id?: number;
  trace_id?: string;
  verify_run_id?: string;
  verify_scenario_id?: string;
  verify_step_id?: string;
  created_at?: string;
  error?: string;
  status?: string;
}

export interface ActivityItem {
  id: string;
  event: string;
  title: string;
  detail?: string;
  tone: 'neutral' | 'good' | 'warn' | 'bad' | 'info';
  createdAt: string;
  traceId?: string | null;
  requestId?: string | null;
}
