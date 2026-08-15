import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard import config_io, mods
from scripts import kspkg, launch_payloads


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_string(field: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return _varint((field << 3) | 2) + _varint(len(raw)) + raw


def write_kspkg(
    path: Path,
    preset_ids=("preset_test_mech_1",),
    *,
    table_size=0x2000000,
    car_name="Test Car",
    encrypted=False,
) -> Path:
    """Write a small sparse package using the real table layout."""
    files = [
        (
            r"content\cars\test_car\test_car.moddedcarcontent",
            _protobuf_string(4, car_name),
        )
    ]
    files.extend(
        (
            rf"content\cars\test_car\{preset_id}.mechanicalcarpreset",
            _protobuf_string(2, f"Variant {index}"),
        )
        for index, preset_id in enumerate(preset_ids, start=1)
    )

    table_start = 4096
    with path.open("wb") as handle:
        offset = 0
        entries = []
        for index, (internal_path, data) in enumerate(files, start=1):
            flags = kspkg.FLAG_ENCRYPTED if encrypted else 0
            stored = kspkg._xor(data) if encrypted else data
            handle.seek(offset)
            handle.write(stored)
            encoded_path = internal_path.encode("ascii")
            entry = kspkg.ENTRY_STRUCT.pack(
                encoded_path.ljust(224, b"\0"),
                0,
                flags,
                len(encoded_path),
                index,
                len(stored),
                offset,
            )
            entries.append(kspkg._xor(entry))
            offset += len(stored)

        handle.seek(table_start)
        for entry in entries:
            handle.write(entry)
        handle.write(kspkg._xor(bytes(kspkg.ENTRY_SIZE)))
        handle.seek(table_start + table_size - 1)
        handle.write(b"\0")
    return path


class KspkgParserTests(unittest.TestCase):
    def setUp(self):
        kspkg.clear_cache()

    def test_reads_current_and_legacy_file_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for table_size in kspkg.TABLE_SIZES:
                with self.subTest(table_size=table_size):
                    package = kspkg.inspect_package(
                        write_kspkg(root / f"table-{table_size}.kspkg", table_size=table_size)
                    )
                    self.assertEqual(package.preset_ids, ("preset_test_mech_1",))
                    self.assertEqual(package.cars[0].display_name, "Test Car")

    def test_reads_encrypted_metadata_and_all_variants(self):
        with tempfile.TemporaryDirectory() as temp:
            package = kspkg.inspect_package(
                write_kspkg(
                    Path(temp) / "variants.kspkg",
                    ("preset_test_mech_1", "preset_test_mech_2", "preset_test_mech_3"),
                    car_name="Example GT3",
                    encrypted=True,
                )
            )

        self.assertEqual(
            package.preset_ids,
            ("preset_test_mech_1", "preset_test_mech_2", "preset_test_mech_3"),
        )
        self.assertEqual(package.cars[0].variants[1].display_name, "Example GT3 - Variant 2")

    def test_rejects_broken_packages_and_packages_without_presets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            broken = root / "broken.kspkg"
            broken.write_bytes(b"not a package")
            empty = write_kspkg(root / "empty.kspkg", ())

            with self.assertRaises(kspkg.KspkgError):
                kspkg.inspect_package(broken)
            with self.assertRaisesRegex(kspkg.KspkgError, "no mechanical car presets"):
                kspkg.inspect_package(empty)

    def test_scan_ignores_json_and_detects_mod_collisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_kspkg(root / "one.kspkg", ("preset_duplicate",))
            write_kspkg(root / "two.kspkg", ("preset_duplicate",))
            (root / "companion.json").write_text('{"kept": true}', encoding="utf-8")

            inventory = kspkg.scan_mods(root)

        self.assertEqual([item.filename for item in inventory], ["one.kspkg", "two.kspkg"])
        self.assertTrue(all(item.status == "conflict" for item in inventory))

    def test_cache_refreshes_after_manual_volume_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_kspkg(Path(temp) / "manual.kspkg", ("preset_one",))
            before = kspkg.scan_mods(path.parent)[0]
            previous_mtime = path.stat().st_mtime_ns
            write_kspkg(path, ("preset_two",))
            os.utime(path, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))
            after = kspkg.scan_mods(path.parent)[0]

        self.assertEqual(before.preset_ids, ("preset_one",))
        self.assertEqual(after.preset_ids, ("preset_two",))


