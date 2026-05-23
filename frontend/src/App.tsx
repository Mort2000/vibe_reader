import { useState, useEffect, useCallback, useRef } from 'react';
import type { BookSummary, ChapterSummary, Paragraph, ImportResult } from './types';
import * as api from './api/client';
import BookList from './components/BookList';
import ChapterNav from './components/ChapterNav';
import ReaderView from './components/ReaderView';
import type { ReaderViewHandle } from './components/ReaderView';
import ImportDropZone from './components/ImportDropZone';
import './App.css';

type View = 'library' | 'reader';

export default function App() {
  const [view, setView] = useState<View>('library');
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [currentBook, setCurrentBook] = useState<BookSummary | null>(null);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [currentChapterIdx, setCurrentChapterIdx] = useState<number>(0);
  const [paragraphs, setParagraphs] = useState<Paragraph[]>([]);
  const [progressParagraphIdx, setProgressParagraphIdx] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const readerRef = useRef<ReaderViewHandle>(null);

  const refreshBooks = useCallback(async () => {
    const result = await api.getBooks();
    setBooks(result.items);
  }, []);

  useEffect(() => {
    refreshBooks();
  }, [refreshBooks]);

  const loadChapter = useCallback(async (bookId: number, chapterIdx: number) => {
    setLoading(true);
    try {
      const resp = await api.getParagraphs(bookId, chapterIdx, true);
      setParagraphs(resp.items);
      setCurrentChapterIdx(chapterIdx);
    } finally {
      setLoading(false);
    }
  }, []);

  const openBook = useCallback(
    async (book: BookSummary) => {
      setCurrentBook(book);
      const chResult = await api.getChapters(book.id);
      setChapters(chResult.items);

      const progress = await api.getProgress(book.id);
      const startIdx = progress.updated_at ? progress.chapter_idx : 0;
      setProgressParagraphIdx(progress.updated_at ? progress.paragraph_idx : null);

      if (chResult.items.length > 0) {
        const chapter = chResult.items.find((c) => c.idx === startIdx) || chResult.items[0];
        await loadChapter(book.id, chapter.idx);
      }

      setView('reader');
    },
    [loadChapter]
  );

  const handleImported = useCallback(
    async (result: ImportResult) => {
      await refreshBooks();
      openBook(result.book);
    },
    [refreshBooks, openBook]
  );

  const handleChapterSelect = useCallback(
    (idx: number) => {
      if (!currentBook) return;
      readerRef.current?.flush();
      loadChapter(currentBook.id, idx);
      setProgressParagraphIdx(null);
    },
    [currentBook, loadChapter]
  );

  const handleProgressChange = useCallback(
    async (chapterIdx: number, paragraphIdx: number, scrollPct: number) => {
      if (!currentBook) return;
      try {
        await api.updateProgress(currentBook.id, chapterIdx, paragraphIdx, scrollPct);
      } catch {
        // silently ignore progress update failures
      }
    },
    [currentBook]
  );

  const handleBack = useCallback(() => {
    readerRef.current?.flush();
    setView('library');
    setCurrentBook(null);
    setChapters([]);
    setParagraphs([]);
    refreshBooks();
  }, [refreshBooks]);

  if (view === 'library') {
    return (
      <div className="app library-view">
        <header className="app-header">
          <h1>Vibe Reader</h1>
        </header>
        {books.length === 0 ? (
          <ImportDropZone onImported={handleImported} />
        ) : null}
        <BookList
          books={books}
          onSelect={openBook}
          onImported={handleImported}
        />
      </div>
    );
  }

  return (
    <div className="app reader-view-layout">
      <header className="reader-header">
        <button className="back-btn" onClick={handleBack}>
          &larr; Library
        </button>
        <h1>{currentBook?.title}</h1>
      </header>
      <div className="reader-body">
        <aside className="reader-sidebar">
          <ChapterNav
            chapters={chapters}
            currentIdx={currentChapterIdx}
            onSelect={handleChapterSelect}
          />
        </aside>
        <main className="reader-main">
          {loading ? (
            <div className="loading">Loading...</div>
          ) : (
            <ReaderView
              ref={readerRef}
              chapterIdx={currentChapterIdx}
              paragraphs={paragraphs}
              initialParagraphIdx={progressParagraphIdx}
              onProgressChange={handleProgressChange}
            />
          )}
        </main>
      </div>
    </div>
  );
}
