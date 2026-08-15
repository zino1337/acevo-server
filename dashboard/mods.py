"""Small KSPKG-only mod manager used by the dashboard and container startup."""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from scripts import kspkg, launch_payloads as lp

UPLOAD_CHUNK_SIZE = 1024 * 1024
_mutation_lock = threading.Lock()


def _mods_dir() -> Path:
    return Path(os.environ.get("ACEVO_MODS_DIR", "/data/mods"))


def _wine_mods_dir() -> Path:
    return Path(
        os.environ.get(
            "ACEVO_WINE_MODS_DIR",
            "/data/server/steamapps/compatdata/4564210/pfx/drive_c/users/steamuser/Saved Games/ACE-Server/mods",
        )
    )


class ModError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def prepare_storage(mods_dir: Path | None = None, wine_mods_dir: Path | None = None) -> Path:
    """Create ``/data/mods`` and point the Wine Saved Games directory at it.

    Older installations may already contain a real Wine mods directory.  Its complete
    contents are moved as one directory when the public directory does not exist.  If both
    locations contain data, nothing is overwritten and startup fails with a useful error.
    """
    mods_dir = mods_dir or _mods_dir()
    wine_mods_dir = wine_mods_dir or _wine_mods_dir()
    mods_dir.parent.mkdir(parents=True, exist_ok=True)
    wine_mods_dir.parent.mkdir(parents=True, exist_ok=True)

    if wine_mods_dir.is_symlink():
        target = wine_mods_dir.resolve(strict=False)
        expected = mods_dir.resolve(strict=False)
        if target != expected:
            raise ModError(f"Wine mods path points to {target}, expected {expected}")
        mods_dir.mkdir(parents=True, exist_ok=True)
        return mods_dir

    if not mods_dir.exists() and wine_mods_dir.is_dir():
        wine_mods_dir.rename(mods_dir)
    else:
        mods_dir.mkdir(parents=True, exist_ok=True)

    if wine_mods_dir.exists():
        if not wine_mods_dir.is_dir():
            raise ModError(f"Wine mods path is not a directory: {wine_mods_dir}")
        legacy_entries = list(wine_mods_dir.iterdir())
        conflicts = [entry.name for entry in legacy_entries if (mods_dir / entry.name).exists()]
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ModError(f"cannot merge existing mod directories; duplicate names: {names}")
        for entry in legacy_entries:
            shutil.move(str(entry), str(mods_dir / entry.name))
        wine_mods_dir.rmdir()

    wine_mods_dir.symlink_to(mods_dir, target_is_directory=True)
    return mods_dir


def _safe_filename(value: object) -> str:
    name = str(value or "")
    if (
        not name
        or len(name.encode("utf-8")) > 240
        or name.startswith(".")
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 for char in name)
        or Path(name).suffix.lower() != ".kspkg"
    ):
        raise ModError("filename must be a plain .kspkg file name")
    return name


def _official_ids(cfg: dict) -> set[str]:
    return {car["internal_name"] for car in cfg.get("official_cars_data", cfg["cars_data"])}


def _serialize_car(car: kspkg.ModCar) -> dict:
    return {
        "display_name": car.display_name,
        "variants": [
            {"preset_id": variant.preset_id, "display_name": variant.display_name} for variant in car.variants
        ],
    }


def _serialize_item(item: kspkg.ModInventoryItem) -> dict:
    return {
        "filename": item.filename,
        "size": item.size,
        "cars": [_serialize_car(car) for car in item.cars],
        "preset_ids": list(item.preset_ids),
        "official_preset_ids": list(item.official_preset_ids),
        "variant_count": len(item.preset_ids),
        "status": item.status,
        "error": item.error,
    }


def inventory(mods_dir: Path | None = None, cfg: dict | None = None) -> dict:
    mods_dir = mods_dir or _mods_dir()
    cfg = cfg or lp.load_config()
    items = kspkg.scan_mods(mods_dir, _official_ids(cfg))
    return {
        "mods_dir": str(mods_dir),
        "mods": [_serialize_item(item) for item in items if item.filename],
        "total_size": sum(item.size for item in items if item.filename),
    }


def _server_running() -> bool:
    from . import server_control

    return bool(server_control.status()["running"])


def upload(
    stream,
    content_length: int,
    filename: object,
    mods_dir: Path | None = None,
    config_path: Path | None = None,
) -> dict:
    with _mutation_lock:
        return _upload(stream, content_length, filename, mods_dir, config_path)


