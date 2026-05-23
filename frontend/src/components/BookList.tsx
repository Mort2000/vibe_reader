import { useState, useRef } from 'react';
import type { BookSummary, ImportResult } from '../types';
import * as api from '../api/client';

interface Props {
  books: BookSummary[];
  onSelect: (book: BookSummary) => void;
  onImported: (result: ImportResult) => void;
}

export default function BookList({ books, onSelect, onImported }: Props) {
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleImport = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setImporting(true);
    setError(null);
    try {
      const result = await api.importEpub(file);
      onImported(result);
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message || 'Import failed';
      setError(msg);
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div className="book-list">
      <div className="book-list-header">
        <h2>Books</h2>
        <div className="import-area">
          <input ref={fileRef} type="file" accept=".epub" onChange={handleImport} disabled={importing} />
          {importing && <span className="loading">Importing...</span>}
          {error && <span className="error">{error}</span>}
        </div>
      </div>

      {books.length === 0 ? (
        <p className="empty">No books yet. Import an epub to start reading.</p>
      ) : (
        <ul className="books">
          {books.map((book) => (
            <li key={book.id} className="book-item" onClick={() => onSelect(book)}>
              <div className="book-info">
                <span className="book-title">{book.title}</span>
                {book.author && <span className="book-author">{book.author}</span>}
                <span className="book-chapters">{book.total_chapters} chapters</span>
              </div>
              {book.last_progress && (
                <span className="book-progress">
                  Ch.{book.last_progress.chapter_idx} / P.{book.last_progress.paragraph_idx}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
