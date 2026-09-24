from __future__ import annotations

import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .auth import resolve_session
from .client import MimoClient, UpstreamError, models_payload, normalize_model
from .config import AuthSession, Settings, dump_public
from .formats import (
    DONE_LINE,
    anthropic_to_openai,
    normalize_openai_request,
    openai_chunk_to_anthropic_events,
    openai_error,
    openai_from_upstream,
    openai_to_anthropic,
    sse_line,
    stream_chunk,
)
from .multimodal import (
    ADAPTATION_NOTES,
    CAPABILITY_MATRIX,
    build_asr_body,
    build_tts_body,
    extract_audio_from_message,
    extract_text_from_message,
    normalize_openai_messages,
)
from .responses import chat_to_responses, responses_to_chat


class BridgeState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: AuthSession | None = None
        self.note = ""
        self.reload()

    def reload(self, cookie: str | None = None) -> None:
        self.session, self.note = resolve_session(self.settings, cookie)

    def client(self) -> MimoClient:
        if not self.session or not self.session.has_session():
            raise UpstreamError(401, "no xiaomi session; run: mimo-bridge auth --cookie '...'", kind="auth-expired")
        return MimoClient(self.settings, self.session)


def make_handler(state: BridgeState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mimo-agent-bridge/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[bridge] {self.address_string()} {fmt % args}")

        def _json(self, status: int, payload: dict[str, Any], extra: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _auth_ok(self) -> bool:
            key = state.settings.bridge_api_key
            if not key:
                return True
            header = self.headers.get("Authorization") or ""
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            if not token:
                token = self.headers.get("x-api-key") or ""
            return token == key

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/health", "/v1/health"):
                self._json(
                    200,
                    {
                        "ok": True,
                        "service": "mimo-agent-bridge",
                        "authenticated": bool(state.session and state.session.has_session()),
                        "base_url": state.settings.base_url,
                    },
                )
                return
            if not self._auth_ok():
                self._json(401, {"error": {"message": "bridge api key required", "type": "auth_error"}})
                return
            if path in ("/", "/ui", "/ui/"):
                html = (Path(__file__).resolve().parent / "static" / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if path in ("/v1",):
                self._json(200, {"ok": True, "docs": "/openapi.json", "ui": "/ui", "plan": "/plan", "models": "/v1/models", "capabilities": "/v1/capabilities"})
                return
            if path == "/openapi.json":
                self._json(200, openapi_spec(state.settings))
                return
            try:
                client = state.client()
            except UpstreamError as e:
                self._json(401 if e.kind == "auth-expired" else 502, {"error": {"message": str(e), "type": e.kind}})
                return

            if path == "/v1/models":
                try:
                    payload = models_payload(client.model_list())
                    self._json(200, payload)
                except UpstreamError as e:
                    self._json(502, {"error": {"message": str(e), "type": e.kind}})
                return
            if path in ("/plan", "/v1/plan", "/v1/dashboard/billing/subscription"):
                try:
                    sub = client.subscription()
                    if path.endswith("subscription"):
                        current = (sub or {}).get("current") if isinstance(sub, dict) else None
                        self._json(200, {"object": "billing.subscription", "data": sub, "current": current})
                    else:
                        self._json(200, {"ok": True, "subscription": sub, "auth": dump_public(state.session)})
                except UpstreamError as e:
                    self._json(502, {"error": {"message": str(e), "type": e.kind}})
                return
            if path in ("/usage", "/v1/usage", "/v1/dashboard/billing/usage"):
                try:
                    usage = client.usage()
                    if path.endswith("usage") and "dashboard" in path:
                        self._json(200, {"object": "billing.usage", "data": usage})
                    else:
                        self._json(200, {"ok": True, "usage": usage})
                except UpstreamError as e:
                    self._json(502, {"error": {"message": str(e), "type": e.kind}})
                return
            if path in ("/account", "/v1/account"):
                try:
                    me = client.me()
                    self._json(200, {"ok": True, "account": me, "auth": dump_public(state.session)})
                except UpstreamError as e:
                    self._json(502, {"error": {"message": str(e), "type": e.kind}})
                return
            if path in ("/v1/capabilities", "/capabilities"):
                self._json(
                    200,
                    {
                        "ok": True,
                        "protocols": ["openai.chat.completions", "openai.responses", "openai.images", "openai.audio", "anthropic.messages"],
                        "models": CAPABILITY_MATRIX,
                        "adaptation_notes": ADAPTATION_NOTES,
                    },
                )
                return
            self._json(404, {"error": {"message": f"unknown path {path}", "type": "invalid_request_error"}})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if not self._auth_ok():
                self._json(401, {"error": {"message": "bridge api key required", "type": "auth_error"}})
                return
            if path in ("/v1/chat/completions", "/v1/completions"):
                self._handle_chat()
                return
            if path in ("/v1/messages", "/v1/messages?"):
                self._handle_anthropic_messages()
                return
            if path == "/v1/responses":
                self._handle_responses()
                return
            if path == "/v1/audio/speech":
                self._handle_speech()
                return
            if path == "/v1/audio/transcriptions":
                self._handle_transcriptions()
                return
            if path in ("/v1/images/generations", "/v1/images/edits"):
                self._handle_images()
                return
            self._json(404, {"error": {"message": f"unknown path {path}", "type": "invalid_request_error"}})

        def _handle_chat(self) -> None:
            body = self._read_json()
            model = normalize_model(str(body.get("model") or "mimo-v2.6-pro"))
            if not body.get("messages"):
                self._json(400, openai_error("messages is required")["body"])
                return
            messages, extras = normalize_openai_messages(body.get("messages") or [])
            body = dict(body)
            body["messages"] = messages
            req = normalize_openai_request(body, model)
            req.update(extras)
            # pass through multimodal / reasoning helper fields
            for k in ("asr_options", "audio", "reasoning_content"):
                if body.get(k) is not None and k not in req:
                    req[k] = body[k]
            stream = bool(req.get("stream"))
            try:
                client = state.client()
            except UpstreamError as e:
                self._json(401 if e.kind == "auth-expired" else 502, {"error": {"message": str(e), "type": e.kind}})
                return
            try:
                if not stream:
                    payload = client.chat_completions(req, stream=False)
                    self._json(200, openai_from_upstream(payload, model))
                    return
                resp = client.chat_completions(req, stream=True)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                saw_any = False
                for chunk in client.iter_sse(resp):
                    saw_any = True
                    # Re-stamp model/id for clients that care.
                    if isinstance(chunk, dict):
                        chunk.setdefault("model", model)
                        chunk.setdefault("object", "chat.completion.chunk")
                    self.wfile.write(sse_line(chunk))
                    self.wfile.flush()
                if not saw_any:
                    self.wfile.write(sse_line(stream_chunk(model, {"role": "assistant", "content": ""}, "stop")))
                self.wfile.write(DONE_LINE)
                self.wfile.flush()
            except UpstreamError as e:
                if stream:
                    self.wfile.write(sse_line({"error": {"message": str(e), "type": e.kind}}))
                    self.wfile.write(DONE_LINE)
                else:
                    self._json(502 if e.kind != "auth-expired" else 401, {"error": {"message": str(e), "type": e.kind}})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": {"message": str(e), "type": "server_error"}})

        def _handle_responses(self) -> None:
            body = self._read_json()
            model = normalize_model(str(body.get("model") or "mimo-v2.6-pro"))
            chat_req = responses_to_chat(body, model)
            chat_req["messages"], extras = normalize_openai_messages(chat_req.get("messages") or [])
            chat_req.update(extras)
            stream = bool(chat_req.get("stream"))
            try:
                client = state.client()
            except UpstreamError as e:
                self._json(401 if e.kind == "auth-expired" else 502, {"error": {"message": str(e), "type": e.kind, "code": e.kind}})
                return
            try:
                if not stream:
                    payload = client.chat_completions(chat_req, stream=False)
                    self._json(200, chat_to_responses(payload, model))
                    return
                resp = client.chat_completions(chat_req, stream=True)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                resp_id = f"resp_{uuid.uuid4().hex[:24]}"
                seq = 0

                def ev(kind: str, **fields: Any) -> None:
                    nonlocal seq
                    payload = {"type": kind, "sequence_number": seq}
                    seq += 1
                    payload.update(fields)
                    self.wfile.write(sse_line(payload))
                    self.wfile.flush()

                ev("response.created", response={"id": resp_id, "object": "response", "status": "in_progress", "model": model, "output": []})
                ev(
                    "response.output_item.added",
                    output_index=0,
                    item={"type": "message", "id": f"msg_{uuid.uuid4().hex[:16]}", "role": "assistant", "status": "in_progress", "content": []},
                )
                ev("response.content_part.added", item_id=0, output_index=0, content_index=0, part={"type": "output_text", "text": ""})
                for chunk in client.iter_sse(resp):
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            ev("response.output_text.delta", output_index=0, content_index=0, delta=text)
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(reasoning, str) and reasoning:
                            ev("response.reasoning_summary_text.delta", output_index=0, content_index=0, delta=reasoning)
                ev("response.content_part.done", item_id=0, output_index=0, content_index=0, part={"type": "output_text", "text": ""})
                ev("response.output_item.done", output_index=0, item={"type": "message", "role": "assistant", "status": "completed", "content": []})
                ev("response.completed", response={"id": resp_id, "object": "response", "status": "completed", "model": model})
            except UpstreamError as e:
                self._json(502 if e.kind != "auth-expired" else 401, {"error": {"message": str(e), "type": e.kind}})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": {"message": str(e), "type": "server_error"}})

        def _handle_speech(self) -> None:
            body = self._read_json()
            text = str(body.get("input") or body.get("text") or "")
            if not text:
                self._json(400, openai_error("input is required")["body"])
                return
            voice = body.get("voice")
            fmt = str(body.get("response_format") or body.get("format") or "mp3").lower()
            if fmt in ("opus", "aac", "flac", "wav", "pcm"):
                fmt = "wav" if fmt in ("wav", "pcm") else "mp3"
            try:
                tts_body = build_tts_body(
                    text,
                    voice=voice if isinstance(voice, str) else None,
                    fmt=fmt,
                    instructions=body.get("instructions"),
                    voice_design=body.get("voice_design"),
                    voice_file=body.get("voice_file"),
                )
                client = state.client()
                payload = client.chat_completions(tts_body, stream=False)
                message = (payload.get("choices") or [{}])[0].get("message") or {}
                audio, mime = extract_audio_from_message(message)
                if not audio:
                    # URI case
                    if mime == "application/uri":
                        self._json(200, {"url": audio.decode("utf-8")})
                        return
                    self._json(502, openai_error("upstream returned no audio", "api_error", status=502)["body"])
                    return
                if mime.startswith("audio/uri") or mime == "application/uri":
                    self._json(200, {"url": audio.decode("utf-8")})
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime or ("audio/mpeg" if fmt == "mp3" else "audio/wav"))
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
            except UpstreamError as e:
                self._json(502 if e.kind != "auth-expired" else 401, openai_error(str(e), e.kind, status=502)["body"])
            except Exception as e:  # noqa: BLE001
                self._json(500, openai_error(str(e), "server_error", status=500)["body"])

        def _handle_transcriptions(self) -> None:
            body = self._read_json()
            audio = body.get("file") or body.get("audio") or body.get("data")
            if not audio:
                self._json(400, openai_error("file/audio is required")["body"])
                return
            language = str(body.get("language") or "auto")
            try:
                asr_body = build_asr_body(str(audio), language=language)
                client = state.client()
                payload = client.chat_completions(asr_body, stream=False)
                message = (payload.get("choices") or [{}])[0].get("message") or {}
                text = extract_text_from_message(message) or str(message.get("content") or "")
                if body.get("response_format") == "verbose_json":
                    self._json(200, {"text": text, "language": language, "segments": [], "words": []})
                else:
                    self._json(200, {"text": text})
            except UpstreamError as e:
                self._json(502 if e.kind != "auth-expired" else 401, openai_error(str(e), e.kind, status=502)["body"])
            except Exception as e:  # noqa: BLE001
                self._json(500, openai_error(str(e), "server_error", status=500)["body"])

        def _handle_images(self) -> None:
            body = self._read_json()
            if not body.get("prompt"):
                self._json(400, openai_error("prompt is required")["body"])
                return
            # normalize local image paths to data URLs for edit
            try:
                if body.get("image") and isinstance(body["image"], str) and not body["image"].startswith(("data:", "http")):
                    from .multimodal import load_data_url

                    body["image"], _ = load_data_url(body["image"], "image/png")
                if body.get("image_urls"):
                    from .multimodal import load_data_url

                    body["image_urls"] = [load_data_url(u, "image/png")[0] for u in body["image_urls"]]
                client = state.client()
                raw = client.images_generations(body)
                # normalize to OpenAI {created, data:[{b64_json|url}]}
                data_items = []
                if isinstance(raw, dict):
                    inner = raw.get("data")
                    if isinstance(inner, dict) and isinstance(inner.get("data"), list):
                        data_items = inner["data"]
                    elif isinstance(inner, list):
                        data_items = inner
                    elif isinstance(raw.get("data"), list):
                        data_items = raw["data"]
                self._json(200, {"created": int(time.time()), "data": data_items, "raw_code": raw.get("code") if isinstance(raw, dict) else None})
            except UpstreamError as e:
                self._json(502 if e.kind != "auth-expired" else 401, openai_error(str(e), e.kind, status=502)["body"])
            except Exception as e:  # noqa: BLE001
                self._json(500, openai_error(str(e), "server_error", status=500)["body"])

        def _handle_anthropic_messages(self) -> None:
            body = self._read_json()
            model = normalize_model(str(body.get("model") or "mimo-v2.6-pro"))
            if not body.get("messages"):
                self._json(400, openai_error("messages is required")["body"])
                return
            oai = anthropic_to_openai(body, model)
            stream = bool(oai.get("stream"))
            try:
                client = state.client()
            except UpstreamError as e:
                self._json(401 if e.kind == "auth-expired" else 502, {"type": "error", "error": {"type": e.kind, "message": str(e)}})
                return
            try:
                if not stream:
                    payload = client.chat_completions(oai, stream=False)
                    self._json(200, openai_to_anthropic(payload, model))
                    return
                # Stream as Anthropic SSE events, built from OpenAI chunks.
                resp = client.chat_completions(oai, stream=True)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                def ev(event: str, data: dict[str, Any]) -> None:
                    self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                msg_id = f"msg_{model}"
                ev("message_start", {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})
                ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
                index = 0
                for chunk in client.iter_sse(resp):
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            ev("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}})
                ev("content_block_stop", {"type": "content_block_stop", "index": index})
                ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 0}})
                ev("message_stop", {"type": "message_stop"})
            except UpstreamError as e:
                self._json(502, {"type": "error", "error": {"type": e.kind, "message": str(e)}})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"type": "error", "error": {"type": "server_error", "message": str(e)}})

    return Handler