class ModCatalogTests(unittest.TestCase):
    def setUp(self):
        kspkg.clear_cache()

    def test_official_ids_are_overlaid_and_new_ids_are_appended(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mods_dir = root / "mods"
            mods_dir.mkdir()
            write_kspkg(
                mods_dir / "overlay.kspkg",
                ("preset_gt3rs_mech_1", "preset_new_mod_mech_1"),
                car_name="Manthey Test",
            )
            environment = {
                "ACEVO_MODS_DIR": str(mods_dir),
                "ACEVO_SERVER_INSTALL_DIR": str(root / "server"),
            }
            with patch.dict(os.environ, environment, clear=False):
                config = launch_payloads.load_config()
                cars = config["cars_data"]
                server_doc, _season_doc, warnings = launch_payloads.build_documents(
                    {"EVENT_CARS": "preset_gt3rs_mech_1,preset_new_mod_mech_1"}
                )

        self.assertEqual(sum(car["internal_name"] == "preset_gt3rs_mech_1" for car in cars), 1)
        self.assertTrue(next(car for car in cars if car["internal_name"] == "preset_gt3rs_mech_1")["is_mod"])
        self.assertTrue(next(car for car in cars if car["internal_name"] == "preset_new_mod_mech_1")["is_mod"])
        self.assertEqual(warnings, [])
        self.assertEqual(
            {car["car_name"] for car in server_doc["allowed_cars_list_full"]},
            {"preset_gt3rs_mech_1", "preset_new_mod_mech_1"},
        )

    def test_event_cars_all_includes_only_conflict_free_mods(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_kspkg(root / "one.kspkg", ("preset_shared",))
            write_kspkg(root / "two.kspkg", ("preset_shared",))
            write_kspkg(root / "ready.kspkg", ("preset_ready",))
            with patch.dict(os.environ, {"ACEVO_MODS_DIR": str(root)}, clear=False):
                config = launch_payloads.load_config()
                server_doc, _season_doc, _warnings = launch_payloads.build_documents({"EVENT_CARS": "all"})

        catalog_ids = {car["internal_name"] for car in config["cars_data"]}
        payload_ids = {car["car_name"] for car in server_doc["allowed_cars_list_full"]}
        self.assertIn("preset_ready", catalog_ids)
        self.assertIn("preset_ready", payload_ids)
        self.assertNotIn("preset_shared", catalog_ids)
        self.assertNotIn("preset_shared", payload_ids)

    def test_default_car_selection_excludes_installed_mods(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mods_dir = root / "mods"
            mods_dir.mkdir()
            write_kspkg(
                mods_dir / "unselected.kspkg",
                ("preset_gt3rs_mech_1", "preset_new_mod_mech_1"),
                car_name="Unselected Test",
            )
            environment = {
                "ACEVO_MODS_DIR": str(mods_dir),
                "ACEVO_SERVER_INSTALL_DIR": str(root / "server"),
            }
            with patch.dict(os.environ, environment, clear=False):
                config = launch_payloads.load_config()
                server_doc, _season_doc, warnings = launch_payloads.build_documents({})

        selected_ids = {car["car_name"] for car in server_doc["allowed_cars_list_full"]}
        official_ids = {car["internal_name"] for car in config["cars_data"] if not car.get("is_mod")}
        self.assertEqual(warnings, [])
        self.assertEqual(selected_ids, official_ids)
        self.assertNotIn("preset_gt3rs_mech_1", selected_ids)
        self.assertNotIn("preset_new_mod_mech_1", selected_ids)


class ModManagerTests(unittest.TestCase):
    def setUp(self):
        kspkg.clear_cache()

    def test_storage_moves_legacy_contents_and_creates_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public = root / "data" / "mods"
            wine = root / "prefix" / "Saved Games" / "ACE-Server" / "mods"
            wine.mkdir(parents=True)
            write_kspkg(wine / "legacy.kspkg")
            (wine / "legacy.json").write_text('{"preserved": true}', encoding="utf-8")

            result = mods.prepare_storage(public, wine)

            self.assertEqual(result, public)
            self.assertTrue(wine.is_symlink())
            self.assertEqual(wine.resolve(), public.resolve())
            self.assertTrue((public / "legacy.kspkg").is_file())
            self.assertTrue((public / "legacy.json").is_file())

    def test_upload_streams_valid_package_and_cleans_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_kspkg(root / "source.kspkg")
            target = root / "mods"
            with source.open("rb") as stream, patch.object(mods, "_server_running", return_value=False):
                result = mods.upload(stream, source.stat().st_size, "installed.kspkg", target)

            self.assertEqual(result["installed"], "installed.kspkg")
            self.assertTrue((target / "installed.kspkg").is_file())
            self.assertFalse(list(target.glob("*.part")))

    def test_upload_keeps_new_mod_unselected_in_active_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_kspkg(root / "source.kspkg", ("preset_upload_mod",))
            target = root / "mods"
            config_path = root / "server_launcher.json"
            config_path.write_text(
                json.dumps(
                    {
                        "Event": {
                            "Cars": [
                                {"name": "preset_upload_mod", "IsSelected": True},
                                {"name": "preset_695b_mech_1", "IsSelected": True},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                source.open("rb") as stream,
                patch.dict(os.environ, {"ACEVO_MODS_DIR": str(target)}, clear=False),
                patch.object(mods, "_server_running", return_value=False),
            ):
                result = mods.upload(
                    stream,
                    source.stat().st_size,
                    "installed.kspkg",
                    target,
                    config_path,
                )

            self.assertEqual(result["deselected"], ["preset_upload_mod"])
            self.assertTrue((target / "installed.kspkg").is_file())
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            saved_cars = saved["Event"]["Cars"]
            self.assertNotIn("preset_upload_mod", {car["name"] for car in saved_cars})
            official = next(car for car in saved_cars if car["name"] == "preset_695b_mech_1")
            self.assertTrue(official["IsSelected"])

    def test_upload_aborts_on_short_stream_or_server_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "mods"
            with patch.object(mods, "_server_running", return_value=False):
                with self.assertRaisesRegex(mods.ModError, "ended before"):
                    mods.upload(io.BytesIO(b"short"), 10, "short.kspkg", target)

            source = write_kspkg(root / "source.kspkg")
            with source.open("rb") as stream, patch.object(mods, "_server_running", side_effect=[False, True]):
                with self.assertRaisesRegex(mods.ModError, "started during upload"):
                    mods.upload(stream, source.stat().st_size, "race.kspkg", target)

            self.assertFalse((target / "short.kspkg").exists())
            self.assertFalse((target / "race.kspkg").exists())
            self.assertFalse(list(target.glob(".*.part")))

    def test_running_server_and_duplicate_mod_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "mods"
            target.mkdir()
            source = write_kspkg(root / "source.kspkg", ("preset_duplicate",))
            write_kspkg(target / "existing.kspkg", ("preset_duplicate",))

            with source.open("rb") as stream, patch.object(mods, "_server_running", return_value=True):
                with self.assertRaisesRegex(mods.ModError, "stop the game server"):
                    mods.upload(stream, source.stat().st_size, "running.kspkg", target)

            with source.open("rb") as stream, patch.object(mods, "_server_running", return_value=False):
                with self.assertRaisesRegex(mods.ModError, "mechanical preset ID already exists"):
                    mods.upload(stream, source.stat().st_size, "duplicate.kspkg", target)

    def test_delete_removes_selected_mod_from_active_config_and_preserves_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "mods"
            target.mkdir()
            package = write_kspkg(target / "selected.kspkg", ("preset_selected_mod",))
            companion = target / "selected.json"
            companion.write_text('{"preserved": true}', encoding="utf-8")
            config_path = root / "server_launcher.json"

            with patch.dict(os.environ, {"ACEVO_MODS_DIR": str(target)}, clear=True):
                config = launch_payloads.load_config()
                form = config_io.launcher_to_form({}, config)
                form["cars"] = [
                    {"name": "preset_selected_mod", "is_selected": True},
                    {"name": "preset_695b_mech_1", "is_selected": True},
                ]
                config_path.write_text(json.dumps(config_io.form_to_launcher(form, config)), encoding="utf-8")

            with (
                patch.dict(os.environ, {"ACEVO_MODS_DIR": str(target)}, clear=True),
                patch.object(mods, "_server_running", return_value=False),
            ):
                result = mods.delete(package.name, config_path, target)

            self.assertEqual(result["deleted"], "selected.kspkg")
            self.assertEqual(result["deselected"], ["preset_selected_mod"])
            self.assertFalse(package.exists())
            self.assertTrue(companion.exists())
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            saved_cars = saved["Event"]["Cars"]
            self.assertNotIn("preset_selected_mod", {car["name"] for car in saved_cars})
            official = next(car for car in saved_cars if car["name"] == "preset_695b_mech_1")
            self.assertTrue(official["IsSelected"])

    def test_inventory_reports_invalid_manual_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "invalid.kspkg").write_bytes(b"broken")
            result = mods.inventory(root, launch_payloads.load_config())

        self.assertEqual(result["mods"][0]["status"], "invalid")
        self.assertTrue(result["mods"][0]["error"])

    def test_delete_rechecks_server_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "invalid.kspkg"
            package.write_bytes(b"broken")
            with patch.object(mods, "_server_running", side_effect=[False, True]):
                with self.assertRaisesRegex(mods.ModError, "server started"):
                    mods.delete(package.name, root / "server_launcher.json", root)

            self.assertTrue(package.exists())

    def test_delete_with_empty_active_config_does_not_treat_default_all_as_a_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = write_kspkg(root / "default-all.kspkg", ("preset_default_all_mod",))
            config_path = root / "server_launcher.json"
            config_path.touch()

            with (
                patch.dict(os.environ, {"ACEVO_MODS_DIR": str(root)}, clear=True),
                patch.object(mods, "_server_running", return_value=False),
            ):
                result = mods.delete(package.name, config_path, root)

            self.assertEqual(result["deleted"], "default-all.kspkg")
            self.assertEqual(result["deselected"], [])
            self.assertFalse(package.exists())


class ModLauncherFormTests(unittest.TestCase):
    def test_mod_entries_keep_exact_id_and_mod_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_kspkg(root / "form.kspkg", ("preset_form_mod",))
            with patch.dict(os.environ, {"ACEVO_MODS_DIR": str(root)}, clear=False):
                config = launch_payloads.load_config()
                form = config_io.launcher_to_form({}, config)
                form["cars"] = [{"name": "preset_form_mod", "is_selected": True}]
                launcher = config_io.form_to_launcher(form, config)

        car = next(entry for entry in launcher["Event"]["Cars"] if entry["name"] == "preset_form_mod")
        self.assertTrue(car["is_mod"])
        self.assertTrue(car["IsMod"])
        self.assertEqual(car["IsModText"], "MOD")
        self.assertEqual(car["name"], "preset_form_mod")


if __name__ == "__main__":
    unittest.main()
