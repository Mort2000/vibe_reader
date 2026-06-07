import { z } from 'zod';

export const chatStartedEventSchema = z.object({
  turn_id: z.number(),
  session_id: z.number(),
  trace_id: z.string(),
});

export const chatFirstDeltaEventSchema = z.record(z.string(), z.unknown());

export const chatDeltaEventSchema = z.object({
  turn_id: z.number(),
  delta: z.string(),
});

export const chatDoneEventSchema = z.object({
  turn_id: z.number(),
  session_id: z.number(),
  ai_msg: z.string(),
  tokens_in: z.number().nullable(),
  tokens_out: z.number().nullable(),
  trace_id: z.string(),
});

export const chatErrorEventSchema = z.object({
  turn_id: z.number().optional(),
  session_id: z.number().optional(),
  code: z.string(),
  message: z.string(),
  trace_id: z.string().optional(),
  request_id: z.string().nullable().optional(),
});

export const backendEventSchema = z
  .object({
    event_id: z.string().optional(),
    event: z.string().optional(),
    request_id: z.string().nullable().optional(),
    book_id: z.number().optional(),
    chapter_idx: z.number().optional(),
    paragraph_idx: z.number().optional(),
    window_id: z.number().optional(),
    job_id: z.number().optional(),
    trace_id: z.string().optional(),
    verify_run_id: z.string().optional(),
    verify_scenario_id: z.string().optional(),
    verify_step_id: z.string().optional(),
    created_at: z.string().optional(),
    error: z.string().optional(),
    status: z.string().optional(),
  })
  .passthrough();

export type ChatStartedEvent = z.infer<typeof chatStartedEventSchema>;
export type ChatFirstDeltaEvent = z.infer<typeof chatFirstDeltaEventSchema>;
export type ChatDeltaEvent = z.infer<typeof chatDeltaEventSchema>;
export type ChatDoneEvent = z.infer<typeof chatDoneEventSchema>;
export type ChatErrorEvent = z.infer<typeof chatErrorEventSchema>;
