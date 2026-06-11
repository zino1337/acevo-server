"""Web dashboard for configuring and controlling the AC EVO dedicated server.

A small, dependency-free (stdlib only) web app that mirrors the official Windows
"AC EVO Server Launcher": it authors a ``server_launcher.json`` the existing payload
pipeline consumes, validates it through :mod:`scripts.launch_payloads`, and controls the
server lifecycle (start/stop/restart/logs) via ``docker compose``.
"""

from __future__ import annotations

__all__ = ["app", "config_io", "metadata", "server_control"]
