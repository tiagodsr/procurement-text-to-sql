"""Offline stand-in for the OpenAI client, for tests. It never touches the network.

Replies are built with the SDK's own response types, so a shape the real client would not return
fails here too. A FakeClock lets each reply take an exact number of seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx2  # the HTTP library the openai SDK is built on (installed with it)
import openai
from openai.types import CreateEmbeddingResponse
from openai.types.responses import Response


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True)
class Reply:
    """One scripted result of responses.create."""

    text: str = ""
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    refusal: str | None = None
    incomplete: str | None = None  # "max_output_tokens" or "content_filter"
    error: Exception | None = None


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", "https://fake.invalid/v1"))


def build_response(reply: Reply, model: str) -> Response:
    content = (
        {"type": "refusal", "refusal": reply.refusal}
        if reply.refusal is not None
        else {"type": "output_text", "text": reply.text, "annotations": []}
    )
    status = "incomplete" if reply.incomplete else "completed"
    message = {"id": "msg_fake", "type": "message", "role": "assistant", "status": status}
    return Response.model_validate(
        {
            "id": "resp_fake",
            "object": "response",
            "created_at": 0,
            "model": model,
            "status": status,
            "incomplete_details": {"reason": reply.incomplete} if reply.incomplete else None,
            "output": [{**message, "content": [content]}],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": reply.input_tokens,
                "input_tokens_details": {
                    "cached_tokens": reply.cached_tokens,
                    "cache_write_tokens": 0,
                },
                "output_tokens": reply.output_tokens,
                "output_tokens_details": {"reasoning_tokens": reply.reasoning_tokens},
                "total_tokens": reply.input_tokens + reply.output_tokens,
            },
        }
    )


class _Responses:
    def __init__(self, owner: FakeOpenAI) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Response:
        self._owner.calls.append(("responses", kwargs))
        reply = self._owner.replies.pop(0)
        self._owner.clock.advance(reply.seconds)
        if reply.error is not None:
            raise reply.error
        return build_response(reply, kwargs["model"])


class _Embeddings:
    def __init__(self, owner: FakeOpenAI) -> None:
        self._owner = owner

    def create(self, *, model: str, input: list[str], **kwargs: Any) -> CreateEmbeddingResponse:
        self._owner.calls.append(("embeddings", {"model": model, "input": input, **kwargs}))
        self._owner.clock.advance(self._owner.embed_seconds)
        data = [
            {"object": "embedding", "index": i, "embedding": [float(len(text)), float(i)]}
            for i, text in enumerate(input)
        ]
        if self._owner.reverse_embeddings:
            data.reverse()  # the API does not promise order; callers must sort by index
        tokens = sum(len(text.split()) for text in input)
        return CreateEmbeddingResponse.model_validate(
            {
                "object": "list",
                "model": model,
                "data": data,
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            }
        )


class FakeOpenAI:
    """Pops one Reply per responses.create call; embeds each text as [len(text), index]."""

    def __init__(
        self,
        replies: list[Reply] | None = None,
        clock: FakeClock | None = None,
        embed_seconds: float = 0.0,
        reverse_embeddings: bool = False,
    ) -> None:
        self.replies = list(replies or [])
        self.clock = clock or FakeClock()
        self.embed_seconds = embed_seconds
        self.reverse_embeddings = reverse_embeddings
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = _Responses(self)
        self.embeddings = _Embeddings(self)
