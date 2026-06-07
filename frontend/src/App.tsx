import { useCallback, useEffect } from 'react';
import {
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router';
import {
  Activity,
  AlertCircle,
  BookOpen,
  Bot,
  CheckCircle2,
  Layers,
  Library,
  Loader2,
  RefreshCcw,
} from 'lucide-react';

import {
  bookRoutePath,
  modeRoutePath,
  parseRouteNumber,
} from './app/routes';
import { useAppController } from './app/useAppController';
import { BrandMark } from './components/BrandMark';
import { NavSectionToggle } from './components/NavSectionToggle';
import { StatusPill } from './components/StatusPill';
import { ChatCompanion } from './features/assistant/ChatCompanion';
import {
  BookList,
  ImportReceipt,
  LibraryHeader,
} from './features/library/LibraryPanel';
import { StatusCenter } from './features/observability/StatusCenter';
import {
  ChapterNavigator,
  EmptyChapterPanel,
} from './features/reader/ChapterNavigator';
import { EmptyReader, ReaderPreview } from './features/reader/ReaderPreview';
import { chapterDisplayTitle, statusCopy } from './lib/formatters';
import type { BookSummary, PaneMode } from './types';

function App() {
  return (
    <Routes>
      <Route index element={<Navigate to="/library" replace />} />
      <Route path="library" element={<AppRoute mode="library" />} />
      <Route path="reader" element={<AppRoute mode="reader" />} />
      <Route path="chapters" element={<AppRoute mode="chapters" />} />
      <Route path="assistant" element={<AppRoute mode="assistant" />} />
      <Route path="status" element={<AppRoute mode="status" />} />
      <Route path="books/:bookId" element={<AppRoute mode="reader" />} />
      <Route
        path="books/:bookId/chapters"
        element={<AppRoute mode="chapters" />}
      />
      <Route
        path="books/:bookId/chapters/:chapterIdx"
        element={<AppRoute mode="reader" />}
      />
      <Route
        path="books/:bookId/chapters/:chapterIdx/chapters"
        element={<AppRoute mode="chapters" />}
      />
      <Route
        path="books/:bookId/chapters/:chapterIdx/assistant"
        element={<AppRoute mode="assistant" />}
      />
      <Route
        path="books/:bookId/chapters/:chapterIdx/status"
        element={<AppRoute mode="status" />}
      />
      <Route path="*" element={<Navigate to="/library" replace />} />
    </Routes>
  );
}

function AppRoute({ mode }: { mode: PaneMode }) {
  const navigate = useNavigate();
  const params = useParams<{ bookId?: string; chapterIdx?: string }>();
  const routeBookId = parseRouteNumber(params.bookId);
  const routeChapterIdx = parseRouteNumber(params.chapterIdx);

  const navigateToBook = useCallback(
    (book: BookSummary) => {
      void navigate(bookRoutePath(book.id));
    },
    [navigate],
  );
  const navigateToChapter = useCallback(
    (chapterIdx: number) => {
      if (routeBookId === null) return;
      void navigate(
        modeRoutePath('reader', { bookId: routeBookId, chapterIdx }, routeBookId),
      );
    },
    [navigate, routeBookId],
  );

  const app = useAppController({
    routeBookId,
    routeChapterIdx,
    onNavigateToBook: navigateToBook,
    onNavigateToChapter: navigateToChapter,
  });

  useEffect(() => {
    if (routeBookId === null || routeChapterIdx !== null || !app.activeContext) {
      return;
    }
    void navigate(
      modeRoutePath(mode, app.activeContext, app.selectedBookId),
      { replace: true },
    );
  }, [
    app.activeContext,
    app.selectedBookId,
    mode,
    navigate,
    routeBookId,
    routeChapterIdx,
  ]);

  const navigateToMode = useCallback(
    (targetMode: PaneMode) => {
      void navigate(
        modeRoutePath(targetMode, app.activeContext, app.selectedBookId),
      );
    },
    [app.activeContext, app.selectedBookId, navigate],
  );

  const selectedBookLabel =
    app.selectedBook?.title ??
    (app.selectedBookId !== null ? `书籍 #${app.selectedBookId}` : '未选择书籍');

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <strong>Vibe Reader Mini</strong>
            <span>{app.brandSubtitle}</span>
          </div>
        </div>
        <div className="topbar-actions">
          <StatusPill status={app.request.status}>
            {app.request.status === 'loading' && <Loader2 size={14} className="spin" />}
            {app.request.status === 'success' && <CheckCircle2 size={14} />}
            {app.request.status === 'error' && <AlertCircle size={14} />}
            {statusCopy(app.request.status)}
          </StatusPill>
          <button
            className="icon-button"
            onClick={() => void app.loadBootstrap()}
            title="刷新运行状态"
          >
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      <main className="workspace" data-mode={mode}>
        <aside
          className={`library-panel nav-section ${
            app.libraryCollapsed ? 'is-collapsed' : ''
          }`}
        >
          <NavSectionToggle
            icon={<Library size={18} />}
            label="书库"
            current={selectedBookLabel}
            collapsed={app.libraryCollapsed}
            onToggle={app.toggleLibraryCollapsed}
          />
          <div className="nav-section-body">
            <LibraryHeader
              query={app.query}
              setQuery={app.setQuery}
              onRefresh={app.refreshBooks}
              onImport={app.handleImport}
              importProgress={app.importProgress}
            />
            <BookList
              books={app.books}
              selectedBookId={app.selectedBookId}
              onSelect={app.selectBook}
            />
            {app.importResult && <ImportReceipt result={app.importResult} />}
          </div>
        </aside>

        <aside
          className={`chapter-panel nav-section ${
            app.chaptersCollapsed ? 'is-collapsed' : ''
          }`}
        >
          <NavSectionToggle
            icon={<Layers size={18} />}
            label="章节"
            current={
              app.selectedBookId !== null
                ? chapterDisplayTitle(app.activeChapter)
                : '先选择书籍'
            }
            collapsed={app.chaptersCollapsed}
            onToggle={app.toggleChaptersCollapsed}
          />
          <div className="nav-section-body">
            {app.selectedBookId !== null ? (
              <ChapterNavigator
                activeChapter={app.activeChapter}
                chapters={app.chapters}
                onSelectChapter={app.selectChapter}
              />
            ) : (
              <EmptyChapterPanel />
            )}
          </div>
        </aside>

        <section className="reader-stage">
          {app.selectedBookId !== null ? (
            <ReaderPreview
              activeChapter={app.activeChapter}
              paragraphs={app.paragraphs}
              chapterStatus={app.chapterStatus}
              progress={app.progress}
              progressSync={app.progressSync}
              selectedParagraph={app.selectedParagraph}
              restorePending={app.restorePending}
              currentWindow={app.currentWindow}
              onVisibleParagraph={app.setSelectedParagraph}
              onSaveProgress={app.saveCurrentProgress}
              onRestoreSettled={app.settleRestore}
            />
          ) : (
            <EmptyReader
              onImport={app.handleImport}
              importProgress={app.importProgress}
            />
          )}
        </section>

        <aside className="assistant-panel">
          <ChatCompanion
            chatTurns={app.chatTurns}
            streamingTurn={app.streamingTurn}
            chatInput={app.chatInput}
            chatStatus={app.chatStatus}
            selectedBook={app.selectedBook}
            activeChapter={app.activeChapter}
            selectedParagraph={app.selectedParagraph}
            onInputChange={app.setChatInput}
            onSend={app.sendChat}
            onAbort={app.abortChat}
          />
          <StatusCenter
            request={app.request}
            runtime={app.runtime}
            settings={app.settings}
            connection={app.connection}
            events={app.events}
            selectedBook={app.selectedBook}
            activeChapter={app.activeChapter}
            currentWindow={app.currentWindow}
            windowCounts={app.windowCounts}
            jobs={app.jobs}
            onRetryWindow={app.retryCurrentWindow}
          />
        </aside>
      </main>

      <nav className="mobile-nav" aria-label="移动端导航">
        <button
          className={mode === 'library' ? 'active' : ''}
          onClick={() => navigateToMode('library')}
        >
          <Library size={18} />
          书库
        </button>
        <button
          className={mode === 'reader' ? 'active' : ''}
          onClick={() => navigateToMode('reader')}
        >
          <BookOpen size={18} />
          阅读
        </button>
        <button
          className={mode === 'chapters' ? 'active' : ''}
          onClick={() => navigateToMode('chapters')}
        >
          <Layers size={18} />
          章节
        </button>
        <button
          className={mode === 'assistant' ? 'active' : ''}
          onClick={() => navigateToMode('assistant')}
        >
          <Bot size={18} />
          AI
        </button>
        <button
          className={mode === 'status' ? 'active' : ''}
          onClick={() => navigateToMode('status')}
        >
          <Activity size={18} />
          状态
        </button>
      </nav>
    </div>
  );
}

export default App;
