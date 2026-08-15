import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from dashboard import app, config_io, live, metadata, mods, server_control
from scripts import launch_payloads

FIXTURE = Path(__file__).parent / "fixtures" / "server_launcher_windows_sample.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def summary(result):
    return result["report"]["server_summary"], result["report"]["season_summary"]


class MetadataTests(unittest.TestCase):
    def test_metadata_has_cars_tracks_enums(self):
        meta = metadata.build_metadata()
        car_count = len(launch_payloads.load_config()["cars_data"])
        self.assertEqual(len(meta["cars"]), car_count)
        self.assertTrue(meta["tracks"]["practice"])
        self.assertTrue(meta["tracks"]["race_weekend"])
        self.assertEqual(len(meta["enums"]["weather"]), 8)
        self.assertEqual(len(meta["enums"]["server_type"]), 2)
        self.assertLessEqual(meta["pi_min"], meta["pi_max"])

    def test_enum_values_are_full_pipeline_tokens(self):
        meta = metadata.build_metadata()
        ranked = next(o for o in meta["enums"]["server_type"] if o["label"] == "Ranked")
        self.assertEqual(ranked["value"], "MultiplayerServerListSessionType_RANKED")

    def test_track_entry_shape(self):
        track = metadata.build_metadata()["tracks"]["practice"][0]
        self.assertIn("|", track["token"])
        self.assertIn("(pit:", track["display"])
        self.assertIsInstance(track["max_pit_slot"], int)

    def test_public_server_racing_classes(self):
        cars = {car["display_name"]: car["classes"] for car in metadata.build_metadata()["cars"]}
        expected_counts = {"f1": 2, "gt3": 6, "gt2": 5, "gt4": 3, "cup": 10}
        for class_name, expected in expected_counts.items():
            self.assertEqual(sum(class_name in classes for classes in cars.values()), expected, class_name)

        self.assertIn("gt3", cars["Porsche 911 GT3 R Rennsport (992) - Unrestricted"])
        self.assertNotIn("gt3", cars["Porsche 911 GT3 Cup (992) - ABS TC"])
        self.assertNotIn("gt4", cars["Porsche 718 Cayman GT4 RS - Standard"])
        self.assertIn("cup", cars["BMW M2 CS Racing - 350"])

    def test_racing_class_filter_options(self):
        options = metadata.build_metadata()["categories"]["class"]
        self.assertEqual([option["value"] for option in options], ["f1", "gt3", "gt2", "gt4", "cup"])


class RoundTripTests(unittest.TestCase):
    def test_windows_file_round_trips_without_warnings(self):
        form = config_io.launcher_to_form(load_fixture())
        result = config_io.validate(form)
        self.assertEqual(result["warnings"], [])

    def test_selected_cars_and_tuning_preserved(self):
        form = config_io.launcher_to_form(load_fixture())
        selected = {c["name"]: c for c in form["cars"] if c["is_selected"]}
        self.assertIn("preset_695b_mech_1", selected)
        self.assertIn("preset_75t_mech_1", selected)
        self.assertNotIn("preset_sf25_mech_1", selected)  # IsSelected: false in fixture
        self.assertEqual(selected["preset_695b_mech_1"]["ballast"], 12.0)
        self.assertEqual(selected["preset_695b_mech_1"]["restrictor"], 3.0)

    def test_validate_reports_selected_car_count(self):
        form = config_io.launcher_to_form(load_fixture())
        server_summary, _ = summary(config_io.validate(form))
        self.assertEqual(server_summary["car_count"], 2)

    def test_race_laps_mode_detected(self):
        form = config_io.launcher_to_form(load_fixture())
        race = form["sessions"]["race"]
        self.assertEqual(race["duration_type"], launch_payloads.MAPPINGS["duration_type"]["laps"])
        self.assertEqual(race["laps"], 12)
        _, season = summary(config_io.validate(form))
        self.assertEqual(season["durations"]["race"], 12)  # laps, not seconds


