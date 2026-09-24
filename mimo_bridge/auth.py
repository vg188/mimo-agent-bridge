from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import struct
import sys
from pathlib import Path

from .config import AuthSession, Settings, app_data_dir, dump_public, load_auth, save_auth

# Cookie names that indicate a Xiaomi business session.
SESSION_COOKIE_RE = re.compile(r"(^|_)(serviceToken|passToken|_ph)$")


def parse_cookie_header(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    text = raw.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    for part in text.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            out[k] = v
    return out


def cookie_header_from_pairs(pairs: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def session_from_cookie_header(raw: str, source: str = "manual") -> AuthSession:
    pairs = parse_cookie_header(raw)
    return AuthSession(
        cookie_header=cookie_header_from_pairs(pairs),
        user_id=pairs.get("userId", ""),
        display_name="",
        region="CN",
        source=source,
    )


def chrome_cookie_db_candidates() -> list[Path]:
    root = app_data_dir()
    return [
        root / "Partitions" / "xiaomi-account" / "Network" / "Cookies",
        root / "Network" / "Cookies",
        root / "Partitions" / "xiaomi-account" / "Cookies",
    ]


def _copy_unlocked(src: Path, dst: Path) -> bool:
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        pass
    try:
        data = src.read_bytes()
        dst.write_bytes(data)
        return True
    except OSError:
        return False


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("DPAPI only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _aes_gcm_decrypt(key: bytes, blob: bytes) -> bytes:
    """Decrypt Chromium v10+ cookie value: b'v10' + 12B nonce + ciphertext + 16B tag."""
    if blob[:3] not in (b"v10", b"v11"):
        # Older DPAPI-only values.
        return _dpapi_unprotect(blob)
    nonce = blob[3:15]
    tag = blob[-16:]
    ct = blob[15:-16]
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

        return AESGCM(key).decrypt(nonce, ct + tag, None)
    except Exception:
        pass
    return _aes_gcm_bcrypt(key, nonce, ct, tag)


def _aes_gcm_bcrypt(key: bytes, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    BCRYPT_AES_ALGORITHM = "AES"
    BCRYPT_CHAIN_MODE_GCM = "ChainingModeGCM"
    BCRYPT_AUTH_MODE_CHAIN_CALLS_FLAG = 0x00000001

    class BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("dwInfoVersion", wintypes.ULONG),
            ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbNonce", wintypes.ULONG),
            ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbAuthData", wintypes.ULONG),
            ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbTag", wintypes.ULONG),
            ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbMacContext", wintypes.ULONG),
            ("cbAAD", wintypes.ULONG),
            ("cbData", wintypes.ULONGLONG),
            ("dwFlags", wintypes.ULONG),
        ]

    class BCRYPT_KEY_DATA_BLOB_HEADER(ctypes.Structure):
        _fields_ = [
            ("dwMagic", wintypes.ULONG),
            ("dwVersion", wintypes.ULONG),
            ("cbKeyData", wintypes.ULONG),
        ]

    bcrypt = ctypes.WinDLL("bcrypt")
    alg = wintypes.HANDLE()
    key_handle = wintypes.HANDLE()
    flags = BCRYPT_AUTH_MODE_CHAIN_CALLS_FLAG

    nt = bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), BCRYPT_AES_ALGORITHM, None, 0)
    if nt:
        raise OSError(f"BCryptOpenAlgorithmProvider {nt}")
    try:
        prop = BCRYPT_CHAIN_MODE_GCM.encode("utf-16-le") + b"\x00\x00"
        nt = bcrypt.BCryptSetProperty(
            alg, BCRYPT_CHAIN_MODE_GCM.encode("utf-16-le"), prop, len(prop), 0
        )
        if nt:
            raise OSError(f"BCryptSetProperty {nt}")
        # BCRYPT_KEY_DATA_BLOB_MAGIC = 0x4D42444B, serialized as little-endian III + key bytes.
        blob = struct.pack("<III", 0x4D42444B, 1, len(key)) + key
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        nt = bcrypt.BCryptImportKey(alg, None, BCRYPT_KEY_DATA_BLOB_HEADER, ctypes.byref(key_handle), None, 0, buf, len(blob), 0)
        if nt:
            raise OSError(f"BCryptImportKey {nt}")
        try:
            info = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = 1
            nonce_buf = (ctypes.c_ubyte * len(nonce)).from_buffer_copy(nonce)
            tag_buf = (ctypes.c_ubyte * len(tag)).from_buffer_copy(tag)
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbNonce = len(nonce)
            info.pbTag = ctypes.cast(tag_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbTag = len(tag)
            out = (ctypes.c_ubyte * len(ct)).from_buffer_copy(ct)
            out_len = wintypes.ULONG()
            nt = bcrypt.BCryptDecrypt(
                key_handle,
                out,
                len(ct),
                ctypes.byref(info),
                None,
                0,
                out,
                len(ct),
                ctypes.byref(out_len),
                0,
            )
            if nt:
                raise OSError(f"BCryptDecrypt {nt}")
            tag[:] = info.pbTag[: info.cbTag] if False else tag[:]  # tag is verify-only
            return bytes(out[: out_len.value])
        finally:
            bcrypt.BCryptDestroyKey(key_handle)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def _load_chromium_key(user_data_dir: Path) -> bytes:
    local_state = user_data_dir / "Local State"
    raw = json.loads(local_state.read_text(encoding="utf-8"))
    enc_key_b64 = raw["os_crypt"]["encrypted_key"]
    import base64

    encrypted = base64.b64decode(enc_key_b64)
    if encrypted.startswith(b"DPAPI"):
        encrypted = encrypted[5:]
    return _dpapi_unprotect(encrypted)


def try_copy_locked(src: Path, dst: Path) -> bool:
    """Try multiple ways to snapshot a Chromium cookie DB even if Desktop holds it."""
    # 1) plain copy
    if _copy_unlocked(src, dst):
        return True
    # 2) esentutl /y (VSS-less file copy, sometimes works)
    if os.name == "nt":
        try:
            import subprocess

            r = subprocess.run(
                ["esentutl", "/y", str(src), "/d", str(dst), "/o"],
                capture_output=True,
                timeout=15,
            )
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                return True
        except Exception:
            pass
        # 3) robocopy backup mode
        try:
            import subprocess

            parent = src.parent
            r = subprocess.run(
                ["robocopy", str(parent), str(dst.parent), src.name, "/B", "/COPY:DAT", "/R:0", "/W:0"],
                capture_output=True,
                timeout=15,
            )
            candidate = dst.parent / src.name
            if candidate.exists() and candidate.stat().st_size > 0:
                if candidate != dst:
                    try:
                        dst.write_bytes(candidate.read_bytes())
                    except OSError:
                        return candidate.exists()
                return True
        except Exception:
            pass
    return False


def wait_and_extract(timeout_s: float = 60.0, interval_s: float = 0.4, verbose: bool = False) -> AuthSession | None:
    """
    Auto-recover session while MiMo Desktop holds the cookie DB exclusive lock.
    The moment Desktop releases it (user quits Desktop, or it restarts), we snapshot and decrypt.
    """
    import time as _time

    deadline = _time.time() + max(0.0, timeout_s)
    attempt = 0
    while True:
        attempt += 1
        session = extract_from_cookie_db(verbose=verbose)
        if session and session.has_session():
            return session
        if _time.time() >= deadline:
            return None
        if verbose and attempt % 5 == 1:
            print(f"[auto] waiting for cookie DB unlock… ({int(max(0, deadline - _time.time()))}s left)")
        _time.sleep(interval_s)


def extract_from_cookie_db(db_path: Path | None = None, verbose: bool = False) -> AuthSession | None:
    """Best-effort extract Xiaomi session cookies from Chromium cookie DB."""
    candidates = [db_path] if db_path else chrome_cookie_db_candidates()
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "mimo-bridge-cookies.db"
    key = None
    try:
        key = _load_chromium_key(app_data_dir())
        if verbose:
            print(f"[extract] chromium key loaded ({len(key)} bytes)")
    except Exception as e:
        key = None
        if verbose:
            print(f"[extract] chromium key unavailable: {e}")

    for src in candidates:
        if not src or not src.exists():
            if verbose:
                print(f"[extract] skip missing {src}")
            continue
        if not try_copy_locked(src, tmp):
            if verbose:
                print(f"[extract] locked/unreadable {src}")
            continue
        try:
            conn = sqlite3.connect(f"file:{tmp}?immutable=1", uri=True)
            rows = conn.execute(
                "select name, encrypted_value, value, host_key from cookies"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        pairs: dict[str, str] = {}
        for name, enc, plain, host in rows:
            if not name:
                continue
            value = plain or ""
            if not value and enc:
                if key is None:
                    continue
                try:
                    value = _aes_gcm_decrypt(key, enc).decode("utf-8", "replace")
                except Exception:
                    continue
            if not value:
                continue
            # Keep cookies useful for mimo-server / xiaomi auth.
            if any(s in (host or "") for s in ("xiaomimimo.com", "xiaomi.com", "mi.com")) or name in {
                "userId",
                "passToken",
                "cUserId",
                "serviceToken",
            }:
                pairs[name] = value
        if verbose:
            print(f"[extract] {src}: decrypted_names={sorted(pairs)}")
        if pairs and ({"passToken", "userId"} <= set(pairs) or any(n.endswith("_serviceToken") for n in pairs)):
            return session_from_cookie_header(cookie_header_from_pairs(pairs), source=f"cookie-db:{src}")
    return None


def resolve_session(settings: Settings, cookie_arg: str | None = None) -> tuple[AuthSession | None, str]:
    """Resolve auth from CLI/env/file/cookie-db. Returns (session, note)."""
    if cookie_arg:
        s = session_from_cookie_header(cookie_arg, source="cli")
        return s, "loaded cookie from CLI/env"
    env_cookie = os.environ.get("MIMO_COOKIE") or os.environ.get("MIMO_BRIDGE_COOKIE")
    if env_cookie:
        return session_from_cookie_header(env_cookie, source="env"), "loaded cookie from MIMO_COOKIE"

    existing = load_auth(settings)
    if existing and existing.has_session():
        return existing, f"loaded cookie from {settings.auth_path}"

    extracted = extract_from_cookie_db(verbose=False)
    if extracted and extracted.has_session():
        save_auth(settings, extracted)
        return extracted, f"extracted from cookie DB and saved to {settings.auth_path}"

    return None, (
        "no session found. While MiMo Desktop is running the cookie DB is locked.\n"
        "Fix: DevTools → Application → Cookies → copy Cookie header, then:\n"
        "  mimo-bridge auth --cookie 'userId=...; passToken=...; serviceToken=...'\n"
        "Or close MiMo Desktop briefly and run: mimo-bridge auth --extract"
    )


def auth_status(settings: Settings) -> dict:
    session = load_auth(settings)
    info = dump_public(session)
    dbs = chrome_cookie_db_candidates()
    info["cookie_db_paths"] = [str(p) for p in dbs]
    probe = Path(os.environ.get("TEMP", "/tmp")) / "mimo-probe.db"
    readable = []
    for p in dbs:
        if p.exists() and _copy_unlocked(p, probe):
            readable.append(str(p))
    info["cookie_db_readable"] = readable
    return info
