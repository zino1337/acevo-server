import base64
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from scripts import launch_payloads


OFFICIAL_0_9_MANDATORY_SEASON_PAYLOAD = (
    "AAAHNnic1VXLbtswELwHyD8QPPvgR2q0ujmJ4BiFU8NxG/S02EgrialEKhRlWwj87wUpyY+4NnxsjtyZWc6O"
    "pNX79RVjPMaMwFQ5cY/xMWY0VSEtqpxgPrrz4dn3v/uP97zjyLQkabjH3u2JMW40Bn+s8FajDAv2gCZIai5j"
    "PMVKlZbOx7Nt0bUAiVl94YzNMaAt6hpCSjI2icUH33pDbrFNY2CdK23gtVCSeyzCtKDOboxAyUjEe/5yjYERA"
    "UFYajTCiQbdbucjbERGoCIIsdqpGeMVoeYe63f7N42GMZ4p6cx93ZVqXe/LrpKo0ip7wz2dkKWxU7f3M8YLCp"
    "QMD2vOTFamRuSpINelxjZHvtWStKOvUBghY5C0NlBQUdSz9o5HzXDt2GAUvKj1AemtxFRE1Ym0WvSThdXavjyr"
    "VnEuqhXqrMxPJNWAnyyoxvXlOTWCczFpPPr4/o0draAnSimwyH3LWEymPj8Q/+8B9z8k7Exfnq+jn0s3E3LbJV"
    "Ia8hQr0sUhp9EfcwZ7HBmiUbqCXBgojMq5x4wuaeuE3kqhqQBTaYIgQRnbRHb7d5+jKSopTYW0m/iAYruvhAzV"
    "ints2O3uLfYVoUlIn34NnmuC+zM9+KNfv2E+mjw2/6VW/UIJLkX9DE+2uG1J8LQYLSZ3TQ8hhRGYQqyFHZ9P6v"
    "NYixx+zBaT6c8pv77a/AUROQY1"
)
MANDATORY_KEYS = {"mandatory_pit_stop", "pit_window", "requires_refuelling", "requires_tyre_change"}


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
            "SelectedServerTypeValue": "MultiplayerServerListSessionType_UNRANKED",
            "ServerName": "Windows Tool Server",
            "MaxPlayers": 8,
            "MaxPlayersLimit": 50,
            "TcpPort": 9701,
            "UdpPort": 9701,
            "HttpPort": 8081,
            "IsCycleEnabled": False,
            "DriverPassword": "driver-password",
            "SpectatorPassword": "spectator-password",
            "AdminPassword": "admin-password",
            "EntryListUrl": "https://entry.example.test/list.json",
            "ResultsPostUrl": "https://results.example.test/launcher",
            "EntryListPath": "C:\\acevo\\entrylist.json",
            "ResultsPath": "C:\\acevo\\results",
            "SelectedTuningTypeValue": "TuningDenied",
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
                    "is_mod": False,
                    "IsModText": "",
                    "IsMod": False,
                    "Ballast": 12.5,
                    "Restrictor": 3.0,
                },
                {
                    "IsSelected": True,
                    "name": "ks_caterham_acmd_mech_1",
                    "display_name": "Caterham Academy - Academy",
                    "is_mod": False,
                    "IsModText": "",
                    "IsMod": False,
                    "Ballast": 0,
                    "Restrictor": 0,
                },
            ],
            "ShowOnlySelected": False,
            "ShowOnlyOfficial": False,
            "SelectOnlyOfficialCarsCommand": {
                "$type": "CommunityToolkit.Mvvm.Input.RelayCommand, CommunityToolkit.Mvvm"
            },
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
            {"EVENT_CARS": ("Mercedes_Benz_190E_25_16_Evo_II,Porsche_911_Turbo_36_964_Standard")}
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            selected_car_names(server_doc),
            {"preset_190e_mech_1", "preset_964_mech_1"},
        )

    def test_new_0_7_car_env_tokens_match(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CARS": (
                    "Audi_R8_LMS_GT3_Evo_II,Datsun_240Z_S30_Standard,Datsun_240Z_S30_Tuned,"
                    "Porsche_911_GT2_RS_Clubsport_Evo_991II,Porsche_935"
                )
            }
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            selected_car_names(server_doc),
            {
                "preset_r8gt3_mech_1",
                "preset_240z_mech_1",
                "preset_240z_mech_2",
                "preset_gt2rscs_mech_1",
                "preset_935_mech_1",
            },
        )

    def test_new_0_8_car_env_tokens_match(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {"EVENT_CARS": "KTM_X_Bow_GT2,KTM_X_Bow_GT4,Volkswagen_Golf_8_R"}
        )

        self.assertEqual(warnings, [])
        self.assertEqual(
            selected_car_names(server_doc),
            {"preset_xbgt2_mech_1", "preset_xbgt4_mech_1", "preset_mk8r_mech_1"},
        )

    def test_0_9_car_catalog_matches_launcher_values(self):
        cars = {car["internal_name"]: car for car in launch_payloads.load_config()["bundled_cars_data"]}
        self.assertEqual(len(cars), 100)
        self.assertEqual(
            {
                name: (
                    cars[name]["display_name"],
                    cars[name]["performance_indicator"],
                    cars[name]["property_1"],
                    cars[name]["property_2"],
                    cars[name]["property_3"],
                )
                for name in ("preset_r8gt2_mech_1", "preset_r8v10_mech_1", "preset_rx7fd_mech_1")
            },
            {
                "preset_r8gt2_mech_1": ("Audi R8 LMS GT2 - GT2", 17.9, 1, 0, 0),
                "preset_r8v10_mech_1": (
                    "Audi R8 V10 performance quattro - Coupe quattro",
                    13.5,
                    0,
                    0,
                    0,
                ),
                "preset_rx7fd_mech_1": ("Mazda RX-7 FD Spirit R - Standard", 9.8, 0, 2, 0),
            },
        )

        changed_pi = {
            "preset_r8gt3_mech_1": 19.5,
            "preset_r8gt4_mech_1": 14.7,
            "preset_rs3_mech_1": 11.5,
            "preset_rs6_mech_1": 11.9,
            "preset_sq_mech_1": 10.8,
            "preset_m2csr_mech_1": 11.7,
            "preset_m2csr_mech_2": 12.0,
            "preset_m4gt3_mech_1": 20.0,
            "preset_dalexp_mech_1": 21.6,
            "preset_dalsc_mech_3": 20.4,
            "preset_296gt3_mech_1": 21.2,
            "preset_f488ce_mech_1": 19.1,
            "preset_f40lm_mech_1": 19.2,
            "preset_msggt3_mech_1": 18.3,
            "preset_nsxr_mech_1": 9.9,
            "preset_xbgt2_mech_1": 18.2,
            "preset_xbgt4_mech_1": 18.0,
            "preset_stevo2_mech_1": 17.4,
            "preset_exigev6_mech_2": 13.9,
            "preset_exigev6_mech_1": 12.6,
            "preset_mcgt2_mech_1": 16.7,
            "preset_mx5ndcup_mech_1": 10.1,
            "preset_mx5ndcup_mech_2": 10.4,
            "preset_amggt2_mech_1": 17.0,
            "preset_gt4csmr_mech_1": 15.4,
            "preset_gt2rscs_mech_1": 17.2,
            "preset_992c_mech_2": 16.3,
            "preset_992c_mech_1": 16.3,
            "preset_992c_mech_3": 16.3,
            "preset_992ren_mech_1": 18.6,
            "preset_992ren_mech_2": 19.2,
            "preset_964_mech_1": 12.2,
            "preset_964_mech_2": 12.3,
            "preset_935_mech_1": 16.9,
            "preset_mk8gti_mech_1": 10.6,
            "preset_mk8r_mech_1": 9.8,
        }
        self.assertEqual({name: cars[name]["performance_indicator"] for name in changed_pi}, changed_pi)
        self.assertEqual(
            {
                name: cars[name]["display_name"]
                for name in ("preset_stevo2_mech_1", "preset_sto_mech_1", "preset_sto_mech_2")
            },
            {
                "preset_stevo2_mech_1": "Lamborghini Huracán ST EVO2 - Super Trofeo",
                "preset_sto_mech_1": "Lamborghini Huracán STO - Road",
                "preset_sto_mech_2": "Lamborghini Huracán STO - Trackday",
            },
        )

    def test_variant_car_env_tokens_are_unique(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {
                "EVENT_CARS": (
                    "Toyota_GR86_Trueno_Edition,"
                    "Porsche_911_GT3_R_Rennsport_992_GT3,"
                    "Porsche_911_GT3_R_Rennsport_992_Unrestricted"
                )
            }
        )

        self.assertEqual(warnings, [])
        self.assertEqual(
            selected_car_names(server_doc),
            {"preset_gr86_mech_2", "preset_992ren_mech_1", "preset_992ren_mech_2"},
        )

    def test_ambiguous_car_env_tokens_warn_and_are_ignored(self):
        server_doc, _, warnings = launch_payloads.build_documents(
            {"EVENT_CARS": "Porsche_935,Porsche_911_GT3_R_Rennsport_992"}
        )

        self.assertTrue(any("ambiguous car token" in warning for warning in warnings))
        self.assertEqual(selected_car_names(server_doc), {"preset_935_mech_1"})

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
        self.assertEqual(game_config["min_waiting_for_players"], 10)
        self.assertEqual(game_config["max_waiting_for_players"], 60)
        self.assertEqual(resolved(report, "PRACTICE_DURATION_MINUTES")["value"], 180)
        self.assertIn("converted to 10800 seconds", resolved(report, "PRACTICE_DURATION_MINUTES")["note"])
        self.assertEqual(resolved(report, "RACE_DURATION_MINUTES")["value"], 25)
        self.assertIn("converted to 1500 seconds", resolved(report, "RACE_DURATION_MINUTES")["note"])
        self.assertEqual(resolved(report, "RACE_MIN_WAITING_FOR_PLAYERS_SECONDS")["value"], 10)
        self.assertEqual(resolved(report, "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS")["value"], 60)

    def test_race_waiting_for_players_seconds_override(self):
        _, season_doc, warnings, report = launch_payloads.build_documents_with_report(
            {
                "EVENT_TYPE": "Race_Weekend",
                "RACE_MIN_WAITING_FOR_PLAYERS_SECONDS": "30",
                "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS": "90",
            }
        )
        self.assertEqual(warnings, [])
        game_config = season_doc["game_config"]
        self.assertEqual(game_config["min_waiting_for_players"], 30)
        self.assertEqual(game_config["max_waiting_for_players"], 90)
        self.assertEqual(resolved(report, "RACE_MIN_WAITING_FOR_PLAYERS_SECONDS")["source"], "env")
        self.assertEqual(resolved(report, "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS")["source"], "env")

    def test_race_waiting_for_players_max_clamps_to_min(self):
        _, season_doc, warnings, report = launch_payloads.build_documents_with_report(
            {
                "EVENT_TYPE": "Race_Weekend",
                "RACE_MIN_WAITING_FOR_PLAYERS_SECONDS": "120",
                "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS": "30",
            }
        )
        self.assertTrue(any("RACE_MAX_WAITING_FOR_PLAYERS_SECONDS" in warning for warning in warnings))
        game_config = season_doc["game_config"]
        self.assertEqual(game_config["min_waiting_for_players"], 120)
        self.assertEqual(game_config["max_waiting_for_players"], 120)
        self.assertEqual(resolved(report, "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS")["value"], 120)
        self.assertEqual(resolved(report, "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS")["source"], "fallback")

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

    def test_mandatory_pitstop_defaults_off_and_omits_all_payload_fields(self):
        _, season_doc, warnings = launch_payloads.build_documents(
            {"EVENT_TYPE": "Race_Weekend", "RACE_DURATION_TYPE": "Time", "RACE_DURATION_MINUTES": "25"}
        )
        self.assertEqual(warnings, [])
        self.assertTrue(MANDATORY_KEYS.isdisjoint(season_doc["game_config"]))

    def test_mandatory_pitstop_emits_all_requirement_combinations(self):
        for refuel in (False, True):
            for tyre_change in (False, True):
                with self.subTest(refuel=refuel, tyre_change=tyre_change):
                    _, season_doc, warnings = launch_payloads.build_documents(
                        {
                            "EVENT_TYPE": "Race_Weekend",
                            "RACE_DURATION_TYPE": "Time",
                            "RACE_DURATION_MINUTES": "50",
                            "RACE_MANDATORY_PITSTOP_ENABLED": "true",
                            "RACE_MANDATORY_PITSTOP_WINDOW_SECONDS": "600",
                            "RACE_MANDATORY_PITSTOP_REFUEL": str(refuel).lower(),
                            "RACE_MANDATORY_PITSTOP_TYRE_CHANGE": str(tyre_change).lower(),
                        }
                    )
                    self.assertEqual(warnings, [])
                    self.assertEqual(
                        {key: season_doc["game_config"][key] for key in MANDATORY_KEYS},
                        {
                            "mandatory_pit_stop": True,
                            "pit_window": 600,
                            "requires_refuelling": refuel,
                            "requires_tyre_change": tyre_change,
                        },
                    )

    def test_mandatory_pitstop_invalid_modes_disable_with_warning(self):
        cases = (
            {"EVENT_TYPE": "Practice", "RACE_DURATION_TYPE": "Time", "RACE_DURATION_MINUTES": "50"},
            {"EVENT_TYPE": "Race_Weekend", "RACE_DURATION_TYPE": "Laps", "RACE_DURATION_LAPS": "10"},
            {"EVENT_TYPE": "Race_Weekend", "RACE_DURATION_TYPE": "Time", "RACE_DURATION_MINUTES": "20"},
        )
        for case in cases:
            with self.subTest(case=case):
                _, season_doc, warnings, report = launch_payloads.build_documents_with_report(
                    {**case, "RACE_MANDATORY_PITSTOP_ENABLED": "true"}
                )
                self.assertTrue(any("mandatory pitstops require" in warning for warning in warnings))
                self.assertTrue(MANDATORY_KEYS.isdisjoint(season_doc["game_config"]))
                self.assertEqual(resolved(report, "RACE_MANDATORY_PITSTOP_ENABLED")["value"], False)
                self.assertEqual(resolved(report, "RACE_MANDATORY_PITSTOP_ENABLED")["source"], "fallback")

    def test_mandatory_pitstop_window_is_clamped_to_race_duration(self):
        _, season_doc, warnings, report = launch_payloads.build_documents_with_report(
            {
                "EVENT_TYPE": "Race_Weekend",
                "RACE_DURATION_MINUTES": "25",
                "RACE_MANDATORY_PITSTOP_ENABLED": "true",
                "RACE_MANDATORY_PITSTOP_WINDOW_SECONDS": "9999",
            }
        )
        self.assertTrue(any("outside 1-1500" in warning for warning in warnings))
        self.assertEqual(season_doc["game_config"]["pit_window"], 1500)
        self.assertEqual(resolved(report, "RACE_MANDATORY_PITSTOP_WINDOW_SECONDS")["value"], 1500)
        self.assertEqual(resolved(report, "RACE_MANDATORY_PITSTOP_WINDOW_SECONDS")["source"], "fallback")

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
        self.assertEqual(server_doc["entry_list_server_url"], "https://entry.example.test/list.json")
        self.assertEqual(server_doc["results_post_url"], "https://results.example.test/launcher")
        self.assertEqual(server_doc["entry_list_path"], "C:\\acevo\\entrylist.json")
        self.assertEqual(server_doc["results_path"], "C:\\acevo\\results")
        self.assertEqual(server_doc["type"], "MultiplayerServerListSessionType_UNRANKED")
        self.assertEqual(server_doc["tuning_type"], "TuningDenied")

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
        self.assertEqual(resolved(report, "SERVER_TYPE")["source"], "json")
        self.assertEqual(resolved(report, "SERVER_TUNING_TYPE")["source"], "json")
        self.assertEqual(resolved(report, "SERVER_ENTRY_LIST_URL")["source"], "json")
        self.assertEqual(resolved(report, "SERVER_ENTRY_LIST_PATH")["source"], "json")
        self.assertEqual(resolved(report, "SERVER_RESULTS_PATH")["source"], "json")
        self.assertEqual(resolved(report, "EVENT_CARS")["source"], "json")
        self.assertEqual(resolved(report, "RACE_MIN_WAITING_FOR_PLAYERS_SECONDS")["source"], "json")
        self.assertEqual(resolved(report, "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS")["source"], "json")
        self.assertEqual(resolved(report, "SERVER_LAUNCHER_JSON")["source"], "env")

    def test_server_launcher_0_9_imports_time_mode_and_defaults_mandatory_off(self):
        fixture = Path("tests/fixtures/server_launcher_windows_0_9_sample.json")
        server_doc, season_doc, warnings, report = launch_payloads.build_documents_with_report(
            {"SERVER_LAUNCHER_JSON": str(fixture)}
        )

        self.assertEqual(
            warnings,
            [
                "server_launcher.json: the official 0.9 launcher does not serialize the mandatory pitstop "
                "enabled state; defaulting it to Off."
            ],
        )
        self.assertEqual(season_doc["game_config"]["race_duration_type"], launch_payloads.RACE_DURATION_TYPE_TIME)
        self.assertEqual(season_doc["game_config"]["race_duration"], 3000)
        self.assertTrue(MANDATORY_KEYS.isdisjoint(season_doc["game_config"]))
        self.assertEqual(resolved(report, "RACE_MANDATORY_PITSTOP_ENABLED")["value"], False)
        self.assertEqual(
            selected_car_names(server_doc),
            {"preset_r8gt2_mech_1", "preset_r8v10_mech_1", "preset_rx7fd_mech_1"},
        )
        self.assertNotIn("entry_list_server_url", server_doc)
        self.assertNotIn("results_post_url", server_doc)
        self.assertEqual(season_doc["event"]["track_length"], "3916")
        self.assertNotIn("max_pit_slot", season_doc["event"])

    def test_server_launcher_0_9_duration_one_imports_laps(self):
        document = json.loads(
            Path("tests/fixtures/server_launcher_windows_0_9_sample.json").read_text(encoding="utf-8")
        )
        document["Sessions"]["RaceSession"]["Duration"] = 1
        document["Sessions"]["RaceSession"]["Length"] = 12
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), document)
            _, season_doc, warnings = launch_payloads.build_documents({"SERVER_LAUNCHER_JSON": str(path)})

        self.assertEqual(warnings, [])
        self.assertEqual(season_doc["game_config"]["race_duration_type"], launch_payloads.RACE_DURATION_TYPE_LAPS)
        self.assertEqual(season_doc["game_config"]["race_duration"], 12)

    def test_server_type_and_tuning_type_env_values(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "SERVER_TYPE": "Unranked",
                "SERVER_TUNING_TYPE": "TuningDenied",
            }
        )

        self.assertEqual(warnings, [])
        self.assertEqual(server_doc["type"], "MultiplayerServerListSessionType_UNRANKED")
        self.assertEqual(server_doc["tuning_type"], "TuningDenied")
        self.assertEqual(resolved(report, "SERVER_TYPE")["source"], "env")
        self.assertEqual(resolved(report, "SERVER_TUNING_TYPE")["source"], "env")

    def test_server_type_and_tuning_type_invalid_values_fallback(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "SERVER_TYPE": "Both",
                "SERVER_TUNING_TYPE": "Tuning_Denied",
            }
        )

        self.assertTrue(any("SERVER_TYPE" in warning and "unknown value" in warning for warning in warnings))
        self.assertTrue(any("SERVER_TUNING_TYPE" in warning and "unknown value" in warning for warning in warnings))
        self.assertEqual(server_doc["type"], "MultiplayerServerListSessionType_RANKED")
        self.assertEqual(server_doc["tuning_type"], "TuningAllowed")
        self.assertEqual(resolved(report, "SERVER_TYPE")["source"], "fallback")
        self.assertEqual(resolved(report, "SERVER_TUNING_TYPE")["source"], "fallback")

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

    def test_dashboard_priority_overrides_env_and_related_car_filters(self):
        document = launcher_document()
        document["Sessions"]["PracticeSession"]["Length"] = 3600
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), document)
            server_doc, season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {
                    "SERVER_LAUNCHER_JSON": str(path),
                    "SERVER_NAME": "ENV Server",
                    "EVENT_TYPE": "Practice",
                    "EVENT_CARS": "Ferrari_F2004",
                    "EVENT_CAR_CATEGORY": "all",
                    "EVENT_BAN_CARS": "Abarth_695_Biposto",
                    "PRACTICE_DURATION_MINUTES": "1",
                },
                config_priority="dashboard",
            )

        self.assertEqual(warnings, [])
        self.assertEqual(server_doc["server_name"], "Windows Tool Server")
        self.assertEqual(season_doc["game_type"], "GameModeType_RACE_WEEKEND")
        self.assertEqual(season_doc["game_config"]["practice_duration"], 3600)
        self.assertEqual(selected_car_names(server_doc), {"preset_695b_mech_1", "ks_caterham_acmd_mech_1"})
        self.assertEqual(resolved(report, "SERVER_NAME")["source"], "json")
        self.assertEqual(resolved(report, "EVENT_CARS")["source"], "json")
        self.assertEqual(resolved(report, "EVENT_CAR_CATEGORY")["source"], "unresolved")

    def test_saved_priority_marker_controls_default_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_launcher_json(root, launcher_document())
            (root / launch_payloads.CONFIG_STATE_FILENAME).write_text(
                json.dumps({"config_source": "dashboard"}), encoding="utf-8"
            )
            server_doc, _season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path), "SERVER_NAME": "ENV Server"}
            )

        self.assertEqual(warnings, [])
        self.assertEqual(server_doc["server_name"], "Windows Tool Server")
        self.assertEqual(report["config_priority"], "dashboard")

    def test_dashboard_priority_does_not_leak_env_car_filters_when_none_are_selected(self):
        document = launcher_document()
        for car in document["Event"]["Cars"]:
            car["IsSelected"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = write_launcher_json(Path(tmp), document)
            server_doc, _season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path), "EVENT_CARS": "Ferrari_F2004"},
                config_priority="dashboard",
            )

        self.assertEqual(warnings, [])
        self.assertGreater(len(selected_car_names(server_doc)), 1)
        self.assertNotEqual(selected_car_names(server_doc), {"preset_f2004_mech_1"})
        self.assertEqual(resolved(report, "EVENT_CARS")["source"], "default")

    def test_missing_dashboard_config_falls_back_to_env_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "missing.json"
            (root / launch_payloads.CONFIG_STATE_FILENAME).write_text(
                json.dumps({"config_source": "dashboard"}), encoding="utf-8"
            )
            server_doc, _season_doc, warnings, report = launch_payloads.build_documents_with_report(
                {"SERVER_LAUNCHER_JSON": str(path), "SERVER_NAME": "ENV Server"}
            )

        self.assertEqual(server_doc["server_name"], "ENV Server")
        self.assertEqual(report["config_priority"], "env")
        self.assertTrue(any("Dashboard config is unavailable" in warning for warning in warnings))

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

    def test_server_launcher_json_uses_race_waiting_for_players_values(self):
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

        self.assertFalse(any("per-session waiting player values differ" in warning for warning in warnings))
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
                "track_length": "3916",
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
        self.assertEqual(season_doc_tourist["event"]["track_length"], "19300")
        self.assertNotIn("max_pit_slot", season_doc_tourist["event"])

        _, season_doc_kyalami_practice, warnings_kyalami_practice = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Practice",
                "EVENT_TRACK": "Kyalami_GP",
            }
        )
        self.assertEqual(warnings_kyalami_practice, [])
        self.assertEqual(
            season_doc_kyalami_practice["event"],
            {
                "track": "Kyalami",
                "layout": "GP",
                "event_name": "GP Time Attack",
                "track_length": "4522",
            },
        )

        _, season_doc_kyalami_race, warnings_kyalami_race = launch_payloads.build_documents(
            {
                "EVENT_TYPE": "Race_Weekend",
                "EVENT_TRACK": "Kyalami_GP",
            }
        )
        self.assertEqual(warnings_kyalami_race, [])
        self.assertEqual(
            season_doc_kyalami_race["event"],
            {
                "track": "Kyalami",
                "layout": "GP",
                "event_name": "GP Race",
                "track_length": "4522",
            },
        )

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
                "track_length": "20832",
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
                "SERVER_MIN_WAITING_PLAYERS": "30",
                "SERVER_MAX_WAITING_PLAYERS": "60",
            }
        )
        self.assertTrue(any("PRACTICE_DURATION" in warning for warning in warnings))
        self.assertTrue(any("RACE_DURATION" in warning for warning in warnings))
        self.assertTrue(any("PRACTICE_MAX_WAIT_TO_BOX" in warning for warning in warnings))
        self.assertTrue(any("PRACTICE_OVERTIME_WAITING_NEXT_SESSION" in warning for warning in warnings))
        self.assertTrue(any("SERVER_MIN_WAITING_PLAYERS" in warning for warning in warnings))
        self.assertTrue(any("SERVER_MAX_WAITING_PLAYERS" in warning for warning in warnings))

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
        declared_length = struct.unpack(">I", raw[:4])[0]
        expected_json = json.dumps(server_doc, separators=(",", ":")).encode("utf-8")
        self.assertEqual(declared_length, len(expected_json))
        self.assertEqual(raw[4:6], b"x\x01")

        decoded = launch_payloads.decode_payload(payload)
        self.assertEqual(decoded, server_doc)

        season_payload = launch_payloads.encode_payload(season_doc)
        decoded_season = launch_payloads.decode_payload(season_payload)
        self.assertEqual(decoded_season, season_doc)

    def test_payload_decoder_accepts_legacy_little_endian_frame(self):
        document = {"legacy": True, "message": "0.8 diagnostic payload"}
        encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(encoded, level=0)
        payload = base64.b64encode(struct.pack("<I", len(compressed)) + compressed).decode("ascii")
        self.assertEqual(launch_payloads.decode_payload(payload), document)

    def test_official_0_9_golden_payload_decodes(self):
        raw = base64.b64decode(OFFICIAL_0_9_MANDATORY_SEASON_PAYLOAD)
        document = launch_payloads.decode_payload(OFFICIAL_0_9_MANDATORY_SEASON_PAYLOAD)
        self.assertEqual(struct.unpack(">I", raw[:4])[0], len(zlib.decompress(raw[4:])))
        self.assertEqual(document["game_type"], "GameModeType_RACE_WEEKEND")
        self.assertEqual(document["game_config"]["race_duration"], 3000)
        self.assertEqual(
            {key: document["game_config"][key] for key in MANDATORY_KEYS},
            {
                "mandatory_pit_stop": True,
                "pit_window": 600,
                "requires_refuelling": False,
                "requires_tyre_change": False,
            },
        )

    def test_server_payload_uses_0_9_schema(self):
        server_doc, season_doc, warnings = launch_payloads.build_documents({})
        self.assertEqual(warnings, [])
        self.assertEqual(
            set(server_doc),
            {
                "server_tcp_listener_port",
                "server_udp_listener_port",
                "server_tcp_internal_port",
                "server_udp_internal_port",
                "server_http_port",
                "server_name",
                "max_players",
                "cycle",
                "allowed_cars_list_full",
                "driver_password",
                "spectator_password",
                "admin_password",
                "type",
                "tuning_type",
                "entry_list_path",
                "results_path",
            },
        )
        self.assertIsInstance(season_doc["event"]["track_length"], str)
        self.assertNotIn("max_pit_slot", season_doc["event"])

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
        self.assertEqual(len(server_doc["allowed_cars_list_full"]), 100)
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

    def test_entry_and_result_settings_are_passed_through(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {
                "SERVER_ENTRY_LIST_URL": "https://entry.example.test/list.json",
                "SERVER_RESULTS_POST_URL": "https://results.example.test/acevo?token=result-secret",
                "SERVER_ENTRY_LIST_PATH": "/data/entrylist.json",
                "SERVER_RESULTS_PATH": "/data/results",
            }
        )
        self.assertEqual(warnings, [])

        self.assertEqual(server_doc["entry_list_server_url"], "https://entry.example.test/list.json")
        self.assertEqual(server_doc["results_post_url"], "https://results.example.test/acevo?token=result-secret")
        self.assertNotIn("token", server_doc)
        self.assertEqual(server_doc["entry_list_path"], "/data/entrylist.json")
        self.assertEqual(server_doc["results_path"], "/data/results")
        self.assertEqual(
            resolved(report, "SERVER_ENTRY_LIST_URL")["value"],
            "https://entry.example.test/list.json",
        )
        self.assertEqual(
            resolved(report, "SERVER_RESULTS_POST_URL")["value"],
            "https://results.example.test/acevo?token=result-secret",
        )
        self.assertEqual(resolved(report, "SERVER_ENTRY_LIST_PATH")["value"], "/data/entrylist.json")
        self.assertEqual(resolved(report, "SERVER_RESULTS_PATH")["value"], "/data/results")

    def test_result_token_env_is_ignored_as_unknown(self):
        server_doc, _, warnings, report = launch_payloads.build_documents_with_report(
            {"SERVER_RESULTS_TOKEN": "result-secret"}
        )

        self.assertTrue(any("SERVER_RESULTS_TOKEN" in warning for warning in warnings))
        self.assertNotIn("token", server_doc)
        self.assertFalse(any(item["key"] == "SERVER_RESULTS_TOKEN" for item in report["resolved_env"]))

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
                launch_payloads.car_env_token_for_car(car, launch_payloads.load_config()["cars_data"]),
                car["display_name"],
                f"{float(car['performance_indicator']):.1f}",
            )
            for car in launch_payloads.load_config()["cars_data"]
        ]

        self.assertEqual(rows, expected)
        self.assertEqual(len({row[0] for row in rows}), len(rows))

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
