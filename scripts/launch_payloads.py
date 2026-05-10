#!/usr/bin/env python3
"""Generate AC EVO launch payload blobs from environment variables."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import sys
import unicodedata
import zlib
from functools import lru_cache
from pathlib import Path

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}
SESSION_PREFIXES = ("PRACTICE", "QUALIFY", "WARMUP", "RACE")
SESSION_NAMES = {
    "PRACTICE": "practice",
    "QUALIFY": "qualify",
    "WARMUP": "warmup",
    "RACE": "race",
}
DEFAULT_SESSION_DATE = {
    "YEAR": 2024,
    "MONTH": 8,
    "DAY": 15,
}
SESSION_DATE_RANGES = {
    "YEAR": (1, 9999),
    "MONTH": (1, 12),
    "DAY": (1, 31),
}
SESSION_FIELDS = (
    "YEAR",
    "MONTH",
    "DAY",
    "DURATION_MINUTES",
    "HOUR",
    "MINUTE",
    "TIME_MULTIPLIER",
    "MAX_WAIT_TO_BOX_SECONDS",
    "OVERTIME_WAITING_NEXT_SESSION_SECONDS",
)
ENV_BASE_KEYS = (
    "SERVER_NAME",
    "SERVER_MAX_PLAYERS",
    "SERVER_TCP_PORT",
    "SERVER_UDP_PORT",
    "SERVER_HTTP_PORT",
    "SERVER_CYCLE_ENABLED",
    "SERVER_DRIVER_PASSWORD",
    "SERVER_SPECTATOR_PASSWORD",
    "SERVER_ADMIN_PASSWORD",
    "SERVER_MIN_WAITING_PLAYERS",
    "SERVER_MAX_WAITING_PLAYERS",
    "SERVER_RESULTS_POST_URL",
    "SERVER_RESULTS_TOKEN",
    "EVENT_TYPE",
    "EVENT_INITIAL_GRIP",
    "EVENT_WEATHER_BEHAVIOUR",
    "EVENT_WEATHER",
    "EVENT_TRACK",
    "EVENT_CARS",
    "EVENT_CAR_CATEGORY",
    "EVENT_BAN_CARS",
    "EVENT_BAN_CAR_CATEGORY",
    "RACE_DURATION_TYPE",
    "RACE_DURATION_LAPS",
    "ACEVO_SERVER_INSTALL_DIR",
)
STRICT_TOKEN_ENV_KEYS = {
    "EVENT_TYPE",
    "EVENT_INITIAL_GRIP",
    "EVENT_WEATHER_BEHAVIOUR",
    "EVENT_WEATHER",
    "EVENT_TRACK",
    "EVENT_CARS",
    "EVENT_CAR_CATEGORY",
    "EVENT_BAN_CARS",
    "EVENT_BAN_CAR_CATEGORY",
    "RACE_DURATION_TYPE",
}
ACTIVE_SESSIONS = {
    "GameModeType_PRACTICE": {"PRACTICE"},
    "GameModeType_RACE_WEEKEND": {"PRACTICE", "QUALIFY", "WARMUP", "RACE"},
}
RACE_DURATION_TYPE_TIME = "GameModeSelectionDuration_TIME"
RACE_DURATION_TYPE_LAPS = "GameModeSelectionDuration_LAPS"

RUNTIME_KEYS = {
    "config_prefixes": ["SERVER_", "EVENT_", "PRACTICE_", "QUALIFY_", "WARMUP_", "RACE_", "ACEVO_"],
    "sensitive_env_keys": [
        "SERVER_DRIVER_PASSWORD",
        "SERVER_SPECTATOR_PASSWORD",
        "SERVER_ADMIN_PASSWORD",
        "SERVER_RESULTS_TOKEN",
    ],
    "external_runtime_env": {
        "ACEVO_FORCE_SOFTWARE_RENDERING": {"default": "true", "note": "used by start.sh Proton rendering mode"}
    },
}

MAPPINGS = {
    "duration_type": {"time": RACE_DURATION_TYPE_TIME, "laps": RACE_DURATION_TYPE_LAPS},
    "event_type": {"practice": "GameModeType_PRACTICE", "race weekend": "GameModeType_RACE_WEEKEND"},
    "initial_grip": {"green": "InitialGrip_GREEN", "fast": "InitialGrip_FAST", "optimum": "InitialGrip_OPTIMUM"},
    "weather_behaviour": {
        "static": "GameModeSelectionWeatherBehaviour_STATIC",
        "dynamic": "GameModeSelectionWeatherBehaviour_DYNAMIC",
    },
    "weather": {
        "clear": "GameModeSelectionWeatherType_CLEAR",
        "scattered clouds": "GameModeSelectionWeatherType_SCATTERED_CLOUDS",
        "broken clouds": "GameModeSelectionWeatherType_BROKEN_CLOUDS",
        "overcast": "GameModeSelectionWeatherType_OVERCAST",
        "drizzle": "GameModeSelectionWeatherType_DRIZZLE",
        "rain": "GameModeSelectionWeatherType_RAIN",
        "heavy rain": "GameModeSelectionWeatherType_HEAVY_RAIN",
        "damp": "GameModeSelectionWeatherType_DAMP",
    },
}

CAR_TYPES_MAP = {"road": 0, "race": 1, "track": 2}
CAR_ERAS_MAP = {"modern": 0, "vintage": 1, "yt": 2}
CAR_ENGINES_MAP = {"ice": 0, "ev": 1, "hybrid": 2}
CAR_CATEGORY_NAMES = {"all", *CAR_TYPES_MAP, *CAR_ERAS_MAP, *CAR_ENGINES_MAP}


def normalize_label(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = text.encode("ascii", "ignore").decode("ascii").replace("_", " ")
    return re.sub(r"\s+", " ", text.strip().lower())


def env_token(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", text)).strip("_")


def token_has_whitespace(value: str) -> bool:
    return bool(re.search(r"\s", value))


def normalize_enum_map(raw: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, enum in raw.items():
        out[normalize_label(label)] = enum
        out[normalize_label(enum)] = enum
    return out


def car_env_token(display_name: str) -> str:
    base_name = display_name.split(" - ", 1)[0]
    base_name = re.sub(r"(?<=\d)\.(?=\d)", "", base_name)
    return env_token(base_name)


def track_env_token(track: dict) -> str:
    return env_token(f"{track['track']}_{track['layout']}")


def track_token(track: dict) -> str:
    return f"{track['track']}|{track['layout']}|{track['event_name']}|{track['track_length']}"


def parse_track_token(token: str, pit_slot: int = 32) -> dict:
    parts = [p.strip() for p in token.split("|")]
    if len(parts) != 4:
        raise ValueError("invalid track token")
    return {
        "track": parts[0],
        "layout": parts[1],
        "event_name": parts[2],
        "track_length": int(parts[3]),
        "max_pit_slot": pit_slot,
    }


def track_aliases(track: dict) -> tuple[str, ...]:
    km_dot = f"{track['track_length'] / 1000:.2f}"
    km_comma = km_dot.replace(".", ",")
    pit = track["max_pit_slot"]
    return (
        normalize_label(track_env_token(track)),
        normalize_label(f"{track['track']} {track['layout']}"),
        normalize_label(f"{track['track']} {track['layout']} [{km_dot}km] (pit:{pit})"),
        normalize_label(f"{track['track']} {track['layout']} [{km_comma}km] (pit:{pit})"),
        normalize_label(track_token(track)),
    )


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_config() -> dict:
    scripts_dir = Path(__file__).resolve().parent
    root = scripts_dir.parent / "config"
    runtime = RUNTIME_KEYS
    mappings = {k: normalize_enum_map(v) for k, v in MAPPINGS.items()}

    defaults = _read_json(root / "defaults.json")

    try:
        cars = _read_json(scripts_dir / "mappings" / "cars.json")
    except Exception:
        cars = []

    car_lookup = {normalize_label(item["display_name"]): item["internal_name"] for item in cars}
    cars_data = cars

    tracks_by_event: dict[str, dict[str, dict]] = {"GameModeType_PRACTICE": {}, "GameModeType_RACE_WEEKEND": {}}

    try:
        practice_tracks = _read_json(scripts_dir / "mappings" / "events_practice.json")
        for track in practice_tracks.get("events", []):
            for alias in track_aliases(track):
                tracks_by_event["GameModeType_PRACTICE"][alias] = track
    except Exception:
        pass

    try:
        race_tracks = _read_json(scripts_dir / "mappings" / "events_race_weekend.json")
        for track in race_tracks.get("events", []):
            for alias in track_aliases(track):
                tracks_by_event["GameModeType_RACE_WEEKEND"][alias] = track
    except Exception:
        pass

    session_defaults = {prefix: defaults.get("sessions", {}).get(prefix.lower(), {}) for prefix in SESSION_PREFIXES}

    session_keys = [f"{p}_{f}" for p in SESSION_PREFIXES for f in SESSION_FIELDS]
    supported_key_order = [*ENV_BASE_KEYS, *runtime["external_runtime_env"].keys(), *session_keys]

    return {
        "server_defaults": defaults.get("server", {}),
        "event_defaults": defaults.get("event", {}),
        "session_defaults": session_defaults,
        "runtime": runtime,
        "mappings": mappings,
        "car_lookup": car_lookup,
        "cars_data": cars_data,
        "tracks_by_event": tracks_by_event,
        "supported_key_order": supported_key_order,
        "supported_keys": set(supported_key_order),
    }


class EnvState:
    def __init__(self, env: dict[str, str], sensitive_keys: set[str]) -> None:
        self.env = env
        self.sensitive_keys = sensitive_keys
        self.warnings: list[str] = []
        self.resolved: dict[str, dict] = {}

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def set(self, key: str, value, source: str, note: str = "") -> None:
        if key in self.sensitive_keys and isinstance(value, str):
            value = "***MASKED***" if value else ""
        self.resolved[key] = {"value": value, "source": source, "note": note}

    def _raw(self, key: str) -> str:
        return self.env.get(key, "")

    def string(self, key: str, default: str, allow_empty: bool = False) -> str:
        raw = self._raw(key)
        if key not in self.env:
            self.set(key, default, "default")
            return default
        value = raw.strip()
        if not value and not allow_empty:
            self.warn(f"{key}: empty value, using default.")
            self.set(key, default, "fallback", "empty input")
            return default
        self.set(key, value if value or allow_empty else "", "env")
        return value

    def integer(self, key: str, default: int) -> int:
        raw = self._raw(key).strip()
        if key not in self.env or not raw:
            self.set(key, default, "default")
            return default
        try:
            value = int(raw)
        except ValueError:
            self.warn(f"{key}: unknown integer '{raw}', using default '{default}'.")
            self.set(key, default, "fallback", f"invalid integer: {raw}")
            return default
        self.set(key, value, "env")
        return value

    def integer_in_range(self, key: str, default: int, minimum: int, maximum: int) -> int:
        value = self.integer(key, default)
        if minimum <= value <= maximum:
            return value
        self.warn(f"{key}: integer '{value}' outside {minimum}-{maximum}, using default '{default}'.")
        self.set(key, default, "fallback", f"out of range: {value}")
        return default

    def boolean(self, key: str, default: bool) -> bool:
        raw = self._raw(key).strip().lower()
        if key not in self.env or not raw:
            self.set(key, default, "default")
            return default
        if raw in TRUE_VALUES:
            self.set(key, True, "env")
            return True
        if raw in FALSE_VALUES:
            self.set(key, False, "env")
            return False
        self.warn(f"{key}: unknown boolean '{raw}', using default '{str(default).lower()}'.")
        self.set(key, default, "fallback", f"invalid boolean: {raw}")
        return default

    def enum(self, key: str, mapping: dict[str, str], default: str) -> str:
        raw = self._raw(key).strip()
        if key not in self.env or not raw:
            self.set(key, default, "default")
            return default
        if key in STRICT_TOKEN_ENV_KEYS and token_has_whitespace(raw):
            self.warn(f"{key}: spaces are not allowed; use '_' tokens, using default.")
            self.set(key, default, "fallback", f"spaces are not allowed: {raw}")
            return default
        mapped = mapping.get(normalize_label(raw))
        if mapped is None:
            self.warn(f"{key}: unknown value '{raw}', using default.")
            self.set(key, default, "fallback", f"invalid enum: {raw}")
            return default
        self.set(key, mapped, "env")
        return mapped


def prepare_state(state: EnvState, cfg: dict) -> None:
    prefixes = tuple(cfg["runtime"]["config_prefixes"])
    for key in sorted(state.env):
        if key not in cfg["supported_keys"] and key.startswith(prefixes):
            state.warn(f"Unknown ENV '{key}' ignored.")

    state.string("ACEVO_SERVER_INSTALL_DIR", "/data/server")
    for key, meta in cfg["runtime"]["external_runtime_env"].items():
        state.set(key, state.env.get(key, meta.get("default", "")), "external_runtime", meta.get("note", ""))


def resolve_track(state: EnvState, cfg: dict, event_type: str) -> dict:
    lookup = cfg["tracks_by_event"].get(event_type, {})

    default_track_label = normalize_label(cfg["event_defaults"].get("track", ""))
    fallback = lookup.get(default_track_label)

    if fallback is None:
        if event_type == "GameModeType_PRACTICE":
            fallback = parse_track_token("Nurburgring|Touristenfahrten|Touristenfahrten Time Attack|19300", 50)
        else:
            fallback = parse_track_token("Nurburgring|Nordschleife|Nordschleife Race|20832", 22)

    raw = state.env.get("EVENT_TRACK", "").strip()
    if not raw:
        state.set("EVENT_TRACK", track_env_token(fallback), "default")
        return fallback

    if token_has_whitespace(raw):
        state.warn(f"EVENT_TRACK: spaces are not allowed; use '_' tokens, using default for event type '{event_type}'.")
        state.set("EVENT_TRACK", track_env_token(fallback), "fallback", f"spaces are not allowed: {raw}")
        return fallback

    track = lookup.get(normalize_label(raw))
    if track is not None:
        state.set("EVENT_TRACK", track_env_token(track), "env")
        return track

    if "|" in raw:
        try:
            custom = parse_track_token(raw, fallback["max_pit_slot"])
            state.set("EVENT_TRACK", track_env_token(custom), "env")
            return custom
        except ValueError:
            pass

    state.warn(f"EVENT_TRACK: unknown track '{raw}', using default for event type '{event_type}'.")
    state.set("EVENT_TRACK", track_env_token(fallback), "fallback", f"invalid track: {raw}")
    return fallback


def all_car_names(cfg: dict) -> list[str]:
    return [car["internal_name"] for car in cfg["cars_data"]]


def add_unique(target: list[str], seen: set[str], values: list[str]) -> None:
    for value in values:
        if value not in seen:
            seen.add(value)
            target.append(value)


def car_matches_label(car: dict, label: str) -> bool:
    display_name = car.get("display_name", "")
    display_label = normalize_label(display_name)
    token_label = normalize_label(car_env_token(display_name))
    return label == display_label or label == token_label or label in display_label or label in token_label


def car_label_variants(value: str) -> tuple[str, ...]:
    variants = [normalize_label(value)]
    if "_" in value or " - " not in value:
        variants.append(normalize_label(car_env_token(value)))
    return tuple(dict.fromkeys(label for label in variants if label))


def matching_cars_for_labels(cfg: dict, labels: tuple[str, ...]) -> list[str]:
    return [car["internal_name"] for car in cfg["cars_data"] if any(car_matches_label(car, label) for label in labels)]


def resolve_car_filter(
    state: EnvState, cfg: dict, key: str, raw: str, allowed_pool: set[str] | None = None
) -> tuple[list[str], int]:
    if token_has_whitespace(raw):
        state.warn(f"{key}: spaces are not allowed; use '_' tokens, ignoring value.")
        return [], 1

    raw_labels = [item.strip() for item in raw.split(",") if item.strip()]
    if not raw_labels:
        return [], 0

    if any("all" in car_label_variants(label) for label in raw_labels):
        return all_car_names(cfg), 0

    selected: list[str] = []
    seen: set[str] = set()
    invalid = 0

    for raw_label in raw_labels:
        labels = car_label_variants(raw_label)
        matches = matching_cars_for_labels(cfg, labels)
        if not matches:
            invalid += 1
            state.warn(f"{key}: unknown car '{labels[0]}', ignoring.")
            continue
        if allowed_pool is not None and not any(match in allowed_pool for match in matches):
            state.warn(f"{key}: car '{labels[0]}' is not in allowed car pool, ignoring.")
        add_unique(selected, seen, matches)

    return selected, invalid


def category_matches_car(
    car: dict, selected_types: set[int], selected_eras: set[int], selected_engines: set[int]
) -> bool:
    match_type = not selected_types or car.get("property_1") in selected_types
    match_era = not selected_eras or car.get("property_2") in selected_eras
    match_engine = not selected_engines or car.get("property_3") in selected_engines
    return match_type and match_era and match_engine


def resolve_category_filter(state: EnvState, cfg: dict, key: str, raw: str) -> tuple[list[str], int]:
    if token_has_whitespace(raw):
        state.warn(f"{key}: spaces are not allowed; use '_' tokens, ignoring value.")
        return [], 1

    categories = [normalize_label(item) for item in raw.split(",") if item.strip()]
    if not categories:
        return [], 0

    invalid_categories = [category for category in categories if category not in CAR_CATEGORY_NAMES]
    for category in invalid_categories:
        state.warn(f"{key}: unknown category '{category}', ignoring.")

    if "all" in categories:
        return all_car_names(cfg), len(invalid_categories)

    selected_types = {CAR_TYPES_MAP[c] for c in categories if c in CAR_TYPES_MAP}
    selected_eras = {CAR_ERAS_MAP[c] for c in categories if c in CAR_ERAS_MAP}
    selected_engines = {CAR_ENGINES_MAP[c] for c in categories if c in CAR_ENGINES_MAP}

    if not selected_types and not selected_eras and not selected_engines:
        return [], len(invalid_categories)

    selected = [
        car["internal_name"]
        for car in cfg["cars_data"]
        if category_matches_car(car, selected_types, selected_eras, selected_engines)
    ]
    return selected, len(invalid_categories)


def set_filter_state(state: EnvState, key: str, raw: str, invalid_count: int) -> None:
    if not raw.strip():
        state.set(key, "", "default")
        return
    if token_has_whitespace(raw):
        state.set(key, "", "fallback", "spaces are not allowed; use '_' tokens")
        return
    note = "invalid values ignored" if invalid_count else ""
    state.set(key, raw, "env", note)


def resolve_cars(state: EnvState, cfg: dict) -> list[str]:
    cars_raw = state.env.get("EVENT_CARS", "").strip()
    category_raw = state.env.get("EVENT_CAR_CATEGORY", "").strip()
    ban_cars_raw = state.env.get("EVENT_BAN_CARS", "").strip()
    ban_category_raw = state.env.get("EVENT_BAN_CAR_CATEGORY", "").strip()

    selected: list[str] = []
    seen: set[str] = set()

    if not cars_raw and not category_raw:
        selected = all_car_names(cfg)
        seen = set(selected)
        state.set("EVENT_CARS", "all", "default")
        state.set("EVENT_CAR_CATEGORY", "all", "default")
    else:
        category_matches, invalid_categories = resolve_category_filter(state, cfg, "EVENT_CAR_CATEGORY", category_raw)
        add_unique(selected, seen, category_matches)
        if category_raw:
            set_filter_state(state, "EVENT_CAR_CATEGORY", category_raw, invalid_categories)

        car_matches, invalid_cars = resolve_car_filter(state, cfg, "EVENT_CARS", cars_raw)
        add_unique(selected, seen, car_matches)
        if cars_raw:
            set_filter_state(state, "EVENT_CARS", cars_raw, invalid_cars)

        if not selected:
            state.warn("EVENT_CARS / EVENT_CAR_CATEGORY: no valid cars found, using fallback 'all'.")
            selected = all_car_names(cfg)
            seen = set(selected)
            state.set("EVENT_CARS", "all", "fallback", "fallback all selected")
            state.set("EVENT_CAR_CATEGORY", "all", "fallback", "fallback all selected")

    ban_matches: list[str] = []
    ban_seen: set[str] = set()
    allowed_pool = set(selected)

    category_bans, invalid_ban_categories = resolve_category_filter(
        state, cfg, "EVENT_BAN_CAR_CATEGORY", ban_category_raw
    )
    if ban_category_raw and category_bans and not any(car in allowed_pool for car in category_bans):
        state.warn("EVENT_BAN_CAR_CATEGORY: matched cars are not in allowed car pool, ignoring.")
    add_unique(ban_matches, ban_seen, category_bans)
    set_filter_state(state, "EVENT_BAN_CAR_CATEGORY", ban_category_raw, invalid_ban_categories)

    car_bans, invalid_ban_cars = resolve_car_filter(state, cfg, "EVENT_BAN_CARS", ban_cars_raw, allowed_pool)
    add_unique(ban_matches, ban_seen, car_bans)
    set_filter_state(state, "EVENT_BAN_CARS", ban_cars_raw, invalid_ban_cars)

    if ban_matches:
        banned = set(ban_matches)
        selected = [car for car in selected if car not in banned]

    if not selected:
        state.warn(
            "EVENT_BAN_CARS / EVENT_BAN_CAR_CATEGORY: ban filters removed all allowed cars, using fallback 'all'."
        )
        selected = all_car_names(cfg)
        state.set("EVENT_CARS", "all", "fallback", "fallback all selected after ban filters")
        state.set("EVENT_CAR_CATEGORY", "all", "fallback", "fallback all selected after ban filters")

    return selected


def set_conversion_note(state: EnvState, key: str, seconds: int) -> None:
    if key in state.resolved:
        note = f"converted to {seconds} seconds for payload"
        existing = state.resolved[key].get("note", "")
        state.resolved[key]["note"] = f"{existing}; {note}" if existing else note


def race_duration_laps_default(cfg: dict) -> int:
    return int(cfg["session_defaults"]["RACE"].get("duration_laps", 10))


def resolve_sessions(state: EnvState, cfg: dict, event_type: str, race_duration_type: str) -> dict[str, dict]:
    active = ACTIVE_SESSIONS.get(event_type, {"PRACTICE"})
    sessions: dict[str, dict] = {}

    for prefix in SESSION_PREFIXES:
        defaults = cfg["session_defaults"][prefix]
        session: dict[str, object] = {"ACTIVE": prefix in active}
        for field in SESSION_FIELDS:
            key = f"{prefix}_{field}"
            default = defaults.get(field.lower(), DEFAULT_SESSION_DATE.get(field, 0))
            if prefix in active:
                if prefix == "RACE" and field == "DURATION_MINUTES" and race_duration_type == RACE_DURATION_TYPE_LAPS:
                    value = int(default)
                    session[field] = value
                    session["DURATION_SECONDS"] = 0
                    state.set(key, value, "ignored_by_duration_type", "ignored because RACE_DURATION_TYPE=Laps")
                    continue
                if field in SESSION_DATE_RANGES:
                    minimum, maximum = SESSION_DATE_RANGES[field]
                    session[field] = state.integer_in_range(key, int(default), minimum, maximum)
                    continue
                value = state.integer(key, int(default))
                session[field] = value
                if field == "DURATION_MINUTES":
                    seconds = value * 60
                    session["DURATION_SECONDS"] = seconds
                    set_conversion_note(state, key, seconds)
                continue
            if field in DEFAULT_SESSION_DATE:
                value = int(default)
            elif field == "TIME_MULTIPLIER":
                value = 1
            else:
                value = 0
            session[field] = value
            if field == "DURATION_MINUTES":
                session["DURATION_SECONDS"] = 0
            state.set(key, value, "ignored_by_event_type", f"ignored because EVENT_TYPE={event_type}")
        sessions[prefix] = session

    return sessions


def session_time(session: dict) -> dict:
    return {
        "year": int(session["YEAR"]),
        "month": int(session["MONTH"]),
        "day": int(session["DAY"]),
        "hour": int(session["HOUR"]),
        "minute": int(session["MINUTE"]),
        "second": 0,
        "time_multiplier": int(session["TIME_MULTIPLIER"]),
    }


def build_server_doc(state: EnvState, cfg: dict, event_type: str, selected_cars: list[str], track: dict) -> dict:
    defaults = cfg["server_defaults"]

    launch_path_by_event_type = {
        "GameModeType_PRACTICE": "content\\\\data\\\\practice.seasondefinition",
        "GameModeType_RACE_WEEKEND": "content\\\\data\\\\race_weekend.seasondefinition",
    }

    launch_path = launch_path_by_event_type.get(
        event_type,
        launch_path_by_event_type[cfg["event_defaults"]["type"]],
    )

    server_name = state.string("SERVER_NAME", defaults["server_name"])
    max_players = state.integer("SERVER_MAX_PLAYERS", int(defaults["max_players"]))
    track_max_players = int(track.get("max_pit_slot") or max_players)
    if track_max_players > 0 and max_players > track_max_players:
        state.warn(
            f"SERVER_MAX_PLAYERS: {max_players} exceeds track maximum {track_max_players}, "
            f"downscaled to {track_max_players}."
        )
        state.set(
            "SERVER_MAX_PLAYERS",
            track_max_players,
            "fallback",
            f"downscaled from {max_players}; track maximum {track_max_players}",
        )
        max_players = track_max_players
    tcp_port = state.integer("SERVER_TCP_PORT", int(defaults["tcp_port"]))
    udp_port = state.integer("SERVER_UDP_PORT", int(defaults["udp_port"]))
    http_port = state.integer("SERVER_HTTP_PORT", int(defaults["http_port"]))

    return {
        "server_tcp_listener_port": tcp_port,
        "server_udp_listener_port": udp_port,
        "server_tcp_internal_port": tcp_port,
        "server_udp_internal_port": udp_port,
        "server_http_port": http_port,
        "server_name": server_name,
        "launch_path": launch_path,
        "netcode_update_interval": 20,
        "driver_password": state.string("SERVER_DRIVER_PASSWORD", defaults["driver_password"], allow_empty=True),
        "spectator_password": state.string(
            "SERVER_SPECTATOR_PASSWORD",
            defaults["spectator_password"],
            allow_empty=True,
        ),
        "max_players": max_players,
        "allowed_cars_list_full": [
            {"car_name": car_name, "ballast": 0.0, "restrictor": 0.0} for car_name in selected_cars
        ],
        "type": defaults["server_type"],
        "cycle": state.boolean("SERVER_CYCLE_ENABLED", bool(defaults["cycle_enabled"])),
        "admin_password": state.string("SERVER_ADMIN_PASSWORD", defaults["admin_password"], allow_empty=True),
        "pi_min": 0.0,
        "pi_max": 100.0,
        "property_1": False,
        "property_2": False,
        "property_3": False,
        "entry_list_server_url": "",
        "results_post_url": state.string("SERVER_RESULTS_POST_URL", "", allow_empty=True),
        "token": state.string("SERVER_RESULTS_TOKEN", "", allow_empty=True),
        "tuning_allowed": True,
        "entry_list_path": "",
        "results_path": "",
    }


def build_game_config(
    state: EnvState, cfg: dict, sessions: dict[str, dict], event_type: str, race_duration_type: str
) -> dict:
    game: dict[str, object] = {}
    for prefix, name in SESSION_NAMES.items():
        session = sessions[prefix]
        game[f"{name}_duration"] = int(session["DURATION_SECONDS"])
        game[f"{name}_time_of_day"] = session_time(session)
        game[f"{name}_overtime_waiting_next_session"] = int(session["OVERTIME_WAITING_NEXT_SESSION_SECONDS"])
        game[f"{name}_max_wait_to_box"] = int(session["MAX_WAIT_TO_BOX_SECONDS"])

    game["race_duration_type"] = race_duration_type
    race_laps_default = race_duration_laps_default(cfg)
    if not sessions["RACE"]["ACTIVE"]:
        state.set("RACE_DURATION_LAPS", 0, "ignored_by_event_type", f"ignored because EVENT_TYPE={event_type}")
    elif race_duration_type == RACE_DURATION_TYPE_LAPS:
        game["race_duration"] = state.integer("RACE_DURATION_LAPS", race_laps_default)
    else:
        state.set(
            "RACE_DURATION_LAPS",
            race_laps_default,
            "ignored_by_duration_type",
            "ignored because RACE_DURATION_TYPE=Time",
        )
    defaults = cfg["server_defaults"]
    game["min_waiting_for_players"] = state.integer(
        "SERVER_MIN_WAITING_PLAYERS", int(defaults.get("min_waiting_players", 10))
    )
    game["max_waiting_for_players"] = state.integer(
        "SERVER_MAX_WAITING_PLAYERS", int(defaults.get("max_waiting_players", 30))
    )
    return game


def build_season_doc(
    state: EnvState, cfg: dict, event_type: str, track: dict, sessions: dict[str, dict], race_duration_type: str
) -> dict:
    defaults = cfg["event_defaults"]
    mappings = cfg["mappings"]
    return {
        "game_type": event_type,
        "game_config": build_game_config(state, cfg, sessions, event_type, race_duration_type),
        "event": track,
        "weather_type": state.enum("EVENT_WEATHER", mappings["weather"], defaults["weather"]),
        "weather_behaviour": state.enum(
            "EVENT_WEATHER_BEHAVIOUR",
            mappings["weather_behaviour"],
            defaults["weather_behaviour"],
        ),
        "initial_grip": state.enum("EVENT_INITIAL_GRIP", mappings["initial_grip"], defaults["initial_grip"]),
        "export_json": False,
    }


def build_report(cfg: dict, state: EnvState, server_doc: dict, season_doc: dict) -> dict:
    resolved_env = [
        {"key": key, **state.resolved.get(key, {"value": "", "source": "unresolved", "note": ""})}
        for key in cfg["supported_key_order"]
    ]
    game = season_doc["game_config"]
    event = season_doc["event"]

    return {
        "resolved_env": resolved_env,
        "warnings": list(state.warnings),
        "server_summary": {
            "server_name": server_doc["server_name"],
            "ports": {
                "tcp": server_doc["server_tcp_listener_port"],
                "udp": server_doc["server_udp_listener_port"],
                "http": server_doc["server_http_port"],
            },
            "max_players": server_doc["max_players"],
            "cycle": server_doc["cycle"],
            "launch_path": server_doc["launch_path"],
            "car_count": len(server_doc["allowed_cars_list_full"]),
        },
        "season_summary": {
            "game_type": season_doc["game_type"],
            "track": track_token(event),
            "weather": season_doc["weather_type"],
            "weather_behaviour": season_doc["weather_behaviour"],
            "initial_grip": season_doc["initial_grip"],
            "durations": {name: game[f"{name}_duration"] for name in SESSION_NAMES.values()},
        },
    }


def build_documents_with_report(env: dict[str, str]) -> tuple[dict, dict, list[str], dict]:
    cfg = load_config()
    state = EnvState(env, set(cfg["runtime"]["sensitive_env_keys"]))
    prepare_state(state, cfg)

    event_type = state.enum("EVENT_TYPE", cfg["mappings"]["event_type"], cfg["event_defaults"]["type"])
    race_duration_type = state.enum("RACE_DURATION_TYPE", cfg["mappings"]["duration_type"], RACE_DURATION_TYPE_TIME)
    sessions = resolve_sessions(state, cfg, event_type, race_duration_type)
    selected_cars = resolve_cars(state, cfg)
    track = resolve_track(state, cfg, event_type)

    server_doc = build_server_doc(state, cfg, event_type, selected_cars, track)
    season_doc = build_season_doc(state, cfg, event_type, track, sessions, race_duration_type)
    report = build_report(cfg, state, server_doc, season_doc)
    return server_doc, season_doc, state.warnings, report


def build_documents(env: dict[str, str]) -> tuple[dict, dict, list[str]]:
    return build_documents_with_report(env)[:3]


def encode_payload(document: dict) -> str:
    compressed = zlib.compress(json.dumps(document, separators=(",", ":")).encode("utf-8"))
    return base64.b64encode(struct.pack("<I", len(compressed)) + compressed).decode("ascii")


def decode_payload(payload: str) -> dict:
    raw = base64.b64decode(payload)
    if len(raw) < 4:
        raise ValueError("Payload shorter than length prefix.")
    expected = struct.unpack("<I", raw[:4])[0]
    compressed = raw[4:]
    if expected != len(compressed):
        raise ValueError(f"Length prefix mismatch: expected {expected}, got {len(compressed)}.")
    return json.loads(zlib.decompress(compressed).decode("utf-8"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate AC EVO launch payloads from ENV values.")
    parser.add_argument("--server-out", default="/tmp/acevo-serverconfig.b64")
    parser.add_argument("--season-out", default="/tmp/acevo-seasondefinition.b64")
    parser.add_argument("--report-out", default="/tmp/acevo-resolved-env.json")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--print-report", action="store_true")
    args = parser.parse_args(argv)
    server_doc, season_doc, warnings, report = build_documents_with_report(dict(os.environ))

    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)

    Path(args.server_out).write_text(encode_payload(server_doc), encoding="utf-8")
    Path(args.season_out).write_text(encode_payload(season_doc), encoding="utf-8")
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.print_json:
        print(json.dumps(server_doc, indent=2))
        print(json.dumps(season_doc, indent=2))
    if args.print_report:
        print(json.dumps(report, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
