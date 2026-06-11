"""CLI entrypoint: ``python -m dashboard [--host H] [--port P] [--config PATH] [--user U] [--password P]``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard import app  # noqa: E402  (after sys.path fix so `scripts` resolves when run anywhere)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dashboard",
        description="Web dashboard to configure and control the AC EVO dedicated server.",
    )
    parser.add_argument("--host", default=os.environ.get("ACEVO_DASHBOARD_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DASHBOARD_PORT") or os.environ.get("ACEVO_DASHBOARD_PORT") or "8090"),
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("ACEVO_DASHBOARD_CONFIG", str(app.DEFAULT_CONFIG_PATH)),
        help="Path to the server_launcher.json the dashboard reads/writes.",
    )
    parser.add_argument("--user", default=os.environ.get("DASHBOARD_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("DASHBOARD_PASSWORD", ""))
    args = parser.parse_args(argv)

    config = app.DashboardConfig(
        config_path=Path(args.config),
        user=args.user,
        password=args.password,
    )
    app.serve(config, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
