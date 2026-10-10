"""On-demand authenticated localhost JSON API. GUI and CLI need no server."""
from __future__ import annotations

import contextlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, unquote, urlparse

from .types import LumenError


def api_token(state_dir=None):
    from .jobs import _atomic, _lock, state_root
    path = state_root(state_dir) / "api_token.json"
    with _lock(path.with_suffix(".lock")):
        if path.is_file():
            try:
                token = json.loads(path.read_text())["token"]
                if isinstance(token, str) and len(token) >= 32:
                    return token
            except (ValueError, KeyError, OSError):
                pass
        token = secrets.token_urlsafe(32)
        _atomic(path, {"token": token})
        return token


def create_server(state_dir=None, *, host="127.0.0.1", port=0, token=None, dispatcher=None):
    from .service import dispatch
    if host != "127.0.0.1":
        raise LumenError("invalid_host", "本机 API 只允许绑定 127.0.0.1")
    dispatcher = dispatcher or dispatch
    token = token or api_token(state_dir)
    if not isinstance(token, str) or len(token) < 16 or not token.isascii():
        raise LumenError("invalid_token", "API token 至少需要 16 个字符")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            header = self.headers.get("Authorization", "")
            if not hmac.compare_digest(header, "Bearer " + token):
                self.reply(401, {"ok": False, "error": {"code": "unauthorized", "message": "需要本机 API bearer token"}})
                return False
            origin = self.headers.get("Origin")
            if origin:
                self.reply(403, {"ok": False, "error": {"code": "origin_denied", "message": "此接口用于本机客户端，不接受网页跨来源请求"}})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/schema":
                from .cli import schema
                self.reply(200, {"ok": True, "action": "schema", "result": schema()})
                return
            if parsed.path == "/api/status":
                action, params = "status", {}
            elif parsed.path == "/api/documents":
                action, params = "list", {key: values[-1] for key, values in parse_qs(parsed.query).items() if key in ("query", "project", "kind", "tag")}
            elif parsed.path.startswith("/api/documents/"):
                action, params = "get", {"document_id": unquote(parsed.path[len("/api/documents/"):])}
                query = parse_qs(parsed.query)
                if "version" in query:
                    try:
                        params["version"] = int(query["version"][-1])
                    except ValueError:
                        self.reply(400, {"ok": False, "error": {"code": "invalid_request", "message": "version 必须是整数"}})
                        return
            elif parsed.path == "/api/tasks":
                action, params = "tasks", {}
            elif parsed.path.startswith("/api/tasks/"):
                action, params = "task", {"task_id": unquote(parsed.path[len("/api/tasks/"):])}
            else:
                self.reply(404, {"ok": False, "error": {"code": "not_found", "message": "接口不存在"}})
                return
            result = dispatcher(action, params, state_dir)
            self.reply(200 if result["ok"] else 400, result)

        def do_POST(self):
            if not self.authorized():
                return
            if urlparse(self.path).path != "/api/rpc":
                self.reply(404, {"ok": False, "error": {"code": "not_found", "message": "接口不存在"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 8_000_000:
                    raise ValueError("请求内容过大")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or not isinstance(data.get("action"), str) or not isinstance(data.get("params", {}), dict):
                    raise ValueError("需要 {action:string,params:object}")
                result = dispatcher(data["action"], data.get("params", {}), state_dir)
                self.reply(200 if result["ok"] else 400, result)
            except (ValueError, TypeError) as exc:
                self.reply(400, {"ok": False, "error": {"code": "invalid_request", "message": str(exc)}})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.lumen_token = token
    return server


def serve(state_dir=None, *, host="127.0.0.1", port=0, token=None):
    server = create_server(state_dir, host=host, port=port, token=token)
    from .jobs import state_root
    print(json.dumps({"ok": True, "event": "ready", "host": host, "port": server.server_address[1], "token": server.lumen_token,
                      "state_dir": str(state_root(state_dir)), "rpc": "/api/rpc", "schema": "/api/schema"}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
