#!/usr/bin/env node
/**
 * Vibe Reader verify-owned AIMock server with request-aware paragraph comment handlers.
 *
 * Usage:
 *   node server.mjs --profile mvp_default --port 4010 --strict
 */

import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { LLMock } from "@copilotkit/aimock";

const __dirname = dirname(fileURLToPath(import.meta.url));

const { values: cli } = parseArgs({
  options: {
    profile: { type: "string", default: "mvp_default" },
    port: { type: "string", default: "4010" },
    host: { type: "string", default: "127.0.0.1" },
    strict: { type: "boolean", default: true },
    seed: { type: "string", default: "20260522" },
  },
});

const profileName = cli.profile;
const profilePath = join(__dirname, "profiles", `${profileName}.json`);
if (!existsSync(profilePath)) {
  console.error(`Profile not found: ${profilePath}`);
  process.exit(1);
}

/** @type {Record<string, unknown>} */
const profile = JSON.parse(readFileSync(profilePath, "utf8"));
const seed = Number(cli.seed ?? profile.seed ?? 20260522);
const commentCfg = /** @type {Record<string, unknown>} */ (profile.comment ?? {});
const usageCfg = /** @type {Record<string, unknown>} */ (profile.usage ?? {});
const faultCfg = /** @type {Record<string, unknown>} */ (profile.fault ?? {});

/** One provider error per profile run (design §10: first 429/500 then success). */
let providerErrorOnceFired = false;

const mock = new LLMock({
  port: parseInt(String(cli.port), 10),
  host: String(cli.host),
  strict: cli.strict !== false,
  metrics: true,
});

mock.loadFixtureFile(join(__dirname, "fixtures/common/s0_ping.json"));

/**
 * @param {import("@copilotkit/aimock").MockRequest} req
 */
function lastUserContent(req) {
  const messages = req.messages ?? [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg?.role === "user") {
      return typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content ?? "");
    }
  }
  return "";
}

/**
 * @param {import("@copilotkit/aimock").MockRequest} req
 */
function hasToolResult(req) {
  return (req.messages ?? []).some((m) => m?.role === "tool");
}

/**
 * @param {string} content
 */
function parseTargetParagraphs(content) {
  const match = content.match(/comment_target_paragraphs\s*=\s*\[([\d,\s]+)\]/);
  if (!match) return [];
  return match[1]
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => Number.isFinite(n));
}

/**
 * @param {string} text
 */
function contextHash(text) {
  return createHash("sha256").update(text).digest("hex").slice(0, 8);
}

/**
 * @param {string} text
 * @param {number} inputDivisor
 * @param {number} outputDivisor
 */
function estimateUsage(text, inputDivisor = 2, outputDivisor = 2) {
  const input = Math.max(1, Math.ceil(text.length / inputDivisor));
  const output = Math.max(1, Math.ceil(16 / outputDivisor));
  return {
    prompt_tokens: input,
    completion_tokens: output,
    total_tokens: input + output,
  };
}

/**
 * @param {number[]} targets
 * @param {Record<string, unknown>} cfg
 */
function selectTargets(targets, cfg) {
  const max = Number(cfg.max_comments_per_window ?? 3);
  if (max <= 0 || targets.length === 0) return [];
  const sorted = [...targets].sort((a, b) => a - b);
  return sorted.slice(0, max);
}

/**
 * @param {number} idx
 * @param {string[]} cycle
 */
function commentTypeFor(idx, cycle) {
  if (!cycle.length) return "observation";
  return cycle[idx % cycle.length];
}

/**
 * @param {number} paragraphIdx
 * @param {string} type
 * @param {string} ctxHash
 */
function buildCommentText(paragraphIdx, type, ctxHash) {
  const marker = `[stub:${profileName}]`;
  return `${marker} P${paragraphIdx} ctx=${ctxHash} type=${type}`;
}

/**
 * @param {number} paragraphIdx
 * @param {string} variant
 */
function invalidPayload(paragraphIdx, variant) {
  switch (variant) {
    case "out_of_range_idx":
      return {
        paragraph_idx: paragraphIdx + 99999,
        comment: `[stub:${profileName}] out_of_range`,
        comment_type: "observation",
      };
    case "empty_comment":
      return {
        paragraph_idx: paragraphIdx,
        comment: "   ",
        comment_type: "observation",
      };
    case "invalid_comment_type":
    default:
      return {
        paragraph_idx: paragraphIdx,
        comment: `[stub:${profileName}] invalid type`,
        comment_type: "not_a_valid_type",
      };
  }
}

/**
 * @param {import("@copilotkit/aimock").MockRequest} req
 */
function isCommentPrompt(req) {
  const content = lastUserContent(req);
  return content.includes("comment_target_paragraphs") && content.includes("<CURRENT_WINDOW>");
}

/**
 * @param {import("@copilotkit/aimock").MockRequest} req
 */