def openapi_spec(settings: Settings) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "MiMo Agent Bridge",
            "version": "0.1.0",
            "description": "Expose MiMo Desktop subscription as OpenAI / Anthropic style local APIs.",
        },
        "servers": [{"url": f"http://{settings.host}:{settings.port}"}],
        "paths": {
            "/health": {"get": {"summary": "Health", "responses": {"200": {"description": "OK"}}}},
            "/plan": {"get": {"summary": "Current subscription plan", "responses": {"200": {"description": "Plan"}}}},
            "/usage": {"get": {"summary": "Usage quota", "responses": {"200": {"description": "Usage"}}}},
            "/account": {"get": {"summary": "Account info", "responses": {"200": {"description": "Account"}}}},
            "/v1/models": {"get": {"summary": "OpenAI models", "responses": {"200": {"description": "Models"}}}},
            "/v1/chat/completions": {"post": {"summary": "OpenAI chat completions", "responses": {"200": {"description": "Completion"}}}},
            "/v1/messages": {"post": {"summary": "Anthropic messages", "responses": {"200": {"description": "Message"}}}},
            "/v1/responses": {"post": {"summary": "OpenAI Responses", "responses": {"200": {"description": "Response"}}}},
            "/v1/audio/speech": {"post": {"summary": "TTS (adapted)", "responses": {"200": {"description": "Audio"}}}},
            "/v1/audio/transcriptions": {"post": {"summary": "ASR (adapted)", "responses": {"200": {"description": "Transcript"}}}},
            "/v1/images/generations": {"post": {"summary": "Image generation", "responses": {"200": {"description": "Images"}}}},
            "/v1/capabilities": {"get": {"summary": "Protocol + multimodal capability matrix", "responses": {"200": {"description": "Caps"}}}},
            "/v1/dashboard/billing/subscription": {"get": {"summary": "Billing subscription", "responses": {"200": {"description": "Sub"}}}},
            "/v1/dashboard/billing/usage": {"get": {"summary": "Billing usage", "responses": {"200": {"description": "Usage"}}}},
        },
    }


def serve(settings: Settings) -> None:
    state = BridgeState(settings)
    handler = make_handler(state)
    httpd = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(f"[bridge] listening on http://{settings.host}:{settings.port}")
    print(f"[bridge] upstream base: {settings.base_url}")
    print(f"[bridge] auth: {state.note}")
    if state.session and state.session.has_session():
        print(f"[bridge] user_id: {state.session.user_id or state.session.cookie_dict().get('userId', '')}")
    else:
        print("[bridge] WARNING: no Xiaomi session. OpenAI/plan endpoints will 401 until you run auth.")
    print("[bridge] endpoints: /v1/models /v1/chat/completions /v1/messages /plan /usage /account /health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] bye")
    finally:
        httpd.server_close()
