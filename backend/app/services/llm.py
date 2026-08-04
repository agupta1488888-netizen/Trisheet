"""Anthropic client.

The only module that talks to the model. It transports prompts and returns
text; it holds no opinion about report content.

The model is never asked to compute, recall or estimate a figure. Callers pass
already-computed facts and ask for prose about them. That rule is enforced by
the callers — m10 builds its prompt from `Fact` objects only — but two things
here support it:

- Output is constrained to a JSON schema the caller supplies, so the model
  answers in a shape the caller can verify rather than in free prose.
- Sampling variance is held at or below `LLM_MAX_TEMPERATURE`. A writer that
  restates supplied figures has nothing to gain from variance and everything
  to lose. Not every current model accepts a sampling parameter — the newest
  reject `temperature` with HTTP 400 — so it is sent only to models that take
  one, and the same intent is otherwise carried by the low effort setting and
  the output schema.

The one exception
    `complete_json_with_web_search` grants Anthropic's hosted web-search
    tool for a single call — the one place this module lets the model reach
    outside what it was directly given. Reserved for chat's tier-4 fallback;
    every other caller uses `complete_json`.

Degradation
    The model is not a hard dependency. Every failure is raised as `LlmError`,
    and callers are expected to continue without the prose rather than fail the
    report.

Public interface
    complete_json(system, user, schema, *, purpose) -> dict
    complete_json_with_web_search(system, user, schema, *, purpose) -> dict
    is_configured() -> bool
    reset_client() -> None
    close_client() -> None

On the explicit http_client below
    AsyncAnthropic builds its own httpx.AsyncClient when none is given, and
    that construction does more than a plain one: it sets TCP keepalive
    socket_options at the transport level and mounts a transport per scheme
    from the environment's proxy variables
    (anthropic._base_client.AsyncHttpxClientWrapper). On this deployment that
    reliably failed every call with `APIConnectionError('Connection error.')`
    even though DNS, the TLS handshake and a plain httpx request to the same
    host all succeeded — diagnosed by passing a plain client to the SDK and
    watching the same call go through, which is the config below. The keepalive
    `setsockopt` calls are believed to be the specific cause: platforms with
    sandboxed or virtualised networking (this one included) can reject them
    outright, and httpcore surfaces that as an opaque connect failure rather
    than naming the socket option that was refused.
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

import httpx

from app.config import (
    LLM_CACHE_READ_MULTIPLIER,
    LLM_CACHE_WRITE_MULTIPLIER,
    LLM_EFFORT,
    LLM_INPUT_COST_PER_MTOK,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MAX_RETRIES,
    LLM_MAX_TEMPERATURE,
    LLM_MODEL,
    LLM_MODELS_ACCEPTING_TEMPERATURE,
    LLM_OUTPUT_COST_PER_MTOK,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
    LLM_WEB_SEARCH_MAX_USES,
    TOKENS_PER_MILLION,
    get_settings,
)

logger = logging.getLogger(__name__)


class LlmError(Exception):
    """Base class for every model failure. Callers catch this, not anthropic."""


class LlmNotConfiguredError(LlmError):
    """No API key is set, so there is nothing to send a prompt to."""


class LlmUnavailableError(LlmError):
    """The model could not be reached, or refused the request."""


class LlmMalformedResponseError(LlmError):
    """The model answered, but not in the shape the schema required."""


class _MessagesApi(Protocol):
    """The one endpoint this module calls."""

    async def create(self, **request: object) -> object: ...


class _AnthropicClient(Protocol):
    """The slice of the SDK surface this module depends on.

    Declared here rather than imported so the dependency is a named shape
    instead of `Any`, and so the module still type-checks where the SDK's own
    overloads do not yet describe `output_config`.
    """

    messages: _MessagesApi


_client: _AnthropicClient | None = None
#: The transport the SDK is given explicitly rather than left to build its
#: own. Held separately from `_client` because `reset_client` — used to force
#: a rebuilt Anthropic client on configuration reload — has no reason to tear
#: down a working connection pool along with it.
_http_client: httpx.AsyncClient | None = None


# --- Token accounting --------------------------------------------------------
# What a report cost is a fact about that run, so it is measured rather than
# estimated: every response's own usage block is added up, and the total is
# priced once at the rates in force. Nothing here samples or extrapolates.


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens consumed, as the API reported them."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @property
    def cost_usd(self) -> float:
        """Spend in US dollars, at the rates configured for this model.

        A cached read is billed at a fraction of the input rate and a cache
        write at a premium over it, so the four counts cannot be collapsed into
        two before pricing.
        """
        input_rate = LLM_INPUT_COST_PER_MTOK / TOKENS_PER_MILLION
        output_rate = LLM_OUTPUT_COST_PER_MTOK / TOKENS_PER_MILLION
        return (
            self.input_tokens * input_rate
            + self.output_tokens * output_rate
            + self.cache_read_tokens * input_rate * LLM_CACHE_READ_MULTIPLIER
            + self.cache_write_tokens * input_rate * LLM_CACHE_WRITE_MULTIPLIER
        )

    def plus(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class UsageLedger:
    """Running total for one report.

    Mutable on purpose: it is handed out by `usage_scope`, filled in by every
    call made inside that scope, and read once at the end.
    """

    def __init__(self) -> None:
        self.usage = TokenUsage()

    def add(self, usage: TokenUsage) -> None:
        self.usage = self.usage.plus(usage)


#: Scoped per report rather than per process. A context variable, not a global,
#: because several reports run concurrently in one worker and each asyncio task
#: inherits the context it was created in — so a report's ledger collects its
#: own calls and no one else's, with no parameter threaded through m10.
_ledger: contextvars.ContextVar[UsageLedger | None] = contextvars.ContextVar(
    "llm_usage_ledger", default=None
)


@contextmanager
def usage_scope() -> Iterator[UsageLedger]:
    """Collects the token usage of every call made inside the block."""
    ledger = UsageLedger()
    token = _ledger.set(ledger)
    try:
        yield ledger
    finally:
        _ledger.reset(token)


def _record(response: object) -> None:
    """Adds one response's usage to the ledger, when a scope is open.

    Reads defensively: a usage block missing a field is reported as zero for
    that field rather than failing the call. An unmeasured token is better
    accounted as zero than as a guess.
    """
    ledger = _ledger.get()
    if ledger is None:
        return

    usage = getattr(response, "usage", None)
    if usage is None:
        return

    ledger.add(
        TokenUsage(
            requests=1,
            input_tokens=_count(usage, "input_tokens"),
            output_tokens=_count(usage, "output_tokens"),
            cache_read_tokens=_count(usage, "cache_read_input_tokens"),
            cache_write_tokens=_count(usage, "cache_creation_input_tokens"),
        )
    )


def _count(usage: object, field: str) -> int:
    value = getattr(usage, field, None)
    return int(value) if isinstance(value, int) else 0


def price(usage: TokenUsage) -> float:
    """Spend for a usage total. Public so the metrics endpoint can reprice."""
    return usage.cost_usd


def scale(usage: TokenUsage, factor: float) -> TokenUsage:
    """A usage total scaled by `factor`, for reporting an average per report."""
    return replace(
        usage,
        input_tokens=int(usage.input_tokens * factor),
        output_tokens=int(usage.output_tokens * factor),
        cache_read_tokens=int(usage.cache_read_tokens * factor),
        cache_write_tokens=int(usage.cache_write_tokens * factor),
    )


def _api_key() -> str:
    """The configured key, with surrounding whitespace removed.

    A platform's own environment-variable UI is a surprisingly common source
    of a stray leading or trailing space — this deployment's `x-api-key`
    header carried one, and h11 rejects a header value that starts with
    whitespace outright: every call failed with an opaque `APIConnectionError`
    that gave no hint the key itself was the problem. `is_configured` already
    stripped before checking truthiness; the value actually handed to the SDK
    did not, which is what let a whitespace-mangled key look "configured" and
    fail anyway. One function now, so the two cannot disagree again.
    """
    return get_settings().anthropic_api_key.get_secret_value().strip()


def is_configured() -> bool:
    """True when an API key is present.

    Callers use this to skip the model entirely rather than build a prompt and
    then fail on it.
    """
    return bool(_api_key())


def reset_client() -> None:
    """Drops the cached Anthropic client. Used by tests and on config reload.

    Leaves the underlying `httpx.AsyncClient` in place. That connection pool
    is not tied to any one Anthropic client and there is no reason to tear it
    down along with it — `close_client` is the one that does that, on
    application shutdown.
    """
    global _client  # noqa: PLW0603 — one process-wide pooled client by design
    _client = None


async def close_client() -> None:
    """Closes the owned httpx client. Called on application shutdown."""
    global _client, _http_client  # noqa: PLW0603 — mirrors edgar.close_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
    _client = None


def _get_http_client() -> httpx.AsyncClient:
    """The transport handed to the SDK, built once and reused."""
    global _http_client  # noqa: PLW0603 — mirrors _get_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS)
    return _http_client


def _get_client() -> _AnthropicClient:
    """Returns the shared async client, building it on first use.

    Given its own `http_client` explicitly rather than left to build one — see
    the module docstring for why the SDK's own construction reliably fails on
    this deployment while an equivalent plain client succeeds.
    """
    global _client  # noqa: PLW0603 — mirrors reset_client
    if _client is not None:
        return _client

    if not is_configured():
        message = (
            "No Anthropic API key is configured. Set ANTHROPIC_API_KEY before "
            "requesting generated prose."
        )
        raise LlmNotConfiguredError(message)

    try:
        from anthropic import AsyncAnthropic

        # Cast to the protocol above: the request carries `output_config`,
        # which the SDK's typed overloads do not yet describe.
        _client = cast(
            "_AnthropicClient",
            AsyncAnthropic(
                api_key=_api_key(),
                http_client=_get_http_client(),
                max_retries=LLM_MAX_RETRIES,
            ),
        )
    except ImportError as cause:
        message = "The anthropic package is not installed."
        raise LlmUnavailableError(message) from cause
    except Exception as cause:  # noqa: BLE001 — surfaced as a typed failure
        # The key is never logged; only that construction failed.
        logger.error("Could not build the Anthropic client", exc_info=cause)
        message = f"Could not reach the model: {cause}"
        raise LlmUnavailableError(message) from cause

    return _client


def _sampling_arguments(model: str) -> dict[str, Any]:
    """Temperature, when the model takes one.

    A model that rejects the parameter is not sent it — passing it would fail
    the request outright, which is a worse outcome than expressing the same
    intent through effort and the output schema. Which path was taken is
    logged, so a run's determinism settings can be read off its own logs.
    """
    if model in LLM_MODELS_ACCEPTING_TEMPERATURE:
        return {"temperature": min(LLM_TEMPERATURE, LLM_MAX_TEMPERATURE)}
    logger.debug(
        "Model does not accept a sampling temperature; relying on effort",
        extra={"model": model, "effort": LLM_EFFORT},
    )
    return {}


async def complete_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    purpose: str,
    model: str = LLM_MODEL,
    max_tokens: int = LLM_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Sends one prompt and returns the JSON object the model answered with.

    Args:
        system: The system prompt. Rules, not content.
        user: The user turn. For m10 this is a table of already-computed facts
            and nothing else.
        schema: JSON Schema the answer is constrained to. Must describe an
            object; the model cannot answer with anything else.
        purpose: Short label for logs, e.g. "write:financials".
        model: Overridable for tests.
        max_tokens: Output ceiling.

    Returns:
        The parsed object.

    Raises:
        LlmNotConfiguredError: no API key.
        LlmUnavailableError: the request failed.
        LlmMalformedResponseError: the answer was not a JSON object, or the
            model stopped before finishing one.
    """
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {
            "effort": LLM_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
        **_sampling_arguments(model),
    }
    return await _send_json_request(request, purpose=purpose, model=model)


