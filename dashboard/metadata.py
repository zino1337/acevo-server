"""Build the metadata payload that powers the dashboard's dropdowns, checkboxes and sliders.

Everything here is derived from :mod:`scripts.launch_payloads` so the dashboard never drifts
from the values the server pipeline actually accepts (car list, tracks, enum tokens, defaults).
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts import launch_payloads as lp

# Reverse of the property-index maps in launch_payloads (0 -> "road", etc.).
_TYPE_LABELS = {v: k for k, v in lp.CAR_TYPES_MAP.items()}
_ERA_LABELS = {v: k for k, v in lp.CAR_ERAS_MAP.items()}
_ENGINE_LABELS = {v: k for k, v in lp.CAR_ENGINES_MAP.items()}

# Human labels for the category filter checkboxes (value = launch_payloads category token).
_CATEGORY_LABELS = {
    "road": "Road",
    "race": "Race",
    "track": "Track",
    "modern": "Modern",
    "vintage": "Vintage",
    "yt": "YT",
    "ice": "ICE",
    "ev": "EV",
    "hybrid": "Hybrid",
}

_RACING_CLASS_LABELS = {
    "f1": "F1",
    "gt3": "GT3",
    "gt2": "GT2",
    "gt4": "GT4",
    "cup": "Cup / One-make",
}

# Enum option lists: (human label, key into the raw MAPPINGS dict). Values resolve to the exact
# enum string the server pipeline expects, so we stay in sync if those strings ever change.
_ENUM_OPTIONS = {
    "server_type": [("Ranked", "ranked"), ("Unranked", "unranked")],
    "tuning_type": [("Tuning Allowed", "tuningallowed"), ("Tuning Denied", "tuningdenied")],
    "event_type": [("Practice", "practice"), ("Race Weekend", "race weekend")],
    "initial_grip": [("Green", "green"), ("Fast", "fast"), ("Optimum", "optimum")],
    "weather": [
        ("Clear", "clear"),
        ("Scattered Clouds", "scattered clouds"),
        ("Broken Clouds", "broken clouds"),
        ("Overcast", "overcast"),
        ("Drizzle", "drizzle"),
        ("Rain", "rain"),
        ("Heavy Rain", "heavy rain"),
        ("Damp", "damp"),
    ],
    "weather_behaviour": [("Static", "static"), ("Dynamic", "dynamic")],
    "duration_type": [("Time", "time"), ("Laps", "laps")],
}


def _racing_classes(car: dict) -> list[str]:
    """Return useful public-server classes derived from official race-car variants."""
    if car.get("property_1") != lp.CAR_TYPES_MAP["race"]:
        return []

    name = car["display_name"]
    classes = []
    if re.search(r"SF-25|F2004|Formula|\bF1\b", name):
        classes.append("f1")
    if re.search(r"\bGT3\b", name) and "GT3 Cup" not in name:
        # Intentionally includes the Rennsport Unrestricted variant for public servers.
        classes.append("gt3")
    if re.search(r"\bGT2\b", name):
        classes.append("gt2")
    if re.search(r"\bGT4\b", name):
        classes.append("gt4")
    if re.search(r"Cup|Challenge|Trofeo|Academy|M2 CS Racing", name):
        classes.append("cup")
    return classes


def _car_entry(car: dict) -> dict:
    is_mod = bool(car.get("is_mod"))
    return {
        "internal_name": car["internal_name"],
        "display_name": car["display_name"],
        "pi": None if is_mod else car.get("performance_indicator", 0.0),
        "type": None if is_mod else _TYPE_LABELS.get(car.get("property_1"), "road"),
        "era": None if is_mod else _ERA_LABELS.get(car.get("property_2"), "modern"),
        "engine": None if is_mod else _ENGINE_LABELS.get(car.get("property_3"), "ice"),
        "classes": _racing_classes(car),
        "p1": 0 if is_mod else car.get("property_1", 0),
        "p2": 0 if is_mod else car.get("property_2", 0),
        "p3": 0 if is_mod else car.get("property_3", 0),
        "is_mod": is_mod,
        "mod_file": car.get("mod_file", ""),
        "runtime_name": car.get("runtime_name", ""),
    }


def track_display(track: dict) -> str:
    """Render a track the way the Windows launcher does: ``Brands Hatch GP [3,92km] (pit:32)``."""
    km_comma = f"{track['track_length'] / 1000:.2f}".replace(".", ",")
    return f"{track['track']} {track['layout']} [{km_comma}km] (pit:{track['max_pit_slot']})"


def _track_entry(track: dict) -> dict:
    return {
        "token": lp.track_token(track),
        "display": track_display(track),
        "track": track["track"],
        "layout": track["layout"],
        "event_name": track["event_name"],
        "length_m": track["track_length"],
        "max_pit_slot": track["max_pit_slot"],
    }


def _events_file(name: str) -> Path:
    return Path(lp.__file__).resolve().parent / "mappings" / name


def _track_list(name: str) -> list[dict]:
    try:
        data = lp._read_json(_events_file(name))
    except Exception:
        return []
    return [_track_entry(track) for track in data.get("events", [])]


def _enum_options(key: str) -> list[dict]:
    mapping = lp.MAPPINGS[key]
    return [{"label": label, "value": mapping[lookup]} for label, lookup in _ENUM_OPTIONS[key]]


def _categories() -> dict:
    def options(token_map: dict) -> list[dict]:
        return [{"value": name, "label": _CATEGORY_LABELS[name]} for name in token_map]

    return {
        "type": options(lp.CAR_TYPES_MAP),
        "era": options(lp.CAR_ERAS_MAP),
        "engine": options(lp.CAR_ENGINES_MAP),
        "class": [{"value": name, "label": label} for name, label in _RACING_CLASS_LABELS.items()],
    }


def _session_defaults(cfg: dict, prefix: str) -> dict:
    """Form defaults for one session, in the seconds-based shape the dashboard UI uses."""
    raw = cfg["session_defaults"].get(prefix, {})
    return {
        "length_sec": int(raw.get("duration_minutes", 5)) * 60,
        "hour": int(raw.get("hour", 16)),
        "minute": int(raw.get("minute", 0)),
        "time_multiplier": int(raw.get("time_multiplier", 1)),
        "max_wait_to_box": int(raw.get("max_wait_to_box_seconds", 10)),
        "overtime_waiting_next_session": int(raw.get("overtime_waiting_next_session_seconds", 10)),
        "min_waiting_for_players": int(raw.get("min_waiting_for_players_seconds", 10)),
        "max_waiting_for_players": int(raw.get("max_waiting_for_players_seconds", 30)),
    }


def _defaults(cfg: dict) -> dict:
    server = cfg["server_defaults"]
    event = cfg["event_defaults"]
    race = _session_defaults(cfg, "race")
    race["duration_type"] = lp.MAPPINGS["duration_type"]["time"]
    race["laps"] = int(cfg["session_defaults"].get("race", {}).get("duration_laps", 10))
    race_defaults = cfg["session_defaults"].get("race", {})
    race["mandatory_pitstop_enabled"] = bool(race_defaults.get("mandatory_pitstop_enabled", False))
    race["mandatory_pitstop_window_seconds"] = int(race_defaults.get("mandatory_pitstop_window_seconds", 600))
    race["mandatory_pitstop_refuel"] = bool(race_defaults.get("mandatory_pitstop_refuel", True))
    race["mandatory_pitstop_tyre_change"] = bool(race_defaults.get("mandatory_pitstop_tyre_change", True))
    return {
        "server": {
            "server_name": server.get("server_name", "AC EVO Server"),
            "max_players": int(server.get("max_players", 20)),
            "tcp_port": int(server.get("tcp_port", 9700)),
            "udp_port": int(server.get("udp_port", 9700)),
            "http_port": int(server.get("http_port", 8080)),
            "server_type": server.get("server_type"),
            "tuning_type": server.get("tuning_type"),
            "cycle_enabled": bool(server.get("cycle_enabled", True)),
            "driver_password": "",
            "spectator_password": "",
            "admin_password": "",
            "entry_list_url": "",
            "results_post_url": "",
            "entry_list_path": "",
            "results_path": "",
        },
        "event": {
            "type": event.get("type"),
            "weather": event.get("weather"),
            "weather_behaviour": event.get("weather_behaviour"),
            "initial_grip": event.get("initial_grip"),
            "show_only_selected": False,
        },
        "sessions": {
            "practice": _session_defaults(cfg, "practice"),
            "qualify": _session_defaults(cfg, "qualify"),
            "warmup": _session_defaults(cfg, "warmup"),
            "race": race,
        },
    }


def build_metadata() -> dict:
    """Return everything the frontend needs, including the current mod directory."""
    cfg = lp.load_config()
    cars = [_car_entry(car) for car in cfg["cars_data"]]
    pis = [car["pi"] for car in cars if isinstance(car["pi"], (int, float))]
    return {
        "cars": cars,
        "pi_min": min(pis) if pis else 0.0,
        "pi_max": max(pis) if pis else 100.0,
        "tracks": {
            "practice": _track_list("events_practice.json"),
            "race_weekend": _track_list("events_race_weekend.json"),
        },
        "enums": {key: _enum_options(key) for key in _ENUM_OPTIONS},
        "categories": _categories(),
        "defaults": _defaults(cfg),
    }
