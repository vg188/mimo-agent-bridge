from __future__ import annotations

import argparse
import json
import os
import sys

from .auth import auth_status, extract_from_cookie_db, resolve_session, session_from_cookie_header, wait_and_extract
from .client import MimoClient, UpstreamError, models_payload, normalize_model
from .config import dump_public, load_auth, save_auth, settings_from_env
from .server import serve


def cmd_auth(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    if args.status:
        print(json.dumps(auth_status(settings), ensure_ascii=False, indent=2))
        return 0
    if args.extract or getattr(args, "auto", False):
        wait_s = float(getattr(args, "wait", 0) or 0)
        session = extract_from_cookie_db(verbose=True)
        if not (session and session.has_session()) and wait_s > 0:
            print(f"[auto] Cookie 库被 MiMo Desktop 占用，等待解锁 {wait_s:.0f}s…", file=sys.stderr)
            print("[auto] 现在可以：关闭 MiMo Desktop（约 2 秒）→ 将自动识别，无需粘贴 Cookie", file=sys.stderr)
            session = wait_and_extract(timeout_s=wait_s, verbose=True)
        if not session or not session.has_session():
            print("auto extract failed (cookie DB still locked).", file=sys.stderr)
            print("最简单：关闭 MiMo Desktop 后执行 mimo-bridge auth --auto --wait 30", file=sys.stderr)
            print("或粘贴 Cookie: mimo-bridge auth --cookie 'userId=...; passToken=...; serviceToken=...'", file=sys.stderr)
            return 2
        path = save_auth(settings, session)
        print(json.dumps({"ok": True, "saved": str(path), "auth": dump_public(session)}, ensure_ascii=False, indent=2))
        return 0
    cookie_raw = args.cookie
    if getattr(args, "cookie_file", None):
        cookie_raw = open(args.cookie_file, encoding="utf-8").read()
    if cookie_raw:
        session = session_from_cookie_header(cookie_raw, source="cli")
        if not session.has_session():
            print("cookie missing passToken/serviceToken", file=sys.stderr)
            return 2
        path = save_auth(settings, session)
        # Fill profile from /me if possible.
        try:
            me = MimoClient(settings, session).me()
            if isinstance(me, dict):
                session.user_id = str(me.get("userId") or session.user_id or "")
                for key in ("nickname", "nickName", "name", "displayName", "userName"):
                    if me.get(key):
                        session.display_name = str(me[key])
                        break
                if me.get("region"):
                    session.region = str(me["region"])
                save_auth(settings, session)
        except UpstreamError as e:
            print(f"warning: /user/xiaomi/me failed: {e}", file=sys.stderr)
        print(json.dumps({"ok": True, "saved": str(path), "auth": dump_public(session)}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(auth_status(settings), ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.base_url:
        settings.base_url = args.base_url.rstrip("/")
    if args.cookie:
        session = session_from_cookie_header(args.cookie, source="cli")
        save_auth(settings, session)
    serve(settings)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    session, note = resolve_session(settings, args.cookie)
    if not session or not session.has_session():
        print(json.dumps({"ok": False, "error": note}, ensure_ascii=False, indent=2))
        return 2
    client = MimoClient(settings, session)
    try:
        sub = client.subscription()
        usage = client.usage()
        me = client.me()
    except UpstreamError as e:
        print(json.dumps({"ok": False, "error": str(e), "kind": e.kind}, ensure_ascii=False, indent=2))
        return 1
    current = (sub or {}).get("current") if isinstance(sub, dict) else None
    out = {
        "ok": True,
        "auth": dump_public(session),
        "account": me,
        "current_plan": current,
        "subscription": sub,
        "usage": usage,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    session, note = resolve_session(settings, args.cookie)
    if not session or not session.has_session():
        print(json.dumps({"ok": False, "error": note}, ensure_ascii=False, indent=2))
        return 2
    try:
        raw = MimoClient(settings, session).model_list()
    except UpstreamError as e:
        print(json.dumps({"ok": False, "error": str(e), "kind": e.kind}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(models_payload(raw), ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import print_doctor, run_doctor

    report = run_doctor(check_upstream=bool(getattr(args, "upstream", False)))
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_doctor(report)
    return 0 if report.get("ok") else 1


def cmd_chat(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    session, note = resolve_session(settings, args.cookie)
    if not session or not session.has_session():
        print(json.dumps({"ok": False, "error": note}, ensure_ascii=False, indent=2))
        return 2
    model = normalize_model(args.model)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": bool(args.stream),
    }
    if args.max_tokens:
        body["max_tokens"] = args.max_tokens
    client = MimoClient(settings, session)
    try:
        if not args.stream:
            payload = client.chat_completions(body, stream=False)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        resp = client.chat_completions(body, stream=True)
        for chunk in client.iter_sse(resp):
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
        sys.stdout.write("\n")
        return 0
    except UpstreamError as e:
        print(json.dumps({"ok": False, "error": str(e), "kind": e.kind, "status": e.status}, ensure_ascii=False), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mimo-bridge",
        description="MiMo Desktop subscription → local OpenAI/Anthropic API bridge for personal agents.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="Import Xiaomi session cookies from MiMo Desktop")
    auth.add_argument("--cookie", help="Cookie header from DevTools (userId/passToken/serviceToken...)")
    auth.add_argument("--cookie-file", help="File containing Cookie header text")
    auth.add_argument("--extract", action="store_true", help="Try extract from Chromium cookie DB")
    auth.add_argument("--auto", action="store_true", help="Auto-detect session (recommended)")
    auth.add_argument("--wait", type=float, default=0, help="Seconds to wait for cookie DB unlock (e.g. 60)")
    auth.add_argument("--status", action="store_true", help="Show current auth status")
    auth.set_defaults(func=cmd_auth)

    serve_p = sub.add_parser("serve", help="Start local API gateway")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--base-url", default=None, help="Upstream base, default https://mimo-server-cn.xiaomimimo.com/api")
    serve_p.add_argument("--cookie", help="Optional cookie header to save before serving")
    serve_p.set_defaults(func=cmd_serve)

    plan = sub.add_parser("plan", help="Show current plan + usage")
    plan.add_argument("--cookie", default=None)
    plan.set_defaults(func=cmd_plan)

    models = sub.add_parser("models", help="List available models")
    models.add_argument("--cookie", default=None)
    models.set_defaults(func=cmd_models)

    doctor = sub.add_parser("doctor", help="Environment / auth / upstream self-check")
    doctor.add_argument("--upstream", action="store_true", help="Also ping upstream /user/xiaomi/me")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    chat = sub.add_parser("chat", help="Quick chat smoke test")
    chat.add_argument("prompt", help="User prompt")
    chat.add_argument("--model", default="mimo-v2.6-pro")
    chat.add_argument("--stream", action="store_true")
    chat.add_argument("--max-tokens", type=int, default=None)
    chat.add_argument("--cookie", default=None)
    chat.set_defaults(func=cmd_chat)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
