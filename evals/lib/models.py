"""
One client interface for all providers. The contract:

    client.chat(model, messages, temperature, max_tokens=...) -> ChatResponse

A `ChatResponse` always carries text + token usage + per-call latency, so
the harness can compute tk/s and the per-run logs can show the real cost
of every call.

Three implementations:
  - `OpenAICompatibleClient` for any OpenAI-compatible /v1/chat/completions
    endpoint (Ollama, vLLM, OpenAI, OpenRouter, proxies, ...).
  - `AnthropicClient` for Anthropic's native Messages API. System messages
    are pulled out into the `system` field, as the API requires.
  - `StubClient` returns canned responses driven by a small policy. Token
    counts are crudely estimated from string length so the rest of the
    pipeline still has non-zero numbers to chew on.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


_THINK_BLOCK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)
_ONLY_THINKING_MARKER = "(only thinking content returned)"


def strip_think(text: str) -> str:
    """Remove `<think>…</think>` blocks (and an unterminated trailing one).

    Anything the harness *acts on* — CALC/SYMB extraction, FINAL_ANSWER
    parsing, card-name matching, the assistant turn fed back as history —
    must come from the answer, never from the reasoning. A `CALC:` the model
    merely *considers* while thinking is not a tool call, and a
    `FINAL_ANSWER:` that only appears inside `<think>` is not an answer.
    """
    out = _THINK_BLOCK_RE.sub("", text)
    if out.lstrip().startswith(_ONLY_THINKING_MARKER):
        return ""
    return out.strip()


# ---------------------------------------------------------------------------
# Common return type.
# ---------------------------------------------------------------------------
@dataclass
class ChatResponse:
    """`text` is the full trace for logs and the method judge — for Ollama
    thinking runs it is `<think>reasoning</think>` + the answer. `answer` is
    what the harness parses and feeds back as history: the model's content
    with any reasoning removed."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    # Answer-only content when the provider returns reasoning separately
    # (Ollama's `message.thinking`). None → derive it from `text`.
    content: Optional[str] = None
    thinking: str = ""

    @property
    def answer(self) -> str:
        if self.content is not None:
            return self.content
        return strip_think(self.text)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def tokens_per_second(self) -> float:
        """Completion tk/s — the relevant generation rate, not throughput."""
        if self.latency_s <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / self.latency_s


