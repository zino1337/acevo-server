"""Thread-safe live driver state reconstructed from dedicated-server output.

The server does not expose driver timing through a documented API. Its log does,
so ``server_control`` feeds each output line here once as it is written. Unknown
lines are ignored: a game update may reduce available data, but must never affect
the server process.
"""

from __future__ import annotations

import re
import threading

_CONNECT = re.compile(r"connecting gamecar ([0-9a-fA-F-]+) \(([^|]+?) \| \d+\)")
_CAR = re.compile(r"connected \([a-z]+\) on car ([\w.-]+), with new carId ([0-9a-fA-F-]+)")
_NUMBER = re.compile(r"Car \[([0-9a-fA-F-]+)\] #(\d+) for driver")
_LAP = re.compile(r"New lap carId ([0-9a-fA-F-]+): (\d+:\d+\.\d+)")
_DISCONNECT = re.compile(r"Removing disconnected remote_car ([0-9a-fA-F-]+)")
_PLAYERS = re.compile(r"Server updated: (\d+) players")
_LAP_TIME = re.compile(r"^(\d+):(\d+)\.(\d+)$")

_lock = threading.Lock()
_drivers: dict[str, dict] = {}
_players = 0
_generation = 0


def lap_to_ms(value: str) -> int | None:
    match = _LAP_TIME.match((value or "").strip())
    if not match:
        return None
    minutes, seconds, millis = (int(part) for part in match.groups())
    return minutes * 60000 + seconds * 1000 + millis


def _key(car_id: str) -> str:
    return car_id.replace("-", "").lower()


def _entry(car_id: str) -> dict:
    return _drivers.setdefault(
        _key(car_id),
        {
            "car_id": car_id,
            "name": "",
            "car": "",
            "number": None,
            "laps": 0,
            "last_lap_ms": None,
            "best_lap_ms": None,
        },
    )


def consume_line(line: bytes | str, generation: int | None = None) -> None:
    """Consume one complete server-output line without retaining personal IDs."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    if not isinstance(line, str):
        return

    global _players
    with _lock:
        if generation is not None and generation != _generation:
            return
        if (hit := _CONNECT.search(line)) is not None:
            _entry(hit.group(1))["name"] = hit.group(2).strip()
            _players = max(_players, sum(bool(driver["name"]) for driver in _drivers.values()))
        elif (hit := _CAR.search(line)) is not None:
            _entry(hit.group(2))["car"] = hit.group(1)
        elif (hit := _NUMBER.search(line)) is not None:
            _entry(hit.group(1))["number"] = int(hit.group(2))
        elif (hit := _LAP.search(line)) is not None:
            lap_ms = lap_to_ms(hit.group(2))
            if lap_ms is not None:
                driver = _entry(hit.group(1))
                driver["laps"] += 1
                driver["last_lap_ms"] = lap_ms
                if driver["best_lap_ms"] is None or lap_ms < driver["best_lap_ms"]:
                    driver["best_lap_ms"] = lap_ms
        elif (hit := _DISCONNECT.search(line)) is not None:
            _drivers.pop(_key(hit.group(1)), None)
            _players = min(_players, len(_drivers))
        elif (hit := _PLAYERS.search(line)) is not None:
            _players = int(hit.group(1))
            if _players == 0:
                _drivers.clear()


def reset() -> int:
    """Clear state and return a token for the next server-output stream."""
    global _generation, _players
    with _lock:
        _drivers.clear()
        _players = 0
        _generation += 1
        return _generation


def snapshot() -> dict:
    with _lock:
        drivers = [dict(driver) for driver in _drivers.values()]
        players = _players
    drivers.sort(key=lambda driver: (driver["best_lap_ms"] is None, driver["best_lap_ms"] or 0, driver["name"]))
    return {"players": players, "drivers": drivers}
