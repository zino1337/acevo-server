import base64
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import launch_payloads


def resolved(report, key):
    return next(item for item in report["resolved_env"] if item["key"] == key)


def all_car_names():
    return {car["internal_name"] for car in launch_payloads.load_config()["cars_data"]}


def selected_car_names(server_doc):
    return {car["car_name"] for car in server_doc["allowed_cars_list_full"]}


def write_launcher_json(base: Path, value) -> Path:
    path = base / "server_launcher.json"
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")
    return path


def launcher_document():
    return {
        "Server": {
            "SelectedServerTypeValue": "MultiplayerServerListSessionType_BOTH",
            "ServerName": "Windows Tool Server",
            "MaxPlayers": 8,
            "TcpPort": 9701,
            "UdpPort": 9701,
            "HttpPort": 8081,
            "IsCycleEnabled": False,
            "DriverPassword": "driver-password",
            "SpectatorPassword": "spectator-password",
            "AdminPassword": "admin-password",
            "ResultsPostUrl": "https://results.example.test/launcher",
        },
        "Event": {
            "SelectedSessionTypeValue": "GameModeType_RACE_WEEKEND",
            "SelectedWeatherTypeValue": "GameModeSelectionWeatherType_SCATTERED_CLOUDS",
            "SelectedWeatherBehaviorValue": "GameModeSelectionWeatherBehaviour_DYNAMIC",
            "SelectedInitialGripValue": "InitialGrip_OPTIMUM",
            "SelectedTrackValue": "Watkins Glen International|GP Inner Loop|GP Inner Loop Race|5552",
            "Cars": [
                {
                    "IsSelected": True,
                    "name": "preset_695b_mech_1",
                    "display_name": "Abarth 695 Biposto - Standard",
                    "Ballast": 12.5,
                    "Restrictor": 3.0,
                },
                {
                    "IsSelected": True,
                    "name": "ks_caterham_acmd_mech_1",
                    "display_name": "Caterham Academy - Academy",
                    "Ballast": 0,
                    "Restrictor": 0,
                },
            ],
            "ShowOnlySelected": False,
        },
        "Sessions": {
            "PracticeSession": {
                "forceTimeDuration": True,
                "TimeMultiplier": 2,
                "Length": 600,
                "Hour": 10,
                "Minute": 15,
                "MaxWaitToBox": 11,
                "OvertimeWaitingNextSession": 12,
                "MinWaitingForPlayers": 2,
                "MaxWaitingForPlayers": 12,
            },
            "QualifyingSession": {
                "forceTimeDuration": True,
                "TimeMultiplier": 1,
                "Length": 300,
                "Hour": 11,
                "Minute": 0,
                "MaxWaitToBox": 13,
                "OvertimeWaitingNextSession": 14,
                "MinWaitingForPlayers": 2,
                "MaxWaitingForPlayers": 12,
            },
            "WarmupSession": {
                "forceTimeDuration": True,
                "TimeMultiplier": 1,
                "Length": 120,
                "Hour": 11,
                "Minute": 30,
                "MaxWaitToBox": 15,
                "OvertimeWaitingNextSession": 16,
                "MinWaitingForPlayers": 2,
                "MaxWaitingForPlayers": 12,
            },
            "RaceSession": {
                "forceTimeDuration": False,
                "TimeMultiplier": 1,
                "Length": 8,
                "Hour": 12,
                "Minute": 0,
                "MaxWaitToBox": 17,
                "OvertimeWaitingNextSession": 18,
                "MinWaitingForPlayers": 2,
                "MaxWaitingForPlayers": 12,
            },
        },
    }


