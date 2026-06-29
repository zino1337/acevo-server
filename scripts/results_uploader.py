#!/usr/bin/env python3
"""Monitor /data/results and send new JSON files to a webhook endpoint via PATCH request.

Polls the results directory every 5 seconds, reads new JSON files, and PATCH them to the
configured RESULTS_WEBHOOK_URL. Tracks uploaded files locally to avoid re-uploads after
container restarts. Logs all errors to stdout without skipping subsequent files.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


POLL_INTERVAL_SECONDS = 5
RESULTS_DIR = Path(os.environ.get("SERVER_RESULTS_PATH", "/data/results"))
RESULTS_WEBHOOK_URL = os.environ.get("RESULTS_WEBHOOK_URL", "").strip()
SENT_RESULTS_TRACKER = Path.home() / ".acevo_results_sent.json"


def _load_sent_results() -> set[str]:
    """Load the set of already-uploaded result filenames from tracker file."""
    if not SENT_RESULTS_TRACKER.exists():
        return set()
    try:
        with open(SENT_RESULTS_TRACKER, "r") as f:
            data = json.load(f)
            return set(data.get("sent", []))
    except Exception as e:
        print(f"WARNING: Failed to load sent results tracker: {e}", file=sys.stderr)
        return set()


def _save_sent_results(sent: set[str]) -> None:
    """Save the set of uploaded result filenames to tracker file."""
    try:
        with open(SENT_RESULTS_TRACKER, "w") as f:
            json.dump({"sent": sorted(sent)}, f)
    except Exception as e:
        print(f"WARNING: Failed to save sent results tracker: {e}", file=sys.stderr)


def _send_result(url: str, file_path: Path) -> bool:
    """Send a result JSON file to the webhook via PATCH request.

    Args:
        url: The PATCH endpoint URL.
        file_path: Path to the JSON file to send.

    Returns:
        True if successful (2xx response), False otherwise.
    """
    try:
        # Read the JSON file
        with open(file_path, "r") as f:
            json_data = json.load(f)

        # Prepare the PATCH request
        json_body = json.dumps(json_data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=json_body,
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )

        # Send the request
        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.status
            response_body = response.read().decode("utf-8")
            if 200 <= status_code < 300:
                print(f"SUCCESS: {file_path.name} -> {url} (HTTP {status_code})")
                if response_body:
                    print(f"Response body: {response_body}")
                return True
            else:
                print(
                    f"ERROR: {file_path.name} returned HTTP {status_code}",
                    file=sys.stderr,
                )
                if response_body:
                    print(f"Response body: {response_body}", file=sys.stderr)
                return False

    except json.JSONDecodeError as e:
        print(
            f"ERROR: {file_path.name} is not valid JSON: {e}",
            file=sys.stderr,
        )
        return False
    except urllib.error.HTTPError as e:
        print(
            f"ERROR: {file_path.name} -> {url} HTTP {e.code}: {e.reason}",
            file=sys.stderr,
        )
        return False
    except urllib.error.URLError as e:
        print(
            f"ERROR: {file_path.name} -> {url} connection failed: {e.reason}",
            file=sys.stderr,
        )
        return False
    except Exception as e:
        print(
            f"ERROR: {file_path.name} upload failed: {e}",
            file=sys.stderr,
        )
        return False


def _poll_results_directory(webhook_url: str) -> None:
    """Continuously poll the results directory for new JSON files and upload them.

    Args:
        webhook_url: The PATCH endpoint URL to send results to.
    """
    sent_results = _load_sent_results()
    print(
        f"Results uploader started: monitoring {RESULTS_DIR} -> {webhook_url}",
        file=sys.stderr,
    )

    while True:
        try:
            # Ensure results directory exists
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)

            # Scan for JSON files
            json_files = sorted(RESULTS_DIR.glob("*.json"))

            for file_path in json_files:
                file_name = file_path.name

                # Skip already-uploaded files
                if file_name in sent_results:
                    continue

                # Try to send; log errors but continue
                if _send_result(webhook_url, file_path):
                    sent_results.add(file_name)
                    _save_sent_results(sent_results)

        except Exception as e:
            print(
                f"ERROR: Poll cycle failed: {e}",
                file=sys.stderr,
            )

        # Wait before next poll
        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    """Main entry point."""
    if not RESULTS_WEBHOOK_URL:
        print(
            "WARNING: RESULTS_WEBHOOK_URL not set; results uploader is disabled.",
            file=sys.stderr,
        )
        sys.exit(0)

    try:
        _poll_results_directory(RESULTS_WEBHOOK_URL)
    except KeyboardInterrupt:
        print("Results uploader stopped.", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
