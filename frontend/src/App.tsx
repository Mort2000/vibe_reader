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

function App() {
  const app = useAppController();

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

      <main className="workspace" data-mode={app.mode}>
        <aside
          className={`library-panel nav-section ${
            app.libraryCollapsed ? 'is-collapsed' : ''
          }`}
        >
          <NavSectionToggle
            icon={<Library size={18} />}
            label="书库"
            current={app.selectedBook?.title || '未选择书籍'}
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
              selectedBookId={app.selectedBook?.id ?? null}
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
              app.selectedBook ? chapterDisplayTitle(app.activeChapter) : '先选择书籍'
            }
            collapsed={app.chaptersCollapsed}
            onToggle={app.toggleChaptersCollapsed}
          />
          <div className="nav-section-body">
            {app.selectedBook ? (
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
          {app.selectedBook ? (
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
          className={app.mode === 'library' ? 'active' : ''}
          onClick={() => app.setMode('library')}
        >
          <Library size={18} />
          书库
        </button>
        <button
          className={app.mode === 'reader' ? 'active' : ''}
          onClick={() => app.setMode('reader')}
        >
          <BookOpen size={18} />
          阅读
        </button>
        <button
          className={app.mode === 'chapters' ? 'active' : ''}
          onClick={() => app.setMode('chapters')}
        >
          <Layers size={18} />
          章节
        </button>
        <button
          className={app.mode === 'assistant' ? 'active' : ''}
          onClick={() => app.setMode('assistant')}
        >
          <Bot size={18} />
          AI
        </button>
        <button
          className={app.mode === 'status' ? 'active' : ''}
          onClick={() => app.setMode('status')}
        >
          <Activity size={18} />
          状态
        </button>
      </nav>
    </div>
  );
}

export default App;
