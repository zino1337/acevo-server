"""Translate between the dashboard form and the official ``server_launcher.json`` format,
and validate by round-tripping through the real server pipeline.

The form is a flat, seconds-based JSON the frontend posts. ``form_to_launcher`` renders it as a
Windows-launcher-compatible file (full car list, dual key casing, ``Length`` in seconds,
``Duration`` 0/1 for Time/Laps). ``launcher_to_form`` parses either a dashboard- or
Windows-generated file back into the form. ``validate``/``save`` run the candidate file through
:func:`scripts.launch_payloads.build_documents_with_report` so the UI sees exactly how the
server will interpret it.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

from scripts import launch_payloads as lp

from . import metadata

_FALLBACK_TRACK = "Nurburgring|Touristenfahrten|Touristenfahrten Time Attack|19300"
_NON_DASHBOARD_ENV_KEYS = {"SERVER_LAUNCHER_JSON", "ACEVO_SERVER_INSTALL_DIR"}
_CAR_FILTER_ENV_KEYS = {
    "EVENT_CARS",
    "EVENT_CAR_CATEGORY",
    "EVENT_BAN_CARS",
    "EVENT_BAN_CAR_CATEGORY",
}
_PASSWORD_FIELDS = {
    "SERVER_DRIVER_PASSWORD": "driver_password",
    "SERVER_SPECTATOR_PASSWORD": "spectator_password",
    "SERVER_ADMIN_PASSWORD": "admin_password",
}
_config_write_lock = threading.RLock()


# --- value coercion -------------------------------------------------------------------------


def _as_str(value) -> str:
    return "" if value is None else str(value)


def _as_int(value, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _get(source: dict, *keys, default=None):
    """First present, non-null value among ``keys`` (handles PascalCase + lowercase variants)."""
    for key in keys:
        if isinstance(source, dict) and source.get(key) is not None:
            return source[key]
    return default


def _is_race(event_type: str) -> bool:
    return "RACE_WEEKEND" in event_type.upper() or event_type.strip().lower() in {"race weekend", "race_weekend"}


def _is_laps(duration_type: str) -> bool:
    return "LAPS" in duration_type.upper()


# --- track lookups --------------------------------------------------------------------------


def _pit_by_token() -> dict[str, int]:
    out: dict[str, int] = {}
    for track_list in metadata.build_metadata()["tracks"].values():
        for track in track_list:
            out[track["token"]] = track["max_pit_slot"]
    return out


def _default_track_token(cfg: dict, event: dict) -> str:
    tracks = metadata.build_metadata()["tracks"]
    event_type = _as_str(event.get("type")) or cfg["event_defaults"]["type"]
    key = "race_weekend" if _is_race(event_type) else "practice"
    track_list = tracks.get(key) or tracks.get("practice") or []
    return track_list[0]["token"] if track_list else _FALLBACK_TRACK


# --- form -> launcher json ------------------------------------------------------------------


def _session_block(
    name: str,
    session: dict,
    visible: bool,
    *,
    duration_type: int,
    length: int,
    persist_mandatory_enabled: bool = False,
) -> dict:
    block = {
        "TimeMultiplier": _as_int(session.get("time_multiplier"), 1),
        "IsVisible": bool(visible),
        "Name": name,
        "Duration": duration_type,
        "Length": length,
        "Hour": _as_int(session.get("hour"), 16),
        "Minute": _as_int(session.get("minute"), 0),
        "MaxWaitToBox": _as_int(session.get("max_wait_to_box"), 10),
        "OvertimeWaitingNextSession": _as_int(session.get("overtime_waiting_next_session"), 10),
        "WindowTimeMandatoryPitstop": _as_int(session.get("mandatory_pitstop_window_seconds"), 600),
        "MinWaitingForPlayers": _as_int(session.get("min_waiting_for_players"), 10),
        "MaxWaitingForPlayers": _as_int(session.get("max_waiting_for_players"), 30),
        "MandatoryPitStopRefuel": _as_bool(session.get("mandatory_pitstop_refuel"), True),
        "MandatoryPitStopTyreChange": _as_bool(session.get("mandatory_pitstop_tyre_change"), True),
        "EnableTraffic": True,
        "SelectedSpawnValue": None,
    }
    if persist_mandatory_enabled:
        block["MandatoryPitStopEnabled"] = _as_bool(session.get("mandatory_pitstop_enabled"), False)
    return block


def _race_block(session: dict, visible: bool) -> dict:
    duration_type = _as_str(session.get("duration_type")) or lp.MAPPINGS["duration_type"]["time"]
    laps_mode = _is_laps(duration_type)
    length = _as_int(session.get("laps"), 10) if laps_mode else _as_int(session.get("length_sec"), 1500)
    block = _session_block(
        "Race",
        session,
        visible,
        duration_type=1 if laps_mode else 0,
        length=length,
        persist_mandatory_enabled=True,
    )
    block["MaxWaitToBox"] = _as_int(session.get("max_wait_to_box"), 60)
    mandatory_enabled = (
        visible
        and not laps_mode
        and length > lp.MANDATORY_PITSTOP_MIN_RACE_SECONDS
        and _as_bool(session.get("mandatory_pitstop_enabled"), False)
    )
    block["MandatoryPitStopEnabled"] = mandatory_enabled
    if mandatory_enabled:
        block["WindowTimeMandatoryPitstop"] = min(
            max(_as_int(session.get("mandatory_pitstop_window_seconds"), 600), 1),
            length,
        )
    return block


def _cars_block(cfg: dict, cars_form: list) -> list[dict]:
    overrides: dict[str, dict] = {}
    for entry in cars_form or []:
        name = _get(entry, "name", "Name")
        if not name:
            continue
        overrides[name] = {
            "is_selected": _as_bool(_get(entry, "is_selected", "IsSelected", default=False)),
            "ballast": _as_float(_get(entry, "ballast", "Ballast", default=0.0)),
            "restrictor": _as_float(_get(entry, "restrictor", "Restrictor", default=0.0)),
        }

    cars: list[dict] = []
    for car in cfg["cars_data"]:
        name = car["internal_name"]
        override = overrides.get(name, {})
        selected = bool(override.get("is_selected", False))
        ballast = float(override.get("ballast", 0.0))
        restrictor = float(override.get("restrictor", 0.0))
        pi = _as_float(car.get("performance_indicator"), 0.0)
        p1 = _as_int(car.get("property_1"), 0)
        p2 = _as_int(car.get("property_2"), 0)
        p3 = _as_int(car.get("property_3"), 0)
        is_mod = bool(car.get("is_mod"))
        cars.append(
            {
                "is_selected": selected,
                "ballast": ballast,
                "restrictor": restrictor,
                "performance_indicator": pi,
                "property_1": p1,
                "property_2": p2,
                "property_3": p3,
                "is_mod": is_mod,
                "name": name,
                "display_name": car.get("display_name", ""),
                "IsModText": "MOD" if is_mod else "",
                "IsSelected": selected,
                "Ballast": ballast,
                "Restrictor": restrictor,
                "PerformanceIndicator": pi,
                "P1": p1,
                "P2": p2,
                "P3": p3,
                "IsMod": is_mod,
            }
        )
    return cars


def form_to_launcher(form: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    server = form.get("server", {}) or {}
    event = form.get("event", {}) or {}
    sessions = form.get("sessions", {}) or {}

    event_type = _as_str(event.get("type")) or cfg["event_defaults"]["type"]
    race = _is_race(event_type)

    track_token = _as_str(event.get("track")) or _default_track_token(cfg, event)
    max_players = _as_int(server.get("max_players"), int(cfg["server_defaults"]["max_players"]))
    pit = _pit_by_token().get(track_token)
    max_players_limit = _as_int(server.get("max_players_limit"), pit or max_players) or (pit or max_players)

    server_block = {
        "SelectedServerTypeValue": _as_str(server.get("server_type")) or cfg["server_defaults"]["server_type"],
        "ServerName": _as_str(server.get("server_name")) or cfg["server_defaults"]["server_name"],
        "MaxPlayers": max_players,
        "MaxPlayersLimit": max_players_limit,
        "TcpPort": _as_int(server.get("tcp_port"), int(cfg["server_defaults"]["tcp_port"])),
        "UdpPort": _as_int(server.get("udp_port"), int(cfg["server_defaults"]["udp_port"])),
        "HttpPort": _as_int(server.get("http_port"), int(cfg["server_defaults"]["http_port"])),
        "IsCycleEnabled": _as_bool(server.get("cycle_enabled"), bool(cfg["server_defaults"]["cycle_enabled"])),
        "DriverPassword": _as_str(server.get("driver_password")),
        "SpectatorPassword": _as_str(server.get("spectator_password")),
        "AdminPassword": _as_str(server.get("admin_password")),
        "EntryListUrl": _as_str(server.get("entry_list_url")),
        "ResultsPostUrl": _as_str(server.get("results_post_url")),
        "EntryListPath": _as_str(server.get("entry_list_path")),
        "ResultsPath": _as_str(server.get("results_path")),
        "SelectedTuningTypeValue": _as_str(server.get("tuning_type")) or cfg["server_defaults"]["tuning_type"],
    }

    event_block = {
        "SelectedSessionTypeValue": event_type,
        "SelectedWeatherTypeValue": _as_str(event.get("weather")) or cfg["event_defaults"]["weather"],
        "SelectedWeatherBehaviorValue": (
            _as_str(event.get("weather_behaviour")) or cfg["event_defaults"]["weather_behaviour"]
        ),
        "SelectedInitialGripValue": _as_str(event.get("initial_grip")) or cfg["event_defaults"]["initial_grip"],
        "SelectedTrackValue": track_token,
        "Cars": _cars_block(cfg, form.get("cars", [])),
        "ShowOnlySelected": _as_bool(event.get("show_only_selected"), False),
        "ShowOnlyOfficial": False,
        "SelectOnlyOfficialCarsCommand": {"$type": "CommunityToolkit.Mvvm.Input.RelayCommand, CommunityToolkit.Mvvm"},
    }

    sessions_block = {
        "PracticeSession": _session_block(
            "Practice",
            sessions.get("practice", {}),
            True,
            duration_type=0,
            length=_as_int((sessions.get("practice") or {}).get("length_sec"), 300),
        ),
        "QualifyingSession": _session_block(
            "Qualify",
            sessions.get("qualify", {}),
            race,
            duration_type=0,
            length=_as_int((sessions.get("qualify") or {}).get("length_sec"), 600),
        ),
        "WarmupSession": _session_block(
            "Warmup",
            sessions.get("warmup", {}),
            race,
            duration_type=0,
            length=_as_int((sessions.get("warmup") or {}).get("length_sec"), 300),
        ),
        "RaceSession": _race_block(sessions.get("race", {}) or {}, race),
        "FreeroamSession": _session_block(
            "FreeRoam",
            {},
            False,
            duration_type=0,
            length=300,
        ),
    }

    return {"Server": server_block, "Event": event_block, "Sessions": sessions_block}


# --- launcher json -> form ------------------------------------------------------------------


def _session_form(session: dict) -> dict:
    return {
        "length_sec": _as_int(_get(session, "Length", "length", "Duration", "duration"), 300),
        "hour": _as_int(_get(session, "Hour", "hour"), 16),
        "minute": _as_int(_get(session, "Minute", "minute"), 0),
        "time_multiplier": _as_int(_get(session, "TimeMultiplier", "time_multiplier"), 1),
        "max_wait_to_box": _as_int(_get(session, "MaxWaitToBox", "max_wait_to_box"), 10),
        "overtime_waiting_next_session": _as_int(
            _get(session, "OvertimeWaitingNextSession", "overtime_waiting_next_session"), 10
        ),
        "min_waiting_for_players": _as_int(_get(session, "MinWaitingForPlayers", "min_waiting_for_players"), 10),
        "max_waiting_for_players": _as_int(_get(session, "MaxWaitingForPlayers", "max_waiting_for_players"), 30),
    }


def _race_session_form(session: dict) -> dict:
    form = _session_form(session)
    if "forceTimeDuration" in session:
        laps_mode = not _as_bool(_get(session, "forceTimeDuration", default=True), default=True)
    else:
        laps_mode = _as_int(_get(session, "Duration", "duration"), 0) == 1
    length = _as_int(_get(session, "Length", "length", "Duration", "duration"), 1500)
    if laps_mode:
        form["duration_type"] = lp.MAPPINGS["duration_type"]["laps"]
        form["laps"] = length
        form["length_sec"] = 1500
    else:
        form["duration_type"] = lp.MAPPINGS["duration_type"]["time"]
        form["length_sec"] = length
        form["laps"] = 10
    form["max_wait_to_box"] = _as_int(_get(session, "MaxWaitToBox", "max_wait_to_box"), 60)
    form["mandatory_pitstop_enabled"] = _as_bool(
        _get(session, "MandatoryPitStopEnabled", "mandatory_pitstop_enabled"),
        False,
    )
    form["mandatory_pitstop_window_seconds"] = _as_int(
        _get(session, "WindowTimeMandatoryPitstop", "mandatory_pitstop_window_seconds"),
        600,
    )
    form["mandatory_pitstop_refuel"] = _as_bool(
        _get(session, "MandatoryPitStopRefuel", "mandatory_pitstop_refuel"),
        True,
    )
    form["mandatory_pitstop_tyre_change"] = _as_bool(
        _get(session, "MandatoryPitStopTyreChange", "mandatory_pitstop_tyre_change"),
        True,
    )
    return form


def launcher_to_form(doc: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    server = doc.get("Server", {}) if isinstance(doc, dict) else {}
    event = doc.get("Event", {}) if isinstance(doc, dict) else {}
    sessions = doc.get("Sessions", {}) if isinstance(doc, dict) else {}
    known = {car["internal_name"] for car in cfg["cars_data"]}

    server_form = {
        "server_name": _as_str(_get(server, "ServerName", "server_name")) or cfg["server_defaults"]["server_name"],
        "max_players": _as_int(_get(server, "MaxPlayers", "max_players"), int(cfg["server_defaults"]["max_players"])),
        "max_players_limit": _as_int(_get(server, "MaxPlayersLimit", "max_players_limit"), 0) or None,
        "tcp_port": _as_int(_get(server, "TcpPort", "tcp_port"), int(cfg["server_defaults"]["tcp_port"])),
        "udp_port": _as_int(_get(server, "UdpPort", "udp_port"), int(cfg["server_defaults"]["udp_port"])),
        "http_port": _as_int(_get(server, "HttpPort", "http_port"), int(cfg["server_defaults"]["http_port"])),
        "server_type": _as_str(_get(server, "SelectedServerTypeValue", "server_type"))
        or cfg["server_defaults"]["server_type"],
        "tuning_type": _as_str(_get(server, "SelectedTuningTypeValue", "tuning_type"))
        or cfg["server_defaults"]["tuning_type"],
        "cycle_enabled": _as_bool(_get(server, "IsCycleEnabled", "cycle_enabled", default=True), default=True),
        "driver_password": _as_str(_get(server, "DriverPassword", "driver_password")),
        "spectator_password": _as_str(_get(server, "SpectatorPassword", "spectator_password")),
        "admin_password": _as_str(_get(server, "AdminPassword", "admin_password")),
        "entry_list_url": _as_str(_get(server, "EntryListUrl", "entry_list_url")),
        "results_post_url": _as_str(_get(server, "ResultsPostUrl", "results_post_url")),
        "entry_list_path": _as_str(_get(server, "EntryListPath", "entry_list_path")),
        "results_path": _as_str(_get(server, "ResultsPath", "results_path")),
    }

    event_form = {
        "type": _as_str(_get(event, "SelectedSessionTypeValue", "type")) or cfg["event_defaults"]["type"],
        "weather": _as_str(_get(event, "SelectedWeatherTypeValue", "weather")) or cfg["event_defaults"]["weather"],
        "weather_behaviour": _as_str(
            _get(event, "SelectedWeatherBehaviorValue", "SelectedWeatherBehaviourValue", "weather_behaviour")
        )
        or cfg["event_defaults"]["weather_behaviour"],
        "initial_grip": _as_str(_get(event, "SelectedInitialGripValue", "initial_grip"))
        or cfg["event_defaults"]["initial_grip"],
        "track": _as_str(_get(event, "SelectedTrackValue", "track")),
        "show_only_selected": _as_bool(_get(event, "ShowOnlySelected", "show_only_selected", default=False)),
    }

    cars_form: list[dict] = []
    for entry in event.get("Cars") or []:
        if not isinstance(entry, dict):
            continue
        name = _get(entry, "name", "Name")
        if name not in known:
            display = _get(entry, "display_name", "DisplayName")
            name = cfg["car_lookup"].get(lp.normalize_label(_as_str(display))) if display else None
        if not name:
            continue
        cars_form.append(
            {
                "name": name,
                "is_selected": _as_bool(_get(entry, "IsSelected", "is_selected", default=False)),
                "ballast": _as_float(_get(entry, "Ballast", "ballast", default=0.0)),
                "restrictor": _as_float(_get(entry, "Restrictor", "restrictor", default=0.0)),
            }
        )

    sessions_form = {
        "practice": _session_form(sessions.get("PracticeSession", {}) or {}),
        "qualify": _session_form(sessions.get("QualifyingSession", {}) or {}),
        "warmup": _session_form(sessions.get("WarmupSession", {}) or {}),
        "race": _race_session_form(sessions.get("RaceSession", {}) or {}),
    }

    return {"server": server_form, "event": event_form, "cars": cars_form, "sessions": sessions_form}


# --- runtime docs -> form -------------------------------------------------------------------


def _session_defaults(cfg: dict, prefix: str) -> dict:
    return cfg["session_defaults"].get(prefix.upper(), {})


def _session_default_seconds(defaults: dict, fallback_minutes: int = 5) -> int:
    return _as_int(defaults.get("duration_minutes"), fallback_minutes) * 60


def _runtime_session_form(game: dict, name: str, defaults: dict) -> dict:
    time_of_day = game.get(f"{name}_time_of_day", {})
    if not isinstance(time_of_day, dict):
        time_of_day = {}
    return {
        "length_sec": _as_int(game.get(f"{name}_duration"), _session_default_seconds(defaults)),
        "hour": _as_int(time_of_day.get("hour"), _as_int(defaults.get("hour"), 16)),
        "minute": _as_int(time_of_day.get("minute"), _as_int(defaults.get("minute"), 0)),
        "time_multiplier": _as_int(
            time_of_day.get("time_multiplier"),
            _as_int(defaults.get("time_multiplier"), 1),
        ),
        "max_wait_to_box": _as_int(
            game.get(f"{name}_max_wait_to_box"),
            _as_int(defaults.get("max_wait_to_box_seconds"), 10),
        ),
        "overtime_waiting_next_session": _as_int(
            game.get(f"{name}_overtime_waiting_next_session"),
            _as_int(defaults.get("overtime_waiting_next_session_seconds"), 10),
        ),
        "min_waiting_for_players": _as_int(defaults.get("min_waiting_for_players_seconds"), 10),
        "max_waiting_for_players": _as_int(defaults.get("max_waiting_for_players_seconds"), 30),
    }


def _runtime_race_session_form(game: dict, defaults: dict) -> dict:
    form = _runtime_session_form(game, "race", defaults)
    duration_type = _as_str(game.get("race_duration_type")) or lp.RACE_DURATION_TYPE_TIME
    form["duration_type"] = duration_type
    if _is_laps(duration_type):
        form["laps"] = _as_int(game.get("race_duration"), _as_int(defaults.get("duration_laps"), 10))
        form["length_sec"] = _session_default_seconds(defaults, 25)
    else:
        form["laps"] = _as_int(defaults.get("duration_laps"), 10)
    form["min_waiting_for_players"] = _as_int(
        game.get("min_waiting_for_players"),
        _as_int(defaults.get("min_waiting_for_players_seconds"), 10),
    )
    form["max_waiting_for_players"] = _as_int(
        game.get("max_waiting_for_players"),
        _as_int(defaults.get("max_waiting_for_players_seconds"), 30),
    )
    form["mandatory_pitstop_enabled"] = _as_bool(game.get("mandatory_pit_stop"), False)
    form["mandatory_pitstop_window_seconds"] = _as_int(
        game.get("pit_window"),
        _as_int(defaults.get("mandatory_pitstop_window_seconds"), 600),
    )
    form["mandatory_pitstop_refuel"] = _as_bool(
        game.get("requires_refuelling"),
        _as_bool(defaults.get("mandatory_pitstop_refuel"), True),
    )
    form["mandatory_pitstop_tyre_change"] = _as_bool(
        game.get("requires_tyre_change"),
        _as_bool(defaults.get("mandatory_pitstop_tyre_change"), True),
    )
    return form


def _runtime_track_token(season_doc: dict, cfg: dict) -> str:
    track = season_doc.get("event", {})
    if isinstance(track, dict):
        try:
            return lp.track_token(track)
        except (KeyError, TypeError, ValueError):
            pass
    event = {"type": _as_str(season_doc.get("game_type")) or cfg["event_defaults"]["type"]}
    return _default_track_token(cfg, event)


def _runtime_cars_form(server_doc: dict, cfg: dict) -> list[dict]:
    selected: dict[str, dict] = {}
    for entry in server_doc.get("allowed_cars_list_full") or []:
        if not isinstance(entry, dict):
            continue
        name = _as_str(_get(entry, "car_name", "name", "Name"))
        if not name:
            continue
        selected[name] = {
            "ballast": _as_float(_get(entry, "ballast", "Ballast", default=0.0)),
            "restrictor": _as_float(_get(entry, "restrictor", "Restrictor", default=0.0)),
        }

    return [
        {
            "name": car["internal_name"],
            "is_selected": car["internal_name"] in selected,
            "ballast": selected.get(car["internal_name"], {}).get("ballast", 0.0),
            "restrictor": selected.get(car["internal_name"], {}).get("restrictor", 0.0),
        }
        for car in cfg["cars_data"]
    ]


def runtime_documents_to_form(server_doc: dict, season_doc: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    game = season_doc.get("game_config", {}) if isinstance(season_doc, dict) else {}
    if not isinstance(game, dict):
        game = {}

    max_players = _as_int(server_doc.get("max_players"), int(cfg["server_defaults"]["max_players"]))
    track_token = _runtime_track_token(season_doc, cfg)
    max_players_limit = _pit_by_token().get(track_token, max_players)

    server_form = {
        "server_name": _as_str(server_doc.get("server_name")) or cfg["server_defaults"]["server_name"],
        "max_players": max_players,
        "max_players_limit": max_players_limit,
        "tcp_port": _as_int(
            server_doc.get("server_tcp_listener_port"),
            int(cfg["server_defaults"]["tcp_port"]),
        ),
        "udp_port": _as_int(
            server_doc.get("server_udp_listener_port"),
            int(cfg["server_defaults"]["udp_port"]),
        ),
        "http_port": _as_int(server_doc.get("server_http_port"), int(cfg["server_defaults"]["http_port"])),
        "server_type": _as_str(server_doc.get("type")) or cfg["server_defaults"]["server_type"],
        "tuning_type": _as_str(server_doc.get("tuning_type")) or cfg["server_defaults"]["tuning_type"],
        "cycle_enabled": _as_bool(server_doc.get("cycle"), bool(cfg["server_defaults"]["cycle_enabled"])),
        "driver_password": _as_str(server_doc.get("driver_password")),
        "spectator_password": _as_str(server_doc.get("spectator_password")),
        "admin_password": _as_str(server_doc.get("admin_password")),
        "entry_list_url": _as_str(server_doc.get("entry_list_server_url")),
        "results_post_url": _as_str(server_doc.get("results_post_url")),
        "entry_list_path": _as_str(server_doc.get("entry_list_path")),
        "results_path": _as_str(server_doc.get("results_path")),
    }
    event_form = {
        "type": _as_str(season_doc.get("game_type")) or cfg["event_defaults"]["type"],
        "weather": _as_str(season_doc.get("weather_type")) or cfg["event_defaults"]["weather"],
        "weather_behaviour": _as_str(season_doc.get("weather_behaviour")) or cfg["event_defaults"]["weather_behaviour"],
        "initial_grip": _as_str(season_doc.get("initial_grip")) or cfg["event_defaults"]["initial_grip"],
        "track": _runtime_track_token(season_doc, cfg),
        "show_only_selected": False,
    }
    sessions_form = {
        "practice": _runtime_session_form(game, "practice", _session_defaults(cfg, "PRACTICE")),
        "qualify": _runtime_session_form(game, "qualify", _session_defaults(cfg, "QUALIFY")),
        "warmup": _runtime_session_form(game, "warmup", _session_defaults(cfg, "WARMUP")),
        "race": _runtime_race_session_form(game, _session_defaults(cfg, "RACE")),
    }
    return {
        "server": server_form,
        "event": event_form,
        "cars": _runtime_cars_form(server_doc, cfg),
        "sessions": sessions_form,
    }


def effective_runtime_form(
    config_path: str | os.PathLike | None = None,
    env: dict | None = None,
    cfg: dict | None = None,
) -> dict:
    cfg = cfg or lp.load_config()
    runtime_env = {str(key): str(value) for key, value in (os.environ if env is None else env).items()}
    if config_path is not None and "SERVER_LAUNCHER_JSON" not in runtime_env:
        runtime_env["SERVER_LAUNCHER_JSON"] = str(config_path)
    server_doc, season_doc, _warnings, _report = lp.build_documents_with_report(runtime_env)
    return runtime_documents_to_form(server_doc, season_doc, cfg)


# --- validation + persistence ---------------------------------------------------------------


def _runtime_env(env: dict | None, config_path: str | os.PathLike | None = None) -> dict[str, str]:
    runtime_env = {str(key): str(value) for key, value in (os.environ if env is None else env).items()}
    if config_path is not None:
        runtime_env["SERVER_LAUNCHER_JSON"] = str(config_path)
    return runtime_env


def dashboard_managed_env_keys(cfg: dict | None = None) -> tuple[str, ...]:
    cfg = cfg or lp.load_config()
    external = set(cfg["runtime"]["external_runtime_env"])
    return tuple(
        key for key in cfg["supported_key_order"] if key not in _NON_DASHBOARD_ENV_KEYS and key not in external
    )


def _saved_document_available(path: str | os.PathLike) -> bool:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict)


def _mandatory_pitstop_extension_warning(path: str | os.PathLike) -> str:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    event = doc.get("Event", {}) if isinstance(doc, dict) else {}
    sessions = doc.get("Sessions", {}) if isinstance(doc, dict) else {}
    race = sessions.get("RaceSession", {}) if isinstance(sessions, dict) else {}
    if not isinstance(event, dict) or not isinstance(race, dict):
        return ""
    if not _is_race(_as_str(_get(event, "SelectedSessionTypeValue", "type"))):
        return ""
    if "MandatoryPitStopEnabled" in race or "mandatory_pitstop_enabled" in race:
        return ""
    if "forceTimeDuration" in race:
        laps_mode = not _as_bool(race.get("forceTimeDuration"), True)
    else:
        laps_mode = _as_int(_get(race, "Duration", "duration"), 0) == 1
    length = _as_int(_get(race, "Length", "length", "Duration", "duration"), 0)
    if laps_mode or length <= lp.MANDATORY_PITSTOP_MIN_RACE_SECONDS:
        return ""
    return (
        "This official AC EVO 0.9 launcher file does not store the Mandatory Pitstop main switch. "
        "It was imported as Off; enable it explicitly in the Dashboard if required."
    )


def config_source_info(
    path: str | os.PathLike,
    env: dict | None = None,
    cfg: dict | None = None,
) -> dict:
    cfg = cfg or lp.load_config()
    runtime_env = _runtime_env(env, path)
    requested, note = lp.requested_config_priority(runtime_env)
    dashboard_available = _saved_document_available(path)
    effective = requested if requested != "dashboard" or dashboard_available else "env"
    warning = ""
    if note.startswith("invalid"):
        warning = note
    if requested == "dashboard" and not dashboard_available:
        warning = "Dashboard configuration is missing or invalid. Environment priority is active."
    if not warning and dashboard_available:
        warning = _mandatory_pitstop_extension_warning(path)
    env_keys = sorted(key for key in dashboard_managed_env_keys(cfg) if key in runtime_env)
    return {
        "config_source": effective,
        "environment_available": bool(env_keys),
        "dashboard_available": dashboard_available,
        "source_switch_available": bool(env_keys) and dashboard_available,
        "source_warning": warning,
    }


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, document: dict) -> None:
    _atomic_write_bytes(path, json.dumps(document, indent=2).encode("utf-8"))


def config_state_path(config_path: str | os.PathLike) -> Path:
    return Path(config_path).parent / lp.CONFIG_STATE_FILENAME


def set_config_source(
    source: str,
    config_path: str | os.PathLike,
    env: dict | None = None,
    cfg: dict | None = None,
) -> dict:
    source = _as_str(source).strip().lower()
    if source not in lp.CONFIG_PRIORITIES:
        return {"ok": False, "error": "config source must be 'env' or 'dashboard'"}
    state_path = config_state_path(config_path)
    with _config_write_lock:
        if source == "dashboard" and not _saved_document_available(config_path):
            return {"ok": False, "error": "saved Dashboard configuration is missing or invalid"}
        _atomic_write_json(state_path, {"config_source": source})
    return {"ok": True, "state_path": str(state_path), **config_source_info(config_path, env, cfg)}


def _build_doc(doc: dict, env: dict | None, priority: str) -> tuple[dict, dict, list[str], dict]:
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="acevo-dashboard-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)
        runtime_env = _runtime_env(env, tmp)
        return lp.build_documents_with_report(runtime_env, config_priority=priority)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _resolved_by_key(report: dict) -> dict[str, dict]:
    return {entry["key"]: entry for entry in report.get("resolved_env", [])}


def _car_signature(server_doc: dict) -> tuple[tuple, ...]:
    return tuple(
        sorted(
            (
                car.get("car_name"),
                int(car.get("ballast", 0)),
                float(car.get("restrictor", 0.0)),
            )
            for car in server_doc.get("allowed_cars_list_full", [])
        )
    )


def _target_uses_key(key: str, season_doc: dict, desired: dict) -> bool:
    game_type = season_doc.get("game_type")
    if game_type == "GameModeType_PRACTICE" and key.startswith(("QUALIFY_", "WARMUP_", "RACE_")):
        return False
    entry = desired.get(key, {})
    return entry.get("source") not in {"ignored_by_event_type", "ignored_by_duration_type", "unresolved"}


def _env_conflicts(doc: dict, env: dict | None, cfg: dict) -> list[str]:
    if env is None:
        return []
    runtime_env = _runtime_env(env)
    managed = dashboard_managed_env_keys(cfg)
    if not any(key in runtime_env for key in managed):
        return []

    current_server, current_season, _current_warnings, current_report = _build_doc(doc, runtime_env, "env")
    desired_server, desired_season, _desired_warnings, desired_report = _build_doc(doc, runtime_env, "dashboard")
    current = _resolved_by_key(current_report)
    desired = _resolved_by_key(desired_report)
    cars_differ = _car_signature(current_server) != _car_signature(desired_server)
    conflicts: list[str] = []

    for key in managed:
        if key not in runtime_env:
            continue
        current_entry = current.get(key, {})
        if key in _CAR_FILTER_ENV_KEYS:
            if cars_differ and (runtime_env.get(key, "").strip() or current_entry.get("source") == "env"):
                conflicts.append(key)
            continue
        if not _target_uses_key(key, desired_season, desired):
            continue
        if current_entry.get("source") != "env":
            continue
        if key in _PASSWORD_FIELDS:
            field = _PASSWORD_FIELDS[key]
            differs = current_server.get(field) != desired_server.get(field)
        else:
            differs = current_entry.get("value") != desired.get(key, {}).get("value")
        if differs:
            conflicts.append(key)
    return conflicts


def _validate_doc(doc: dict, env: dict | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    _server, _season, warnings, report = _build_doc(doc, {}, "dashboard")
    return {
        "warnings": list(warnings),
        "report": report,
        "env_conflicts": _env_conflicts(doc, env, cfg),
    }


def validate(form: dict, cfg: dict | None = None, env: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    return _validate_doc(form_to_launcher(form, cfg), env, cfg)


def save(form: dict, path: str | os.PathLike, cfg: dict | None = None, env: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    doc = form_to_launcher(form, cfg)
    result = _validate_doc(doc, env, cfg)
    target = Path(path)
    with _config_write_lock:
        _atomic_write_json(target, doc)
    result["ok"] = True
    result["path"] = str(target)
    result.update(config_source_info(target, env, cfg))
    return result


def apply(form: dict, path: str | os.PathLike, cfg: dict | None = None, env: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    doc = form_to_launcher(form, cfg)
    result = _validate_doc(doc, env, cfg)
    target = Path(path)
    with _config_write_lock:
        previous = target.read_bytes() if target.exists() else None
        state_target = config_state_path(target)
        previous_state = state_target.read_bytes() if state_target.exists() else None
        try:
            _atomic_write_json(target, doc)
            source_result = set_config_source("dashboard", target, env, cfg)
            if not source_result.get("ok"):
                raise OSError(source_result.get("error", "cannot activate Dashboard priority"))
        except Exception:
            if previous is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write_bytes(target, previous)
            if previous_state is None:
                try:
                    state_target.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write_bytes(state_target, previous_state)
            raise
    result.update(source_result)
    result["path"] = str(target)
    return result


def load_saved(path: str | os.PathLike, cfg: dict | None = None) -> dict | None:
    cfg = cfg or lp.load_config()
    target = Path(path)
    if not target.exists():
        return None
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    return launcher_to_form(doc, cfg)


# --- named configuration profiles -----------------------------------------------------------
# Profiles are launcher-format JSON files in <config dir>/configs/<name>.json — a library you
# load from, separate from the active server_launcher.json the server actually runs.

_PROFILE_NAME_RE = re.compile(r"[^A-Za-z0-9 ._-]")


def profiles_dir(config_path: str | os.PathLike) -> Path:
    return Path(config_path).parent / "configs"


def _safe_profile_name(name) -> str | None:
    """Sanitize a profile name to a safe filename stem (no path traversal). None if unusable."""
    cleaned = _PROFILE_NAME_RE.sub("", _as_str(name)).strip()
    if not cleaned or cleaned in {".", ".."}:
        return None
    return cleaned


def list_profiles(config_path: str | os.PathLike) -> list[dict]:
    directory = profiles_dir(config_path)
    if not directory.is_dir():
        return []
    profiles: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        server = doc.get("Server", {}) if isinstance(doc, dict) else {}
        event = doc.get("Event", {}) if isinstance(doc, dict) else {}
        profiles.append(
            {
                "name": path.stem,
                "server_name": _as_str(_get(server, "ServerName", "server_name")),
                "track": _as_str(_get(event, "SelectedTrackValue", "track")),
                "mode": _as_str(_get(event, "SelectedSessionTypeValue", "type")),
                "modified": path.stat().st_mtime,
            }
        )
    return profiles


def save_profile(name, form: dict, config_path: str | os.PathLike, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    safe = _safe_profile_name(name)
    if not safe:
        return {"ok": False, "error": "invalid profile name"}
    doc = form_to_launcher(form, cfg)
    result = _validate_doc(doc)
    directory = profiles_dir(config_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{safe}.json"
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    result["ok"] = True
    result["name"] = safe
    result["path"] = str(target)
    return result


def load_profile(name, config_path: str | os.PathLike, cfg: dict | None = None) -> dict | None:
    cfg = cfg or lp.load_config()
    safe = _safe_profile_name(name)
    if not safe:
        return None
    target = profiles_dir(config_path) / f"{safe}.json"
    return load_saved(target, cfg)


def delete_profile(name, config_path: str | os.PathLike) -> dict:
    safe = _safe_profile_name(name)
    if not safe:
        return {"ok": False, "error": "invalid profile name"}
    target = profiles_dir(config_path) / f"{safe}.json"
    try:
        target.unlink()
    except FileNotFoundError:
        return {"ok": False, "error": "profile not found"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": safe}