class FormToLauncherTests(unittest.TestCase):
    def base_form(self, event_type):
        meta = metadata.build_metadata()
        cfg = launch_payloads.load_config()
        form = config_io.launcher_to_form({}, cfg)  # all defaults
        form["event"]["type"] = event_type
        form["event"]["track"] = meta["tracks"]["race_weekend" if "RACE_WEEKEND" in event_type else "practice"][0][
            "token"
        ]
        form["cars"] = [{"name": "preset_695b_mech_1", "is_selected": True, "ballast": 5, "restrictor": 1.5}]
        return form

    def test_emits_full_car_list_with_both_casings(self):
        doc = config_io.form_to_launcher(self.base_form("GameModeType_PRACTICE"))
        cars = doc["Event"]["Cars"]
        self.assertEqual(len(cars), len(launch_payloads.load_config()["cars_data"]))
        sample = cars[0]
        for key in (
            "name",
            "display_name",
            "IsSelected",
            "Ballast",
            "Restrictor",
            "P1",
            "is_selected",
            "ballast",
            "is_mod",
            "IsModText",
            "IsMod",
        ):
            self.assertIn(key, sample)
        self.assertFalse(sample["is_mod"])
        self.assertEqual(sample["IsModText"], "")
        self.assertFalse(sample["IsMod"])
        self.assertFalse(doc["Event"]["ShowOnlyOfficial"])
        self.assertIn("SelectOnlyOfficialCarsCommand", doc["Event"])
        selected = next(c for c in cars if c["name"] == "preset_695b_mech_1")
        self.assertTrue(selected["IsSelected"])
        self.assertEqual(selected["Ballast"], 5)
        self.assertEqual(selected["Restrictor"], 1.5)

    def test_advanced_server_fields_round_trip(self):
        form = self.base_form("GameModeType_PRACTICE")
        form["server"]["entry_list_url"] = "https://entry.example.test/list.json"
        form["server"]["results_post_url"] = "https://results.example.test/post"
        form["server"]["entry_list_path"] = "C:\\acevo\\entrylist.json"
        form["server"]["results_path"] = "C:\\acevo\\results"

        doc = config_io.form_to_launcher(form)
        self.assertEqual(doc["Server"]["EntryListUrl"], "https://entry.example.test/list.json")
        self.assertEqual(doc["Server"]["ResultsPostUrl"], "https://results.example.test/post")
        self.assertEqual(doc["Server"]["EntryListPath"], "C:\\acevo\\entrylist.json")
        self.assertEqual(doc["Server"]["ResultsPath"], "C:\\acevo\\results")

        reloaded = config_io.launcher_to_form(doc)
        self.assertEqual(reloaded["server"]["entry_list_url"], "https://entry.example.test/list.json")
        self.assertEqual(reloaded["server"]["results_post_url"], "https://results.example.test/post")
        self.assertEqual(reloaded["server"]["entry_list_path"], "C:\\acevo\\entrylist.json")
        self.assertEqual(reloaded["server"]["results_path"], "C:\\acevo\\results")

    def test_session_visibility_follows_event_type(self):
        practice = config_io.form_to_launcher(self.base_form("GameModeType_PRACTICE"))
        self.assertTrue(practice["Sessions"]["PracticeSession"]["IsVisible"])
        self.assertFalse(practice["Sessions"]["RaceSession"]["IsVisible"])

        race = config_io.form_to_launcher(self.base_form("GameModeType_RACE_WEEKEND"))
        self.assertTrue(race["Sessions"]["RaceSession"]["IsVisible"])
        self.assertTrue(race["Sessions"]["QualifyingSession"]["IsVisible"])

    def test_duration_in_length_seconds(self):
        form = self.base_form("GameModeType_PRACTICE")
        form["sessions"]["practice"]["length_sec"] = 1234
        doc = config_io.form_to_launcher(form)
        practice = doc["Sessions"]["PracticeSession"]
        self.assertEqual(practice["Length"], 1234)
        self.assertEqual(practice["Duration"], 0)
        self.assertTrue(practice["forceTimeDuration"])

    def test_race_time_vs_laps_force_time_duration(self):
        form = self.base_form("GameModeType_RACE_WEEKEND")
        form["sessions"]["race"]["duration_type"] = launch_payloads.MAPPINGS["duration_type"]["laps"]
        form["sessions"]["race"]["laps"] = 8
        race = config_io.form_to_launcher(form)["Sessions"]["RaceSession"]
        self.assertFalse(race["forceTimeDuration"])
        self.assertEqual(race["Length"], 8)

        form["sessions"]["race"]["duration_type"] = launch_payloads.MAPPINGS["duration_type"]["time"]
        form["sessions"]["race"]["length_sec"] = 1800
        race = config_io.form_to_launcher(form)["Sessions"]["RaceSession"]
        self.assertTrue(race["forceTimeDuration"])
        self.assertEqual(race["Length"], 1800)

    def test_max_players_limit_from_track_pit(self):
        meta = metadata.build_metadata()
        track = meta["tracks"]["practice"][0]
        form = self.base_form("GameModeType_PRACTICE")
        form["event"]["track"] = track["token"]
        doc = config_io.form_to_launcher(form)
        self.assertEqual(doc["Server"]["MaxPlayersLimit"], track["max_pit_slot"])


class SaveLoadTests(unittest.TestCase):
    def test_save_writes_file_that_round_trips(self):
        import tempfile

        form = config_io.launcher_to_form(load_fixture())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "server_launcher.json"
            result = config_io.save(form, path)
            self.assertTrue(path.exists())
            self.assertEqual(result["warnings"], [])
            reloaded = config_io.load_saved(path)
            self.assertEqual(reloaded["server"]["server_name"], form["server"]["server_name"])
            self.assertEqual(reloaded["event"]["track"], form["event"]["track"])

    def test_load_saved_missing_file_returns_none(self):
        self.assertIsNone(config_io.load_saved(Path("does-not-exist-12345.json")))


