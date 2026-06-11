import base64
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from dashboard import app, config_io, metadata, server_control
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
        for key in ("name", "display_name", "IsSelected", "Ballast", "Restrictor", "P1", "is_selected", "ballast"):
            self.assertIn(key, sample)
        selected = next(c for c in cars if c["name"] == "preset_695b_mech_1")
        self.assertTrue(selected["IsSelected"])
        self.assertEqual(selected["Ballast"], 5)
        self.assertEqual(selected["Restrictor"], 1.5)

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


class FakeProc:
    """Minimal stand-in for subprocess.Popen. ``alive_polls`` poll() calls return None
    (still running) before the process is reported as finished with ``poll_result``."""

    def __init__(self, pid=4242, poll_result=None, alive_polls=0):
        self.pid = pid
        self.returncode = poll_result
        self._poll = poll_result
        self._alive_polls = alive_polls
        self._calls = 0

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
        self.addCleanup(setattr, server_control, "_proc", None)
        self.addCleanup(setattr, server_control, "_last_exit", None)

    def test_start_spawns_server_process(self):
        script = self.tmp / "run_server.sh"
        script.write_text("#!/usr/bin/env bash\n")
        fake = FakeProc(pid=4242)
        with (
            patch.object(server_control, "RUN_SERVER_SCRIPT", script),
            patch.object(server_control.subprocess, "Popen", return_value=fake) as popen,
        ):
            result = server_control.start()
        self.assertTrue(result["ok"])
        self.assertTrue(result["running"])
        self.assertEqual(result["pid"], 4242)
        _args, kwargs = popen.call_args
        self.assertIn(str(script), _args[0])
        self.assertTrue(kwargs["start_new_session"])

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
        with patch.object(server_control.os, "killpg", create=True) as killpg:
            result = server_control.stop()
        self.assertFalse(result["running"])
        killpg.assert_called()
        self.assertEqual(killpg.call_args_list[0][0][1], server_control.signal.SIGTERM)
        self.assertIsNone(server_control._proc)

    def test_logs_returns_tail(self):
        log = self.tmp / "logs" / "server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("line1\nline2\nline3\n")
        out = server_control.logs(tail=2)
        self.assertTrue(out["ok"])
        self.assertEqual(out["lines"], "line2\nline3")


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


class HttpIntegrationTests(unittest.TestCase):
    def make(self, password):
        config = app.DashboardConfig(config_path=Path("nonexistent.json"), user="admin", password=password)
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


if __name__ == "__main__":
    unittest.main()
