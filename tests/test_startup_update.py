import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = ROOT / "scripts" / "start.sh"
RUN_SERVER_SCRIPT = ROOT / "scripts" / "run_server.sh"


class StartupUpdateTests(unittest.TestCase):
    def run_update_flow(self, auto_update: str, update_exit: int = 0):
        with tempfile.TemporaryDirectory() as temp_dir:
            events_path = Path(temp_dir) / "events"
            command = "\n".join(
                [
                    f'source "{START_SCRIPT}"',
                    "run_server_update() {",
                    "    printf 'update\\n' >> \"${EVENTS_PATH}\"",
                    f"    return {update_exit}",
                    "}",
                    "run_server_update_if_enabled",
                    "printf 'dashboard\\n' >> \"${EVENTS_PATH}\"",
                ]
            )
            environment = os.environ.copy()
            environment["AUTO_UPDATE"] = auto_update
            environment["EVENTS_PATH"] = str(events_path)
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            events = events_path.read_text().splitlines() if events_path.exists() else []
            return result, events

    def test_update_is_owned_by_container_start(self):
        start_source = START_SCRIPT.read_text()
        run_server_source = RUN_SERVER_SCRIPT.read_text()

        self.assertIn("/opt/acevo/scripts/update.sh", start_source)
        self.assertNotIn("/opt/acevo/scripts/update.sh", run_server_source)
        self.assertNotIn("AUTO_UPDATE", run_server_source)

    def test_auto_update_false_skips_update(self):
        result, events = self.run_update_flow("false")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ["dashboard"])

    def test_auto_update_true_runs_update_before_dashboard(self):
        result, events = self.run_update_flow("true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ["update", "dashboard"])

    def test_update_failure_does_not_block_dashboard(self):
        result, events = self.run_update_flow("true", update_exit=42)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ["update", "dashboard"])
        self.assertIn("Steam update failed with exit code 42", result.stderr)

    def test_mod_storage_is_prepared_before_dashboard(self):
        source = START_SCRIPT.read_text()
        main = source.split("main() {", 1)[1]

        self.assertIn("python3 -m dashboard.mods", source)
        self.assertLess(main.index("run_server_update_if_enabled"), main.index("prepare_mod_storage"))
        self.assertLess(main.index("prepare_mod_storage"), main.index("exec python3 -m dashboard"))


if __name__ == "__main__":
    unittest.main()
