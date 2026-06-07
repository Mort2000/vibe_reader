import { renderToString } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import {
  type AppControllerOptions,
  useAppController,
} from './app/useAppController';
import type { BookSummary, ChapterSummary } from './types';

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
}));
const componentMocks = vi.hoisted(() => ({
  chapterNavigatorProps: null as CapturedChapterNavigatorProps | null,
}));

interface CapturedChapterNavigatorProps {
  onSelectChapter: (chapterIdx: number) => void;
}

vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>(
    'react-router',
  );
  return {
    ...actual,
    useNavigate: () => routerMocks.navigate,
    useBlocker: () => ({ state: 'unblocked' }),
  };
});

vi.mock('./app/useAppController', () => ({
  useAppController: vi.fn(),
}));

vi.mock('./features/reader/ChapterNavigator', async () => {
  const actual = await vi.importActual<
    typeof import('./features/reader/ChapterNavigator')
  >('./features/reader/ChapterNavigator');
  return {
    ...actual,
    ChapterNavigator: (props: CapturedChapterNavigatorProps) => {
      componentMocks.chapterNavigatorProps = props;
      return null;
    },
  };
});

let capturedOptions: AppControllerOptions | null = null;

function makeBook(bookId: number): BookSummary {
  return {
    id: bookId,
    title: `Book ${bookId}`,
    author: null,
    cover_url: null,
    total_chapters: 10,
    imported_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    last_progress: null,
  };
}

function makeChapter(bookId: number, chapterIdx: number): ChapterSummary {
  return {
    book_id: bookId,
    idx: chapterIdx,
    title: `Chapter ${chapterIdx}`,
    paragraph_count: 0,
    token_estimate: 0,
  };
}

function makeControllerStub(options: AppControllerOptions) {
  const selectedBook =
    options.routeBookId !== null ? makeBook(options.routeBookId) : null;
  const activeContext =
    options.routeBookId !== null && options.routeChapterIdx !== null
      ? {
          bookId: options.routeBookId,
          chapterIdx: options.routeChapterIdx,
        }
      : null;
  const activeChapter = activeContext
    ? makeChapter(activeContext.bookId, activeContext.chapterIdx)
    : null;

  return {
    runtime: null,
    settings: null,
    books: selectedBook ? [selectedBook] : [],
    query: '',
    setQuery: vi.fn(),
    selectedBookId: options.routeBookId,
    selectedBook,
    activeContext,
    chapters: activeChapter ? [activeChapter] : [],
    libraryCollapsed: false,
    chaptersCollapsed: false,
    request: { status: 'idle', label: 'idle' },
    importResult: null,
    importProgress: 'idle',
    paragraphs: [],
    chapterStatus: 'idle',
    progress: null,
    progressSync: 'idle',
    selectedParagraph: 0,
    setSelectedParagraph: vi.fn(),
    currentWindow: null,
    windowCounts: { ready: 0, target: 0 },
    jobs: [],
    chatTurns: [],
    chatInput: '',
    setChatInput: vi.fn(),
    chatStatus: 'idle',
    streamingTurn: null,
    restorePending: false,
    connection: 'idle',
    events: [],
    activeChapter,
    brandSubtitle: 'Vibe Reader Mini',
    loadBootstrap: vi.fn(async () => undefined),
    refreshBooks: vi.fn(async () => undefined),
    handleImport: vi.fn(async () => undefined),
    selectBook: vi.fn(),
    selectChapter: vi.fn((chapterIdx: number) => {
      options.onNavigateToChapter(chapterIdx);
    }),
    saveCurrentProgress: vi.fn(),
    settleRestore: vi.fn(),
    retryCurrentWindow: vi.fn(),
    sendChat: vi.fn(),
    abortChat: vi.fn(),
    toggleLibraryCollapsed: vi.fn(),
    toggleChaptersCollapsed: vi.fn(),
  } as ReturnType<typeof useAppController>;
}

function renderRoute(path: string) {
  return renderToString(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe('App routes', () => {
  beforeEach(() => {
    capturedOptions = null;
    componentMocks.chapterNavigatorProps = null;
    routerMocks.navigate.mockReset();
    vi.mocked(useAppController).mockImplementation((options) => {
      capturedOptions = options;
      return makeControllerStub(options);
    });
  });

  it('passes book and chapter params from deep links into the controller', () => {
    renderRoute('/books/12/chapters/5/status');

    expect(capturedOptions?.routeBookId).toBe(12);
    expect(capturedOptions?.routeChapterIdx).toBe(5);
  });

  it('wires chapter navigator selection to the reader route for the current book', () => {
    renderRoute('/books/12/chapters/5/chapters');

    componentMocks.chapterNavigatorProps?.onSelectChapter(8);

    expect(routerMocks.navigate).toHaveBeenCalledWith('/books/12/chapters/8');
  });

  it('wires same-book selection to the reader route for the active context', () => {
    renderRoute('/books/12/chapters/5/chapters');

    capturedOptions?.onNavigateToBook(makeBook(12), {
      bookId: 12,
      chapterIdx: 5,
    });

    expect(routerMocks.navigate).toHaveBeenCalledWith('/books/12/chapters/5');
  });

  it('wires new-book selection to the book route', () => {
    renderRoute('/books/12/chapters/5/chapters');

    capturedOptions?.onNavigateToBook(makeBook(13), null);

    expect(routerMocks.navigate).toHaveBeenCalledWith('/books/13');
  });
});