class LaunchPayloadTests(unittest.TestCase):
    def test_event_label_mapping(self):
        cases = [
            (
                {
                    "EVENT_TYPE": "practice",
                    "EVENT_INITIAL_GRIP": "gReEn",
                    "EVENT_WEATHER_BEHAVIOUR": "stATic",
                    "EVENT_WEATHER": "clear",
                },
                (
                    "GameModeType_PRACTICE",
                    "InitialGrip_GREEN",
                    "GameModeSelectionWeatherBehaviour_STATIC",
                    "GameModeSelectionWeatherType_CLEAR",
                ),
            ),
            (
                {
                    "EVENT_TYPE": "Race_Weekend",
                    "EVENT_INITIAL_GRIP": "FAST",
                    "EVENT_WEATHER_BEHAVIOUR": "DYNAMIC",
                    "EVENT_WEATHER": "HEAVY_RAIN",
                },
                (
                    "GameModeType_RACE_WEEKEND",
                    "InitialGrip_FAST",
                    "GameModeSelectionWeatherBehaviour_DYNAMIC",
                    "GameModeSelectionWeatherType_HEAVY_RAIN",
                ),
            ),
            (
                {
                    "EVENT_TYPE": "Practice",
                    "EVENT_INITIAL_GRIP": "Optimum",
                    "EVENT_WEATHER_BEHAVIOUR": "Static",
                    "EVENT_WEATHER": "Damp",
                },
                (
                    "GameModeType_PRACTICE",
                    "InitialGrip_OPTIMUM",
                    "GameModeSelectionWeatherBehaviour_STATIC",
                    "GameModeSelectionWeatherType_DAMP",
                ),
            ),
        ]

        for env, expected in cases:
            _, season_doc, warnings = launch_payloads.build_documents(env)
            self.assertEqual(warnings, [])
            self.assertEqual(
                (
                    season_doc["game_type"],
                    season_doc["initial_grip"],
                    season_doc["weather_behaviour"],
                    season_doc["weather_type"],
                ),
                expected,
            )

    def test_selected_cars_sets_dual_flags(self):
        env = {"EVENT_CARS": ("Abarth_695_Biposto,Caterham_Academy,Ferrari_F2004")}

        server_doc, _, warnings = launch_payloads.build_documents(env)
        self.assertEqual(warnings, [])

        selected = server_doc["allowed_cars_list_full"]
        self.assertEqual(len(selected), 3)

        selected_names = {car["car_name"] for car in selected}
        self.assertEqual(
            selected_names,
            {"preset_695b_mech_1", "ks_caterham_acmd_mech_1", "preset_f2004_mech_1"},
        )

        for car in selected:
            self.assertEqual(car["ballast"], 0.0)
            self.assertEqual(car["restrictor"], 0.0)

    def test_documented_car_env_tokens_match(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {"EVENT_CARS": ("Mercedes_Benz_190E_25_16_Evo_II,Porsche_911_Turbo_36_(964)")}
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            selected_car_names(server_doc),
            {"preset_190e_mech_1", "preset_964_mech_1", "preset_964_mech_2"},
        )

    def test_event_type_controls_active_sessions(self):
        _, season_doc_practice, warnings_practice = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Practice",
                "PRACTICE_DURATION_MINUTES": "3",
                "QUALIFY_DURATION_MINUTES": "999",
                "WARMUP_DURATION_MINUTES": "888",
                "RACE_DURATION_MINUTES": "7",
            }
        )
        self.assertEqual(warnings_practice, [])
        game_config = season_doc_practice["game_config"]
        self.assertEqual(game_config["practice_duration"], 180)
        self.assertEqual(game_config["qualify_duration"], 0)
        self.assertEqual(game_config["warmup_duration"], 0)
        self.assertEqual(game_config["race_duration"], 0)

        _, season_doc_weekend, warnings_weekend = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Race_Weekend",
                "PRACTICE_DURATION_MINUTES": "5",
                "QUALIFY_DURATION_MINUTES": "4",
                "WARMUP_DURATION_MINUTES": "2",
                "RACE_DURATION_MINUTES": "9",
            }
        )
        self.assertEqual(warnings_weekend, [])
        weekend = season_doc_weekend["game_config"]
        self.assertEqual(weekend["practice_duration"], 300)
        self.assertEqual(weekend["qualify_duration"], 240)
        self.assertEqual(weekend["warmup_duration"], 120)
        self.assertEqual(weekend["race_duration"], 540)

    def test_default_session_durations(self):
        _, season_doc, warnings, report = launch_payloads.build_documents_with_report({"EVENT_TYPE": "Race_Weekend"})
        self.assertEqual(warnings, [])

        game_config = season_doc["game_config"]
        self.assertEqual(game_config["practice_duration"], 10800)
        self.assertEqual(game_config["race_duration"], 1500)
        self.assertEqual(game_config["race_max_wait_to_box"], 60)
        self.assertEqual(resolved(report, "PRACTICE_DURATION_MINUTES")["value"], 180)
        self.assertIn("converted to 10800 seconds", resolved(report, "PRACTICE_DURATION_MINUTES")["note"])
        self.assertEqual(resolved(report, "RACE_DURATION_MINUTES")["value"], 25)
        self.assertIn("converted to 1500 seconds", resolved(report, "RACE_DURATION_MINUTES")["note"])

    def test_race_duration_laps(self):
        _, season_doc_default, warnings_default, report_default = launch_payloads.build_documents_with_report(
            {"EVENT_TYPE": "Race_Weekend", "RACE_DURATION_TYPE": "Laps"}
        )
        self.assertEqual(warnings_default, [])
        self.assertEqual(season_doc_default["game_config"]["race_duration"], 10)
        self.assertEqual(resolved(report_default, "RACE_DURATION_LAPS")["value"], 10)
        self.assertEqual(
            resolved(report_default, "RACE_DURATION_MINUTES")["source"],
            "ignored_by_duration_type",
        )

        _, season_doc_custom, warnings_custom, report_custom = launch_payloads.build_documents_with_report(
            {
                "EVENT_TYPE": "Race_Weekend",
                "RACE_DURATION_TYPE": "Laps",
                "RACE_DURATION_LAPS": "12",
            }
        )
        self.assertEqual(warnings_custom, [])
        self.assertEqual(season_doc_custom["game_config"]["race_duration"], 12)
        self.assertEqual(resolved(report_custom, "RACE_DURATION_LAPS")["value"], 12)

    def test_wait_values_stay_seconds(self):
        _, season_doc, warnings, report = launch_payloads.build_documents_with_report(
            {
                "PRACTICE_MAX_WAIT_TO_BOX_SECONDS": "10",
                "PRACTICE_OVERTIME_WAITING_NEXT_SESSION_SECONDS": "10",
            }
        )
        self.assertEqual(warnings, [])
        game_config = season_doc["game_config"]
        self.assertEqual(game_config["practice_max_wait_to_box"], 10)
        self.assertEqual(game_config["practice_overtime_waiting_next_session"], 10)
        self.assertEqual(resolved(report, "PRACTICE_MAX_WAIT_TO_BOX_SECONDS")["value"], 10)
        self.assertEqual(resolved(report, "PRACTICE_OVERTIME_WAITING_NEXT_SESSION_SECONDS")["value"], 10)

    def test_server_launcher_json_imports_known_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), launcher_document())
            server_doc, season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path)}
            )

        self.assertEqual(warnings, [])
        self.assertEqual(server_doc["server_name"], "Windows Tool Server")
        self.assertEqual(server_doc["server_tcp_listener_port"], 9701)
        self.assertEqual(server_doc["server_http_port"], 8081)
        self.assertFalse(server_doc["cycle"])
        self.assertEqual(server_doc["driver_password"], "driver-password")
        self.assertEqual(server_doc["admin_password"], "admin-password")
        self.assertEqual(server_doc["results_post_url"], "https://results.example.test/launcher")
        self.assertEqual(server_doc["type"], "MultiplayerServerListSessionType_BOTH")

        cars = {car["car_name"]: car for car in server_doc["allowed_cars_list_full"]}
        self.assertEqual(set(cars), {"preset_695b_mech_1", "ks_caterham_acmd_mech_1"})
        self.assertEqual(cars["preset_695b_mech_1"]["ballast"], 12)
        self.assertEqual(cars["preset_695b_mech_1"]["restrictor"], 3.0)

        game_config = season_doc["game_config"]
        self.assertEqual(season_doc["game_type"], "GameModeType_RACE_WEEKEND")
        self.assertEqual(season_doc["weather_type"], "GameModeSelectionWeatherType_SCATTERED_CLOUDS")
        self.assertEqual(season_doc["weather_behaviour"], "GameModeSelectionWeatherBehaviour_DYNAMIC")
        self.assertEqual(season_doc["event"]["track"], "Watkins Glen International")
        self.assertEqual(season_doc["event"]["layout"], "GP Inner Loop")
        self.assertEqual(game_config["practice_duration"], 600)
        self.assertEqual(game_config["qualify_duration"], 300)
        self.assertEqual(game_config["warmup_duration"], 120)
        self.assertEqual(game_config["race_duration_type"], "GameModeSelectionDuration_LAPS")
        self.assertEqual(game_config["race_duration"], 8)
        self.assertEqual(game_config["race_max_wait_to_box"], 17)
        self.assertEqual(game_config["race_overtime_waiting_next_session"], 18)
        self.assertEqual(game_config["min_waiting_for_players"], 2)
        self.assertEqual(game_config["max_waiting_for_players"], 12)

        self.assertEqual(resolved(report, "SERVER_NAME")["source"], "json")
        self.assertEqual(resolved(report, "EVENT_CARS")["source"], "json")
        self.assertEqual(resolved(report, "SERVER_LAUNCHER_JSON")["source"], "env")

    def test_env_overrides_server_launcher_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), launcher_document())
            server_doc, season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {
                    "SERVER_LAUNCHER_JSON": str(path),
                    "SERVER_NAME": "ENV Server",
                    "EVENT_TYPE": "Practice",
                    "EVENT_TRACK": "Brands_Hatch_GP",
                    "EVENT_CARS": "Ferrari_F2004",
                }
            )

        self.assertEqual(warnings, [])
        self.assertEqual(server_doc["server_name"], "ENV Server")
        self.assertEqual(season_doc["game_type"], "GameModeType_PRACTICE")
        self.assertEqual(selected_car_names(server_doc), {"preset_f2004_mech_1"})
        self.assertEqual(server_doc["allowed_cars_list_full"][0]["ballast"], 0.0)
        self.assertEqual(resolved(report, "SERVER_NAME")["source"], "env")
        self.assertEqual(resolved(report, "EVENT_CARS")["source"], "env")

    def test_invalid_server_launcher_json_warns_and_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), "{")
            server_doc, season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path)}
            )

        self.assertTrue(any("invalid JSON" in warning for warning in warnings))
        self.assertEqual(server_doc["server_name"], "AC EVO Nordschleife Trackday")
        self.assertEqual(season_doc["game_type"], "GameModeType_PRACTICE")
        self.assertEqual(resolved(report, "SERVER_LAUNCHER_JSON")["source"], "env")

    def test_server_launcher_json_unknown_track_falls_back(self):
        document = launcher_document()
        document["Event"]["SelectedTrackValue"] = "Unknown Track|Unknown Layout|Unknown Race|1234"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), document)
            _, season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path)}
            )

        self.assertTrue(any("EVENT_TRACK" in warning and "unknown track" in warning for warning in warnings))
        self.assertEqual(season_doc["event"]["track"], "Nurburgring")
        self.assertEqual(season_doc["event"]["layout"], "Nordschleife")
        self.assertEqual(resolved(report, "EVENT_TRACK")["source"], "fallback")

    def test_server_launcher_json_unknown_selected_cars_falls_back_to_all(self):
        document = launcher_document()
        document["Event"]["Cars"] = [{"IsSelected": True, "name": "preset_does_not_exist"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), document)
            server_doc, _, warnings, _ = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path)}
            )

        self.assertTrue(any("selected car 'preset_does_not_exist' is unknown" in warning for warning in warnings))
        self.assertTrue(any("no valid selected cars found" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), all_car_names())

    def test_server_launcher_json_inconsistent_waiting_players_warns_and_uses_primary_session(self):
        document = launcher_document()
        document["Sessions"]["PracticeSession"]["MinWaitingForPlayers"] = 1
        document["Sessions"]["PracticeSession"]["MaxWaitingForPlayers"] = 3
        document["Sessions"]["RaceSession"]["MinWaitingForPlayers"] = 5
        document["Sessions"]["RaceSession"]["MaxWaitingForPlayers"] = 9
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), document)
            _, season_doc, warnings, _ = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path)}
            )

        self.assertTrue(any("per-session waiting player values differ" in warning for warning in warnings))
        self.assertEqual(season_doc["game_config"]["min_waiting_for_players"], 5)
        self.assertEqual(season_doc["game_config"]["max_waiting_for_players"], 9)

    def test_dynamic_weather_session_times_include_default_date(self):
        _, season_doc, warnings = launch_payloads.build_documents(
            {
                "EVENT_WEATHER": "Scattered_Clouds",
                "EVENT_WEATHER_BEHAVIOUR": "Dynamic",
            }
        )
        self.assertEqual(warnings, [])

        for name in launch_payloads.SESSION_NAMES.values():
            time_of_day = season_doc["game_config"][f"{name}_time_of_day"]
            self.assertEqual(time_of_day["year"], 2024)
            self.assertEqual(time_of_day["month"], 8)
            self.assertEqual(time_of_day["day"], 15)

    def test_session_date_overrides_are_applied(self):
        _, season_doc, warnings = launch_payloads.build_documents(
            {
                "PRACTICE_YEAR": "2025",
                "PRACTICE_MONTH": "9",
                "PRACTICE_DAY": "21",
            }
        )
        self.assertEqual(warnings, [])

        time_of_day = season_doc["game_config"]["practice_time_of_day"]
        self.assertEqual(time_of_day["year"], 2025)
        self.assertEqual(time_of_day["month"], 9)
        self.assertEqual(time_of_day["day"], 21)

    def test_invalid_session_date_values_fallback(self):
        _, season_doc, warnings = launch_payloads.build_documents(
            {
                "PRACTICE_YEAR": "0",
                "PRACTICE_MONTH": "13",
                "PRACTICE_DAY": "0",
            }
        )

        self.assertTrue(any("PRACTICE_YEAR" in warning for warning in warnings))
        self.assertTrue(any("PRACTICE_MONTH" in warning for warning in warnings))
        self.assertTrue(any("PRACTICE_DAY" in warning for warning in warnings))
        time_of_day = season_doc["game_config"]["practice_time_of_day"]
        self.assertEqual(time_of_day["year"], 2024)
        self.assertEqual(time_of_day["month"], 8)
        self.assertEqual(time_of_day["day"], 15)

    def test_track_mapping_known_and_unknown_fallback(self):
        _, season_doc, warnings = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Practice",
                "EVENT_TRACK": "Brands_Hatch_GP",
            }
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            season_doc["event"],
            {
                "track": "Brands Hatch",
                "layout": "GP",
                "event_name": "GP Time Attack",
                "track_length": 3916,
                "max_pit_slot": 32,
            },
        )

        _, season_doc_tourist, warnings_tourist = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Practice",
                "EVENT_TRACK": "Nurburgring_Touristenfahrten",
            }
        )
        self.assertEqual(warnings_tourist, [])
        self.assertEqual(season_doc_tourist["event"]["layout"], "Touristenfahrten")
        self.assertEqual(season_doc_tourist["event"]["max_pit_slot"], 50)

        _, season_doc_unknown, warnings_unknown = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Race_Weekend",
                "EVENT_TRACK": "Fake_Track_GP",
            }
        )
        self.assertTrue(any("EVENT_TRACK" in warning for warning in warnings_unknown))
        self.assertEqual(
            season_doc_unknown["event"],
            {
                "track": "Nurburgring",
                "layout": "Nordschleife",
                "event_name": "Nordschleife Race",
                "track_length": 20832,
                "max_pit_slot": 22,
            },
        )

    def test_structured_env_values_with_spaces_warn_and_fallback(self):
        server_doc, season_doc, warnings = launch_payloads.build_documents(
            {
                "SERVER_NAME": "Name With Spaces",
                "EVENT_TYPE": "Race Weekend",
                "EVENT_WEATHER": "Heavy Rain",
                "EVENT_TRACK": "Brands Hatch GP",
            }
        )

        self.assertEqual(server_doc["server_name"], "Name With Spaces")
        self.assertTrue(any("EVENT_TYPE" in warning and "spaces are not allowed" in warning for warning in warnings))
        self.assertTrue(any("EVENT_WEATHER" in warning and "spaces are not allowed" in warning for warning in warnings))
        self.assertTrue(any("EVENT_TRACK" in warning and "spaces are not allowed" in warning for warning in warnings))
        self.assertEqual(season_doc["game_type"], "GameModeType_PRACTICE")
        self.assertEqual(season_doc["weather_type"], "GameModeSelectionWeatherType_CLEAR")
        self.assertEqual(season_doc["event"]["layout"], "Touristenfahrten")

    def test_server_max_players_downscales_to_track_limit(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "EVENT_TRACK": "Donington_Park_GP",
                "SERVER_MAX_PLAYERS": "50",
            }
        )

        self.assertEqual(server_doc["max_players"], 19)
        self.assertTrue(any("SERVER_MAX_PLAYERS: 50 exceeds track maximum 19" in warning for warning in warnings))
        self.assertEqual(resolved(report, "SERVER_MAX_PLAYERS")["value"], 19)
        self.assertIn("downscaled from 50", resolved(report, "SERVER_MAX_PLAYERS")["note"])

    def test_server_max_players_at_or_below_track_limit_is_unchanged(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "EVENT_TRACK": "Donington_Park_GP",
                "SERVER_MAX_PLAYERS": "19",
            }
        )

        self.assertEqual(warnings, [])
        self.assertEqual(server_doc["max_players"], 19)
        self.assertEqual(resolved(report, "SERVER_MAX_PLAYERS")["value"], 19)

    def test_unknown_values_warn_and_default(self):
        server_doc, season_doc, warnings = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Whatever",
                "EVENT_INITIAL_GRIP": "SuperFast",
                "EVENT_WEATHER_BEHAVIOUR": "Weird",
                "EVENT_WEATHER": "Storm",
                "EVENT_TRACK": "Unknown_Track",
                "EVENT_CARS": "Not A Car",
            }
        )

        self.assertGreaterEqual(len(warnings), 5)
        self.assertEqual(season_doc["game_type"], "GameModeType_PRACTICE")
        self.assertEqual(season_doc["initial_grip"], "InitialGrip_OPTIMUM")
        self.assertEqual(
            season_doc["weather_behaviour"],
            "GameModeSelectionWeatherBehaviour_STATIC",
        )
        self.assertEqual(season_doc["weather_type"], "GameModeSelectionWeatherType_CLEAR")

        self.assertTrue(any("no valid cars found" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), all_car_names())

    def test_unknown_env_keys_are_reported(self):
        _, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Practice",
                "PRACTICE_ENABLED": "true",
                "SERVER_WHATEVER": "1",
            }
        )
        self.assertTrue(any("PRACTICE_ENABLED" in warning for warning in warnings))
        self.assertTrue(any("SERVER_WHATEVER" in warning for warning in warnings))

    def test_old_second_based_duration_env_keys_are_reported_as_unknown(self):
        _, _, warnings = launch_payloads.build_documents(
            {
                "PRACTICE_DURATION": "10800",
                "RACE_DURATION": "1500",
                "PRACTICE_MAX_WAIT_TO_BOX": "10",
                "PRACTICE_OVERTIME_WAITING_NEXT_SESSION": "10",
            }
        )
        self.assertTrue(any("PRACTICE_DURATION" in warning for warning in warnings))
        self.assertTrue(any("RACE_DURATION" in warning for warning in warnings))
        self.assertTrue(any("PRACTICE_MAX_WAIT_TO_BOX" in warning for warning in warnings))
        self.assertTrue(any("PRACTICE_OVERTIME_WAITING_NEXT_SESSION" in warning for warning in warnings))

    def test_payload_encoding_roundtrip(self):
        server_doc, season_doc, warnings = launch_payloads.build_documents(
            {
                "SERVER_NAME": "Test Server",
                "EVENT_TYPE": "Race_Weekend",
                "EVENT_WEATHER": "Rain",
            }
        )
        self.assertEqual(warnings, [])

        payload = launch_payloads.encode_payload(server_doc)
        raw = base64.b64decode(payload)
        declared_length = struct.unpack("<I", raw[:4])[0]
        self.assertEqual(declared_length, len(raw) - 4)

        decoded = launch_payloads.decode_payload(payload)
        self.assertEqual(decoded, server_doc)

        season_payload = launch_payloads.encode_payload(season_doc)
        decoded_season = launch_payloads.decode_payload(season_payload)
        self.assertEqual(decoded_season, season_doc)

    def test_force_software_rendering_report_default_and_override(self):
        _, _, warnings_default, report_default = launch_payloads.build_documents_with_report({})
        self.assertEqual(warnings_default, [])
        self.assertEqual(resolved(report_default, "ACEVO_FORCE_SOFTWARE_RENDERING")["value"], "true")

        _, _, warnings_override, report_override = launch_payloads.build_documents_with_report(
            {"ACEVO_FORCE_SOFTWARE_RENDERING": "false"}
        )
        self.assertEqual(warnings_override, [])
        self.assertEqual(resolved(report_override, "ACEVO_FORCE_SOFTWARE_RENDERING")["value"], "false")

    def test_invalid_integer_and_boolean_values_fallback(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "SERVER_CYCLE_ENABLED": "maybe",
                "SERVER_MAX_PLAYERS": "many",
            }
        )

        self.assertTrue(any("SERVER_CYCLE_ENABLED" in warning for warning in warnings))
        self.assertTrue(any("SERVER_MAX_PLAYERS" in warning for warning in warnings))
        self.assertTrue(server_doc["cycle"])
        self.assertEqual(server_doc["max_players"], 20)

    def test_default_car_selection_is_all(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report({})
        self.assertEqual(warnings, [])
        self.assertEqual(selected_car_names(server_doc), all_car_names())
        self.assertEqual(resolved(report, "EVENT_CARS")["value"], "all")
        self.assertEqual(resolved(report, "EVENT_CARS")["source"], "default")
        self.assertEqual(resolved(report, "EVENT_CAR_CATEGORY")["value"], "all")
        self.assertEqual(resolved(report, "EVENT_CAR_CATEGORY")["source"], "default")
        self.assertEqual(resolved(report, "EVENT_BAN_CARS")["value"], "")
        self.assertEqual(resolved(report, "EVENT_BAN_CAR_CATEGORY")["value"], "")

    def test_car_category_filtering_intersection(self):
        server_doc, _, warnings = launch_payloads.build_documents({"EVENT_CAR_CATEGORY": "Road,EV"})
        self.assertEqual(warnings, [])

        selected_names = {car["car_name"] for car in server_doc["allowed_cars_list_full"]}
        self.assertEqual(selected_names, {"preset_mln_mech_1", "preset_a290b_mech_1"})

    def test_invalid_car_category_warns_and_falls_back(self):
        server_doc, _, warnings = launch_payloads.build_documents({"EVENT_CAR_CATEGORY": "Spaceship"})

        self.assertTrue(any("EVENT_CAR_CATEGORY" in warning for warning in warnings))
        self.assertTrue(any("no valid cars found" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), all_car_names())

    def test_ban_cars_removes_matching_env_tokens_from_all(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CARS": "all",
                "EVENT_BAN_CARS": "Ferrari_SF_25,Ferrari_F2004",
            }
        )
        self.assertEqual(warnings, [])

        selected_names = selected_car_names(server_doc)
        self.assertEqual(len(selected_names), len(all_car_names()) - 2)
        self.assertNotIn("preset_sf25_mech_1", selected_names)
        self.assertNotIn("preset_f2004_mech_1", selected_names)

    def test_ban_category_removes_from_allowed_category_pool(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CAR_CATEGORY": "Road",
                "EVENT_BAN_CAR_CATEGORY": "EV",
            }
        )
        self.assertEqual(warnings, [])

        selected_names = selected_car_names(server_doc)
        self.assertNotIn("preset_mln_mech_1", selected_names)
        self.assertNotIn("preset_a290b_mech_1", selected_names)
        self.assertIn("preset_695b_mech_1", selected_names)

    def test_ban_car_outside_allowed_pool_warns_without_changes(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CAR_CATEGORY": "EV",
                "EVENT_BAN_CARS": "Ferrari_F2004",
            }
        )

        self.assertTrue(any("EVENT_BAN_CARS" in warning for warning in warnings))
        self.assertTrue(any("not in allowed car pool" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), {"preset_mln_mech_1", "preset_a290b_mech_1"})

    def test_invalid_ban_category_warns_without_changes(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CARS": "Abarth_695_Biposto",
                "EVENT_BAN_CAR_CATEGORY": "Spaceship",
            }
        )

        self.assertTrue(any("EVENT_BAN_CAR_CATEGORY" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), {"preset_695b_mech_1"})

    def test_ban_filters_emptying_pool_fall_back_to_all(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CARS": "Ferrari_F2004",
                "EVENT_BAN_CARS": "Ferrari_F2004",
            }
        )

        self.assertTrue(any("ban filters removed all allowed cars" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), all_car_names())

    def test_result_post_settings_are_passed_through(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "SERVER_RESULTS_POST_URL": "https://results.example.test/acevo",
                "SERVER_RESULTS_TOKEN": "result-secret",
            }
        )
        self.assertEqual(warnings, [])

        self.assertEqual(server_doc["results_post_url"], "https://results.example.test/acevo")
        self.assertEqual(server_doc["token"], "result-secret")
        self.assertEqual(server_doc["results_path"], "")
        self.assertEqual(server_doc["entry_list_path"], "")
        self.assertEqual(server_doc["entry_list_server_url"], "")
        self.assertEqual(resolved(report, "SERVER_RESULTS_POST_URL")["value"], "https://results.example.test/acevo")
        self.assertEqual(resolved(report, "SERVER_RESULTS_TOKEN")["value"], "***MASKED***")

    def test_sensitive_values_are_masked_in_report(self):
        _, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "SERVER_ADMIN_PASSWORD": "admin-secret",
                "SERVER_DRIVER_PASSWORD": "driver-secret",
                "SERVER_SPECTATOR_PASSWORD": "spectator-secret",
            }
        )
        self.assertEqual(warnings, [])

        self.assertEqual(resolved(report, "SERVER_ADMIN_PASSWORD")["value"], "***MASKED***")
        self.assertEqual(resolved(report, "SERVER_DRIVER_PASSWORD")["value"], "***MASKED***")
        self.assertEqual(resolved(report, "SERVER_SPECTATOR_PASSWORD")["value"], "***MASKED***")

    def test_readme_track_max_players_table_matches_mappings(self):
        rows = {}
        in_tracks = False
        for line in Path("README.md").read_text(encoding="utf-8").splitlines():
            if line == "## Tracks":
                in_tracks = True
                continue
            if in_tracks and line.startswith("## "):
                break
            if in_tracks and line.startswith("| `"):
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                rows[cells[0].strip("`")] = cells

        self.assertIn("Nurburgring_Touristenfahrten", rows)

        expected = {}
        for path, column in (
            ("scripts/mappings/events_practice.json", "practice"),
            ("scripts/mappings/events_race_weekend.json", "race"),
        ):
            events = json.loads(Path(path).read_text(encoding="utf-8"))["events"]
            for event in events:
                key = launch_payloads.track_env_token(event)
                expected.setdefault(key, {"practice": "-", "race": "-"})
                expected[key][column] = str(event["max_pit_slot"])

        for key, values in expected.items():
            self.assertIn(key, rows)
            self.assertEqual(rows[key][3], values["practice"])
            self.assertEqual(rows[key][4], values["race"])

    def test_readme_cars_score_table_matches_mappings(self):
        rows = []
        in_cars = False
        for line in Path("README.md").read_text(encoding="utf-8").splitlines():
            if line == "## Cars":
                in_cars = True
                continue
            if in_cars and line.startswith("## "):
                break
            if in_cars and line.startswith("| `"):
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if cells[0] == "`all`":
                    continue
                rows.append((cells[0].strip("`"), cells[1], cells[2]))

        expected = [
            (
                launch_payloads.car_env_token(car["display_name"]),
                car["display_name"],
                f"{float(car['performance_indicator']):.1f}",
            )
            for car in launch_payloads.load_config()["cars_data"]
        ]

        self.assertEqual(rows, expected)

    def test_cli_writes_payload_and_report_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            server_out = base / "server.b64"
            season_out = base / "season.b64"
            report_out = base / "report.json"

            with patch.dict(
                os.environ,
                {"SERVER_NAME": "CLI Test Server", "EVENT_TYPE": "Practice"},
                clear=True,
            ):
                exit_code = launch_payloads.main(
                    [
                        "--server-out",
                        str(server_out),
                        "--season-out",
                        str(season_out),
                        "--report-out",
                        str(report_out),
                    ]
                )

            self.assertEqual(exit_code, 0)
            server_doc = launch_payloads.decode_payload(server_out.read_text(encoding="utf-8"))
            season_doc = launch_payloads.decode_payload(season_out.read_text(encoding="utf-8"))
            report = json.loads(report_out.read_text(encoding="utf-8"))

        self.assertEqual(server_doc["server_name"], "CLI Test Server")
        self.assertEqual(season_doc["game_type"], "GameModeType_PRACTICE")
        self.assertEqual(report["server_summary"]["server_name"], "CLI Test Server")


if __name__ == "__main__":
    unittest.main()
