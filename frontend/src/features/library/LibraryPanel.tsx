import {
  CheckCircle2,
  Import,
  Library,
  Loader2,
  RefreshCcw,
  Search,
  UploadCloud,
} from 'lucide-react';

import { bookAuthorLabel, formatNumber } from '../../lib/formatters';
import type { BookSummary, ImportResult, LoadStatus } from '../../types';

export function LibraryHeader({
  query,
  setQuery,
  onRefresh,
  onImport,
  importProgress,
}: {
  query: string;
  setQuery: (value: string) => void;
  onRefresh: () => Promise<void>;
  onImport: (file: File) => Promise<void>;
  importProgress: LoadStatus;
}) {
  return (
    <div className="library-header">
      <div className="panel-title">
        <Library size={18} />
        <span>书库</span>
      </div>
      <label className="search-box">
        <Search size={16} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void onRefresh();
          }}
          placeholder="搜索书名或作者"
        />
      </label>
      <ImportButton onImport={onImport} importProgress={importProgress} />
      <button className="soft-button" onClick={() => void onRefresh()}>
        <RefreshCcw size={16} />
        刷新书库
      </button>
    </div>
  );
}

export function ImportButton({
  onImport,
  importProgress,
}: {
  onImport: (file: File) => Promise<void>;
  importProgress: LoadStatus;
}) {
  return (
    <label className={`import-button import-${importProgress}`}>
      {importProgress === 'loading' ? (
        <Loader2 size={18} className="spin" />
      ) : (
        <UploadCloud size={18} />
      )}
      <span>{importProgress === 'loading' ? '导入中' : '导入 EPUB'}</span>
      <input
        type="file"
        accept=".epub,application/epub+zip"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) void onImport(file);
          event.currentTarget.value = '';
        }}
      />
    </label>
  );
}

export function BookList({
  books,
  selectedBookId,
  onSelect,
}: {
  books: BookSummary[];
  selectedBookId: number | null;
  onSelect: (book: BookSummary) => void;
}) {
  if (!books.length) {
    return (
      <div className="empty-list">
        <Import size={26} />
        <strong>还没有书</strong>
        <span>书库等待第一本 EPUB。</span>
      </div>
    );
  }

  return (
    <div className="book-list">
      {books.map((book) => {
        const authorLabel = bookAuthorLabel(book);
        return (
          <button
            className={`book-card ${selectedBookId === book.id ? 'active' : ''}`}
            key={book.id}
            onClick={() => onSelect(book)}
          >
            <BookCover book={book} />
            <span className="book-meta">
              <strong>{book.title}</strong>
              {authorLabel && <span>{authorLabel}</span>}
              <small>
                {book.total_chapters} 章
                {book.last_progress
                  ? ` · 读到 ${book.last_progress.chapter_idx + 1}-${book.last_progress.paragraph_idx + 1}`
                  : ' · 未开始'}
              </small>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function BookCover({ book }: { book: BookSummary }) {
  if (book.cover_url) {
    return <img className="book-cover" src={book.cover_url} alt="" />;
  }
  const initials = book.title.slice(0, 2).toUpperCase();
  return (
    <div className="book-cover fallback-cover" aria-hidden="true">
      <span>{initials}</span>
    </div>
  );
}

export function ImportReceipt({ result }: { result: ImportResult }) {
  return (
    <div className="import-receipt">
      <div>
        <CheckCircle2 size={18} />
        <strong>最近导入完成</strong>
      </div>
      <span>{result.book.title}</span>
      <small>
        {result.import_stats.chapter_count} 章 · {formatNumber(result.import_stats.char_count)} 字符 ·{' '}
        {result.import_stats.duration_ms}ms
      </small>
    </div>
  );
}
