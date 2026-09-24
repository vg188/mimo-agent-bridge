from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .auth import auth_status, chrome_cookie_db_candidates
from .config import app_data_dir, default_config_dir, settings_from_env


def _ok(label: str, detail: str = "") -> dict:
    return {"level": "ok", "label": label, "detail": detail}


def _warn(label: str, detail: str = "") -> dict:
    return {"level": "warn", "label": label, "detail": detail}


def _fail(label: str, detail: str = "") -> dict:
    return {"level": "fail", "label": label, "detail": detail}


def run_doctor(check_upstream: bool = False) -> dict:
    settings = settings_from_env()
    items: list[dict] = []

    # runtime
    items.append(_ok("Python", sys.version.split()[0] + f" ({platform.platform()})"))

    # paths
    app_data = app_data_dir()
    items.append(
        _ok("MiMo Desktop data", str(app_data))
        if app_data.exists()
        else _warn("MiMo Desktop data", f"missing: {app_data}")
    )
    cfg = settings.config_dir
    items.append(_ok("Bridge config dir", str(cfg)))

    # cookie db
    readable = []
    locked = []
    for p in chrome_cookie_db_candidates():
        if not p.exists():
            continue
        try:
            with open(p, "rb") as f:
                f.read(1)
            readable.append(str(p))
        except OSError:
            locked.append(str(p))
    if readable:
        items.append(_ok("Cookie DB readable", "; ".join(readable)))
    if locked:
        items.append(_warn("Cookie DB locked", "; ".join(locked) + " → 用 DevTools 粘贴 Cookie 或关闭 Desktop 后 --extract"))

    # auth
    auth = auth_status(settings)
    if auth.get("authenticated"):
        items.append(_ok("Xiaomi session", f"user_id={auth.get('user_id') or '?'} source={auth.get('source')}"))
    else:
        items.append(_fail("Xiaomi session", "未导入。运行: mimo-bridge auth --cookie '...'"))

    # local port free-ish
    items.append(_ok("Listen target", f"http://{settings.host}:{settings.port}"))

    # upstream ping when requested
    if check_upstream:
        url = settings.base_url.rstrip("/") + "/user/xiaomi/me"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()[:120].decode("utf-8", "replace")
                items.append(_ok("Upstream reachability", f"HTTP {resp.status} {body}"))
        except urllib.error.HTTPError as e:
            items.append(_warn("Upstream reachability", f"HTTP {e.code} (未带 Cookie 时 401 正常)"))
        except Exception as e:  # noqa: BLE001
            items.append(_fail("Upstream reachability", str(e)))

    # python package import
    try:
        import mimo_bridge  # noqa: F401

        items.append(_ok("Package import", f"mimo_bridge {getattr(mimo_bridge, '__version__', '')}"))
    except Exception as e:  # noqa: BLE001
        items.append(_fail("Package import", str(e)))

    # tauri binary hint
    root = Path(__file__).resolve().parents[1]
    name = "mimo-agent-bridge-ui.exe" if os.name == "nt" else "mimo-agent-bridge-ui"
    candidates = [
        root / "ui" / name,
        root / "ui" / "src-tauri" / "target" / "release" / name,
        root / "ui" / "src-tauri" / "target" / "debug" / name,
    ]
    found = next((p for p in candidates if p.exists()), None)
    if found:
        items.append(_ok("Tauri UI", str(found)))
        loader = found.parent / "WebView2Loader.dll"
        if loader.exists():
            items.append(_ok("WebView2Loader.dll", str(loader)))
        else:
            items.append(
                _fail(
                    "WebView2Loader.dll",
                    f"缺失，需与 exe 同目录: {loader}（否则启动报「找不到 WebView2Loader.dll」）",
                )
            )
    else:
        items.append(_warn("Tauri UI", "未找到可执行文件。见 README「Tauri 控制台」"))

    failed = any(i["level"] == "fail" for i in items)
    return {
        "ok": not failed,
        "base_url": settings.base_url,
        "bridge_home": str(settings.config_dir),
        "items": items,
        "next": (
            "auth --cookie '...' 然后 serve"
            if not auth.get("authenticated")
            else "serve 并打开 /ui 或 Tauri"
        ),
    }


def print_doctor(report: dict) -> None:
    icon = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
    print(f"mimo-agent-bridge doctor  ·  base={report['base_url']}")
    print(f"config: {report['bridge_home']}")
    print("-" * 64)
    for item in report["items"]:
        line = f"{icon[item['level']]} {item['label']}"
        if item.get("detail"):
            line += f" — {item['detail']}"
        print(line)
    print("-" * 64)
    print("next:", report["next"])


if __name__ == "__main__":
    print_doctor(run_doctor(check_upstream="--upstream" in sys.argv))