class ConfigSourceTests(unittest.TestCase):
    def practice_form(self, server_name="Dashboard Server"):
        form = config_io.launcher_to_form({})
        form["server"]["server_name"] = server_name
        form["event"]["type"] = "GameModeType_PRACTICE"
        form["event"]["track"] = metadata.build_metadata()["tracks"]["practice"][0]["token"]
        form["sessions"]["practice"]["length_sec"] = 3600
        cars = launch_payloads.load_config()["cars_data"]
        form["cars"] = [{"name": cars[0]["internal_name"], "is_selected": True, "ballast": 0, "restrictor": 0}]
        return form

    def test_apply_activates_dashboard_and_switching_keeps_saved_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_launcher.json"
            env = {"SERVER_NAME": "ENV Server"}
            saved = config_io.save(self.practice_form(), path, env=env)
            self.assertEqual(saved["config_source"], "env")
            self.assertFalse(config_io.config_state_path(path).exists())

            applied = config_io.apply(self.practice_form(), path, env=env)
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["config_source"], "dashboard")
            self.assertEqual(config_io.effective_runtime_form(path, env)["server"]["server_name"], "Dashboard Server")

            env_result = config_io.set_config_source("env", path, env)
            self.assertEqual(env_result["config_source"], "env")
            self.assertEqual(config_io.effective_runtime_form(path, env)["server"]["server_name"], "ENV Server")
            self.assertTrue(path.exists())

            dashboard_result = config_io.set_config_source("dashboard", path, env)
            self.assertEqual(dashboard_result["config_source"], "dashboard")
            self.assertEqual(config_io.effective_runtime_form(path, env)["server"]["server_name"], "Dashboard Server")

    def test_conflicts_are_semantic_and_exclude_inactive_sessions_and_secret_values(self):
        form = self.practice_form()
        cars = launch_payloads.load_config()["cars_data"]
        env = {
            "SERVER_NAME": "ENV Server",
            "SERVER_ADMIN_PASSWORD": "do-not-return-this-secret",
            "EVENT_TYPE": "Race_Weekend",
            "EVENT_CARS": cars[1]["internal_name"],
            "EVENT_CAR_CATEGORY": "all",
            "PRACTICE_DURATION_MINUTES": "1",
            "QUALIFY_DURATION_MINUTES": "1",
        }
        result = config_io.validate(form, env=env)

        self.assertEqual(
            result["env_conflicts"],
            [
                "SERVER_NAME",
                "SERVER_ADMIN_PASSWORD",
                "EVENT_TYPE",
                "EVENT_CARS",
                "EVENT_CAR_CATEGORY",
                "PRACTICE_DURATION_MINUTES",
            ],
        )
        self.assertNotIn("do-not-return-this-secret", json.dumps(result))
        self.assertNotIn("launcher", result)

    def test_equal_aliases_and_converted_duration_do_not_conflict(self):
        form = self.practice_form()
        selected = next(car["name"] for car in form["cars"] if car["is_selected"])
        result = config_io.validate(
            form,
            env={
                "SERVER_NAME": "Dashboard Server",
                "EVENT_TYPE": "Practice",
                "EVENT_CARS": selected,
                "PRACTICE_DURATION_MINUTES": "60",
            },
        )
        self.assertEqual(result["env_conflicts"], [])

    def test_invalid_dashboard_state_falls_back_with_visible_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_launcher.json"
            path.write_text("{", encoding="utf-8")
            config_io.config_state_path(path).write_text(json.dumps({"config_source": "dashboard"}), encoding="utf-8")
            info = config_io.config_source_info(path, {"SERVER_NAME": "ENV Server"})

        self.assertEqual(info["config_source"], "env")
        self.assertFalse(info["dashboard_available"])
        self.assertIn("missing or invalid", info["source_warning"])

    def test_apply_restores_previous_file_when_priority_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server_launcher.json"
            config_io.save(self.practice_form("Before"), path, env={})
            before = path.read_bytes()
            with patch.object(config_io, "set_config_source", return_value={"ok": False, "error": "state failed"}):
                with self.assertRaisesRegex(OSError, "state failed"):
                    config_io.apply(self.practice_form("After"), path, env={})
            self.assertEqual(path.read_bytes(), before)


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg_path = self.tmp / "server_launcher.json"

    def _form(self, server_name="Race Night"):
        form = config_io.launcher_to_form(load_fixture())
        form["server"]["server_name"] = server_name
        return form

    def test_save_list_load_delete_round_trip(self):
        form = self._form("Race Night")
        result = config_io.save_profile("Race Night", form, self.cfg_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], [])

        profiles = config_io.list_profiles(self.cfg_path)
        self.assertEqual([p["name"] for p in profiles], ["Race Night"])
        self.assertEqual(profiles[0]["server_name"], "Race Night")

        loaded = config_io.load_profile("Race Night", self.cfg_path)
        self.assertEqual(loaded["server"]["server_name"], "Race Night")
        self.assertEqual(loaded["event"]["track"], form["event"]["track"])
        selected_saved = {c["name"] for c in form["cars"] if c["is_selected"]}
        selected_loaded = {c["name"] for c in loaded["cars"] if c["is_selected"]}
        self.assertEqual(selected_loaded, selected_saved)

        self.assertTrue(config_io.delete_profile("Race Night", self.cfg_path)["ok"])
        self.assertEqual(config_io.list_profiles(self.cfg_path), [])
        self.assertIsNone(config_io.load_profile("Race Night", self.cfg_path))

    def test_list_empty_without_dir(self):
        self.assertEqual(config_io.list_profiles(self.cfg_path), [])

    def test_safe_profile_name_strips_traversal(self):
        self.assertIsNone(config_io._safe_profile_name(""))
        self.assertIsNone(config_io._safe_profile_name(".."))
        self.assertEqual(config_io._safe_profile_name("a/b"), "ab")
        self.assertNotIn("/", config_io._safe_profile_name("../evil"))
        self.assertNotIn("\\", config_io._safe_profile_name("a\\b"))

    def test_save_invalid_name_errors(self):
        self.assertFalse(config_io.save_profile("..", self._form(), self.cfg_path)["ok"])
        self.assertFalse(config_io.save_profile("/", self._form(), self.cfg_path)["ok"])


