from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

# MiMo Desktop voice models (sent through /route/chat/completions with audio/asr_options).
TTS_MODEL = "mimo-v2.5-tts"
TTS_MODEL_DESIGN = "mimo-v2.5-tts-voicedesign"
TTS_MODEL_CLONE = "mimo-v2.5-tts-voiceclone"
ASR_MODEL = "mimo-v2.5-asr"

DATA_URL_RE = re.compile(r"^data:([^;,]+)?(?:;charset=[^;,]+)?;base64,(.*)$", re.S)


def guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def load_data_url(value: str, default_mime: str) -> tuple[str, str]:
    """Return (data_url, mime) from path / http / data URL / raw base64."""
    v = (value or "").strip()
    if not v:
        raise ValueError("empty media value")
    if v.startswith("data:"):
        m = DATA_URL_RE.match(v)
        if not m:
            # allow already-complete data URL
            return v, default_mime
        mime = m.group(1) or default_mime
        return f"data:{mime};base64,{m.group(2)}", mime
    if re.match(r"^https?://", v, re.I):
        return v, default_mime
    p = Path(v)
    if p.is_file():
        raw = p.read_bytes()
        mime = guess_mime(str(p)) or default_mime
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", mime
    # raw base64
    cleaned = re.sub(r"\s+", "", v)
    try:
        base64.b64decode(cleaned, validate=True)
    except Exception as e:
        raise ValueError(f"unsupported media value (need path/url/data:/base64): {v[:80]}") from e
    return f"data:{default_mime};base64,{cleaned}", default_mime


