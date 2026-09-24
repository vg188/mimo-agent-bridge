from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mimo_bridge.config import AuthSession, Settings  # noqa: E402
from mimo_bridge.formats import anthropic_to_openai, openai_to_anthropic  # noqa: E402
from mimo_bridge.multimodal import build_asr_body, build_tts_body, normalize_openai_messages  # noqa: E402
from mimo_bridge.responses import chat_to_responses, responses_to_chat  # noqa: E402
from mimo_bridge.server import BridgeState, make_handler  # noqa: E402


def test_multimodal_and_responses() -> None:
    msgs, extras = normalize_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述这张图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64,AAAA", "format": "wav"}},
                ],
            }
        ]
    )
    assert msgs[0]["content"][1]["type"] == "image_url"
    assert msgs[0]["content"][2]["input_audio"]["format"] == "wav"

    tts = build_tts_body("你好", voice="mimo_default", fmt="mp3")
    assert tts["model"] == "mimo-v2.5-tts"
    assert tts["audio"]["format"] == "mp3"

    asr = build_asr_body("data:audio/wav;base64,AAAA")
    assert asr["model"] == "mimo-v2.5-asr"
    assert asr["asr_options"]["language"] == "auto"

    chat_req = responses_to_chat(
        {
            "model": "mimo-v2.6-pro",
            "instructions": "be brief",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "max_output_tokens": 64,
        },
        "mimo-v2.6-pro",
    )
    assert chat_req["messages"][0]["role"] == "system"
    assert chat_req["max_tokens"] == 64

    resp = chat_to_responses(
        {
            "choices": [{"message": {"content": "hello", "reasoning_content": "think"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
        "mimo-v2.6-pro",
    )
    assert resp["object"] == "response"
    assert resp["output_text"] == "hello"
    assert resp["usage"]["total_tokens"] == 3
    print("multimodal+responses ok")


def test_formats() -> None:
    oai = anthropic_to_openai(
        {
            "model": "mimo-v2.6-pro",
            "max_tokens": 128,
            "system": "be brief",
            "messages": [{"role": "user", "content": "hi"}],
        },
        "mimo-v2.6-pro",
    )
    assert oai["messages"][0]["role"] == "system"
    assert oai["messages"][1]["content"] == "hi"
    assert oai["max_tokens"] == 128

    anth = openai_to_anthropic(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
        "mimo-v2.6-pro",
    )
    assert anth["content"][0]["text"] == "hello"
    assert anth["stop_reason"] == "end_turn"
    print("formats ok")


def test_server_routes() -> None:
    settings = Settings()
    settings.host = "127.0.0.1"
    settings.port = 8799
    settings.bridge_api_key = ""
    settings.auth_path.parent.mkdir(parents=True, exist_ok=True)
    state = BridgeState(settings)
    state.session = AuthSession(cookie_header="userId=1; passToken=x; serviceToken=y", user_id="1", source="test")
    handler = make_handler(state)
    httpd = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
        (settings.host, settings.port), handler
    )
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.port}/health", timeout=3) as r:
            data = json.loads(r.read().decode())
            assert data["ok"] is True
            assert data["authenticated"] is True
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.port}/openapi.json", timeout=3) as r:
            spec = json.loads(r.read().decode())
            assert "/v1/chat/completions" in spec["paths"]
            assert "/v1/responses" in spec["paths"]
            assert "/v1/audio/speech" in spec["paths"]
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.port}/v1/capabilities", timeout=3) as r:
            caps = json.loads(r.read().decode())
            assert "openai.responses" in caps["protocols"]
            assert "mimo-v2.5-asr" in caps["models"]
        print("server routes ok")
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    test_multimodal_and_responses()
    test_formats()
    test_server_routes()
    print("all tests passed")