class RuntimeFormTests(unittest.TestCase):
    def test_effective_runtime_form_uses_env_values(self):
        cfg = launch_payloads.load_config()
        race_track = next(iter(cfg["tracks_by_event"]["GameModeType_RACE_WEEKEND"].values()))
        selected_car = cfg["cars_data"][0]["internal_name"]
        other_car = cfg["cars_data"][1]["internal_name"]

        form = config_io.effective_runtime_form(
            Path("does-not-exist-12345.json"),
            {
                "SERVER_NAME": "ENV Server",
                "SERVER_MAX_PLAYERS": "9",
                "SERVER_TCP_PORT": "9711",
                "SERVER_UDP_PORT": "9712",
                "SERVER_HTTP_PORT": "8091",
                "SERVER_TYPE": "Unranked",
                "SERVER_TUNING_TYPE": "TuningDenied",
                "SERVER_CYCLE_ENABLED": "false",
                "SERVER_DRIVER_PASSWORD": "driver-env",
                "SERVER_ADMIN_PASSWORD": "admin-env",
                "SERVER_SPECTATOR_PASSWORD": "spectator-env",
                "SERVER_ENTRY_LIST_URL": "https://entry.example.test/list.json",
                "SERVER_RESULTS_POST_URL": "https://results.example.test/post",
                "SERVER_ENTRY_LIST_PATH": "/data/entrylist.json",
                "SERVER_RESULTS_PATH": "/data/results",
                "EVENT_TYPE": "Race_Weekend",
                "EVENT_TRACK": launch_payloads.track_env_token(race_track),
                "EVENT_WEATHER": "Rain",
                "EVENT_WEATHER_BEHAVIOUR": "Dynamic",
                "EVENT_INITIAL_GRIP": "Green",
                "EVENT_CARS": selected_car,
                "PRACTICE_DURATION_MINUTES": "11",
                "PRACTICE_HOUR": "9",
                "PRACTICE_MINUTE": "30",
                "RACE_DURATION_TYPE": "Laps",
                "RACE_DURATION_LAPS": "7",
                "RACE_MIN_WAITING_FOR_PLAYERS_SECONDS": "12",
                "RACE_MAX_WAITING_FOR_PLAYERS_SECONDS": "34",
            },
            cfg,
        )

        self.assertEqual(form["server"]["server_name"], "ENV Server")
        self.assertEqual(form["server"]["max_players"], 9)
        self.assertEqual(form["server"]["tcp_port"], 9711)
        self.assertEqual(form["server"]["udp_port"], 9712)
        self.assertEqual(form["server"]["http_port"], 8091)
        self.assertEqual(form["server"]["server_type"], launch_payloads.MAPPINGS["server_type"]["unranked"])
        self.assertEqual(form["server"]["tuning_type"], launch_payloads.MAPPINGS["tuning_type"]["tuningdenied"])
        self.assertFalse(form["server"]["cycle_enabled"])
        self.assertEqual(form["server"]["driver_password"], "driver-env")
        self.assertEqual(form["server"]["admin_password"], "admin-env")
        self.assertEqual(form["server"]["spectator_password"], "spectator-env")
        self.assertEqual(form["server"]["entry_list_url"], "https://entry.example.test/list.json")
        self.assertEqual(form["server"]["results_post_url"], "https://results.example.test/post")
        self.assertEqual(form["server"]["entry_list_path"], "/data/entrylist.json")
        self.assertEqual(form["server"]["results_path"], "/data/results")
        self.assertEqual(form["event"]["type"], launch_payloads.MAPPINGS["event_type"]["race weekend"])
        self.assertEqual(form["event"]["track"], launch_payloads.track_token(race_track))
        self.assertEqual(form["event"]["weather"], launch_payloads.MAPPINGS["weather"]["rain"])
        self.assertEqual(form["event"]["weather_behaviour"], launch_payloads.MAPPINGS["weather_behaviour"]["dynamic"])
        self.assertEqual(form["event"]["initial_grip"], launch_payloads.MAPPINGS["initial_grip"]["green"])
        self.assertEqual(form["sessions"]["practice"]["length_sec"], 660)
        self.assertEqual(form["sessions"]["practice"]["hour"], 9)
        self.assertEqual(form["sessions"]["practice"]["minute"], 30)
        self.assertEqual(form["sessions"]["race"]["duration_type"], launch_payloads.MAPPINGS["duration_type"]["laps"])
        self.assertEqual(form["sessions"]["race"]["laps"], 7)
        self.assertEqual(form["sessions"]["race"]["min_waiting_for_players"], 12)
        self.assertEqual(form["sessions"]["race"]["max_waiting_for_players"], 34)
        selected = {car["name"]: car for car in form["cars"] if car["is_selected"]}
        self.assertIn(selected_car, selected)
        self.assertNotIn(other_car, selected)

    def test_effective_runtime_form_env_wins_over_saved_file(self):
        cfg = launch_payloads.load_config()
        race_track = next(iter(cfg["tracks_by_event"]["GameModeType_RACE_WEEKEND"].values()))
        saved = config_io.launcher_to_form({}, cfg)
        saved["server"]["server_name"] = "Saved Server"
        saved["event"]["type"] = launch_payloads.MAPPINGS["event_type"]["practice"]

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "server_launcher.json"
            config_path.write_text(json.dumps(config_io.form_to_launcher(saved, cfg)), encoding="utf-8")
            form = config_io.effective_runtime_form(
                config_path,
                {
                    "SERVER_NAME": "ENV Server",
                    "EVENT_TYPE": "Race_Weekend",
                    "EVENT_TRACK": launch_payloads.track_env_token(race_track),
                },
                cfg,
            )

        self.assertEqual(form["server"]["server_name"], "ENV Server")
        self.assertEqual(form["event"]["type"], launch_payloads.MAPPINGS["event_type"]["race weekend"])
        self.assertEqual(form["event"]["track"], launch_payloads.track_token(race_track))


class FakeProc:
    """Minimal stand-in for subprocess.Popen. ``alive_polls`` poll() calls return None
    (still running) before the process is reported as finished with ``poll_result``."""

    def __init__(self, pid=4242, poll_result=None, alive_polls=0, stdout=None):
        self.pid = pid
        self.returncode = poll_result
        self._poll = poll_result
        self._alive_polls = alive_polls
        self._calls = 0
        self.stdout = stdout if stdout is not None else io.BytesIO()

    def poll(self):
        self._calls += 1
        if self._poll is None:
            return None
        return None if self._calls <= self._alive_polls else self._poll

    def wait(self, timeout=None):
        return self._poll if self._poll is not None else 0


class ServerControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = patch.object(server_control, "LOG_FILE", self.tmp / "logs" / "server.log")
        patcher.start()
        self.addCleanup(patcher.stop)
        server_control._proc = None
        server_control._last_exit = None
        server_control._log_thread = None
        self.addCleanup(setattr, server_control, "_proc", None)
        self.addCleanup(setattr, server_control, "_last_exit", None)
        self.addCleanup(setattr, server_control, "_log_thread", None)
        self.addCleanup(self._join_log_thread)
        live.reset()
        self.addCleanup(live.reset)

    def _join_log_thread(self):
        thread = server_control._log_thread
        if thread is not None:
            thread.join(timeout=1)

    def test_start_spawns_server_process(self):
        script = self.tmp / "run_server.sh"
        script.write_text("#!/usr/bin/env bash\n")
        fake = FakeProc(pid=4242)
        with (
            patch.object(server_control, "RUN_SERVER_SCRIPT", script),
            patch.object(server_control.subprocess, "Popen", return_value=fake) as popen,
            patch.object(live, "reset") as reset_live,
        ):
            result = server_control.start()
        self.assertTrue(result["ok"])
        self.assertTrue(result["running"])
        self.assertEqual(result["pid"], 4242)
        _args, kwargs = popen.call_args
        self.assertIn(str(script), _args[0])
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        self.assertTrue(kwargs["start_new_session"])
        reset_live.assert_called_once_with()

    def test_start_missing_script_errors(self):
        with patch.object(server_control, "RUN_SERVER_SCRIPT", self.tmp / "nope.sh"):
            result = server_control.start()
        self.assertFalse(result["ok"])

    def test_start_is_noop_when_running(self):
        server_control._proc = FakeProc(poll_result=None)
        with patch.object(server_control.subprocess, "Popen") as popen:
            result = server_control.start()
        self.assertTrue(result["running"])
        popen.assert_not_called()

    def test_status_reflects_process_state(self):
        server_control._proc = FakeProc(poll_result=None)
        self.assertEqual(server_control.status()["state"], "running")

        server_control._proc = FakeProc(poll_result=5)  # finished with exit code 5
        finished = server_control.status()
        self.assertEqual(finished["state"], "stopped")
        self.assertEqual(finished["last_exit_code"], 5)

    def test_stop_signals_process_group(self):
        # running for the initial check, then reported finished so the wait loop exits promptly
        server_control._proc = FakeProc(pid=4242, poll_result=0, alive_polls=1)
        with (
            patch.object(server_control.os, "killpg", create=True) as killpg,
            patch.object(live, "reset") as reset_live,
        ):
            result = server_control.stop()
        self.assertFalse(result["running"])
        killpg.assert_called()
        self.assertEqual(killpg.call_args_list[0][0][1], server_control.signal.SIGTERM)
        self.assertIsNone(server_control._proc)
        reset_live.assert_called_once_with()

    def test_logs_returns_tail(self):
        log = self.tmp / "logs" / "server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("line1\nline2\nline3\n")
        out = server_control.logs(tail=2)
        self.assertTrue(out["ok"])
        self.assertEqual(out["lines"], "line2\nline3")

    def test_tee_output_writes_log_file_and_stdout(self):
        output = b"connecting gamecar 47efe85b7c69e844-6d27cbdfe96760ab (Max Bearman | 76561198200085390)\nline2\n"
        proc = FakeProc(stdout=io.BytesIO(output))
        captured = io.BytesIO()

        class Stdout:
            buffer = captured

        server_control.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(server_control.sys, "stdout", Stdout()):
            server_control._tee_output(proc, server_control.LOG_FILE)

        self.assertEqual(server_control.LOG_FILE.read_bytes(), output)
        self.assertEqual(captured.getvalue(), output)
        self.assertEqual(live.snapshot()["drivers"][0]["name"], "Max Bearman")


class BasicAuthTests(unittest.TestCase):
    def header(self, user, password):
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return f"Basic {token}"

    def test_no_password_is_public(self):
        self.assertTrue(app.check_basic_auth(None, "admin", ""))

    def test_correct_credentials(self):
        self.assertTrue(app.check_basic_auth(self.header("admin", "s3cret"), "admin", "s3cret"))

    def test_wrong_password_rejected(self):
        self.assertFalse(app.check_basic_auth(self.header("admin", "nope"), "admin", "s3cret"))

    def test_missing_header_rejected(self):
        self.assertFalse(app.check_basic_auth(None, "admin", "s3cret"))

    def test_malformed_header_rejected(self):
        self.assertFalse(app.check_basic_auth("Bearer xyz", "admin", "s3cret"))


