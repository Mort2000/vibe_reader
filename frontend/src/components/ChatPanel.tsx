import { useState, useEffect, useRef, useCallback } from 'react';
import * as api from '../api/client';

interface Props {
  bookId: number;
  chapterIdx: number;
  paragraphIdx: number;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  streaming?: boolean;
  failed?: boolean;
  retryPayload?: { userMsg: string };
}

let _idCounter = 0;
const nextId = (prefix: string) => `${prefix}-${++_idCounter}`;

function matchMessage(m: Message, targetId: string | null): boolean {
  return targetId ? m.id === targetId : !!m.streaming;
}

export default function ChatPanel({ bookId, chapterIdx, paragraphIdx }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const sessionIdRef = useRef(sessionId);
  const paragraphIdxRef = useRef(paragraphIdx);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    paragraphIdxRef.current = paragraphIdx;
  }, [paragraphIdx]);

  // Abort in-flight SSE on unmount or chapter/book change
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      setStreaming(false);
    };
  }, [bookId, chapterIdx]);

  // Load chat history for current chapter
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await api.getChatSession(bookId, chapterIdx);
        if (cancelled) return;
        setSessionId(resp.session.id);
        const turnsResp = await api.getChatTurns(resp.session.id);
        if (cancelled) return;
        const loaded: Message[] = [];
        const items = [...turnsResp.items].reverse();
        for (const t of items) {
          loaded.push({
            id: nextId('u'),
            role: 'user',
            text: t.user_msg,
          });
          if (t.ai_msg) {
            loaded.push({
              id: nextId('a'),
              role: 'assistant',
              text: t.ai_msg,
            });
          }
        }
        setMessages(loaded);
      } catch {
        // History loading is best-effort
      }
    })();
    return () => { cancelled = true; };
  }, [bookId, chapterIdx]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const createStreamCallbacks = useCallback(
    (targetId: string | null) => ({
      onStarted: (d: { session_id?: number }) => {
        if (!sessionIdRef.current && d.session_id) {
          setSessionId(d.session_id);
        }
      },
      onDelta: (_turnId: number, delta: string) => {
        setMessages((prev) =>
          prev.map((m) =>
            matchMessage(m, targetId)
              ? { ...m, text: m.text + delta }
              : m,
          ),
        );
      },
      onDone: (data: { ai_msg: string }) => {
        setMessages((prev) =>
          prev.map((m) =>
            matchMessage(m, targetId)
              ? { ...m, text: data.ai_msg, streaming: false, failed: false, retryPayload: undefined }
              : m,
          ),
        );
        setStreaming(false);
        abortRef.current = null;
        inputRef.current?.focus();
      },
      onError: () => {
        setMessages((prev) =>
          prev.map((m) =>
            matchMessage(m, targetId)
              ? { ...m, streaming: false, failed: true }
              : m,
          ),
        );
        setStreaming(false);
        abortRef.current = null;
      },
    }),
    [],
  );

  const doStream = useCallback(
    (msg: string) => {
      setStreaming(true);

      const assistantId = nextId('a');
      const userMsg: Message = {
        id: nextId('u'),
        role: 'user',
        text: msg,
      };
      const assistantMsg: Message = {
        id: assistantId,
        role: 'assistant',
        text: '',
        streaming: true,
        retryPayload: { userMsg: msg },
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      const controller = api.streamChat(
        bookId,
        chapterIdx,
        paragraphIdxRef.current,
        msg,
        sessionIdRef.current,
        createStreamCallbacks(assistantId),
      );
      abortRef.current = controller;
    },
    [bookId, chapterIdx, createStreamCallbacks],
  );

  const handleSend = useCallback(() => {
    const msg = input.trim();
    if (!msg || streaming) return;

    setInput('');
    doStream(msg);
  }, [input, streaming, doStream]);

  const handleRetry = useCallback(
    (messageId: string, payload: { userMsg: string }) => {
      if (streaming) return;
      setStreaming(true);

      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? { ...m, text: '', failed: false, streaming: true }
            : m,
        ),
      );

      const controller = api.streamChat(
        bookId,
        chapterIdx,
        paragraphIdxRef.current,
        payload.userMsg,
        sessionIdRef.current,
        createStreamCallbacks(messageId),
      );
      abortRef.current = controller;
    },
    [bookId, chapterIdx, streaming, createStreamCallbacks],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <span>Chat</span>
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-empty">
            Ask about what you&apos;re reading
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg chat-msg-${m.role}`}>
            <div className={`chat-msg-content ${m.failed ? 'chat-msg-failed' : ''}`}>
              {m.text}
              {m.streaming && <span className="chat-cursor" />}
              {m.failed && m.retryPayload && (
                <button
                  className="chat-retry-btn"
                  onClick={() => handleRetry(m.id, m.retryPayload!)}
                >
                  Retry
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="chat-input-area">
        <input
          ref={inputRef}
          className="chat-input"
          type="text"
          placeholder="Ask about this passage..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          className="chat-send-btn"
          onClick={handleSend}
          disabled={streaming || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
