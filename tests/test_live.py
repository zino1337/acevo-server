"""Live driver state reconstructed from real AC EVO server-log lines."""

import threading
import unittest

from dashboard import live

CAR_ID = "47efe85b7c69e844-6d27cbdfe96760ab"
SESSION_LINES = [
    "[2026-08-07 21:34:07.403] [server] [info] assigning pit slot 1 competitor_id 76561198200085390",
    f"[2026-08-07 21:34:07.404] [gameplay] [info] 76561198200085390 connected (true) on car "
    f"ks_maserati_mc20_gt2, with new carId {CAR_ID}",
    f"[2026-08-07 21:34:07.404] [server] [info] connecting gamecar {CAR_ID} (Max Bearman | 76561198200085390)",
    f"[2026-08-07 21:34:07.404] [server] [info] Car [{CAR_ID}] #20 for driver Max Bearman [76561198200085390]",
    f"[2026-08-07 21:41:12.100] [gameplay] [info] New lap carId {CAR_ID}: 01:38.500",
    f"[2026-08-07 21:43:06.729] [gameplay] [info] New lap carId {CAR_ID}: 01:36.369",
    "[2026-08-07 21:44:00.000] [server] [info] Server updated: 1 players",
]


class LiveTrackerTests(unittest.TestCase):
    def setUp(self):
        live.reset()
        self.addCleanup(live.reset)

    def consume_session(self):
        for line in SESSION_LINES:
            live.consume_line(line)

    def test_builds_driver_state_without_exposing_steam_id(self):
        self.consume_session()
        result = live.snapshot()
        self.assertEqual(result["players"], 1)
        self.assertEqual(len(result["drivers"]), 1)
        driver = result["drivers"][0]
        self.assertEqual(driver["name"], "Max Bearman")
        self.assertEqual(driver["car"], "ks_maserati_mc20_gt2")
        self.assertEqual(driver["number"], 20)
        self.assertNotIn("steam_id", driver)

    def test_tracks_laps_last_and_best(self):
        self.consume_session()
        driver = live.snapshot()["drivers"][0]
        self.assertEqual(driver["laps"], 2)
        self.assertEqual(driver["last_lap_ms"], 96369)
        self.assertEqual(driver["best_lap_ms"], 96369)

        live.consume_line(f"[gameplay] [info] New lap carId {CAR_ID}: 01:41.000")
        driver = live.snapshot()["drivers"][0]
        self.assertEqual(driver["laps"], 3)
        self.assertEqual(driver["last_lap_ms"], 101000)
        self.assertEqual(driver["best_lap_ms"], 96369)

    def test_accepts_car_before_driver_and_byte_lines(self):
        live.consume_line(SESSION_LINES[1].encode())
        live.consume_line(SESSION_LINES[2].encode())
        driver = live.snapshot()["drivers"][0]
        self.assertEqual(driver["name"], "Max Bearman")
        self.assertEqual(driver["car"], "ks_maserati_mc20_gt2")

    def test_disconnect_normalizes_dashed_car_id_and_removes_driver(self):
        self.consume_session()
        live.consume_line("[server] [info] Removing disconnected remote_car 47efe85b-7c69-e844-6d27-cbdfe96760ab")
        self.assertEqual(live.snapshot(), {"players": 0, "drivers": []})

    def test_interleaved_connections_keep_driver_data_separate(self):
        other_id = "9999999999999999-aaaaaaaaaaaaaaaa"
        lines = [
            f"connected (true) on car preset_m4gt3_mech_1, with new carId {CAR_ID}",
            f"connected (true) on car preset_296gt3_mech_1, with new carId {other_id}",
            f"connecting gamecar {other_id} (Second Driver | 76561198000000002)",
            f"connecting gamecar {CAR_ID} (First Driver | 76561198000000001)",
            f"Car [{CAR_ID}] #20 for driver First Driver [76561198000000001]",
            f"Car [{other_id}] #21 for driver Second Driver [76561198000000002]",
        ]
        for line in lines:
            live.consume_line(line)

        drivers = {driver["name"]: driver for driver in live.snapshot()["drivers"]}
        self.assertEqual(drivers["First Driver"]["car"], "preset_m4gt3_mech_1")
        self.assertEqual(drivers["First Driver"]["number"], 20)
        self.assertEqual(drivers["Second Driver"]["car"], "preset_296gt3_mech_1")
        self.assertEqual(drivers["Second Driver"]["number"], 21)

    def test_zero_player_update_clears_stale_drivers(self):
        self.consume_session()
        live.consume_line("[server] [info] Server updated: 0 players")
        self.assertEqual(live.snapshot(), {"players": 0, "drivers": []})

    def test_reset_clears_current_connection(self):
        self.consume_session()
        live.reset()
        self.assertEqual(live.snapshot(), {"players": 0, "drivers": []})

    def test_old_output_stream_cannot_repopulate_reset_state(self):
        generation = live.reset()
        live.consume_line(SESSION_LINES[2], generation)
        self.assertEqual(len(live.snapshot()["drivers"]), 1)

        live.reset()
        live.consume_line(SESSION_LINES[2], generation)
        self.assertEqual(live.snapshot(), {"players": 0, "drivers": []})

    def test_unknown_and_malformed_lines_are_ignored(self):
        live.consume_line("garbage")
        live.consume_line(None)
        live.consume_line(f"New lap carId {CAR_ID}: invalid")
        self.assertEqual(live.snapshot(), {"players": 0, "drivers": []})

    def test_snapshot_is_safe_while_lines_are_consumed(self):
        errors = []

        def writer():
            try:
                for _ in range(100):
                    self.consume_session()
            except Exception as exc:  # pragma: no cover - captured for the assertion
                errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        for _ in range(100):
            live.snapshot()
        thread.join()
        self.assertEqual(errors, [])


class LapTimeTests(unittest.TestCase):
    def test_converts_minutes_seconds_and_milliseconds(self):
        self.assertEqual(live.lap_to_ms("01:36.369"), 96369)
        self.assertEqual(live.lap_to_ms("12:00.000"), 720000)

    def test_rejects_invalid_values(self):
        self.assertIsNone(live.lap_to_ms("--:--.---"))
        self.assertIsNone(live.lap_to_ms(""))


if __name__ == "__main__":
    unittest.main()
