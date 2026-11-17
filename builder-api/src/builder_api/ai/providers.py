"""Small provider adapters. Provider credentials never appear in conversation items."""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx


class ProviderError(ValueError):
    pass


class OutputTokenLimit(ProviderError):
    def __init__(self, usage: dict[str, Any] | None = None) -> None:
        super().__init__("The response reached its output token limit.")
        self.usage = normalized_usage(usage or {})


async def model_output_limit(provider: str, key: str, model: str, *, transport: Any = None) -> int | None:
    if provider != "anthropic":
        return None
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10, trust_env=False) as client:
            response = await client.get(f"https://api.anthropic.com/v1/models/{quote(model, safe='')}",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            if response.is_success:
                limit = response.json().get("max_tokens")
                if type(limit) is int and limit > 0:
                    return limit
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    # Verified model limits when metadata is temporarily unavailable. Unknown
    # models retain the old conservative budget instead of guessing their cap.
    return 128000 if model in {"claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"} else 4096


def event_json(data: str) -> dict[str, Any]:
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        raise ProviderError("The provider stream contained malformed event JSON. Partial text is retained; try again.") from None
    if not isinstance(event, dict):
        raise ProviderError("The provider stream contained an invalid event shape. Partial text is retained; try again.")
    return event


def tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ProviderError("The provider sent malformed tool arguments. No tools from this response were executed. "
                                "Partial text is retained; try again.") from None
    if not isinstance(value, dict):
        raise ProviderError("The provider sent tool arguments that are not a JSON object. "
                            "No tools from this response were executed. Partial text is retained; try again.")
    return value


async def stream_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Decode SSE frames independently of HTTP byte/UTF-8 chunk boundaries."""
    lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif not line and lines:
            data = "\n".join(lines)
            lines.clear()
            if data != "[DONE]":
                yield event_json(data)
    if lines:
        data = "\n".join(lines)
        if data != "[DONE]":
            yield event_json(data)


async def streamed_response(response: httpx.Response, provider: str,
                            on_text: Callable[[str], Awaitable[None]] | None) -> dict[str, Any]:
    blocks: dict[int, dict[str, Any]] = {}
    arguments: dict[int, str] = {}
    closed_blocks: set[int] = set()
    message: dict[str, Any] | None = None
    async for event in stream_events(response):
        kind = event.get("type")
        if kind in {"error", "response.failed"}:
            raise ProviderError("The provider reported a streaming error. Partial text is retained; try again.")
        if provider == "openai":
            if kind in {"response.output_text.delta", "response.refusal.delta"} and on_text:
                await on_text(event["delta"])
            elif kind == "response.completed":
                return event["response"]
            elif kind == "response.incomplete":
                reason = event.get("response", {}).get("incomplete_details", {}).get("reason")
                if reason == "max_output_tokens":
                    raise OutputTokenLimit(event.get("response", {}).get("usage"))
                raise ProviderError("The provider returned an incomplete response. Partial text is retained; try again.")
        elif kind == "message_start":
            message = event["message"]
        elif kind == "content_block_start":
            blocks[event["index"]] = event["content_block"]
            if event["content_block"].get("text") and on_text:
                await on_text(event["content_block"]["text"])
        elif kind == "content_block_delta":
            index, delta = event["index"], event["delta"]
            block = blocks[index]
            if delta["type"] == "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
                if on_text:
                    await on_text(delta["text"])
            elif delta["type"] == "input_json_delta":
                arguments[index] = arguments.get(index, "") + delta["partial_json"]
            elif delta["type"] in {"thinking_delta", "signature_delta"}:
                field = "thinking" if delta["type"] == "thinking_delta" else "signature"
                block[field] = block.get(field, "") + delta[field]
        elif kind == "content_block_stop":
            # Wait for stop_reason before decoding: a token-limited tool block
            # can end with incomplete JSON and needs a larger-budget retry.
            closed_blocks.add(event["index"])
        elif kind == "message_delta" and message is not None:
            message.update(event["delta"])
            message.setdefault("usage", {}).update(event.get("usage", {}))
        elif kind == "message_stop" and message is not None:
            if message.get("stop_reason") == "max_tokens":
                raise OutputTokenLimit(message.get("usage"))
            if message.get("stop_reason") == "model_context_window_exceeded":
                raise ProviderError("The model's context window is full. Clear the conversation or reduce the task/context; "
                                    "raising the output budget will not fix this. Partial text is retained.")
            if set(blocks) - closed_blocks:
                raise ProviderError("The provider ended before its content blocks were complete. Partial text is retained; try again.")
            for index, partial in arguments.items():
                if partial.strip():
                    blocks[index]["input"] = tool_arguments(partial)
            message["content"] = [blocks[index] for index in sorted(blocks)]
            return message
    raise ProviderError("The provider closed its stream before completing the response. Partial text is retained; try again.")


NON_CHAT = re.compile(r"embed|whisper|tts|dall-e|moderation|davinci|babbage|transcribe|realtime|audio|image|sora")


def chat_models(provider: str, models: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop catalog entries that cannot drive a chat turn (OpenAI lists embeddings, audio, images…)."""
    if provider != "openai":
        return models
    return [item for item in models if not NON_CHAT.search(item["id"])]


def default_model(provider: str, models: list[dict[str, str]]) -> str:
    """The configured default when available, else the most capable-looking family member."""
    from builder_api.config import settings

    ids = [item["id"] for item in models]
    preferred = settings.ai_default_anthropic_model if provider == "anthropic" else settings.ai_default_openai_model
    if preferred in ids:
        return preferred
    families = ("opus", "sonnet", "claude") if provider == "anthropic" else ("gpt-5", "gpt-")
    for family in families:
        # Catalog is sorted by id; reverse prefers the newest version in a family.
        match = next((i for i in reversed(ids) if family in i and not re.search(r"mini|nano|haiku", i)), None)
        if match:
            return match
    return ids[0] if ids else ""


async def list_models(provider: str, key: str, *, transport: Any = None) -> list[dict[str, str]]:
    """Fetch account-visible models without sending a prompt or retaining the key."""
    url = "https://api.openai.com/v1/models" if provider == "openai" else "https://api.anthropic.com/v1/models"
    headers = ({"Authorization": f"Bearer {key}"} if provider == "openai" else
               {"x-api-key": key, "anthropic-version": "2023-06-01"})
    params = {} if provider == "openai" else {"limit": "1000"}
    models: dict[str, str] = {}
    cursors: set[str] = set()
    try:
        async with httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False, trust_env=False) as client:
            for _ in range(10):
                response = await client.get(url, headers=headers, params=params)
                if not response.is_success:
                    raise ProviderError(f"Could not load models (HTTP {response.status_code}). Check the API key and permissions.")
                data = response.json()
                for model in data["data"]:
                    identifier = model["id"]
                    if not isinstance(identifier, str) or not identifier:
                        raise ValueError("Invalid model identifier")
                    models[identifier] = model.get("display_name") or identifier
                if provider == "openai" or not data.get("has_more"):
                    return [{"id": identifier, "label": label} for identifier, label in sorted(models.items())]
                cursor = data.get("last_id")
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise ValueError("Invalid model pagination")
                cursors.add(cursor)
                params["after_id"] = cursor
    except ProviderError:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise ProviderError("Could not load the provider's models. Try again.") from exc
    raise ProviderError("Provider model pagination exceeded the limit. Try again.")


