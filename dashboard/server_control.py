"""Supervise the AC EVO server **process** from inside the container.

The dashboard is the container's main process and stays up regardless of the server. It starts
the server by spawning ``scripts/run_server.sh`` in its own process group (so the whole Proton
tree can be signalled), streams its output to a log file, and exposes start/stop/restart/status/
logs. "Apply config" = restart the process, which regenerates the payload from
``server_launcher.json`` and relaunches Proton. No Docker, compose, or socket involved.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import live

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_SERVER_SCRIPT = Path(os.environ.get("ACEVO_RUN_SERVER", str(REPO_ROOT / "scripts" / "run_server.sh")))
LOG_FILE = Path(os.environ.get("ACEVO_SERVER_LOG", "/data/logs/server.log"))
_TERM_TIMEOUT = 15.0
_MAX_LOG_READ = 256 * 1024

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_last_exit: int | None = None
_log_thread: threading.Thread | None = None


def _write_stdout(data: bytes) -> None:
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
        return
    sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.flush()


def _tee_output(proc: subprocess.Popen, log_file: Path, generation: int | None = None) -> None:
    if proc.stdout is None:
        return
    try:
        with open(log_file, "ab", buffering=0) as log_fh:
            while True:
                chunk = proc.stdout.readline()
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                log_fh.write(chunk)
                try:
                    live.consume_line(chunk, generation)
                except Exception:  # noqa: BLE001 - live data must never affect the server process
                    pass
                _write_stdout(chunk)
    finally:
        try:
            proc.stdout.close()
        except (AttributeError, OSError):
            pass


def _running_locked() -> bool:
    """Caller holds ``_lock``. Reaps a finished child and records its exit code."""
    global _proc, _last_exit, _log_thread
    if _proc is None:
        return False
    if _proc.poll() is None:
        return True
    _last_exit = _proc.returncode
    if _log_thread is not None:
        _log_thread.join(timeout=0.2)
        _log_thread = None
    _proc = None
    live.reset()
    return False


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)  # pid == process-group id (start_new_session)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def start() -> dict:
    with _lock:
        global _proc, _last_exit, _log_thread
        if _running_locked():
            return {"ok": True, "running": True, "message": "server already running"}
        if not RUN_SERVER_SCRIPT.exists():
            return {"ok": False, "error": f"run script not found: {RUN_SERVER_SCRIPT}"}
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "wb", buffering=0):
                pass  # fresh log per server run
            generation = live.reset()
        except OSError as exc:
            return {"ok": False, "error": f"cannot open log file {LOG_FILE}: {exc}"}
        try:
            _proc = subprocess.Popen(  # noqa: S603 - fixed script path, no shell
                ["/usr/bin/env", "bash", str(RUN_SERVER_SCRIPT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(REPO_ROOT),
                start_new_session=True,  # own process group so we can signal the whole Proton tree
            )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        _log_thread = threading.Thread(target=_tee_output, args=(_proc, LOG_FILE, generation), daemon=True)
        _log_thread.start()
        _last_exit = None
        return {"ok": True, "running": True, "pid": _proc.pid}


def stop() -> dict:
    with _lock:
        global _proc, _last_exit, _log_thread
        if not _running_locked():
            live.reset()
            return {"ok": True, "running": False, "message": "server not running"}
        pid = _proc.pid
        _signal_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + _TERM_TIMEOUT
        while time.monotonic() < deadline and _proc.poll() is None:
            time.sleep(0.3)
        if _proc.poll() is None:
            _signal_group(pid, signal.SIGKILL)
        try:
            _last_exit = _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _last_exit = None
        if _log_thread is not None:
            _log_thread.join(timeout=2)
        _log_thread = None
        _proc = None
        live.reset()
        return {"ok": True, "running": False}


def restart() -> dict:
    """Apply config: stop the server, then start it (regenerates payload from server_launcher.json)."""
    stop()
    return start()


def status() -> dict:
    with _lock:
        running = _running_locked()
        return {
            "running": running,
            "state": "running" if running else "stopped",
            "pid": _proc.pid if running and _proc is not None else None,
            "last_exit_code": _last_exit,
        }


def logs(tail: int = 200) -> dict:
    try:
        if not LOG_FILE.exists():
            return {"ok": True, "lines": "", "message": "no server log yet — start the server"}
        size = LOG_FILE.stat().st_size
        with open(LOG_FILE, "rb") as handle:
            if size > _MAX_LOG_READ:
                handle.seek(size - _MAX_LOG_READ)
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()[-int(tail) :]
        return {"ok": True, "lines": "\n".join(lines)}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "lines": ""}