class FrontendStaticTests(unittest.TestCase):
    def test_configuration_priority_flow_is_explicit_and_preflights_apply(self):
        static = Path(__file__).parents[1] / "dashboard" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        source = (static / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="config-priority"', html)
        self.assertIn('id="config-priority-value"', html)
        self.assertIn('class="topbar-statuses"', html)
        self.assertNotIn('id="config-priority-switch"', html)
        self.assertNotIn('id="config-priority"', html.split('<main id="config-view"', 1)[1])
        self.assertIn('configSource === "dashboard" ? "env" : "dashboard"', source)
        self.assertIn('byId("config-priority").addEventListener("click", switchConfigSource)', source)
        self.assertIn("Use ENV priority", source)
        self.assertIn('api.post("/api/validate", { form })', source)
        self.assertIn('api.post("/api/server/apply", { form })', source)
        self.assertNotIn("const saved = await doSave();", source)

    def test_cars_bulk_selection_uses_master_checkbox_only(self):
        source = (Path(__file__).parents[1] / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Select all shown", source)
        self.assertIn("Categories · hiding clears selection", source)
        self.assertIn("cars-list-header", source)
        self.assertIn("name.title = car.display_name", source)
        self.assertIn('nameWrap.className = "car-info"', source)
        self.assertIn("No selected cars match the current filters.", source)
        self.assertIn("No cars match this search.", source)
        self.assertIn("if (carFilters.onlySelected && !cb.checked) renderCarList();", source)
        self.assertNotIn("Select none", source)

    def test_class_filters_and_track_memory_are_wired(self):
        source = (Path(__file__).parents[1] / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("classes: new Set()", source)
        self.assertIn("META.categories.class", source)
        self.assertIn("lastTrackPerMode", source)
        self.assertIn("preferredTrack", source)
        self.assertIn("categoryFilterDefaults(META.categories, META.cars)", source)
        self.assertNotIn("applyCategorySelection", source)
        self.assertNotIn("selectedByCategoryFilters", source)
        self.assertIn("deselectCarsInCategory(META.cars, carState, kind, opt.value)", source)
        self.assertIn('deselectCarsInCategory(META.cars, carState, "mod", "mod")', source)
        self.assertIn("setVisibleCarSelection(shown, carState, value)", source)
        self.assertIn('const CAR_WORKSPACE_STORAGE_KEY = "acevo-car-workspace"', source)
        self.assertIn("sessionStorage.setItem(", source)
        self.assertIn("restoreCarWorkspace(activeConfigPath)", source)
        self.assertIn('"Categories · search covers all cars"', source)
        self.assertIn("if (searchActive()) return matchesCarSearch(car, carFilters.text);", source)
        self.assertIn("const categoriesChanged = cb.checked && ensureCarCategoryVisible(car);", source)
        self.assertIn('span.textContent = "Mod"', source)
        self.assertIn("sortCarsByDisplayName", source)
        self.assertNotIn("SESSION_PRESETS", source)

    def test_car_filter_layout_has_stable_dimensions(self):
        source = (Path(__file__).parents[1] / "dashboard" / "static" / "theme.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", source)
        self.assertIn("grid-template-columns: repeat(7, max-content)", source)
        self.assertIn("@container (max-width: 540px)", source)
        self.assertNotIn(".category-column", source)
        self.assertNotIn(".category-title", source)
        self.assertIn("scrollbar-gutter: stable", source)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr) 72px 72px", source)
        self.assertIn("overflow-x: hidden", source)
        self.assertIn("height: 460px", source)

    def test_mobile_dashboard_breakpoint_exists(self):
        static = Path(__file__).parents[1] / "dashboard" / "static"
        css = (static / "theme.css").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        app_js = (static / "app.js").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn("@media (max-width: 420px)", css)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr) 60px 60px", css)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", css)
        self.assertIn("safe-area-inset-top", css)
        self.assertIn("mobile-card-toggle", css)
        self.assertEqual(html.count('data-mobile-section="'), 5)
        self.assertIn("card.dataset.mobileSection = `session-${key}`", app_js)
        self.assertIn("acevo-mobile-sections", app_js)
        self.assertNotIn("dirty-change-bar", html)

    def test_live_tab_polls_only_while_active(self):
        static = Path(__file__).parents[1] / "dashboard" / "static"
        css = (static / "theme.css").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        app_js = (static / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="tab-live"', html)
        self.assertIn('id="live-view"', html)
        self.assertIn("/api/server/live", app_js)
        self.assertIn("liveTimer = setInterval(refreshLive, 4000)", app_js)
        self.assertIn('if (view === "live") startLivePolling()', app_js)
        self.assertIn("else stopLivePolling()", app_js)
        self.assertIn(".live-driver-row", css)
        self.assertIn("grid-template-columns: 32px repeat(3, minmax(0, 1fr))", css)
        self.assertIn("liveCarDisplayName", app_js)
        self.assertIn(".live-driver-head span:nth-child(n + 4)", css)
        self.assertIn("font-variant-numeric: tabular-nums", css)

    def test_mods_tab_is_kspkg_only_and_contains_client_instructions(self):
        static = Path(__file__).parents[1] / "dashboard" / "static"
        css = (static / "theme.css").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        app_js = (static / "app.js").read_text(encoding="utf-8")
        dashboard_logic = (static / "dashboard_logic.mjs").read_text(encoding="utf-8")

        self.assertIn('id="tab-mods"', html)
        self.assertIn('id="mods-view"', html)
        self.assertIn('<ol class="mods-help">', html)
        self.assertIn('accept=".kspkg"', html)
        self.assertIn("<span>Size</span>", html)
        self.assertIn("asks before stopping it", html)
        self.assertIn("The car and its variants are", html)
        self.assertIn("open the Configuration tab", html)
        self.assertIn("<h2>Cars</h2>", html)
        self.assertNotIn("Car Restrictions", html)
        self.assertIn("As a client, every driver", html)
        self.assertIn(r"%USERPROFILE%\Saved Games\ACE\mods", html)
        self.assertNotIn('accept=".json"', html)
        self.assertNotIn("quota", html.lower())
        self.assertIn('fetch("/api/mods/upload/start"', app_js)
        self.assertIn("/api/mods/upload/chunk?upload_id=", app_js)
        self.assertIn("/api/mods/upload/status?upload_id=", app_js)
        self.assertIn('request.upload.addEventListener("progress"', app_js)
        self.assertIn("Resuming upload at", app_js)
        self.assertIn("The web proxy rejected the upload chunk", dashboard_logic)
        self.assertIn(".mod-upload-label.error", css)
        self.assertIn("removed from the active configuration automatically", app_js)
        self.assertIn("async function modServerIsRunning()", app_js)
        self.assertIn("async function stopServerForModChange()", app_js)
        self.assertIn('result = await api.post("/api/server/stop")', app_js)
        self.assertIn("Stop server and install mod", app_js)
        self.assertIn("Stop server and delete mod", app_js)
        self.assertIn("input.disabled = modMutationActive", app_js)
        self.assertIn("deleteButton.disabled = modMutationActive", app_js)
        self.assertIn("matchesPiFilter", app_js)
        self.assertIn(".mods-row", css)
        self.assertIn("@media (min-width: 1101px)", css)
        self.assertIn("height: 540px", css)


class HttpIntegrationTests(unittest.TestCase):
    def make(self, password, config_path=Path("nonexistent.json")):
        config = app.DashboardConfig(config_path=config_path, user="admin", password=password)
        httpd = app.make_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, httpd.server_address[1]

    def get(self, port, path, auth=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        if auth:
            token = base64.b64encode(auth.encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        return urllib.request.urlopen(req, timeout=5)

    def post(self, port, path, body, auth=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            token = base64.b64encode(auth.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_requires_auth_when_password_set(self):
        httpd, port = self.make("s3cret")
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(port, "/api/metadata")
            self.assertEqual(ctx.exception.code, 401)
            self.assertIn("Basic", ctx.exception.headers.get("WWW-Authenticate", ""))
            ctx.exception.close()

            with self.get(port, "/api/metadata", auth="admin:s3cret") as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read())
            self.assertIn("cars", body)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_public_when_password_empty(self):
        httpd, port = self.make("")
        try:
            with self.get(port, "/api/metadata") as resp:
                self.assertEqual(resp.status, 200)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_mod_routes_list_volume_and_reject_invalid_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            mods_dir = Path(temp) / "mods"
            mods_dir.mkdir()
            (mods_dir / "companion.json").write_text('{"ignored": true}', encoding="utf-8")
            httpd, port = self.make("")
            try:
                with (
                    patch.dict(os.environ, {"ACEVO_MODS_DIR": str(mods_dir)}, clear=False),
                    patch.object(server_control, "status", return_value={"running": False, "state": "stopped"}),
                ):
                    with self.get(port, "/api/mods") as response:
                        body = json.loads(response.read())
                    self.assertEqual(body["mods"], [])
                    self.assertFalse(body["running"])

                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/mods/upload?filename=broken.kspkg",
                        data=b"not a package",
                        headers={"Content-Type": "application/octet-stream"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(ctx.exception.code, 400)
                    error = json.loads(ctx.exception.read())
                    self.assertIn("file table", error["error"])
                    ctx.exception.close()
            finally:
                httpd.shutdown()
                httpd.server_close()

            self.assertEqual([path.name for path in mods_dir.iterdir()], ["companion.json"])

    def test_chunk_upload_routes_report_offset_and_resume_session(self):
        with tempfile.TemporaryDirectory() as temp:
            mods_dir = Path(temp) / "mods"
            mods_dir.mkdir()
            httpd, port = self.make("")
            payload = b"not a package"
            try:
                with (
                    patch.dict(os.environ, {"ACEVO_MODS_DIR": str(mods_dir)}, clear=False),
                    patch.object(server_control, "status", return_value={"running": False, "state": "stopped"}),
                ):
                    start_body = json.dumps(
                        {"filename": "resume.kspkg", "size": len(payload), "last_modified": 123}
                    ).encode()
                    start_request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/mods/upload/start",
                        data=start_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(start_request, timeout=5) as response:
                        started = json.loads(response.read())
                    self.assertEqual(started["offset"], 0)
                    self.assertEqual(started["chunk_size"], mods.MAX_UPLOAD_CHUNK_SIZE)

                    first_request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/mods/upload/chunk?upload_id={started['upload_id']}&offset=0",
                        data=payload[:4],
                        headers={"Content-Type": "application/octet-stream"},
                        method="POST",
                    )
                    with urllib.request.urlopen(first_request, timeout=5) as response:
                        first = json.loads(response.read())
                    self.assertEqual(first["offset"], 4)
                    self.assertFalse(first["complete"])

                    with self.get(
                        port,
                        f"/api/mods/upload/status?upload_id={started['upload_id']}",
                    ) as response:
                        status = json.loads(response.read())
                    self.assertEqual(status["offset"], 4)

                    with urllib.request.urlopen(start_request, timeout=5) as response:
                        resumed = json.loads(response.read())
                    self.assertEqual(resumed["upload_id"], started["upload_id"])
                    self.assertEqual(resumed["offset"], 4)

                    final_request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/mods/upload/chunk?upload_id={started['upload_id']}&offset=4",
                        data=payload[4:],
                        headers={"Content-Type": "application/octet-stream"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(final_request, timeout=5)
                    self.assertEqual(ctx.exception.code, 400)
                    self.assertIn("invalid KSPKG", json.loads(ctx.exception.read())["error"])
                    ctx.exception.close()
            finally:
                httpd.shutdown()
                httpd.server_close()

            self.assertFalse((mods_dir / "resume.kspkg").exists())
            self.assertEqual(list((mods_dir / mods.UPLOADS_DIRNAME).iterdir()), [])

    def test_live_route_is_authenticated_and_omits_private_data(self):
        httpd, port = self.make("s3cret")
        snapshot = {
            "players": 1,
            "drivers": [
                {
                    "car_id": "runtime-id",
                    "name": "Driver",
                    "car": "preset_m4gt3_mech_1",
                    "number": 20,
                    "laps": 3,
                    "last_lap_ms": 98321,
                    "best_lap_ms": 97210,
                }
            ],
        }
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(port, "/api/server/live")
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()

            with (
                patch.object(server_control, "status", return_value={"running": True}),
                patch.object(live, "snapshot", return_value=snapshot),
            ):
                with self.get(port, "/api/server/live", auth="admin:s3cret") as resp:
                    body = json.loads(resp.read())
            self.assertTrue(body["running"])
            self.assertEqual(body["drivers"][0]["best_lap_ms"], 97210)
            self.assertNotIn("steam_id", json.dumps(body))
            self.assertNotIn("listing", body)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_access_requests_do_not_log_to_stderr(self):
        httpd, port = self.make("")
        stderr = io.StringIO()
        try:
            with patch.object(app.sys, "stderr", stderr):
                with self.get(port, "/api/metadata") as resp:
                    self.assertEqual(resp.status, 200)
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertNotIn("[dashboard]", stderr.getvalue())

    def test_config_merges_env_passwords(self):
        with tempfile.TemporaryDirectory() as tmp:
            form = config_io.launcher_to_form({})
            form["server"]["driver_password"] = "saved-driver"
            form["server"]["admin_password"] = "saved-admin"
            form["server"]["spectator_password"] = "saved-spectator"
            config_path = Path(tmp) / "server_launcher.json"
            config_path.write_text(json.dumps(config_io.form_to_launcher(form)), encoding="utf-8")

            httpd, port = self.make("", config_path=config_path)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "SERVER_DRIVER_PASSWORD": "env-driver",
                        "SERVER_ADMIN_PASSWORD": "env-admin",
                        "SERVER_SPECTATOR_PASSWORD": "env-spectator",
                    },
                    clear=True,
                ):
                    with self.get(port, "/api/config") as resp:
                        body = json.loads(resp.read())
            finally:
                httpd.shutdown()
                httpd.server_close()

        server = body["form"]["server"]
        self.assertEqual(server["driver_password"], "env-driver")
        self.assertEqual(server["admin_password"], "env-admin")
        self.assertEqual(server["spectator_password"], "env-spectator")

    def test_config_returns_env_passwords_without_saved_file(self):
        httpd, port = self.make("")
        try:
            with patch.dict(
                os.environ,
                {"SERVER_DRIVER_PASSWORD": "env-driver", "SERVER_ADMIN_PASSWORD": "env-admin"},
                clear=True,
            ):
                with self.get(port, "/api/config") as resp:
                    body = json.loads(resp.read())
        finally:
            httpd.shutdown()
            httpd.server_close()

        server = body["form"]["server"]
        self.assertEqual(server["driver_password"], "env-driver")
        self.assertEqual(server["admin_password"], "env-admin")

    def test_config_returns_runtime_env_values(self):
        cfg = launch_payloads.load_config()
        race_track = next(iter(cfg["tracks_by_event"]["GameModeType_RACE_WEEKEND"].values()))
        selected_car = cfg["cars_data"][0]["internal_name"]
        httpd, port = self.make("")
        try:
            with patch.dict(
                os.environ,
                {
                    "SERVER_NAME": "ENV API Server",
                    "SERVER_MAX_PLAYERS": "12",
                    "EVENT_TYPE": "Race_Weekend",
                    "EVENT_TRACK": launch_payloads.track_env_token(race_track),
                    "EVENT_CARS": selected_car,
                    "RACE_DURATION_TYPE": "Laps",
                    "RACE_DURATION_LAPS": "8",
                },
                clear=True,
            ):
                with self.get(port, "/api/config") as resp:
                    body = json.loads(resp.read())
        finally:
            httpd.shutdown()
            httpd.server_close()

        form = body["form"]
        self.assertEqual(form["server"]["server_name"], "ENV API Server")
        self.assertEqual(form["server"]["max_players"], 12)
        self.assertEqual(form["event"]["type"], launch_payloads.MAPPINGS["event_type"]["race weekend"])
        self.assertEqual(form["event"]["track"], launch_payloads.track_token(race_track))
        self.assertEqual(form["sessions"]["race"]["duration_type"], launch_payloads.MAPPINGS["duration_type"]["laps"])
        self.assertEqual(form["sessions"]["race"]["laps"], 8)
        selected = {car["name"] for car in form["cars"] if car["is_selected"]}
        self.assertEqual(selected, {selected_car})

    def test_apply_and_source_routes_switch_priority_and_restart_only_when_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "server_launcher.json"
            form = ConfigSourceTests().practice_form()
            httpd, port = self.make("", config_path=config_path)
            try:
                with (
                    patch.dict(os.environ, {"SERVER_NAME": "ENV Server"}, clear=True),
                    patch.object(server_control, "status", return_value={"running": False, "state": "stopped"}),
                    patch.object(server_control, "restart") as restart,
                ):
                    with self.post(port, "/api/server/apply", {"form": form}) as response:
                        applied = json.loads(response.read())
                    self.assertTrue(applied["ok"])
                    self.assertFalse(applied["restarted"])
                    restart.assert_not_called()

                with (
                    patch.dict(os.environ, {"SERVER_NAME": "ENV Server"}, clear=True),
                    patch.object(server_control, "status", return_value={"running": True, "state": "running"}),
                    patch.object(server_control, "restart", return_value={"ok": True, "running": True}) as restart,
                ):
                    with self.post(port, "/api/config/source", {"source": "env"}) as response:
                        switched = json.loads(response.read())
                    self.assertEqual(switched["config_source"], "env")
                    self.assertTrue(switched["restarted"])
                    restart.assert_called_once_with()
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_apply_failure_does_not_restart_server(self):
        httpd, port = self.make("")
        try:
            with (
                patch.object(config_io, "apply", side_effect=OSError("write failed")),
                patch.object(server_control, "status") as status,
                patch.object(server_control, "restart") as restart,
            ):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.post(port, "/api/server/apply", {"form": {}})
                self.assertEqual(ctx.exception.code, 500)
                ctx.exception.close()
                status.assert_not_called()
                restart.assert_not_called()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
