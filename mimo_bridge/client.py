from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from .config import AuthSession, Settings


class UpstreamError(Exception):
    def __init__(self, status: int, body: str, kind: str = "failed"):
        super().__init__(f"upstream {status}: {body[:300]}")
        self.status = status
        self.body = body
        self.kind = kind


class MimoClient:
    def __init__(self, settings: Settings, session: AuthSession):
        self.settings = settings
        self.session = session
        base = session.base_url or settings.base_url
        self.base_url = base.rstrip("/")

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Cookie": self.session.merged_header(),
            "X-Client-Version": self.settings.client_version,
            "User-Agent": f"mimo-agent-bridge/{self.settings.client_version}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        stream: bool = False,
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> urllib.response.addinfourl | dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = self._headers(extra_headers)
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        timeout = timeout if timeout is not None else self.settings.request_timeout
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            kind = "auth-expired" if e.code == 401 else "failed"
            raise UpstreamError(e.code, raw, kind=kind) from e
        except urllib.error.URLError as e:
            raise UpstreamError(0, str(e.reason), kind="network") from e

        if stream:
            return resp
        raw = resp.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise UpstreamError(resp.status, raw, kind="malformed") from e
        if isinstance(payload, dict) and payload.get("code") not in (0, None):
            if "choices" in payload or "object" in payload:
                return payload
            raise UpstreamError(resp.status, raw, kind="business")
        return payload

    def account_get(self, path: str) -> Any:
        payload = self.request("GET", path)
        if isinstance(payload, dict) and payload.get("code") not in (0, None):
            raise UpstreamError(200, json.dumps(payload, ensure_ascii=False), kind="business")
        return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    def me(self) -> Any:
        return self.account_get("/user/xiaomi/me")

    def subscription(self) -> Any:
        return self.account_get("/user/xiaomi/subscription/self")

    def usage(self) -> Any:
        return self.account_get("/user/usage")

    def model_list(self) -> Any:
        return self.account_get("/model/list")

    def chat_completions(self, body: dict[str, Any], stream: bool = False):
        payload = dict(body)
        payload["stream"] = bool(stream)
        return self.request("POST", "/route/chat/completions", payload, stream=stream)

    def images_generations(self, body: dict[str, Any]) -> Any:
        headers = {"X-Mimo-Source": "mimocode-desktop"}
        return self.request("POST", "/route/images/generations", body, extra_headers=headers)

    def iter_sse(self, resp: urllib.response.addinfourl) -> Iterator[dict[str, Any]]:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


def normalize_model(model: str) -> str:
    from .config import MODEL_ALIASES

    m = (model or "").strip()
    return MODEL_ALIASES.get(m, MODEL_ALIASES.get(m.lower(), m))


def models_payload(raw: Any) -> dict[str, Any]:
    items = []
    models = []
    if isinstance(raw, dict):
        models = raw.get("models") or []
    elif isinstance(raw, list):
        models = raw
    seen = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        mid = item.get("modelName") or item.get("id") or ""
        if not mid or mid in seen:
            continue
        seen.add(mid)
        items.append(
            {
                "id": mid,
                "object": "model",
                "created": 0,
                "owned_by": "xiaomi-mimo",
                "description": item.get("description") or item.get("name") or "",
                "model_type": item.get("modelType") or "",
                "display_ratio": item.get("displayRatio"),
            }
        )
    for alias, target in (("mimo-auto", "mimo-v2.6-pro"),):
        if alias not in seen:
            items.append({"id": alias, "object": "model", "created": 0, "owned_by": "alias", "description": f"alias of {target}"})
    return {"object": "list", "data": items}
