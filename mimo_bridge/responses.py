from __future__ import annotations

import time
import uuid
from typing import Any


def rid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def responses_to_chat(body: dict[str, Any], model: str) -> dict[str, Any]:
    """
    Convert OpenAI Responses API request → chat.completions request.

    Covers the common subset used by agents:
      input: string | [{role, content: string|parts}]
      instructions → system
      tools / tool_choice / temperature / top_p / max_output_tokens
      stream
    """
    instructions = body.get("instructions")
    raw_input = body.get("input")
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": str(instructions)})

    items = raw_input if isinstance(raw_input, list) else [{"role": "user", "content": raw_input or ""}]
    for item in items:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue
        # Response input items can be message-like or function_call / function_call_output
        itype = item.get("type") or ""
        if itype in ("function_call", "function_call_output") or item.get("call_id") and itype:
            # map to tool messages
            if itype == "function_call" or item.get("type") == "function_call":
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": item.get("call_id") or item.get("id") or rid("call"),
                                "type": "function",
                                "function": {
                                    "name": item.get("name") or "",
                                    "arguments": item.get("arguments") or "{}",
                                },
                            }
                        ],
                    }
                )
            else:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.get("call_id") or item.get("id") or "",
                        "content": item.get("output") if isinstance(item.get("output"), str) else str(item.get("output") or ""),
                    }
                )
            continue

        role = item.get("role") or "user"
        content = item.get("content")
        if isinstance(content, str) or content is None:
            messages.append({"role": role, "content": content})
            continue
        parts_out = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype in ("input_text", "output_text", "text"):
                    parts_out.append({"type": "text", "text": part.get("text") or ""})
                elif ptype == "input_image":
                    url = part.get("image_url") or part.get("url") or ""
                    parts_out.append({"type": "image_url", "image_url": {"url": url}})
                elif ptype == "input_audio":
                    audio = part.get("audio") or part.get("input_audio") or {}
                    if isinstance(audio, str):
                        parts_out.append({"type": "input_audio", "input_audio": {"data": audio}})
                    else:
                        parts_out.append({"type": "input_audio", "input_audio": audio})
                else:
                    parts_out.append(part)
        messages.append({"role": role, "content": parts_out})

    out: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    if body.get("max_output_tokens") is not None:
        out["max_tokens"] = int(body["max_output_tokens"])
    elif body.get("max_tokens") is not None:
        out["max_tokens"] = int(body["max_tokens"])
    for k in ("temperature", "top_p", "tool_choice", "stop"):
        if body.get(k) is not None:
            out[k] = body[k]
    if body.get("tools"):
        tools = []
        for t in body["tools"]:
            if not isinstance(t, dict):
                continue
            if t.get("type") in ("function", None) and (t.get("name") or t.get("function")):
                fn = t.get("function") or t
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": fn.get("name") or t.get("name"),
                            "description": fn.get("description") or t.get("description") or "",
                            "parameters": fn.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}},
                        },
                    }
                )
            else:
                tools.append(t)
        out["tools"] = tools
    if body.get("text") and isinstance(body["text"], dict):
        # structured outputs / verbosity — pass through if upstream accepts
        if body["text"].get("format"):
            out["response_format"] = body["text"]["format"]
    return out


def chat_to_responses(payload: dict[str, Any], model: str, input_items: list[Any] | None = None) -> dict[str, Any]:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content_parts: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content_parts.append({"type": "output_text", "text": text, "annotations": []})
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        content_parts.insert(0, {"type": "reasoning", "summary": [{"type": "summary_text", "text": reasoning}]})

    output: list[dict[str, Any]] = [
        {
            "type": "message",
            "id": rid("msg"),
            "status": "completed",
            "role": "assistant",
            "content": content_parts or [{"type": "output_text", "text": "", "annotations": []}],
        }
    ]
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        output.append(
            {
                "type": "function_call",
                "id": rid("fc"),
                "call_id": call.get("id") or rid("call"),
                "name": fn.get("name") or "",
                "arguments": fn.get("arguments") or "{}",
                "status": "completed",
            }
        )

    stop = choice.get("finish_reason")
    status = "completed" if stop in ("stop", "tool_calls", "function_call") else "incomplete"
    usage = payload.get("usage") or {}
    return {
        "id": rid("resp"),
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "output_text": text if isinstance(text, str) else "",
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
        "metadata": {},
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "error": None,
        "incomplete_details": None,
    }


def responses_sse_chunk(kind: str, **fields: Any) -> dict[str, Any]:
    base = {"type": kind, "sequence_number": fields.pop("sequence_number", 0)}
    base.update(fields)
    return base


def stream_responses_from_chat_chunks(chunks: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Non-streaming helper: fold chat chunks into one Responses object (used by tests)."""
    text = []
    finish = "stop"
    for ch in chunks:
        for choice in ch.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    return [
        chat_to_responses(
            {"choices": [{"message": {"content": "".join(text)}, "finish_reason": finish}]},
            model,
        )
    ]
