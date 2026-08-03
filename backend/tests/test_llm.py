"""Tests for services.llm — request shape and failure handling.

`llm.py` had no direct unit tests before this; every other module exercises
it through `complete_json` monkeypatched at the function level. These test
the module itself: what actually goes into a request, and what each failure
mode of the model's response turns into.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services import llm

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


@dataclass
class _Block:
    type: str
    text: str = ""


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Response:
    content: list[_Block] = field(default_factory=list)
    stop_reason: str | None = "end_turn"
    usage: _Usage = field(default_factory=_Usage)


class _FakeMessages:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response: _Response) -> None:
        self.messages = _FakeMessages(response)


def _install(monkeypatch: pytest.MonkeyPatch, response: _Response) -> _FakeClient:
    client = _FakeClient(response)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return client


async def test_complete_json_returns_the_parsed_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(content=[_Block(type="text", text='{"ok": true}')])
    _install(monkeypatch, response)

    result = await llm.complete_json(
        "system", "user", SCHEMA, purpose="test:ping"
    )

    assert result == {"ok": True}


async def test_complete_json_never_sends_a_tools_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(content=[_Block(type="text", text='{"ok": true}')])
    client = _install(monkeypatch, response)

    await llm.complete_json("system", "user", SCHEMA, purpose="test:ping")

    assert "tools" not in client.messages.calls[0]


async def test_web_search_variant_grants_the_hosted_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(content=[_Block(type="text", text='{"ok": true}')])
    client = _install(monkeypatch, response)

    await llm.complete_json_with_web_search(
        "system", "user", SCHEMA, purpose="test:search"
    )

    sent = client.messages.calls[0]
    assert "tools" in sent
    assert sent["tools"][0]["type"] == "web_search_20250305"
    # The rest of the request is unchanged from complete_json's shape.
    assert sent["output_config"]["format"]["schema"] == SCHEMA


async def test_refusal_raises_llm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(content=[], stop_reason="refusal")
    _install(monkeypatch, response)

    with pytest.raises(llm.LlmUnavailableError):
        await llm.complete_json("system", "user", SCHEMA, purpose="test:ping")


async def test_truncated_output_raises_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(
        content=[_Block(type="text", text="{")], stop_reason="max_tokens"
    )
    _install(monkeypatch, response)

    with pytest.raises(llm.LlmMalformedResponseError):
        await llm.complete_json("system", "user", SCHEMA, purpose="test:ping")


async def test_invalid_json_raises_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(content=[_Block(type="text", text="not json")])
    _install(monkeypatch, response)

    with pytest.raises(llm.LlmMalformedResponseError):
        await llm.complete_json("system", "user", SCHEMA, purpose="test:ping")


async def test_non_object_json_raises_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(content=[_Block(type="text", text="[1, 2, 3]")])
    _install(monkeypatch, response)

    with pytest.raises(llm.LlmMalformedResponseError):
        await llm.complete_json("system", "user", SCHEMA, purpose="test:ping")


async def test_client_construction_failure_raises_llm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        message = "no client for you"
        raise llm.LlmUnavailableError(message)

    monkeypatch.setattr(llm, "_get_client", _boom)

    with pytest.raises(llm.LlmUnavailableError):
        await llm.complete_json("system", "user", SCHEMA, purpose="test:ping")
