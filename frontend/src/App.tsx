import { useState, useEffect, useCallback, useRef } from 'react';
import type { BookSummary, ChapterSummary, Paragraph, ImportResult, WindowInfo } from './types';
import * as api from './api/client';
import BookList from './components/BookList';
import ChapterNav from './components/ChapterNav';
import ReaderView from './components/ReaderView';
import type { ReaderViewHandle } from './components/ReaderView';
import ChatPanel from './components/ChatPanel';
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
  const [currentWindow, setCurrentWindow] = useState<WindowInfo | null>(null);
  const [currentParagraphIdx, setCurrentParagraphIdx] = useState<number>(0);
  const readerRef = useRef<ReaderViewHandle>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const refreshBooks = useCallback(async () => {
    const result = await api.getBooks();
    setBooks(result.items);
  }, []);

  useEffect(() => {
    api.getBooks().then((result) => setBooks(result.items));
  }, []);

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

  const handleRetryWindow = useCallback(
    async (windowId: number) => {
      if (!currentBook) return;
      try {
        await api.retryWindow(windowId);
      } catch {
        // retry failure handled by SSE state
      }
    },
    [currentBook],
  );

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

        try {
          const winResp = await api.getCurrentWindow(book.id, chapter.idx);
          if (winResp.window && winResp.window.status !== 'done') {
            setCurrentWindow(winResp.window);
          }
        } catch {
          // window may not exist yet — that's fine
        }
      }

      setView('reader');
    },
    [loadChapter],
  );

  const handleImported = useCallback(
    async (result: ImportResult) => {
      await refreshBooks();
      openBook(result.book);
    },
    [refreshBooks, openBook],
  );

  const handleChapterSelect = useCallback(
    (idx: number) => {
      if (!currentBook) return;
      readerRef.current?.flush();
      setCurrentWindow(null);
      loadChapter(currentBook.id, idx);
      setProgressParagraphIdx(null);
    },
    [currentBook, loadChapter],
  );

  const handleProgressChange = useCallback(
    async (chapterIdx: number, paragraphIdx: number, scrollPct: number) => {
      if (!currentBook) return;
      setCurrentParagraphIdx(paragraphIdx);
      try {
        const resp = await api.updateProgress(currentBook.id, chapterIdx, paragraphIdx, scrollPct);
        if (resp.current_window) {
          setCurrentWindow(resp.current_window);
        }
      } catch {
        // silently ignore progress update failures
      }
    },
    [currentBook],
  );

  // SSE connection for real-time window/comment updates
  useEffect(() => {
    if (view !== 'reader' || !currentBook) return;

    const es = api.createEventSource(
      currentBook.id,
      currentChapterIdx,
      (event, data) => {
        if (event === 'window.queued') {
          setCurrentWindow((prev) =>
            prev
              ? { ...prev, status: 'pending' }
              : {
                  id: (data.window_id as number) ?? 0,
                  book_id: currentBook.id,
                  chapter_idx: currentChapterIdx,
                  window_seq: 0,
                  start_paragraph_idx: 0,
                  end_paragraph_idx: 0,
                  focus_start_paragraph_idx: 0,
                  focus_end_paragraph_idx: 0,
                  assistant_frontier_paragraph_idx: 0,
                  text_hash: '',
                  context_hash: '',
                  status: 'pending',
                  error: null,
                  created_at: '',
                  updated_at: '',
                  completed_at: null,
                },
          );
        }
        if (event === 'window.running') {
          setCurrentWindow((prev) =>
            prev ? { ...prev, status: 'running' } : prev,
          );
        }
        if (event === 'window.done') {
          setCurrentWindow((prev) =>
            prev ? { ...prev, status: 'done' } : prev,
          );
          loadChapter(currentBook.id, currentChapterIdx);
        }
        if (event === 'window.failed') {
          setCurrentWindow((prev) =>
            prev
              ? { ...prev, status: 'failed', error: (data.error as string) || null }
              : prev,
          );
        }
      },
    );
    eventSourceRef.current = es;

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [view, currentBook, currentChapterIdx, loadChapter]);

  const handleBack = useCallback(() => {
    readerRef.current?.flush();
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setView('library');
    setCurrentBook(null);
    setChapters([]);
    setParagraphs([]);
    setCurrentWindow(null);
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
              currentWindow={currentWindow}
              onRetryWindow={handleRetryWindow}
            />
          )}
        </main>
        <aside className="reader-chat">
          {currentBook && (
            <ChatPanel
              bookId={currentBook.id}
              chapterIdx={currentChapterIdx}
              paragraphIdx={currentParagraphIdx}
            />
          )}
        </aside>
      </div>
    </div>
  );
}
