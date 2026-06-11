"""Translate between the dashboard form and the official ``server_launcher.json`` format,
and validate by round-tripping through the real server pipeline.

The form is a flat, seconds-based JSON the frontend posts. ``form_to_launcher`` renders it as a
byte-faithful Windows-launcher file (full car list, dual key casing, ``Length`` in seconds,
``forceTimeDuration`` for Time/Laps). ``launcher_to_form`` parses either a dashboard- or
Windows-generated file back into the form. ``validate``/``save`` run the candidate file through
:func:`scripts.launch_payloads.build_documents_with_report` so the UI sees exactly how the
server will interpret it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from scripts import launch_payloads as lp

from . import metadata

_FALLBACK_TRACK = "Nurburgring|Touristenfahrten|Touristenfahrten Time Attack|19300"


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


def _session_block(name: str, session: dict, visible: bool, *, force_time: bool, length: int) -> dict:
    return {
        "forceTimeDuration": bool(force_time),
        "TimeMultiplier": _as_int(session.get("time_multiplier"), 1),
        "IsVisible": bool(visible),
        "Name": name,
        "Duration": 0,
        "Length": length,
        "Hour": _as_int(session.get("hour"), 16),
        "Minute": _as_int(session.get("minute"), 0),
        "MaxWaitToBox": _as_int(session.get("max_wait_to_box"), 10),
        "OvertimeWaitingNextSession": _as_int(session.get("overtime_waiting_next_session"), 10),
        "MinWaitingForPlayers": _as_int(session.get("min_waiting_for_players"), 10),
        "MaxWaitingForPlayers": _as_int(session.get("max_waiting_for_players"), 30),
    }


def _race_block(session: dict, visible: bool) -> dict:
    duration_type = _as_str(session.get("duration_type")) or lp.MAPPINGS["duration_type"]["time"]
    laps_mode = _is_laps(duration_type)
    length = _as_int(session.get("laps"), 10) if laps_mode else _as_int(session.get("length_sec"), 1500)
    block = _session_block("Race", session, visible, force_time=not laps_mode, length=length)
    block["MaxWaitToBox"] = _as_int(session.get("max_wait_to_box"), 60)
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
        pi = car.get("performance_indicator", 0.0)
        p1, p2, p3 = car.get("property_1", 0), car.get("property_2", 0), car.get("property_3", 0)
        cars.append(
            {
                "is_selected": selected,
                "ballast": ballast,
                "restrictor": restrictor,
                "performance_indicator": pi,
                "property_1": p1,
                "property_2": p2,
                "property_3": p3,
                "name": name,
                "display_name": car.get("display_name", ""),
                "IsSelected": selected,
                "Ballast": ballast,
                "Restrictor": restrictor,
                "PerformanceIndicator": pi,
                "P1": p1,
                "P2": p2,
                "P3": p3,
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
        "EntryListUrl": "",
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
    }

    sessions_block = {
        "PracticeSession": _session_block(
            "Practice",
            sessions.get("practice", {}),
            True,
            force_time=True,
            length=_as_int((sessions.get("practice") or {}).get("length_sec"), 300),
        ),
        "QualifyingSession": _session_block(
            "Qualify",
            sessions.get("qualify", {}),
            race,
            force_time=True,
            length=_as_int((sessions.get("qualify") or {}).get("length_sec"), 600),
        ),
        "WarmupSession": _session_block(
            "Warmup",
            sessions.get("warmup", {}),
            race,
            force_time=True,
            length=_as_int((sessions.get("warmup") or {}).get("length_sec"), 300),
        ),
        "RaceSession": _race_block(sessions.get("race", {}) or {}, race),
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
    force_time = _as_bool(_get(session, "forceTimeDuration", default=True), default=True)
    length = _as_int(_get(session, "Length", "length", "Duration", "duration"), 1500)
    if force_time:
        form["duration_type"] = lp.MAPPINGS["duration_type"]["time"]
        form["length_sec"] = length
        form["laps"] = 10
    else:
        form["duration_type"] = lp.MAPPINGS["duration_type"]["laps"]
        form["laps"] = length
        form["length_sec"] = 1500
    form["max_wait_to_box"] = _as_int(_get(session, "MaxWaitToBox", "max_wait_to_box"), 60)
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


# --- validation + persistence ---------------------------------------------------------------


def _validate_doc(doc: dict) -> dict:
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="acevo-dashboard-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)
        _server, _season, warnings, report = lp.build_documents_with_report({"SERVER_LAUNCHER_JSON": tmp})
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return {"warnings": list(warnings), "report": report, "launcher": doc}


def validate(form: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    return _validate_doc(form_to_launcher(form, cfg))


def save(form: dict, path: str | os.PathLike, cfg: dict | None = None) -> dict:
    cfg = cfg or lp.load_config()
    doc = form_to_launcher(form, cfg)
    result = _validate_doc(doc)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
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
    return launcher_to_form(doc, cfg)
