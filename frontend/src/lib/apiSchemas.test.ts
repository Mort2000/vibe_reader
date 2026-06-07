import { describe, expect, it } from 'vitest';

import {
  backendEventSchema,
  chatDeltaEventSchema,
  chatDoneEventSchema,
} from './apiSchemas';

describe('apiSchemas', () => {
  it('accepts a complete chat.done event', () => {
    const parsed = chatDoneEventSchema.safeParse({
      turn_id: 7,
      session_id: 3,
      ai_msg: '回答完成',
      tokens_in: 42,
      tokens_out: null,
      trace_id: 'trace-1',
    });

    expect(parsed.success).toBe(true);
  });

  it('rejects malformed chat.delta events before UI callbacks receive them', () => {
    const parsed = chatDeltaEventSchema.safeParse({
      turn_id: 7,
      delta: 12,
    });

    expect(parsed.success).toBe(false);
  });

  it('keeps backend events typed while allowing forward-compatible fields', () => {
    const parsed = backendEventSchema.safeParse({
      event: 'window.done',
      book_id: 1,
      chapter_idx: 2,
      window_id: 9,
      future_field: { ok: true },
    });

    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.event).toBe('window.done');
      expect(parsed.data.future_field).toEqual({ ok: true });
    }
  });
});
