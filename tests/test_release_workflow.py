import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_version_is_semantic(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_main_publishes_latest_and_version_while_staging_is_moving(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        main_block = workflow.split("main)", 1)[1].split(";;", 1)[0]
        staging_block = workflow.split("staging)", 1)[1].split(";;", 1)[0]

        self.assertIn("${DOCKER_IMAGE_NAME}:latest", main_block)
        self.assertIn("${DOCKER_IMAGE_NAME}:${release_version}", main_block)
        self.assertEqual(
            re.findall(r"\$\{DOCKER_IMAGE_NAME\}:[^\s\"]+", staging_block), ["${DOCKER_IMAGE_NAME}:staging"]
        )
        self.assertIn("VERSION must be increased before merging to main", workflow)
        self.assertNotIn("DOCKER_IMAGE_TAG_STAGING", workflow)

    def test_compose_files_keep_the_simple_latest_default(self):
        expected = "image: zino1337/acevo-server:latest"
        for name in ("docker-compose.yml", "docker-compose-race.yml", "docker-compose.winvol.yml"):
            with self.subTest(name=name):
                self.assertIn(expected, (ROOT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
