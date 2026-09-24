from __future__ import annotations

import time
import uuid
from typing import Any


def openai_error(message: str, err_type: str = "invalid_request_error", code: str | None = None, status: int = 400) -> dict:
    return {
        "status": status,
        "body": {
            "error": {
                "message": message,
                "type": err_type,
                "param": None,
                "code": code,
            }
        },
    }


def chat_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def normalize_openai_request(body: dict[str, Any], model: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": model,
        "messages": body.get("messages") or [],
        "stream": bool(body.get("stream")),
    }
    if body.get("max_tokens") is not None:
        out["max_tokens"] = int(body["max_tokens"])
    elif body.get("max_completion_tokens") is not None:
        out["max_tokens"] = int(body["max_completion_tokens"])
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    if body.get("tools"):
        out["tools"] = body["tools"]
    if body.get("tool_choice") is not None:
        out["tool_choice"] = body["tool_choice"]
    if body.get("stop") is not None:
        out["stop"] = body["stop"]
    return out


def openai_from_upstream(payload: dict[str, Any], requested_model: str) -> dict[str, Any]:
    """Pass through OpenAI-shaped chat.completions, fill missing envelope fields."""
    if "choices" in payload:
        data = dict(payload)
        data.setdefault("id", chat_id())
        data.setdefault("object", "chat.completion")
        data.setdefault("created", int(time.time()))
        data.setdefault("model", requested_model)
        return data
    # Unexpected shape — wrap as error-ish completion text.
    return {
        "id": chat_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "raw": payload,
    }


def sse_line(obj: dict[str, Any]) -> bytes:
    import json

    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


DONE_LINE = b"data: [DONE]\n\n"


def stream_chunk(model: str, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": chat_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


# ----- Anthropic Messages <-> OpenAI Chat -----

def anthropic_to_openai(body: dict[str, Any], model: str) -> dict[str, Any]:
    system = body.get("system")
    messages: list[dict[str, Any]] = []
    if system:
        if isinstance(system, list):
            text = "".join(p.get("text", "") for p in system if isinstance(p, dict))
        else:
            text = str(system)
        if text:
            messages.append({"role": "system", "content": text})

    for msg in body.get("messages") or []:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        parts_text = []
        tool_calls = []
        tool_results = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    parts_text.append(part.get("text") or "")
                elif ptype == "tool_use":
                    tool_calls.append(
                        {
                            "id": part.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": part.get("name") or "",
                                "arguments": json_dumps(part.get("input") or {}),
                            },
                        }
                    )
                elif ptype == "tool_result":
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.get("tool_use_id") or "",
                            "content": part.get("content")
                            if isinstance(part.get("content"), str)
                            else json_dumps(part.get("content")),
                        }
                    )
        if tool_results:
            messages.extend(tool_results)
        if tool_calls:
            messages.append({"role": role, "content": "".join(parts_text) or None, "tool_calls": tool_calls})
        elif parts_text or content is None:
            messages.append({"role": role, "content": "".join(parts_text)})

    out: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    if body.get("max_tokens") is not None:
        out["max_tokens"] = int(body["max_tokens"])
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    if body.get("tools"):
        tools = []
        for t in body["tools"]:
            if not isinstance(t, dict):
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description") or "",
                        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                    },
                }
            )
        out["tools"] = tools
    return out


def json_dumps(v: Any) -> str:
    import json

    return json.dumps(v, ensure_ascii=False)


def openai_to_anthropic(payload: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        args = fn.get("arguments") or "{}"
        try:
            import json

            parsed = json.loads(args) if isinstance(args, str) else args
        except json.JSONDecodeError:
            parsed = {"_raw": args}
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name") or "",
                "input": parsed,
            }
        )
    stop = choice.get("finish_reason")
    stop_reason = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
    }.get(stop or "", "end_turn")
    usage = payload.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content or [{"type": "text", "text": ""}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def openai_chunk_to_anthropic_events(chunks: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Convert collected OpenAI stream chunks into Anthropic event frames (non-streaming collect helper)."""
    text_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish = "stop"
    for ch in chunks:
        for choice in ch.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                text_parts.append(delta["content"])
            for call in delta.get("tool_calls") or []:
                idx = int(call.get("index") or 0)
                slot = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                if call.get("id"):
                    slot["id"] = call["id"]
                fn = call.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    return [openai_to_anthropic({"choices": [{"message": {"content": "".join(text_parts)}, "finish_reason": finish}]}, model)]