EPHEMERAL = {"type": "ephemeral"}
CACHEABLE_BLOCKS = {"text", "tool_result", "tool_use", "image", "document"}


def cache_tail(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the newest message as a cache breakpoint without mutating conversation state."""
    if not messages or "content" not in messages[-1]:
        return messages
    last = dict(messages[-1])
    content = last["content"]
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content, "cache_control": EPHEMERAL}]
    elif isinstance(content, list) and content and content[-1].get("type") in CACHEABLE_BLOCKS:
        last["content"] = [*content[:-1], {**content[-1], "cache_control": EPHEMERAL}]
    return [*messages[:-1], last]


def normalized_usage(usage: dict[str, Any]) -> dict[str, int]:
    """input_tokens counts all prompt tokens (Anthropic reports cached ones separately)."""
    read = usage.get("cache_read_input_tokens") or 0
    cached = read or (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
    total = (usage.get("input_tokens") or 0) + read + (usage.get("cache_creation_input_tokens") or 0)
    return {"input_tokens": total, "output_tokens": usage.get("output_tokens") or 0, "cached_input_tokens": cached}


async def complete(provider: str, key: str, model: str, system: str,
                   messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                   max_tokens: int, transport: Any = None,
                   on_text: Callable[[str], Awaitable[None]] | None = None) -> dict[str, Any]:
    if provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {key}"}
        body = {"model": model, "instructions": system, "input": messages, "store": False,
                "include": ["reasoning.encrypted_content"],
                "max_output_tokens": max_tokens,
                "tools": [{"type": "function", **tool, "strict": True} for tool in tools]}
    else:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        # Breakpoints after the static tools+system prefix and after the newest message: later
        # rounds of a turn and later turns reread that prefix from the cache.
        body = {"model": model, "system": [{"type": "text", "text": system, "cache_control": EPHEMERAL}],
                "messages": cache_tail(messages), "max_tokens": max_tokens,
                "tools": [{"name": t["name"], "description": t["description"],
                           "input_schema": t["parameters"]} for t in tools]}
    stage = "opening the response"
    try:
        body["stream"] = True
        # A model may think for hours. Bound connection setup, not generation.
        timeout = httpx.Timeout(connect=30, read=None if on_text else 60, write=60, pool=30)
        async with (
            httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False) as client,
            client.stream("POST", url, headers=headers, json=body) as response,
        ):
            if not response.is_success:
                raise ProviderError(f"Provider request failed (HTTP {response.status_code}). Check the key, model and quota.")
            if "text/event-stream" in response.headers.get("content-type", ""):
                stage = "reading streaming events"
                data = await streamed_response(response, provider, on_text)
            else:
                stage = "reading response JSON"
                await response.aread()
                data = response.json()
                if data.get("stop_reason") == "max_tokens" or (data.get("status") == "incomplete"
                        and (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens"):
                    raise OutputTokenLimit(data.get("usage"))
                if data.get("stop_reason") == "model_context_window_exceeded":
                    raise ProviderError("The model's context window is full. Clear the conversation or reduce the task/context.")
                if data.get("status") in {"failed", "incomplete"}:
                    raise ProviderError("The provider returned an incomplete response. Try again with a smaller request.")
                if on_text:
                    if provider == "openai":
                        for block in data.get("output", []):
                            for part in block.get("content", []):
                                await on_text(part.get("text", ""))
                    else:
                        for block in data.get("content", []):
                            if block.get("type") == "text":
                                await on_text(block["text"])
        stage = "reading the completed response"
        if provider == "openai":
            blocks = data.get("output", [])
            calls = [{"id": b["call_id"], "name": b["name"], "args": tool_arguments(b["arguments"])}
                     for b in blocks if b["type"] == "function_call"]
            text = "".join(c.get("text", c.get("refusal", "")) for b in blocks if b["type"] == "message"
                           for c in b.get("content", []))
            continuation = blocks
            usage = data.get("usage", {})
        else:
            blocks = data.get("content", [])
            calls = [{"id": b["id"], "name": b["name"], "args": tool_arguments(b["input"])}
                     for b in blocks if b["type"] == "tool_use"]
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            continuation = [{"role": "assistant", "content": blocks}]
            usage = data.get("usage", {})
        return {"text": text, "calls": calls, "continuation": continuation, "usage": normalized_usage(usage)}
    except httpx.TimeoutException as exc:
        raise ProviderError("Provider connection or upload timed out. Generation has no read timeout; try again."
                            if on_text else "Provider connection test timed out. Try again.") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("The provider connection was lost. Partial text is retained; try again.") from exc
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        label = "OpenAI" if provider == "openai" else "Claude"
        # Only static stage and exception type: exception text may contain secrets.
        raise ProviderError(f"{label} response format was not recognized while {stage} ({type(exc).__name__}). "
                            "Partial text is retained; try again.") from None


def tool_results(provider: str, results: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    if provider == "openai":
        return [{"type": "function_call_output", "call_id": cid,
                 "output": json.dumps(value, ensure_ascii=False, separators=(",", ":"))} for cid, value in results]
    return [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": cid,
             "content": json.dumps(value, ensure_ascii=False, separators=(",", ":"))} for cid, value in results]}]