function buildCommentToolCalls(req) {
  const content = lastUserContent(req);
  const targets = parseTargetParagraphs(content);
  const ctxHash = contextHash(content);
  const mode = String(commentCfg.mode ?? "tool_call");
  const cycle = /** @type {string[]} */ (commentCfg.comment_type_cycle ?? ["observation"]);
  const inputDivisor = Number(usageCfg.input_divisor ?? 2);
  const outputDivisor = Number(usageCfg.output_divisor ?? 2);

  if (mode === "no_call") {
    return {
      content: `[stub:${profileName}] no tool call for targets=[${targets.join(",")}]`,
      usage: estimateUsage(content, inputDivisor, outputDivisor),
    };
  }

  const selected = selectTargets(targets, commentCfg);
  /** @type {Array<{id: string, name: string, arguments: Record<string, unknown>}>} */
  const toolCalls = [];

  if (mode === "out_of_target") {
    const anchor = selected[0] ?? targets[0] ?? 0;
    const offset = Number(commentCfg.out_of_target_offset ?? 500);
    toolCalls.push({
      id: "call_emit_comment_out_of_target",
      name: "emit_comment",
      arguments: {
        payload: {
          paragraph_idx: anchor + offset,
          comment: `[stub:${profileName}] out_of_target`,
          comment_type: "observation",
        },
      },
    });
    return { toolCalls, usage: estimateUsage(content, inputDivisor, outputDivisor) };
  }

  if (mode === "invalid_tool_args") {
    const variant = String(commentCfg.invalid_variant ?? "invalid_comment_type");
    const paragraphIdx = selected[0] ?? targets[0] ?? 0;
    toolCalls.push({
      id: `call_emit_comment_invalid_${variant}`,
      name: "emit_comment",
      arguments: {
        payload: invalidPayload(paragraphIdx, variant),
      },
    });
    return { toolCalls, usage: estimateUsage(content, inputDivisor, outputDivisor) };
  }

  selected.forEach((paragraphIdx, i) => {
    const commentType = commentTypeFor(i, cycle);
    toolCalls.push({
      id: `call_emit_comment_${paragraphIdx}`,
      name: "emit_comment",
      arguments: {
        payload: {
          paragraph_idx: paragraphIdx,
          comment: buildCommentText(paragraphIdx, commentType, ctxHash),
          comment_type: commentType,
        },
      },
    });
  });

  return {
    toolCalls,
    usage: estimateUsage(content, inputDivisor, outputDivisor),
  };
}

function maybeProviderError() {
  if (!faultCfg.provider_error_once) return null;
  if (providerErrorOnceFired) return null;
  providerErrorOnceFired = true;
  return {
    error: {
      message: "Rate limit exceeded (stub provider_error_once)",
      type: "rate_limit_error",
      code: String(faultCfg.error_code ?? "rate_limit_exceeded"),
    },
    status: Number(faultCfg.error_status ?? 429),
  };
}

// Paragraph comment: initial tool-call round
mock.on(
  {
    predicate: (req) => isCommentPrompt(req) && !hasToolResult(req),
  },
  (req) => {
    const err = maybeProviderError();
    if (err) return err;
    return buildCommentToolCalls(req);
  },
);

// Paragraph comment: post-tool follow-up (PydanticAI ignores natural language)
mock.on(
  {
    predicate: (req) => isCommentPrompt(req) && hasToolResult(req),
  },
  (req) => {
    const content = lastUserContent(req);
    const inputDivisor = Number(usageCfg.input_divisor ?? 2);
    const outputDivisor = Number(usageCfg.output_divisor ?? 2);
    return {
      content: "stub comment tool round complete",
      usage: estimateUsage(content, inputDivisor, outputDivisor),
    };
  },
);

// A3: Compaction structured output — implemented, not yet covered by verify scenarios
mock.on(
  {
    predicate: (req) => {
      const content = lastUserContent(req);
      return (
        content.includes("RollingContextSnapshotOutput") ||
        content.includes("context compaction")
      );
    },
  },
  (req) => {
    const content = lastUserContent(req);
    const ctxHash = contextHash(content);
    const maxAnchors = Number(
      /** @type {Record<string, unknown>} */ (profile.compaction ?? {}).max_anchor_excerpts ?? 3,
    );
    const anchors = [];
    for (let i = 0; i < maxAnchors; i += 1) {
      anchors.push({
        chapter_idx: 0,
        paragraph_idx: 20 + i,
        text: `anchor excerpt hash=${ctxHash}`,
        reason: "current_window",
      });
    }
    return {
      toolCalls: [
        {
          id: "call_final_result_compaction",
          name: "final_result",
          arguments: {
            summary: `[stub:${profileName}] rolling summary ctx=${ctxHash}`,
            comment_digest: "comments: P20,P21",
            chat_digest: "recent chat digest",
            anchor_excerpts: anchors,
            open_questions: [],
          },
        },
      ],
      usage: estimateUsage(content),
    };
  },
);

// A3: Reading chat — implemented with streamingProfile; scenario coverage pending
mock.on(
  {
    predicate: (req) => {
      const content = lastUserContent(req);
      return (
        content.includes("[READING_CHAT]") ||
        content.includes("ReadingChatAgent") ||
        (content.includes("当前段落") && !content.includes("comment_target_paragraphs"))
      );
    },
  },
  (req) => {
    const content = lastUserContent(req);
    const ctxHash = contextHash(content);
    const chapterMatch = content.match(/chapter[_\s=]*(\d+)/i);
    const paragraphMatch = content.match(/paragraph[_\s=]*(\d+)/i);
    const chapter = chapterMatch ? chapterMatch[1] : "0";
    const paragraph = paragraphMatch ? paragraphMatch[1] : "42";
    const chatCfg = /** @type {Record<string, unknown>} */ (profile.chat ?? {});
    const responseText = `[stub:${profileName}][chat][chapter=${chapter}][paragraph=${paragraph}] anchor=P${paragraph} recent_comment_hash=${ctxHash}`;
    const streamingProfile = chatCfg.stream
      ? {
          ttft: Number(chatCfg.ttft_ms ?? 120),
          tps: Number(chatCfg.tps ?? 80),
          jitter: 0.05,
        }
      : undefined;
    return {
      content: responseText,
      usage: estimateUsage(content),
      streamingProfile,
    };
  },
);

const url = await mock.start();
console.log(
  JSON.stringify({
    event: "aimock_started",
    profile: profileName,
    seed,
    url,
    base_url: `${url}/v1`,
    strict: cli.strict !== false,
  }),
);

async function shutdown() {
  await mock.stop();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