def _upload(
    stream,
    content_length: int,
    filename: object,
    mods_dir: Path | None = None,
    config_path: Path | None = None,
) -> dict:
    name = _safe_filename(filename)
    if content_length <= 0:
        raise ModError("Content-Length is required and must be greater than zero", 411)
    if _server_running():
        raise ModError("stop the game server before installing mods", 409)

    mods_dir = mods_dir or _mods_dir()
    mods_dir.mkdir(parents=True, exist_ok=True)
    existing_names = {path.name.casefold() for path in mods_dir.iterdir() if path.is_file()}
    if name.casefold() in existing_names:
        raise ModError(f"a file named {name} is already installed", 409)
    try:
        free = shutil.disk_usage(mods_dir).free
    except OSError as exc:
        raise ModError(f"cannot check free storage: {exc}", 500) from exc
    if free < content_length:
        raise ModError("not enough free storage for this upload", 507)

    temporary = mods_dir / f".{name}.{uuid.uuid4().hex}.part"
    destination = mods_dir / name
    try:
        remaining = content_length
        with temporary.open("xb") as handle:
            while remaining:
                chunk = stream.read(min(UPLOAD_CHUNK_SIZE, remaining))
                if not chunk:
                    raise ModError("upload ended before Content-Length bytes were received")
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        package = kspkg.inspect_package(temporary)
        cfg = lp.load_config()
        existing = kspkg.scan_mods(mods_dir, _official_ids(cfg))
        existing_ids = {preset_id for item in existing for preset_id in item.preset_ids}
        collisions = sorted(set(package.preset_ids) & existing_ids)
        if collisions:
            raise ModError(f"mechanical preset ID already exists: {', '.join(collisions)}", 409)
        if _server_running():
            raise ModError("server started during upload; mod was not installed", 409)
        temporary.replace(destination)
        try:
            deselected = _remove_presets_from_active_config(set(package.preset_ids), config_path) if config_path else []
        except ModError:
            try:
                destination.unlink()
            except OSError:
                pass
            kspkg.clear_cache(destination)
            raise
        kspkg.clear_cache(temporary)
        kspkg.clear_cache(destination)
    except kspkg.KspkgError as exc:
        raise ModError(f"invalid KSPKG: {exc}") from exc
    except OSError as exc:
        raise ModError(f"cannot install mod: {exc}", 500) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    result = inventory(mods_dir)
    result["installed"] = name
    result["deselected"] = deselected
    return result


def _remove_presets_from_active_config(preset_ids: set[str], config_path: Path) -> list[str]:
    if not preset_ids or not config_path.is_file():
        return []
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(document, dict):
        return []
    event = document.get("Event")
    if not isinstance(event, dict) or not isinstance(event.get("Cars"), list):
        return []

    removed: list[str] = []
    cars: list[object] = []
    for car in event["Cars"]:
        name = str(car.get("name") or "") if isinstance(car, dict) else ""
        if name in preset_ids:
            removed.append(name)
        else:
            cars.append(car)
    if not removed:
        return []

    event["Cars"] = cars
    temporary = config_path.with_name(f".{config_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
        temporary.replace(config_path)
    except OSError as exc:
        raise ModError(f"cannot update active configuration: {exc}", 500) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return sorted(set(removed))


def delete(filename: object, config_path: Path, mods_dir: Path | None = None) -> dict:
    with _mutation_lock:
        return _delete(filename, config_path, mods_dir)


def _delete(filename: object, config_path: Path, mods_dir: Path | None = None) -> dict:
    name = _safe_filename(filename)
    if _server_running():
        raise ModError("stop the game server before deleting mods", 409)
    mods_dir = mods_dir or _mods_dir()
    target = mods_dir / name
    if not target.is_file():
        raise ModError("mod not found", 404)
    try:
        package = kspkg.inspect_package_cached(target)
        preset_ids = set(package.preset_ids)
    except (kspkg.KspkgError, OSError):
        preset_ids = set()
    if _server_running():
        raise ModError("server started while checking the mod; it was not deleted", 409)
    deselected = _remove_presets_from_active_config(preset_ids, config_path)
    try:
        target.unlink()
    except OSError as exc:
        raise ModError(f"cannot delete mod: {exc}", 500) from exc
    kspkg.clear_cache(target)
    result = inventory(mods_dir)
    result["deleted"] = name
    result["deselected"] = deselected
    return result


if __name__ == "__main__":
    prepare_storage()
