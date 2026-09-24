"""End-to-end smoke test against a live or in-process bridge.

Usage:
  python -m tests.smoke_live              # spawn in-process server on 8790
  python -m tests.smoke_live --url http://127.0.0.1:8787
  python -m tests.smoke_live --chat       # also call chat (needs auth)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mimo_bridge.config import AuthSession, Settings  # noqa: E402
from mimo_bridge.server import BridgeState, make_handler  # noqa: E402


def req(url: str, method: str = "GET", body: dict | None = None, timeout: float = 8.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.status, json.loads(raw.decode() or "{}")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode() or "{}")
        except Exception:
            return e.code, raw[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    ap.add_argument("--chat", action="store_true")
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()

    httpd = None
    base = args.url.rstrip("/") if args.url else f"http://127.0.0.1:{args.port}"
    if not args.url:
        settings = Settings()
        settings.host = "127.0.0.1"
        settings.port = args.port
        settings.bridge_api_key = ""
        state = BridgeState(settings)
        # fake session so /plan etc. attempt upstream (may 502 without cookie — still valid path test)
        if not state.session:
            state.session = AuthSession(cookie_header="userId=smoke; passToken=x; serviceToken=y", user_id="smoke", source="smoke")
        handler = make_handler(state)
        from http.server import ThreadingHTTPServer

        httpd = ThreadingHTTPServer((settings.host, settings.port), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.2)

    checks = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(("PASS" if ok else "FAIL"), name, detail)

    st, body = req(f"{base}/health")
    check("health", st == 200 and isinstance(body, dict) and body.get("ok") is True, f"{st}")

    st, body = req(f"{base}/openapi.json")
    check(
        "openapi",
        st == 200 and isinstance(body, dict) and "/v1/responses" in body.get("paths", {}),
        f"{st}",
    )

    st, body = req(f"{base}/v1/capabilities")
    check(
        "capabilities",
        st == 200 and "openai.responses" in (body.get("protocols") or []),
        f"{st}",
    )

    st, raw = req(f"{base}/ui")
    check("ui", st == 200 and b"MiMo Agent Bridge" in raw, f"{st}")

    st, body = req(f"{base}/v1/models")
    # without real cookie this may 401/502 — accept as reachable
    check("models-reachable", st in (200, 401, 502), f"{st}")

    st, body = req(
        f"{base}/v1/chat/completions",
        "POST",
        {"model": "mimo-v2.6-pro", "messages": [{"role": "user", "content": "ping"}], "stream": False},
    )
    check("chat-reachable", st in (200, 401, 502), f"{st}")

    st, body = req(
        f"{base}/v1/responses",
        "POST",
        {"model": "mimo-v2.6-pro", "input": "ping", "stream": False},
    )
    check("responses-reachable", st in (200, 401, 502), f"{st}")

    if args.chat and st == 200:
        check("chat-content", isinstance(body, dict), "live ok")

    failed = [c for c in checks if not c[1]]
    print("-" * 40)
    print(f"{len(checks) - len(failed)}/{len(checks)} passed")
    if httpd:
        httpd.shutdown()
        httpd.server_close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
