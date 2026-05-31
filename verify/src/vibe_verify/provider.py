"""Generic OpenAI-compatible stub sidecar and provider mode preparation."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .evidence import EvidenceHub, normalize_usage
from .models import AgentInvocation, Correlation, ToolCall, jsonable, optional_int

_TARGETS = re.compile(r"comment_target_paragraphs\s*=\s*\[([^\]]+)\]")
_RANGE = re.compile(r"^\s*(-?\d+)\s*\.\.=\s*(-?\d+)\s*$")
STUB_IMPLEMENTATION_VERSION = "generic-stub-v2"


@dataclass(frozen=True)
class StubProfile:
    """Deterministic generic stub behavior."""

    name: str = "mvp_default"
    seed: int = 20260522
    model: str = "vibe-reader-stub"
    fault: str = ""
    max_comments_per_window: int = 3
    chat_ttft_ms: int = 0
    chat_tps: int = 1000
    timeout_delay_s: float = 60.0

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StubReply:
    """HTTP-level response produced by the generic stub."""

    status: int
    body: dict[str, Any]
    stream: bool = False
    chunks: list[str] = field(default_factory=list)
    ttft_ms: int = 0
    tps: int = 1000


@dataclass(frozen=True)
class ProviderSession:
    """Prepared model environment for one verify run."""

    mode: str
    backend_env: dict[str, str]
    model: str
    usage_source: str
    stub_profile_hash: str = ""
    sidecar: StubSidecar | None = None


class GenericStubRouter:
    """Route known Agent requests and reject unknown prompt contracts."""

    def __init__(
        self,
        profile: StubProfile | None = None,
        *,
        evidence: EvidenceHub | None = None,
        default_correlation: Correlation | None = None,
    ):
        self.profile = profile or StubProfile()
        self.evidence = evidence
        self.default_correlation = default_correlation or Correlation(run_id="")
        self.journal: list[dict[str, Any]] = []
        self._provider_error_emitted = False

    def route(
        self, request: dict[str, Any], headers: dict[str, str] | None = None
    ) -> StubReply:
        started = time.monotonic()
        prompt = last_user_content(request)
        agent = classify_request(request, prompt)
        reply = self._reply(request, prompt, agent)
        duration_ms = (time.monotonic() - started) * 1000
        correlation = correlation_from_request(
            request, headers or {}, default=self.default_correlation
        )
        record = {
            "agent": agent,
            "correlation": jsonable(correlation),
            "request": request,
            "response": reply.body,
            "status": reply.status,
            "stream": reply.stream,
            "usage_source": "estimate",
            "duration_ms": duration_ms,
            "profile": self.profile.name,
            "profile_hash": self.profile.digest,
            "seed": self.profile.seed,
            "model": self.profile.model,
            "stub_version": STUB_IMPLEMENTATION_VERSION,
        }
        self.journal.append(record)
        if self.evidence is not None:
            self.evidence.record_stub_journal(record)
            self.evidence.record_invocation(
                invocation_from_stub(
                    request=request,
                    reply=reply,
                    agent=agent,
                    profile=self.profile,
                    duration_ms=duration_ms,
                    correlation=correlation,
                )
            )
        return reply

    def _reply(self, request: dict[str, Any], prompt: str, agent: str) -> StubReply:
        if self.profile.fault == "timeout":
            time.sleep(self.profile.timeout_delay_s)
            return error_reply(504, "stub_timeout")
        if self.profile.fault == "provider_error" and not self._provider_error_emitted:
            self._provider_error_emitted = True
            return error_reply(429, "stub_rate_limit")
        if agent == "S0Ping":
            return completion_reply("ok", request, self.profile)
        if agent == "ParagraphCommentAgent":
            if completed_tool_round(request, {"emit_comment"}):
                return completion_reply(
                    "[stub] comments accepted", request, self.profile
                )
            return self._comment_reply(request, prompt)
        if agent == "ContextCompactionAgent":
            if completed_tool_round(request, {"emit_chapter_compressed_summary"}):
                return completion_reply(
                    "[stub] compaction accepted", request, self.profile
                )
            payload = {
                "summary": f"[stub:{self.profile.name}] rolling summary",
                "anchor_excerpts": [],
            }
            return tool_reply(
                [
                    tool_call(
                        "emit_chapter_compressed_summary",
                        {"payload": payload},
                        profile=self.profile,
                    )
                ],
                request,
                self.profile,
            )
        if agent == "ReadingChatAgent":
            content = f"[stub:{self.profile.name}][chat] answer"
            if request.get("stream"):
                return stream_reply(content, request, self.profile)
            return completion_reply(content, request, self.profile)
        return error_reply(422, "unmatched_stub_request")

    def _comment_reply(self, request: dict[str, Any], prompt: str) -> StubReply:
        targets = parse_comment_targets(prompt)
        if not targets:
            return error_reply(422, "missing_comment_targets")
        if self.profile.fault == "no_call":
            return completion_reply("[stub] no tool call", request, self.profile)
        selected = targets[: self.profile.max_comments_per_window]
        if self.profile.fault == "invalid_tool_args":
            selected = selected[:1] or [0]
            arguments = {"paragraph_idx": selected[0], "comment_type": "invalid"}
            tool_calls = [tool_call("emit_comment", arguments, profile=self.profile)]
        else:
            tool_calls = [
                tool_call(
                    "emit_comment",
                    {
                        "paragraph_idx": target,
                        "comment": f"[stub:{self.profile.name}] comment P{target}",
                        "comment_type": "observation",
                    },
                    profile=self.profile,
                )
                for target in selected
            ]
        return tool_reply(tool_calls, request, self.profile)


def classify_prompt(prompt: str) -> str:
    if prompt.strip() == "Reply with exactly: ok":
        return "S0Ping"
    if "comment_target_paragraphs" in prompt:
        return "ParagraphCommentAgent"
    if "context compaction" in prompt.lower() or "ContextCompactionAgent" in prompt:
        return "ContextCompactionAgent"
    if "mode = chat" in prompt.lower() or "ReadingChatAgent" in prompt:
        return "ReadingChatAgent"
    return "Unknown"


def classify_request(request: dict[str, Any], prompt: str | None = None) -> str:
    names = request_tool_names(request)
    if "emit_comment" in names:
        return "ParagraphCommentAgent"
    if "emit_chapter_compressed_summary" in names:
        return "ContextCompactionAgent"
    return classify_prompt(last_user_content(request) if prompt is None else prompt)


def request_tool_names(request: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tool in request.get("tools") or []:
        function = tool.get("function") or {}
        name = function.get("name")
        if name:
            names.add(str(name))
    return names


def parse_comment_targets(prompt: str) -> list[int]:
    match = _TARGETS.search(prompt)
    if not match:
        return []
    result: list[int] = []
    for token in match.group(1).split(","):
        range_match = _RANGE.match(token)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            low, high = sorted((start, end))
            result.extend(range(low, high + 1))
        else:
            result.append(int(token.strip()))
    return sorted(set(result))


def last_user_content(request: dict[str, Any]) -> str:
    messages = request.get("messages") or []
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def completed_tool_round(request: dict[str, Any], names: set[str]) -> bool:
    """Return true once the model sees tool results for a prior stub tool call."""
    messages = request.get("messages") or []
    tool_call_ids: set[str] = set()
    for message in messages:
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            if function.get("name") in names and item.get("id"):
                tool_call_ids.add(str(item["id"]))
    if not tool_call_ids:
        return False
    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_call_id = message.get("tool_call_id")
        if tool_call_id is None or str(tool_call_id) in tool_call_ids:
            return True
    return False


def correlation_from_request(
    request: dict[str, Any],
    headers: dict[str, str],
    *,
    default: Correlation | None = None,
) -> Correlation:
    base = default or Correlation(run_id="")
    raw_metadata = request.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    lower_headers = {key.lower(): value for key, value in headers.items()}

    def pick(*names: str) -> Any:
        for name in names:
            if name in metadata and metadata[name] is not None:
                return metadata[name]
            header_name = name.replace("_", "-")
            if header_name in lower_headers:
                return lower_headers[header_name]
        return ""

    book_id = optional_int(pick("book_id"))
    chapter_idx = optional_int(pick("chapter_idx"))
    window_id = optional_int(pick("window_id"))
    job_id = optional_int(pick("job_id"))
    return Correlation(
        run_id=str(
            pick("run_id", "verify_run_id", "x_verify_run_id") or base.run_id
        ),
        scenario_id=str(
            pick("scenario_id", "verify_scenario_id", "x_verify_scenario_id")
            or base.scenario_id
        ),
        step_id=str(
            pick("step_id", "verify_step_id", "x_verify_step_id") or base.step_id
        ),
        request_id=str(pick("request_id", "x_request_id") or base.request_id),
        trace_id=str(pick("trace_id", "x_trace_id") or base.trace_id),
        book_id=book_id if book_id is not None else base.book_id,
        chapter_idx=chapter_idx if chapter_idx is not None else base.chapter_idx,
        window_id=window_id if window_id is not None else base.window_id,
        job_id=job_id if job_id is not None else base.job_id,
        agent_invocation_id=str(
            pick("agent_invocation_id") or base.agent_invocation_id
        ),
        context_hash=str(pick("context_hash") or base.context_hash),
        prompt_hash=str(pick("prompt_hash") or base.prompt_hash),
    )


def estimate_usage(request: dict[str, Any], output: str) -> dict[str, int]:
    raw = json.dumps(request.get("messages") or [], ensure_ascii=False)
    prompt_tokens = max(1, len(raw) // 4)
    completion_tokens = max(1, len(output) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def base_reply(
    *,
    content: str | None,
    request: dict[str, Any],
    profile: StubProfile,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage_output = content or json.dumps(tool_calls or [], ensure_ascii=False)
    return {
        "id": "chatcmpl-stub-"
        + stable_id(
            profile, {"request": request, "content": usage_output}, "completion"
        ),
        "object": "chat.completion",
        "model": profile.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": estimate_usage(request, usage_output),
    }


def completion_reply(
    content: str, request: dict[str, Any], profile: StubProfile
) -> StubReply:
    return StubReply(
        status=200, body=base_reply(content=content, request=request, profile=profile)
    )


def tool_reply(
    calls: list[dict[str, Any]], request: dict[str, Any], profile: StubProfile
) -> StubReply:
    return StubReply(
        status=200,
        body=base_reply(
            content=None, request=request, profile=profile, tool_calls=calls
        ),
    )


def stream_reply(
    content: str, request: dict[str, Any], profile: StubProfile
) -> StubReply:
    pieces = [content[index : index + 8] for index in range(0, len(content), 8)]
    usage = estimate_usage(request, content)
    chunks = [
        json.dumps(
            {
                "id": "chatcmpl-stub-stream-"
                + stable_id(profile, {"request": request, "piece": piece}, "stream"),
                "object": "chat.completion.chunk",
                "model": profile.model,
                "choices": [{"index": 0, "delta": {"content": piece}}],
            },
            ensure_ascii=False,
        )
        for piece in pieces
    ]
    chunks.append(
        json.dumps(
            {
                "id": "chatcmpl-stub-stream-"
                + stable_id(profile, {"request": request, "done": True}, "stream"),
                "object": "chat.completion.chunk",
                "model": profile.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            }
        )
    )
    return StubReply(
        status=200,
        body={"usage": usage, "content": content},
        stream=True,
        chunks=chunks,
        ttft_ms=profile.chat_ttft_ms,
        tps=profile.chat_tps,
    )


def error_reply(status: int, code: str) -> StubReply:
    return StubReply(
        status=status,
        body={"error": {"message": code, "type": "stub_error", "code": code}},
    )


def tool_call(
    name: str, arguments: dict[str, Any], *, profile: StubProfile
) -> dict[str, Any]:
    return {
        "id": "call_"
        + stable_id(profile, {"name": name, "arguments": arguments}, "tool"),
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def invocation_from_stub(
    *,
    request: dict[str, Any],
    reply: StubReply,
    agent: str,
    profile: StubProfile,
    duration_ms: float,
    correlation: Correlation | None = None,
) -> AgentInvocation:
    body = reply.body
    message = (body.get("choices") or [{}])[0].get("message") or {}
    calls = [
        ToolCall(
            id=item.get("id", ""),
            name=(item.get("function") or {}).get("name", ""),
            arguments=json.loads((item.get("function") or {}).get("arguments", "{}")),
        )
        for item in message.get("tool_calls") or []
    ]
    return AgentInvocation(
        id="inv_" + stable_id(profile, {"request": request, "response": body}, agent),
        agent=agent,
        prompt_messages=list(request.get("messages") or []),
        response=body,
        usage=normalize_usage(
            body.get("usage"), source="estimate", agent=agent, model=profile.model
        ),
        correlation=correlation or Correlation(run_id=""),
        tool_calls=calls,
        duration_ms=duration_ms,
        error=(body.get("error") or {}).get("code", ""),
        thinking_unavailable_reason="stub does not produce reasoning",
    )


class StubSidecar:
    """Context-managed HTTP server exposing the generic stub router."""

    def __init__(
        self,
        router: GenericStubRouter | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        self.router = router or GenericStubRouter()
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("stub sidecar is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> StubSidecar:
        router = self.router

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._json(200, {"status": "ok"})
                    return
                if self.path == "/journal":
                    self._json(200, {"records": router.journal})
                    return
                self._json(404, {"error": {"code": "not_found"}})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/chat/completions":
                    self._json(404, {"error": {"code": "not_found"}})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length) or b"{}")
                reply = router.route(request, dict(self.headers))
                if reply.stream:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    if reply.ttft_ms > 0:
                        time.sleep(reply.ttft_ms / 1000)
                    for chunk in reply.chunks:
                        self.wfile.write(f"data: {chunk}\n\n".encode())
                        self.wfile.flush()
                        if reply.tps > 0:
                            time.sleep(len(chunk) / reply.tps)
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                self._json(reply.status, reply.body)

            def _json(self, status: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> StubSidecar:
        return self.start()

    def __exit__(self, *_args: Any) -> None:
        self.stop()


class ProviderHarness:
    """Prepare either the default Stub sidecar or explicit real provider config."""

    def __init__(
        self,
        *,
        sidecar_factory: Callable[[GenericStubRouter], StubSidecar] = StubSidecar,
    ):
        self.sidecar_factory = sidecar_factory

    def prepare(
        self,
        *,
        mode: str,
        evidence: EvidenceHub | None = None,
        profile: StubProfile | None = None,
        default_correlation: Correlation | None = None,
        real_base_url: str = "",
        real_api_key: str = "",
        real_model: str = "",
        real_budget: Any = None,
    ) -> ProviderSession:
        if mode == "stub":
            active_profile = profile or StubProfile()
            sidecar = self.sidecar_factory(
                GenericStubRouter(
                    active_profile,
                    evidence=evidence,
                    default_correlation=default_correlation,
                )
            ).start()
            return ProviderSession(
                mode="stub",
                backend_env={
                    "VIBE_READER_LLM_BASE_URL": sidecar.base_url,
                    "VIBE_READER_LLM_API_KEY": "verify-stub-key",
                    "VIBE_READER_LLM_MODEL": active_profile.model,
                },
                model=active_profile.model,
                usage_source="estimate",
                stub_profile_hash=active_profile.digest,
                sidecar=sidecar,
            )
        if mode == "real":
            if not all((real_base_url, real_api_key, real_model)):
                raise ValueError("real provider requires base_url, api_key and model")
            validate_real_budget(real_budget)
            return ProviderSession(
                mode="real",
                backend_env={
                    "VIBE_READER_LLM_BASE_URL": real_base_url,
                    "VIBE_READER_LLM_API_KEY": real_api_key,
                    "VIBE_READER_LLM_MODEL": real_model,
                },
                model=real_model,
                usage_source="provider",
            )
        raise ValueError(f"unsupported provider mode: {mode}")

    def cleanup(self, session: ProviderSession) -> None:
        if session.sidecar is not None:
            session.sidecar.stop()


def update_default_correlation(
    session: ProviderSession | None, correlation: Correlation
) -> None:
    """Refresh fallback correlation for local stub requests.

    Real providers must receive correlation from backend-emitted metadata. The
    local stub can safely use this fallback when backend does not forward it.
    """
    if session is not None and session.sidecar is not None:
        session.sidecar.router.default_correlation = correlation


def stable_id(profile: StubProfile, payload: Any, label: str) -> str:
    raw = json.dumps(
        {
            "profile_hash": profile.digest,
            "seed": profile.seed,
            "model": profile.model,
            "label": label,
            "payload": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def validate_real_budget(budget: Any) -> None:
    if budget is None:
        raise ValueError("real provider requires explicit budget")
    values = budget if isinstance(budget, dict) else vars(budget)
    required = ("max_calls", "max_tokens", "max_duration_s", "max_cost_usd")
    missing = [name for name in required if values.get(name) is None]
    if missing:
        raise ValueError("real provider budget missing: " + ", ".join(missing))
    invalid = [name for name in required if float(values[name]) <= 0]
    if invalid:
        raise ValueError("real provider budget must be positive: " + ", ".join(invalid))