# ---------------------------------------------------------------------------
# OpenAI-compatible (covers Ollama, vLLM, OpenAI, OpenRouter, ...).
# Late-imports `openai` so tests pass without it installed.
# ---------------------------------------------------------------------------
class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str],
        default_max_tokens: int = 1024,
        extra_body: Optional[dict[str, Any]] = None,
    ):
        self._base_url = base_url
        self._api_key = api_key
        self._default_max_tokens = default_max_tokens
        # Merged into every request body. Used to pass Ollama-specific
        # options (num_ctx, num_gpu, keep_alive) through the OpenAI-compat
        # shim. Inert for OpenRouter / OpenAI (extra fields are ignored).
        self._extra_body = extra_body or {}
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise RuntimeError(
                    "`openai` package not installed. `uv sync` or `pip install openai` "
                    "(or use --stub to run without it)."
                ) from e
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key or "stub")
        return self._client

    def close(self) -> None:
        """Release the underlying httpx connection pool. Idempotent."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        stop: Optional[list[str]] = None,
        disable_thinking: Optional[bool] = None,   # not honored by OpenAI-compat — accepted for signature compat
    ) -> ChatResponse:
        client = self._get_client()
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or self._default_max_tokens,
            stop=stop,
            extra_body=self._extra_body or None,
        )
        latency = time.monotonic() - t0
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return ChatResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_s=latency,
        )


# ---------------------------------------------------------------------------
# Ollama native client. Uses /api/chat directly (not the OpenAI-compat shim).
#
# Why: Ollama's OpenAI-compat layer silently drops Ollama-specific fields
# like `think`, `options.num_ctx`, `options.num_gpu`, and `keep_alive`. For
# Qwen3 thinking models this is a deal-breaker — `think: false` only takes
# effect on /api/chat. So for any local Ollama endpoint we bypass the SDK
# and talk to /api/chat with urllib (no extra deps).
# ---------------------------------------------------------------------------
class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        default_max_tokens: int = 1024,
        extra_body: Optional[dict[str, Any]] = None,
    ):
        # Accept either http://host:port or http://host:port/v1 — strip the
        # trailing /v1 if present so we hit /api/chat cleanly.
        self._base_url = base_url.rstrip("/")
        if self._base_url.endswith("/v1"):
            self._base_url = self._base_url[:-3]
        self._default_max_tokens = default_max_tokens
        self._extra_body = extra_body or {}

    def close(self) -> None:  # parity with other clients
        pass

    def warmup(self, model: str) -> dict[str, Any]:
        """Load the model into memory (and pin it via keep_alive) BEFORE any
        timed work, so the first real call doesn't pay a cold load and the
        model stays resident for the whole sweep.

        Sends an empty-messages request — Ollama treats this as a pure load
        (done_reason "load"), no generation. Carries the SAME extra_body
        (options + keep_alive) as real calls, so the resident copy matches
        the load params every subsequent call uses → no reload between cells.

        Returns a small status dict; never raises (warmup is best-effort).
        """
        import json as _json
        import urllib.request

        body: dict[str, Any] = {"model": model, "messages": [], "stream": False}
        for k, v in self._extra_body.items():
            body[k] = v
        body.setdefault("keep_alive", "30m")
        req = urllib.request.Request(
            url=self._base_url + "/api/chat",
            data=_json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                payload = _json.loads(r.read().decode("utf-8"))
            return {
                "loaded": True,
                "latency_s": round(time.monotonic() - t0, 2),
                "keep_alive": body.get("keep_alive"),
                "done_reason": payload.get("done_reason"),
            }
        except Exception as e:  # endpoint down / old Ollama — let the run proceed
            return {"loaded": False, "error": repr(e), "keep_alive": body.get("keep_alive")}

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        stop: Optional[list[str]] = None,
        disable_thinking: Optional[bool] = None,
    ) -> ChatResponse:
        """Talk to Ollama's /api/chat.

        `disable_thinking`:
          - True  → request body sets `think: false` (overrides any extra_body)
          - False → request body sets `think: true`  (forces thinking even if
                    extra_body has think:false)
          - None  → defer to extra_body's value (or Ollama's default)
        This is the lever that has to win over config-level extra_body to
        support per-condition `_T` / `_NT` codes.
        """
        import json as _json
        import urllib.request

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        for k, v in self._extra_body.items():
            body[k] = v
        options = dict(body.get("options") or {})
        options.setdefault("temperature", temperature)
        options.setdefault("num_predict", max_tokens or self._default_max_tokens)
        if stop:
            options.setdefault("stop", stop)
        body["options"] = options

        # Per-call thinking toggle OVERRIDES extra_body. Critical: without
        # this, `think: false` baked into config silently suppresses every
        # _T condition. We saw that bug in 002 — same token counts and zero
        # `<think>` tags across both _T and _NT.
        if disable_thinking is True:
            body["think"] = False
        elif disable_thinking is False:
            body["think"] = True

        req = urllib.request.Request(
            url=self._base_url + "/api/chat",
            data=_json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=600) as r:
            payload = _json.loads(r.read().decode("utf-8"))
        latency = time.monotonic() - t0

        msg = payload.get("message") or {}
        content = (msg.get("content") or "").strip()
        thinking = (msg.get("thinking") or "").strip()
        # If we asked for thinking, surface it in the trace prefixed so the
        # judge / per-run log can see what the model reasoned about. Only
        # `content` is the answer the harness parses (ChatResponse.answer).
        text = content
        if thinking and disable_thinking is False:
            text = f"<think>\n{thinking}\n</think>\n\n{content}"
        elif not content and thinking:
            # Defensive: model emitted only thinking, no answer.
            text = f"{_ONLY_THINKING_MARKER}\n<think>\n{thinking}\n</think>"
        prompt_tokens = int(payload.get("prompt_eval_count") or 0)
        completion_tokens = int(payload.get("eval_count") or 0)
        return ChatResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_s=latency,
            content=content,
            thinking=thinking,
        )


# ---------------------------------------------------------------------------
# Anthropic native (Claude).
# ---------------------------------------------------------------------------
class AnthropicClient:
    def __init__(self, *, api_key: Optional[str], default_max_tokens: int = 2048):
        self._api_key = api_key
        self._default_max_tokens = default_max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise RuntimeError(
                    "`anthropic` package not installed. `uv sync` or `pip install anthropic` "
                    "(or use --stub to run without it)."
                ) from e
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        stop: Optional[list[str]] = None,
        disable_thinking: Optional[bool] = None,   # Anthropic ignores; signature compat.
    ) -> ChatResponse:
        client = self._get_client()
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        user_assistant = [m for m in messages if m["role"] != "system"]
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": temperature,
            "messages": user_assistant,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if stop:
            kwargs["stop_sequences"] = stop
        t0 = time.monotonic()
        resp = client.messages.create(**kwargs)
        latency = time.monotonic() - t0
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = (block.text or "").strip()
                break
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return ChatResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_s=latency,
        )


# ---------------------------------------------------------------------------
# Stub client. Returns canned responses, optionally chosen by a policy.
# ---------------------------------------------------------------------------
@dataclass
class StubClient:
    """
    Pluggable stub. The whole pipeline runs against it without any API keys.

    A `policy(messages, model, ctx) -> str` callable inspects the
    conversation so far and returns the next assistant reply text. The stub
    wraps it in a ChatResponse with crudely-estimated token counts so
    downstream logs/tracking show non-zero numbers.

    If `policy` is None, `default_response` is returned verbatim.
    """

    policy: Optional[Callable[[list[dict[str, str]], str, dict[str, Any]], str]] = None
    default_response: str = "FINAL_ANSWER: 0 m/s"
    calls: list[dict[str, Any]] = field(default_factory=list)
    _state: dict[str, Any] = field(default_factory=dict)

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        stop: Optional[list[str]] = None,
        disable_thinking: Optional[bool] = None,   # stub: recorded, otherwise ignored
    ) -> ChatResponse:
        self.calls.append({
            "model": model,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop,
            "disable_thinking": disable_thinking,
        })
        t0 = time.monotonic()
        text = self.policy(messages, model, self._state) if self.policy else self.default_response
        latency = time.monotonic() - t0
        # ~4 chars per token is the conventional rough estimate.
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        return ChatResponse(
            text=text,
            prompt_tokens=max(1, prompt_chars // 4),
            completion_tokens=max(1, len(text) // 4),
            latency_s=max(latency, 1e-4),
        )


# ---------------------------------------------------------------------------
# make_client: dispatch from cfg["provider"].
# ---------------------------------------------------------------------------
def make_client(cfg: dict[str, Any], *, stub: bool = False) -> Any:
    """
    Build a client from a model section of config.yaml.

    Dispatches on cfg["provider"]:
      - "openai" / "openrouter" → OpenAI-compatible HTTP (Ollama, vLLM,
        OpenAI proper, OpenRouter — they all use the same protocol).
      - "anthropic" → native Messages API.

    With stub=True, returns a StubClient regardless.
    """
    if stub:
        return StubClient()
    api_key = os.environ.get(cfg.get("api_key_env") or "", "") or None
    provider = (cfg.get("provider") or "openai").lower()
    max_tokens = cfg.get("max_tokens", 1024)
    if provider == "anthropic":
        return AnthropicClient(api_key=api_key, default_max_tokens=max_tokens)
    if provider == "ollama":
        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError("ollama provider requires a base_url in config")
        return OllamaClient(
            base_url=base_url,
            default_max_tokens=max_tokens,
            extra_body=cfg.get("extra_body") or None,
        )
    if provider in {"openai", "openrouter"}:
        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError(f"{provider} provider requires a base_url in config")
        return OpenAICompatibleClient(
            base_url=base_url,
            api_key=api_key,
            default_max_tokens=max_tokens,
            extra_body=cfg.get("extra_body") or None,
        )
    raise ValueError(f"unknown provider: {provider!r}")


# ---------------------------------------------------------------------------
# Tiny policy helpers — used by tests / stub runs.
# ---------------------------------------------------------------------------
def echo_calc_then_finalize(*, calc_expr: str, final: str) -> Callable[..., str]:
    """A two-step policy: first reply does one CALC, second emits FINAL_ANSWER."""
    def _policy(messages, model, ctx):
        step = ctx.get("step", 0)
        ctx["step"] = step + 1
        if step == 0:
            return f"Let me compute.\nCALC: {calc_expr}"
        return final
    return _policy