def normalize_openai_messages(messages: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Normalize OpenAI / Anthropic-ish multimodal parts into MiMo chat format.

    Supports:
      - text
      - image_url: {url}          -> image_url (pass-through / local file -> data URL)
      - input_audio: {data, format}
      - audio: {data}             (OpenAI audio content part alias)
      - file / input_image aliases
    Also lifts `asr_options` / preserves `reasoning_content`.
    """
    extras: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"
        content = msg.get("content")
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        item: dict[str, Any] = {"role": role}
        if reasoning:
            item["reasoning_content"] = reasoning
        if msg.get("tool_calls"):
            item["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            item["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            item["name"] = msg["name"]

        if isinstance(content, str) or content is None:
            item["content"] = content
            out.append(item)
            continue

        parts: list[dict[str, Any]] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    if isinstance(part, str):
                        parts.append({"type": "text", "text": part})
                    continue
                ptype = part.get("type")
                if ptype in ("text", "input_text"):
                    parts.append({"type": "text", "text": part.get("text") or ""})
                elif ptype in ("image_url", "input_image", "image"):
                    src = part.get("image_url") or part.get("url") or part.get("image") or {}
                    if isinstance(src, dict):
                        url = src.get("url") or src.get("data") or ""
                    else:
                        url = str(src)
                    data_url, _ = load_data_url(url, "image/png")
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
                elif ptype in ("input_audio", "audio"):
                    audio = part.get("input_audio") or part.get("audio") or {}
                    if isinstance(audio, str):
                        data_url, mime = load_data_url(audio, "audio/wav")
                        fmt = "wav" if "wav" in mime or mime.endswith("wave") else "mp3"
                        parts.append({"type": "input_audio", "input_audio": {"data": data_url, "format": fmt}})
                    else:
                        data = audio.get("data") or audio.get("url") or ""
                        fmt = str(audio.get("format") or "wav").lower()
                        mime = "audio/mpeg" if fmt in ("mp3", "mpeg") else "audio/wav"
                        data_url, _ = load_data_url(data, mime)
                        parts.append({"type": "input_audio", "input_audio": {"data": data_url, "format": fmt if fmt in ("mp3", "wav") else "wav"}})
                        if audio.get("language"):
                            extras.setdefault("asr_options", {})["language"] = audio["language"]
                elif ptype == "file":
                    # best-effort: treat as image if image-ish, else skip into text note
                    src = part.get("file") or {}
                    url = src.get("url") or src.get("file_data") or ""
                    if url:
                        try:
                            data_url, mime = load_data_url(url, "application/octet-stream")
                            if mime.startswith("image/"):
                                parts.append({"type": "image_url", "image_url": {"url": data_url}})
                            elif mime.startswith("audio/"):
                                fmt = "mp3" if "mpeg" in mime or "mp3" in mime else "wav"
                                parts.append({"type": "input_audio", "input_audio": {"data": data_url, "format": fmt}})
                        except ValueError:
                            pass
                else:
                    # preserve unknown parts
                    parts.append(part)
        item["content"] = parts if parts else content
        out.append(item)
    return out, extras


def build_tts_body(
    text: str,
    voice: str | None = None,
    fmt: str = "mp3",
    instructions: str | None = None,
    voice_design: str | None = None,
    voice_file: str | None = None,
) -> dict[str, Any]:
    """MiMo TTS request (OpenAI /v1/audio/speech adapter target)."""
    if voice_design and voice_file:
        raise ValueError("exactly one of voice_design / voice_file")
    model = TTS_MODEL
    messages: list[dict[str, Any]] = []
    if voice_design:
        model = TTS_MODEL_DESIGN
        messages.append({"role": "user", "content": f"Voice design: {voice_design}"})
    elif voice_file:
        model = TTS_MODEL_CLONE
        data_url, mime = load_data_url(voice_file, "audio/mpeg")
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": data_url, "format": "mp3" if "mpeg" in mime else "wav"}},
                    {"type": "text", "text": instructions or ""},
                ],
            }
        )
    elif instructions:
        messages.append({"role": "user", "content": instructions})
    messages.append({"role": "assistant", "content": text})
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "audio": {"format": "mp3" if fmt in ("mp3", "mpeg") else "wav"},
        "stream": False,
    }
    if voice:
        body["audio"]["voice"] = "mimo_default" if voice in ("mimo-default", "mimo default", "alloy") else voice
    else:
        body["audio"]["voice"] = "mimo_default"
    return body


def build_asr_body(audio_data: str, language: str = "auto") -> dict[str, Any]:
    data_url, mime = load_data_url(audio_data, "audio/wav")
    fmt = "mp3" if "mpeg" in mime or "mp3" in mime else "wav"
    return {
        "model": ASR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "input_audio", "input_audio": {"data": data_url, "format": fmt}}],
            }
        ],
        "asr_options": {"language": language or "auto"},
        "stream": False,
    }


def extract_audio_from_message(message: dict[str, Any]) -> tuple[bytes | None, str]:
    """Parse TTS chat response → (audio_bytes, mime)."""
    audio = message.get("audio") or {}
    candidates = []
    if isinstance(audio, dict):
        candidates.extend([audio.get("data"), audio.get("b64_json"), audio.get("base64"), audio.get("url")])
    content = message.get("content")
    if isinstance(content, str) and content.startswith("data:audio"):
        candidates.append(content)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("output_audio", "audio", "input_audio"):
                    block = part.get("audio") or part.get("output_audio") or part.get("input_audio") or part
                    if isinstance(block, dict):
                        candidates.extend([block.get("data"), block.get("b64_json"), block.get("base64"), block.get("url")])
                    elif isinstance(block, str):
                        candidates.append(block)
    for cand in candidates:
        if not cand or not isinstance(cand, str):
            continue
        if cand.startswith("data:audio"):
            m = DATA_URL_RE.match(cand)
            if not m:
                continue
            mime = m.group(1) or "audio/mpeg"
            return base64.b64decode(m.group(2)), mime
        if cand.startswith("http://") or cand.startswith("https://"):
            return cand.encode("utf-8"), "application/uri"  # caller may download
        # raw base64
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", cand), validate=True)
            if raw[:4] == b"RIFF":
                return raw, "audio/wav"
            if raw[:3] == b"ID3" or raw[:2] == b"\xff\xfb":
                return raw, "audio/mpeg"
            return raw, "audio/mpeg"
        except Exception:
            continue
    return None, ""


def extract_text_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
    return ""


CAPABILITY_MATRIX = {
    "mimo-v2.6-pro": {
        "input": ["text", "image", "audio"],
        "output": ["text"],
        "tools": True,
        "reasoning": True,
        "reasoning_field": "reasoning_content",
        "structured_output": True,
        "notes": "音频经 input_audio + asr_options；图片 image_url；推理字段 reasoning_content",
    },
    "mimo-v2.6-flash": {
        "input": ["text", "image", "audio"],
        "output": ["text"],
        "tools": True,
        "reasoning": True,
        "reasoning_field": "reasoning_content",
        "structured_output": True,
        "notes": "同 Pro，偏快",
    },
    "mimo-v2.5-tts": {
        "input": ["text"],
        "output": ["audio"],
        "endpoint": "/v1/audio/speech",
        "upstream": "chat.completions+audio",
        "notes": "OpenAI speech 适配到 MiMo chat audio 字段",
    },
    "mimo-v2.5-tts-voicedesign": {
        "input": ["text"],
        "output": ["audio"],
        "endpoint": "/v1/audio/speech",
        "upstream": "chat.completions+audio",
        "notes": "voice_design 音色设计",
    },
    "mimo-v2.5-tts-voiceclone": {
        "input": ["text", "audio"],
        "output": ["audio"],
        "endpoint": "/v1/audio/speech",
        "upstream": "chat.completions+audio",
        "notes": "voice_file 音色克隆样本（mp3/wav ≤10MB）",
    },
    "mimo-v2.5-asr": {
        "input": ["audio"],
        "output": ["text"],
        "endpoint": "/v1/audio/transcriptions",
        "upstream": "chat.completions+input_audio",
        "notes": "中英；可 language=auto",
    },
    "Doubao-Seedream-5.0-pro": {
        "input": ["text", "image"],
        "output": ["image"],
        "endpoint": "/v1/images/generations",
        "upstream": "/route/images/generations",
        "notes": "文生图/图生图，返回 b64_json 或 url",
    },
}

ADAPTATION_NOTES = [
    "OpenAI Responses (/v1/responses)：上游无此协议，网关本地与 chat.completions 互转（含 reasoning 摘要降级）。",
    "OpenAI Audio (/v1/audio/speech|transcriptions)：上游用 chat 扩展字段 audio/asr_options，网关做格式桥接。",
    "Vision：image_url 本地路径会读成 data URL，http(s) 透传；上游需支持 image 附件（v2.6 attachment=true）。",
    "Audio 输入：OpenAI input_audio 与 MiMo input_audio 字段对齐；data URL / 本地 wav·mp3。",
    "reasoning_content：非 OpenAI 标准字段，会在 message 与 Responses reasoning item 中透出。",
    "Video：部分模型声明 video 输入，但桌面主路径以抽帧/附件为主，网关 v0.1 不单独开 /v1/video。",
    "结构化输出 tools/function calling：上游 tool_call=true，标准 tools 透传。",
]
