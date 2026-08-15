"""Stdlib HTTP server: routes the JSON API, serves the static SPA, and gates everything
behind optional HTTP Basic Auth.

Kept dependency-free (``http.server``) to match the repo's zero-runtime-dependency design.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import signal
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config_io, live, metadata, mods, server_control

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_CONFIG_PATH = Path(os.environ.get("ACEVO_DASHBOARD_CONFIG", "/data/server_launcher.json"))
DEFAULT_PASSWORD_PLACEHOLDER = "change-me"


def _auto_start_enabled() -> bool:
    return os.environ.get("AUTO_START_SERVER", "true").strip().lower() in {"1", "true", "yes", "y", "on"}


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


@dataclass
class DashboardConfig:
    config_path: Path
    user: str
    password: str
    static_dir: Path = STATIC_DIR


def check_basic_auth(header: str | None, user: str, password: str) -> bool:
    """Constant-time Basic Auth check. Empty ``password`` disables auth (public)."""
    if not password:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    got_user, _, got_pass = decoded.partition(":")
    user_ok = hmac.compare_digest(got_user, user or "")
    pass_ok = hmac.compare_digest(got_pass, password)
    return user_ok and pass_ok


def _content_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return _CONTENT_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


class DashboardHandler(BaseHTTPRequestHandler):
    config: DashboardConfig = None  # bound per-server by make_server
    server_version = "ACEVODashboard/1.0"
    protocol_version = "HTTP/1.1"

    # --- helpers ---------------------------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        return

    def _authorized(self) -> bool:
        return check_basic_auth(self.headers.get("Authorization"), self.config.user, self.config.password)

    def _send_bytes(self, data: bytes, status: int, content_type: str, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status: int = 200) -> None:
        self._send_bytes(json.dumps(obj).encode("utf-8"), status, "application/json; charset=utf-8")

    def _send_401(self) -> None:
        self._send_bytes(
            b'{"error":"authentication required"}',
            401,
            "application/json; charset=utf-8",
            {"WWW-Authenticate": 'Basic realm="AC EVO Dashboard", charset="UTF-8"'},
        )

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _serve_static(self, rel: str) -> None:
        base = self.config.static_dir.resolve()
        target = (base / rel).resolve()
        if base != target and base not in target.parents:
            return self._send_json({"error": "not found"}, 404)
        if not target.is_file():
            return self._send_json({"error": "not found"}, 404)
        self._send_bytes(target.read_bytes(), 200, _content_type(target.name))

    # --- routing ---------------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._authorized():
            return self._send_401()
        parts = urlsplit(self.path)
        route = parts.path
        try:
            if route == "/":
                return self._serve_static("index.html")
            if route.startswith("/static/"):
                return self._serve_static(route[len("/static/") :])
            if route == "/api/metadata":
                return self._send_json(metadata.build_metadata())
            if route == "/api/config":
                form = config_io.effective_runtime_form(self.config.config_path, os.environ)
                source = config_io.config_source_info(self.config.config_path, os.environ)
                return self._send_json({"config_path": str(self.config.config_path), "form": form, **source})
            if route == "/api/configs":
                return self._send_json({"profiles": config_io.list_profiles(self.config.config_path)})
            if route == "/api/configs/get":
                name = (parse_qs(parts.query).get("name") or [""])[0]
                form = config_io.load_profile(name, self.config.config_path)
                if form is None:
                    return self._send_json({"error": "profile not found"}, 404)
                return self._send_json({"name": name, "form": form})
            if route == "/api/server/status":
                return self._send_json(server_control.status())
            if route == "/api/server/live":
                current = server_control.status()
                snapshot = live.snapshot() if current["running"] else {"players": 0, "drivers": []}
                return self._send_json({"running": current["running"], **snapshot})
            if route == "/api/server/logs":
                tail = int((parse_qs(parts.query).get("tail") or ["200"])[0] or 200)
                return self._send_json(server_control.logs(tail=tail))
            if route == "/api/mods/upload/status":
                upload_id = (parse_qs(parts.query).get("upload_id") or [""])[0]
                return self._send_json(mods.upload_status(upload_id))
            if route == "/api/mods":
                result = mods.inventory()
                result["running"] = server_control.status()["running"]
                return self._send_json(result)
        except mods.ModError as exc:
            return self._send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 - surface any failure as JSON, never 500-crash the loop
            return self._send_json({"error": str(exc)}, 500)
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._authorized():
            return self._send_401()
        parts = urlsplit(self.path)
        route = parts.path
        if route == "/api/mods/upload":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send_json({"error": "invalid Content-Length"}, 400)
            filename = (parse_qs(parts.query).get("filename") or [""])[0]
            try:
                return self._send_json(mods.upload(self.rfile, length, filename, config_path=self.config.config_path))
            except mods.ModError as exc:
                self.close_connection = True
                return self._send_json({"error": str(exc)}, exc.status)
            except Exception as exc:  # noqa: BLE001
                self.close_connection = True
                return self._send_json({"error": str(exc)}, 500)

        if route == "/api/mods/upload/chunk":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send_json({"error": "invalid Content-Length"}, 400)
            query = parse_qs(parts.query)
            upload_id = (query.get("upload_id") or [""])[0]
            offset = (query.get("offset") or [""])[0]
            try:
                return self._send_json(
                    mods.upload_chunk(
                        self.rfile,
                        length,
                        upload_id,
                        offset,
                        self.config.config_path,
                    )
                )
            except mods.ModError as exc:
                self.close_connection = True
                return self._send_json({"error": str(exc)}, exc.status)
            except Exception as exc:  # noqa: BLE001
                self.close_connection = True
                return self._send_json({"error": str(exc)}, 500)

        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "invalid JSON body"}, 400)
        form = body.get("form", body) if isinstance(body, dict) else {}
        name = body.get("name") if isinstance(body, dict) else None
        try:
            if route == "/api/mods/upload/start":
                if not isinstance(body, dict):
                    return self._send_json({"error": "JSON body must be an object"}, 400)
                return self._send_json(
                    mods.start_upload(
                        body.get("filename"),
                        body.get("size"),
                        body.get("last_modified"),
                        self.config.config_path,
                    )
                )
            if route == "/api/validate":
                return self._send_json(config_io.validate(form, env=os.environ))
            if route == "/api/save":
                return self._send_json(config_io.save(form, self.config.config_path, env=os.environ))
            if route == "/api/server/apply":
                result = config_io.apply(form, self.config.config_path, env=os.environ)
                current = server_control.status()
                if current.get("running"):
                    restart = server_control.restart()
                    result["restarted"] = True
                    result["server"] = restart
                    if not restart.get("ok"):
                        result["ok"] = False
                        result["error"] = restart.get("error", "server restart failed")
                else:
                    result["restarted"] = False
                return self._send_json(result)
            if route == "/api/config/source":
                source = body.get("source") if isinstance(body, dict) else None
                result = config_io.set_config_source(source, self.config.config_path, os.environ)
                if not result.get("ok"):
                    return self._send_json(result, 400)
                current = server_control.status()
                if current.get("running"):
                    restart = server_control.restart()
                    result["restarted"] = True
                    result["server"] = restart
                    if not restart.get("ok"):
                        result["ok"] = False
                        result["error"] = restart.get("error", "server restart failed")
                else:
                    result["restarted"] = False
                return self._send_json(result)
            if route == "/api/configs/save":
                return self._send_json(config_io.save_profile(name, form, self.config.config_path))
            if route == "/api/configs/delete":
                return self._send_json(config_io.delete_profile(name, self.config.config_path))
            if route == "/api/server/start":
                return self._send_json(server_control.start())
            if route == "/api/server/stop":
                return self._send_json(server_control.stop())
            if route == "/api/server/restart":
                return self._send_json(server_control.restart())
            if route == "/api/mods/delete":
                return self._send_json(mods.delete(name or body.get("filename"), self.config.config_path))
        except mods.ModError as exc:
            return self._send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": str(exc)}, 500)
        return self._send_json({"error": "not found"}, 404)


def make_server(config: DashboardConfig, host: str = "0.0.0.0", port: int = 8090) -> ThreadingHTTPServer:
    handler = type("BoundDashboardHandler", (DashboardHandler,), {"config": config})
    return ThreadingHTTPServer((host, port), handler)


def serve(config: DashboardConfig, host: str = "0.0.0.0", port: int = 8090) -> None:
    if not config.password:
        print("WARNING: DASHBOARD_PASSWORD is empty — the dashboard is PUBLIC (no authentication).", file=sys.stderr)
    elif config.password == DEFAULT_PASSWORD_PLACEHOLDER:
        print(
            f"WARNING: DASHBOARD_PASSWORD is still the default '{DEFAULT_PASSWORD_PLACEHOLDER}' — "
            "set a strong password.",
            file=sys.stderr,
        )
    httpd = make_server(config, host, port)

    def _shutdown(_signum, _frame):
        print("Shutting down: stopping server process ...", file=sys.stderr)
        try:
            server_control.stop()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            print(f"  (server stop failed: {exc})", file=sys.stderr)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if _auto_start_enabled():
        print(f"Auto-starting AC EVO server: {server_control.start()}")

    print(f"AC EVO dashboard listening on http://{host}:{port}  (config file: {config.config_path})")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
