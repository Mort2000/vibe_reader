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
  current_window: unknown | null;
  jobs: unknown[];
}
