from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://mimo-server-cn.xiaomimimo.com/api"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_CLIENT_VERSION = "26.923.232338"

MODEL_ALIASES = {
    "mimo-auto": "mimo-v2.6-pro",
    "mimo-pro": "mimo-v2.6-pro",
    "mimo-flash": "mimo-v2.6-flash",
    "gpt-4o": "mimo-v2.6-pro",
    "gpt-4o-mini": "mimo-v2.6-flash",
    "claude-3-5-sonnet": "mimo-v2.6-pro",
    "claude-3-5-haiku": "mimo-v2.6-flash",
    "claude-sonnet-4": "mimo-v2.6-pro",
    "claude-haiku-4": "mimo-v2.6-flash",
}


def app_data_dir() -> Path:
    """MiMo Desktop userData directory on this OS."""
    env = os.environ.get("MIMO_APP_DATA")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", "")) / "Xiaomi MiMo"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "Xiaomi MiMo"
    return Path.home() / ".config" / "Xiaomi MiMo"


def sys_platform() -> str:
    import sys

    return sys.platform


def default_config_dir() -> Path:
    env = os.environ.get("MIMO_BRIDGE_HOME")
    if env:
        return Path(env)
    return Path.home() / ".mimo-agent-bridge"


@dataclass
class AuthSession:
    """Xiaomi business session cookies used by MiMo Desktop."""

    cookie_header: str = ""
    user_id: str = ""
    display_name: str = ""
    region: str = "CN"
    source: str = "manual"
    base_url: str = ""

    def cookie_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in self.cookie_header.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                out[k] = v
        return out

    def merged_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookie_dict().items())

    def has_session(self) -> bool:
        names = set(self.cookie_dict())
        return bool(names & {"serviceToken", "passToken"} or any(n.endswith("_serviceToken") for n in names))


@dataclass
class Settings:
    base_url: str = field(default_factory=lambda: os.environ.get("MIMO_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/"))
    host: str = field(default_factory=lambda: os.environ.get("MIMO_BRIDGE_HOST", DEFAULT_HOST))
    port: int = field(default_factory=lambda: int(os.environ.get("MIMO_BRIDGE_PORT", DEFAULT_PORT)))
    client_version: str = field(
        default_factory=lambda: os.environ.get("MIMO_CLIENT_VERSION", DEFAULT_CLIENT_VERSION)
    )
    bridge_api_key: str = field(default_factory=lambda: os.environ.get("MIMO_BRIDGE_API_KEY", ""))
    request_timeout: float = field(default_factory=lambda: float(os.environ.get("MIMO_BRIDGE_TIMEOUT", "120")))
    config_dir: Path = field(default_factory=default_config_dir)

    @property
    def auth_path(self) -> Path:
        return self.config_dir / "auth.json"

    @property
    def cache_models_path(self) -> Path:
        return self.config_dir / "models.json"


def load_auth(settings: Settings) -> AuthSession | None:
    path = settings.auth_path
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    session = AuthSession(
        cookie_header=str(raw.get("cookie_header") or ""),
        user_id=str(raw.get("user_id") or ""),
        display_name=str(raw.get("display_name") or ""),
        region=str(raw.get("region") or "CN"),
        source=str(raw.get("source") or "file"),
        base_url=str(raw.get("base_url") or ""),
    )
    return session if session.cookie_header else None


def save_auth(settings: Settings, session: AuthSession) -> Path:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(session)
    path = settings.auth_path
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def settings_from_env() -> Settings:
    return Settings()


def dump_public(session: AuthSession | None) -> dict[str, Any]:
    if not session:
        return {"authenticated": False}
    cookies = session.cookie_dict()
    return {
        "authenticated": session.has_session(),
        "user_id": session.user_id or cookies.get("userId", ""),
        "display_name": session.display_name,
        "region": session.region,
        "source": session.source,
        "cookie_names": sorted(cookies),
    }