async def complete_json_with_web_search(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    purpose: str,
    model: str = LLM_MODEL,
    max_tokens: int = LLM_MAX_OUTPUT_TOKENS,
    max_searches: int = LLM_WEB_SEARCH_MAX_USES,
) -> dict[str, Any]:
    """Like `complete_json`, but grants the model Anthropic's hosted
    web-search tool for this one call.

    This is the only function in this module that lets the model reach
    outside what a caller put in `user` — every other call answers from
    exactly what it was given. The search itself runs server-side, inside
    Anthropic's own infrastructure; nothing in this codebase executes it or
    sees an intermediate result. Reserved for chat's tier-4 fallback, which
    only reaches this after tiers 1-3 have already come up empty (see
    `chat_agent`), and every claim it can produce is labelled unverified by
    the caller — this function has no opinion about that; it only sends the
    request and returns what came back, exactly like `complete_json` does.
    """
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_searches,
            }
        ],
        "output_config": {
            "effort": LLM_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
        **_sampling_arguments(model),
    }
    return await _send_json_request(request, purpose=purpose, model=model)


async def _send_json_request(
    request: dict[str, Any], *, purpose: str, model: str
) -> dict[str, Any]:
    """The request/response handling shared by every schema-constrained call.

    Callers differ only in what they put in `request` (the base shape
    `complete_json` sends, or the same shape plus a hosted tool); the failure
    modes, usage accounting and parsing are identical either way.
    """
    client = _get_client()

    try:
        response = await client.messages.create(**request)
    except Exception as cause:  # noqa: BLE001 — surfaced as a typed failure
        logger.warning(
            "Model request failed",
            extra={"purpose": purpose, "model": model, "error": str(cause)},
        )
        message = f"The model did not answer for {purpose}: {cause}"
        raise LlmUnavailableError(message) from cause

    # Recorded before the response is inspected: a refused or truncated answer
    # still consumed tokens, and a cost figure that omitted them would
    # understate what the report actually cost.
    _record(response)

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        message = (
            f"The model declined to answer for {purpose}. The report is "
            "generated without this section."
        )
        raise LlmUnavailableError(message)
    if stop_reason == "max_tokens":
        message = (
            f"The model's answer for {purpose} was cut off at the output limit."
        )
        raise LlmMalformedResponseError(message)

    text = _first_text(response)
    if text is None:
        message = f"The model returned no text for {purpose}."
        raise LlmMalformedResponseError(message)

    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as cause:
        logger.warning(
            "Model answer was not valid JSON",
            extra={"purpose": purpose, "model": model},
        )
        message = f"The model's answer for {purpose} was not valid JSON."
        raise LlmMalformedResponseError(message) from cause

    if not isinstance(parsed, dict):
        message = (
            f"The model's answer for {purpose} was a "
            f"{type(parsed).__name__}, not an object."
        )
        raise LlmMalformedResponseError(message)

    logger.info(
        "Model answered",
        extra={
            "purpose": purpose,
            "model": model,
            "stop_reason": stop_reason,
            "output_tokens": getattr(
                getattr(response, "usage", None), "output_tokens", None
            ),
        },
    )
    return parsed


def _first_text(response: object) -> str | None:
    """The first text block of a response.

    Thinking blocks precede text on models that think, so the blocks are
    walked rather than indexed.
    """
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if text.strip():
                return str(text)
    return None
