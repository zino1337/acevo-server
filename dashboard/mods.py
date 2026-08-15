"""Small KSPKG-only mod manager used by the dashboard and container startup."""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts import kspkg, launch_payloads as lp

STREAM_COPY_CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
UPLOAD_SESSION_TTL_SECONDS = 24 * 60 * 60
UPLOADS_DIRNAME = ".uploads"
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


class _UploadPaused(ModError):
    """An upload that can be finalized after the game server stops again."""


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
        cleanup_upload_sessions(mods_dir)
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
    cleanup_upload_sessions(mods_dir)
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
        "runtime_name": car.runtime_name,
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


def _parse_integer(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ModError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ModError(f"{label} must be an integer") from exc
    if parsed < minimum:
        raise ModError(f"{label} must be at least {minimum}")
    return parsed


def _installed_file(mods_dir: Path, name: str) -> Path | None:
    folded = name.casefold()
    return next((path for path in mods_dir.iterdir() if path.is_file() and path.name.casefold() == folded), None)


def _ensure_free_storage(mods_dir: Path, required: int) -> None:
    try:
        free = shutil.disk_usage(mods_dir).free
    except OSError as exc:
        raise ModError(f"cannot check free storage: {exc}", 500) from exc
    if free < required:
        raise ModError("not enough free storage for this upload", 507)


def _uploads_dir(mods_dir: Path) -> Path:
    return mods_dir / UPLOADS_DIRNAME


def _safe_upload_id(value: object) -> str:
    identifier = str(value or "").lower()
    try:
        parsed = uuid.UUID(identifier)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ModError("upload session not found", 404) from exc
    if str(parsed) != identifier:
        raise ModError("upload session not found", 404)
    return identifier


def _session_paths(upload_id: object, mods_dir: Path) -> tuple[str, Path, Path]:
    identifier = _safe_upload_id(upload_id)
    root = _uploads_dir(mods_dir)
    return identifier, root / f"{identifier}.json", root / f"{identifier}.part"


def _remove_upload_session(upload_id: object, mods_dir: Path) -> None:
    try:
        _, manifest, partial = _session_paths(upload_id, mods_dir)
    except ModError:
        return
    for path in (manifest, partial):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_upload_session(session: dict, mods_dir: Path) -> None:
    identifier, manifest, _ = _session_paths(session.get("upload_id"), mods_dir)
    root = manifest.parent
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{identifier}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(session, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(manifest)
    except OSError as exc:
        raise ModError(f"cannot save upload session: {exc}", 500) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _load_upload_session(upload_id: object, mods_dir: Path, now: float | None = None) -> dict:
    identifier, manifest, partial = _session_paths(upload_id, mods_dir)
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
        name = _safe_filename(document.get("filename"))
        total_size = _parse_integer(document.get("total_size"), "total size", 1)
        last_modified = _parse_integer(document.get("last_modified"), "last modified")
        updated_at = float(document.get("updated_at"))
        if (
            document.get("upload_id") != identifier
            or not math.isfinite(updated_at)
            or not partial.is_file()
            or partial.stat().st_size > total_size
        ):
            raise ValueError("invalid upload session")
    except (OSError, TypeError, ValueError, ModError):
        _remove_upload_session(identifier, mods_dir)
        raise ModError("upload session not found", 404) from None

    current_time = time.time() if now is None else now
    if current_time - updated_at >= UPLOAD_SESSION_TTL_SECONDS:
        _remove_upload_session(identifier, mods_dir)
        raise ModError("upload session expired", 404)
    return {
        "upload_id": identifier,
        "filename": name,
        "total_size": total_size,
        "last_modified": last_modified,
        "updated_at": updated_at,
    }


def _session_payload(session: dict, mods_dir: Path, *, complete: bool = False) -> dict:
    _, _, partial = _session_paths(session["upload_id"], mods_dir)
    offset = session["total_size"] if complete else partial.stat().st_size
    expires_at = (
        datetime.fromtimestamp(
            session["updated_at"] + UPLOAD_SESSION_TTL_SECONDS,
            timezone.utc,
        )
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "upload_id": session["upload_id"],
        "filename": session["filename"],
        "offset": offset,
        "total_size": session["total_size"],
        "chunk_size": MAX_UPLOAD_CHUNK_SIZE,
        "expires_at": expires_at,
        "complete": complete,
    }


def _cleanup_upload_sessions(mods_dir: Path, now: float) -> None:
    root = _uploads_dir(mods_dir)
    if not root.is_dir():
        return
    live_ids: set[str] = set()
    for manifest in root.glob("*.json"):
        identifier = manifest.stem.lower()
        try:
            session = _load_upload_session(identifier, mods_dir, now)
        except ModError:
            try:
                manifest.unlink(missing_ok=True)
                (root / f"{manifest.stem}.part").unlink(missing_ok=True)
            except OSError:
                pass
            continue
        live_ids.add(session["upload_id"])
    cutoff = now - UPLOAD_SESSION_TTL_SECONDS
    for path in root.iterdir():
        if path.suffix == ".part" and path.stem.lower() not in live_ids:
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
            except OSError:
                pass
        elif path.suffix == ".tmp":
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
            except OSError:
                pass


def cleanup_upload_sessions(mods_dir: Path | None = None, now: float | None = None) -> None:
    mods_dir = mods_dir or _mods_dir()
    mods_dir.mkdir(parents=True, exist_ok=True)
    with _mutation_lock:
        _cleanup_upload_sessions(mods_dir, time.time() if now is None else now)


def start_upload(
    filename: object,
    total_size: object,
    last_modified: object,
    config_path: Path,
    mods_dir: Path | None = None,
) -> dict:
    with _mutation_lock:
        name = _safe_filename(filename)
        size = _parse_integer(total_size, "total size", 1)
        modified = _parse_integer(last_modified, "last modified")
        if _server_running():
            raise ModError("stop the game server before installing mods", 409)

        mods_dir = mods_dir or _mods_dir()
        mods_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_upload_sessions(mods_dir, time.time())
        if _installed_file(mods_dir, name):
            raise ModError(f"a file named {name} is already installed", 409)

        matching: dict | None = None
        root = _uploads_dir(mods_dir)
        if root.is_dir():
            sessions: list[dict] = []
            for manifest in root.glob("*.json"):
                try:
                    session = _load_upload_session(manifest.stem, mods_dir)
                except ModError:
                    continue
                if session["filename"].casefold() == name.casefold():
                    sessions.append(session)
            for session in sorted(sessions, key=lambda item: item["updated_at"], reverse=True):
                if matching is None and session["total_size"] == size and session["last_modified"] == modified:
                    matching = session
                else:
                    _remove_upload_session(session["upload_id"], mods_dir)

        if matching is not None:
            _, _, partial = _session_paths(matching["upload_id"], mods_dir)
            _ensure_free_storage(mods_dir, size - partial.stat().st_size)
            if partial.stat().st_size == size:
                return _complete_upload_session(matching, mods_dir, config_path)
            matching["updated_at"] = time.time()
            _write_upload_session(matching, mods_dir)
            return _session_payload(matching, mods_dir)

        _ensure_free_storage(mods_dir, size)
        identifier = str(uuid.uuid4())
        root.mkdir(parents=True, exist_ok=True)
        partial = root / f"{identifier}.part"
        try:
            partial.touch(exist_ok=False)
        except OSError as exc:
            raise ModError(f"cannot start upload: {exc}", 500) from exc
        session = {
            "upload_id": identifier,
            "filename": name,
            "total_size": size,
            "last_modified": modified,
            "updated_at": time.time(),
        }
        try:
            _write_upload_session(session, mods_dir)
        except ModError:
            _remove_upload_session(identifier, mods_dir)
            raise
        return _session_payload(session, mods_dir)


def upload_status(upload_id: object, mods_dir: Path | None = None) -> dict:
    mods_dir = mods_dir or _mods_dir()
    with _mutation_lock:
        session = _load_upload_session(upload_id, mods_dir)
        return _session_payload(session, mods_dir)


def upload_chunk(
    stream,
    content_length: object,
    upload_id: object,
    offset: object,
    config_path: Path,
    mods_dir: Path | None = None,
) -> dict:
    with _mutation_lock:
        length = _parse_integer(content_length, "Content-Length", 1)
        requested_offset = _parse_integer(offset, "offset")
        if length > MAX_UPLOAD_CHUNK_SIZE:
            raise ModError("upload chunk exceeds 8 MiB", 413)
        if _server_running():
            raise ModError("stop the game server before installing mods", 409)

        mods_dir = mods_dir or _mods_dir()
        session = _load_upload_session(upload_id, mods_dir)
        _, _, partial = _session_paths(session["upload_id"], mods_dir)
        current_offset = partial.stat().st_size
        if requested_offset != current_offset:
            raise ModError(f"upload offset mismatch; server has {current_offset} bytes", 409)
        if current_offset + length > session["total_size"]:
            raise ModError("upload chunk exceeds the declared file size")
        _ensure_free_storage(mods_dir, length)

        remaining = length
        try:
            with partial.open("ab") as handle:
                while remaining:
                    chunk = stream.read(min(STREAM_COPY_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            session["updated_at"] = time.time()
            _write_upload_session(session, mods_dir)
        except OSError as exc:
            raise ModError(f"cannot save upload chunk: {exc}", 500) from exc
        if remaining:
            raise ModError("upload chunk ended before Content-Length bytes were received")

        if partial.stat().st_size < session["total_size"]:
            return _session_payload(session, mods_dir)
        return _complete_upload_session(session, mods_dir, config_path)


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
    if _installed_file(mods_dir, name):
        raise ModError(f"a file named {name} is already installed", 409)
    _ensure_free_storage(mods_dir, content_length)

    temporary = mods_dir / f".{name}.{uuid.uuid4().hex}.part"
    destination = mods_dir / name
    try:
        remaining = content_length
        with temporary.open("xb") as handle:
            while remaining:
                chunk = stream.read(min(STREAM_COPY_CHUNK_SIZE, remaining))
                if not chunk:
                    raise ModError("upload ended before Content-Length bytes were received")
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        deselected = _install_staged_package(temporary, destination, mods_dir, config_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    result = inventory(mods_dir)
    result["installed"] = name
    result["deselected"] = deselected
    return result


def _install_staged_package(staged: Path, destination: Path, mods_dir: Path, config_path: Path | None) -> list[str]:
    try:
        if destination.exists():
            raise ModError(f"a file named {destination.name} is already installed", 409)
        package = kspkg.inspect_package(staged)
        cfg = lp.load_config()
        existing = kspkg.scan_mods(mods_dir, _official_ids(cfg))
        existing_ids = {preset_id for item in existing for preset_id in item.preset_ids}
        collisions = sorted(set(package.preset_ids) & existing_ids)
        if collisions:
            raise ModError(f"mechanical preset ID already exists: {', '.join(collisions)}", 409)
        if _server_running():
            raise _UploadPaused("server started during upload; mod was not installed", 409)
        staged.replace(destination)
        try:
            deselected = _remove_presets_from_active_config(set(package.preset_ids), config_path) if config_path else []
        except ModError:
            try:
                destination.unlink()
            except OSError:
                pass
            kspkg.clear_cache(destination)
            raise
        kspkg.clear_cache(staged)
        kspkg.clear_cache(destination)
        return deselected
    except kspkg.KspkgError as exc:
        raise ModError(f"invalid KSPKG: {exc}") from exc
    except OSError as exc:
        raise ModError(f"cannot install mod: {exc}", 500) from exc


def _complete_upload_session(session: dict, mods_dir: Path, config_path: Path) -> dict:
    _, _, partial = _session_paths(session["upload_id"], mods_dir)
    destination = mods_dir / session["filename"]
    try:
        deselected = _install_staged_package(partial, destination, mods_dir, config_path)
    except _UploadPaused:
        session["updated_at"] = time.time()
        _write_upload_session(session, mods_dir)
        raise
    except ModError:
        _remove_upload_session(session["upload_id"], mods_dir)
        raise

    _remove_upload_session(session["upload_id"], mods_dir)
    result = inventory(mods_dir)
    result.update(_session_payload(session, mods_dir, complete=True))
    result["installed"] = session["filename"]
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
