"""Anthropic client.

The only module that talks to the model. It transports prompts and returns
text; it holds no opinion about report content.

The model is never asked to compute, recall or estimate a figure. Callers pass
already-computed facts and ask for prose about them.

Public interface
    complete(system, messages) -> str
"""

from __future__ import annotations


async def complete(system: str, messages: list[dict[str, str]]) -> str:
    """Sends a completion request and returns text. Implemented in phase 1."""
    raise NotImplementedError
