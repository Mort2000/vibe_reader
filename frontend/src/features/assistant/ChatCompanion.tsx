import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Loader2,
  MessageSquareText,
} from 'lucide-react';
import { useMemo } from 'react';

import { StatusPill } from '../../components/StatusPill';
import { chapterDisplayTitle, formatNumber } from '../../lib/formatters';
import type { BookSummary, ChapterSummary, ChatTurn, LoadStatus } from '../../types';

export function ChatCompanion({
  chatTurns,
  streamingTurn,
  chatInput,
  chatStatus,
  selectedBook,
  activeChapter,
  selectedParagraph,
  onInputChange,
  onSend,
  onAbort,
}: {
  chatTurns: ChatTurn[];
  streamingTurn: ChatTurn | null;
  chatInput: string;
  chatStatus: LoadStatus;
  selectedBook: BookSummary | null;
  activeChapter: ChapterSummary | null;
  selectedParagraph: number;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onAbort: () => void;
}) {
  const visibleTurns = useMemo(() => {
    const turns = [...chatTurns].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
    return streamingTurn ? [...turns, streamingTurn] : turns;
  }, [chatTurns, streamingTurn]);

  const canSend = Boolean(
    selectedBook && activeChapter && chatInput.trim() && chatStatus !== 'loading',
  );

  return (
    <section className="chat-panel companion-section">
      <div className="panel-title">
        <Bot size={18} />
        <span>AI Companion</span>
      </div>

      <div className="chat-context">
        <span>
          {selectedBook ? selectedBook.title : '未选择书籍'}
          {activeChapter ? ` · ${chapterDisplayTitle(activeChapter)}` : ''}
        </span>
        <StatusPill status={chatStatus}>
          {chatStatus === 'loading' && <Loader2 size={14} className="spin" />}
          {chatStatus === 'success' && <CheckCircle2 size={14} />}
          {chatStatus === 'error' && <AlertCircle size={14} />}
          P{selectedParagraph + 1}
        </StatusPill>
      </div>

      <div className="chat-turns">
        {visibleTurns.length ? (
          visibleTurns.map((turn) => (
            <ChatTurnView key={`${turn.id}-${turn.status}`} turn={turn} />
          ))
        ) : (
          <div className="empty-feed">
            <MessageSquareText size={18} />
            <span>暂无对话记录。</span>
          </div>
        )}
      </div>

      <div className="chat-composer">
        <textarea
          value={chatInput}
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              onSend();
            }
          }}
          placeholder="这里为什么有点奇怪？"
          rows={3}
        />
        <div>
          {chatStatus === 'loading' ? (
            <button className="soft-inline-button" onClick={onAbort}>
              <AlertCircle size={16} />
              停止
            </button>
          ) : (
            <span>当前 P{selectedParagraph + 1}</span>
          )}
          <button className="send-button" disabled={!canSend} onClick={onSend}>
            <MessageSquareText size={16} />
            发送
          </button>
        </div>
      </div>
    </section>
  );
}

function ChatTurnView({ turn }: { turn: ChatTurn }) {
  return (
    <article className={`chat-turn chat-${turn.status}`}>
      <div className="chat-user">
        <strong>你</strong>
        <p>{turn.user_msg}</p>
      </div>
      <div className="chat-ai">
        <strong>
          AI
          {turn.status === 'streaming' && <Loader2 size={14} className="spin" />}
          {turn.status === 'failed' && <AlertCircle size={14} />}
        </strong>
        <p>{turn.ai_msg || (turn.status === 'streaming' ? '正在组织回答...' : '暂无回答')}</p>
        <div className="chat-meta">
          <span>P{turn.paragraph_idx + 1}</span>
          {turn.tokens_in !== null && <span>{formatNumber(turn.tokens_in)} in</span>}
          {turn.tokens_out !== null && <span>{formatNumber(turn.tokens_out)} out</span>}
        </div>
      </div>
    </article>
  );
}
