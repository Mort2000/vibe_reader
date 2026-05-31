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

export interface Paragraph {
  book_id: number;
  chapter_idx: number;
  paragraph_idx: number;
  text: string;
  comments?: ParagraphComment[];
}

export interface ReadingProgress {
  book_id: number;
  chapter_idx: number;
  paragraph_idx: number;
  scroll_pct: number;
  updated_at: string | null;
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

export interface WindowInfo {
  id: number;
  book_id: number;
  chapter_idx: number;
  window_seq: number;
  start_paragraph_idx: number;
  end_paragraph_idx: number;
  focus_start_paragraph_idx: number;
  focus_end_paragraph_idx: number;
  assistant_frontier_paragraph_idx: number;
  text_hash: string;
  context_hash: string;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface JobInfo {
  id: number;
  job_type: string;
  book_id: number;
  chapter_idx: number;
  window_id: number | null;
  status: string;
  attempt_count: number;
  error: string | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface ImportResult {
  book: BookSummary;
  first_chapter: ChapterSummary | null;
  import_stats: {
    chapter_count: number;
    paragraph_count: number;
    char_count: number;
    token_estimate: number;
    duration_ms: number;
  };
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
  current_window: WindowInfo | null;
  jobs: JobInfo[];
}

export interface ChatSession {
  id: number;
  book_id: number;
  chapter_idx: number;
  title: string | null;
  last_paragraph_idx: number;
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
  status: string;
  tokens_in: number | null;
  tokens_out: number | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
}
