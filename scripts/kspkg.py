"""Read the small amount of metadata needed from AC EVO ``.kspkg`` files.

The format details are based on the MIT-licensed ACEvo.Package project.  Packages keep
their file table in the final 64 MiB (current versions) or 32 MiB (older versions).
Both table entries and selected file contents can be XOR encrypted with Kunos' static key.

This module deliberately does not extract packages.  It only lists table entries and reads
the tiny ``.moddedcarcontent`` / ``.mechanicalcarpreset`` protobuf blobs needed to identify
mod cars and their mechanical preset IDs.
"""

from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

XOR_KEY = 0x9F9721A97D1135C1
XOR_BYTES = XOR_KEY.to_bytes(8, "little")
TABLE_SIZES = (0x4000000, 0x2000000)
ENTRY_SIZE = 0x100
ENTRY_STRUCT = struct.Struct("<224siHhQqq")
FLAG_DIRECTORY = 1
FLAG_ENCRYPTED = 1 << 8
MAX_METADATA_SIZE = 4 * 1024 * 1024
DEFAULT_MODS_DIR = Path(os.environ.get("ACEVO_MODS_DIR", "/data/mods"))


class KspkgError(ValueError):
    """Raised when a package cannot be read safely."""


@dataclass(frozen=True)
class PackageEntry:
    path: str
    flags: int
    size: int
    offset: int


@dataclass(frozen=True)
class ModVariant:
    preset_id: str
    display_name: str


@dataclass(frozen=True)
class ModCar:
    display_name: str
    variants: tuple[ModVariant, ...]
    runtime_name: str = ""


@dataclass(frozen=True)
class ModPackage:
    filename: str
    size: int
    cars: tuple[ModCar, ...]

    @property
    def preset_ids(self) -> tuple[str, ...]:
        return tuple(variant.preset_id for car in self.cars for variant in car.variants)


@dataclass(frozen=True)
class ModInventoryItem:
    filename: str
    size: int
    cars: tuple[ModCar, ...]
    preset_ids: tuple[str, ...]
    status: str
    error: str = ""
    official_preset_ids: tuple[str, ...] = ()


_cache_lock = threading.Lock()
_package_cache: dict[tuple[str, int, int], ModPackage] = {}
_runtime_name_cache: dict[tuple[str, int, int], dict[str, str]] = {}


def _xor(data: bytes) -> bytes:
    return bytes(value ^ XOR_BYTES[index % len(XOR_BYTES)] for index, value in enumerate(data))


def _decode_entry(raw: bytes, data_end: int, *, require_content_path: bool = False) -> tuple[PackageEntry | None, int]:
    if len(raw) != ENTRY_SIZE:
        raise KspkgError("truncated file table")
    name_buffer, _unknown, flags, name_length, path_hash, size, offset = ENTRY_STRUCT.unpack(_xor(raw))
    if path_hash == 0:
        return None, path_hash
    if name_length <= 0 or name_length > len(name_buffer):
        raise KspkgError("invalid file name length in file table")
    try:
        name = name_buffer[:name_length].decode("ascii")
    except UnicodeDecodeError as exc:
        raise KspkgError("non-ASCII file name in file table") from exc
    if require_content_path and not name.lower().startswith("content\\"):
        raise KspkgError("file table does not start with a content path")
    if size < 0 or offset < 0 or offset > data_end or size > data_end - offset:
        raise KspkgError(f"invalid offset or size for {name}")
    return PackageEntry(name, flags, size, offset), path_hash


def _table_size(handle, file_size: int) -> int:
    for table_size in TABLE_SIZES:
        if file_size <= table_size:
            continue
        handle.seek(file_size - table_size)
        raw = handle.read(ENTRY_SIZE)
        try:
            entry, _path_hash = _decode_entry(raw, file_size - table_size, require_content_path=True)
        except KspkgError:
            continue
        if entry is not None:
            return table_size
    raise KspkgError("could not detect a supported KSPKG file table")


def read_entries(path: Path) -> tuple[PackageEntry, ...]:
    """Validate and return a package file table without loading the whole table into RAM."""
    try:
        file_size = path.stat().st_size
        with path.open("rb") as handle:
            table_size = _table_size(handle, file_size)
            table_start = file_size - table_size
            handle.seek(table_start)
            entries: list[PackageEntry] = []
            for _index in range(table_size // ENTRY_SIZE):
                entry, _path_hash = _decode_entry(handle.read(ENTRY_SIZE), table_start)
                if entry is None:
                    break
                entries.append(entry)
            else:
                raise KspkgError("file table has no terminator")
    except OSError as exc:
        raise KspkgError(f"cannot read package: {exc}") from exc
    if not entries:
        raise KspkgError("empty KSPKG file table")
    return tuple(entries)


def _read_entry(path: Path, entry: PackageEntry) -> bytes:
    if entry.flags & FLAG_DIRECTORY:
        return b""
    if entry.size > MAX_METADATA_SIZE:
        raise KspkgError(f"metadata file is unexpectedly large: {entry.path}")
    try:
        with path.open("rb") as handle:
            handle.seek(entry.offset)
            data = handle.read(entry.size)
    except OSError as exc:
        raise KspkgError(f"cannot read {entry.path}: {exc}") from exc
    if len(data) != entry.size:
        raise KspkgError(f"truncated metadata file: {entry.path}")
    return _xor(data) if entry.flags & FLAG_ENCRYPTED else data


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise KspkgError("invalid protobuf varint in car metadata")


def _protobuf_strings(data: bytes) -> dict[int, list[str]]:
    """Return printable top-level length-delimited protobuf fields.

    AC EVO metadata is protobuf, but the project intentionally avoids generated schemas and
    runtime dependencies.  We only need its top-level human-readable strings.
    """
    fields: dict[int, list[str]] = {}
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number, wire_type = key >> 3, key & 7
        if field_number <= 0:
            raise KspkgError("invalid protobuf field in car metadata")
        if wire_type == 0:
            _value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            offset += 8
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if length < 0 or end > len(data):
                raise KspkgError("truncated protobuf field in car metadata")
            raw = data[offset:end]
            offset = end
            try:
                value = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if value.isprintable() and value.strip():
                fields.setdefault(field_number, []).append(value.strip())
        elif wire_type == 5:
            offset += 4
        else:
            raise KspkgError("unsupported protobuf wire type in car metadata")
        if offset > len(data):
            raise KspkgError("truncated protobuf value in car metadata")
    return fields


def _car_root(internal_path: str) -> str:
    parts = internal_path.replace("/", "\\").split("\\")
    if len(parts) >= 3 and parts[0].lower() == "content" and parts[1].lower() == "cars":
        return "\\".join(parts[:3]).lower()
    parent = str(PureWindowsPath(internal_path).parent)
    return parent.lower()


def _runtime_name(root: str) -> str:
    return PureWindowsPath(root).name


def _fallback_car_name(root: str, package_stem: str) -> str:
    tail = package_stem or PureWindowsPath(root).name
    return tail.replace("_", " ").strip() or package_stem


def _variant_label(fields: dict[int, list[str]], preset_id: str) -> str:
    candidates = [*(fields.get(2) or []), *(fields.get(3) or [])]
    generic = {"standard", "modded car base preset", "base preset", "default"}
    for candidate in candidates:
        if candidate.casefold().replace("_", " ") not in generic:
            return candidate
    for candidate in candidates:
        if candidate:
            return candidate
    return preset_id


def inspect_package(path: Path) -> ModPackage:
    entries = read_entries(path)
    car_names: dict[str, str] = {}
    variants: dict[str, list[tuple[str, str]]] = {}

    for entry in entries:
        lower = entry.path.lower()
        if lower.endswith(".moddedcarcontent"):
            fields = _protobuf_strings(_read_entry(path, entry))
            display_name = (fields.get(4) or [""])[0]
            if display_name:
                car_names[_car_root(entry.path)] = display_name

    seen_presets: set[str] = set()
    for entry in entries:
        if not entry.path.lower().endswith(".mechanicalcarpreset"):
            continue
        preset_id = PureWindowsPath(entry.path).stem
        if not preset_id or preset_id in seen_presets:
            raise KspkgError(f"duplicate or empty mechanical preset ID: {preset_id or '(empty)'}")
        seen_presets.add(preset_id)
        fields = _protobuf_strings(_read_entry(path, entry))
        variants.setdefault(_car_root(entry.path), []).append((preset_id, _variant_label(fields, preset_id)))

    if not seen_presets:
        raise KspkgError("package contains no mechanical car presets")

    cars: list[ModCar] = []
    multiple_variants = len(seen_presets) > 1
    for root, raw_variants in sorted(variants.items()):
        car_name = car_names.get(root) or _fallback_car_name(root, path.stem)
        rendered = []
        for preset_id, variant_name in sorted(raw_variants):
            display_name = f"{car_name} - {variant_name}" if multiple_variants else car_name
            rendered.append(ModVariant(preset_id=preset_id, display_name=display_name))
        cars.append(ModCar(display_name=car_name, variants=tuple(rendered), runtime_name=_runtime_name(root)))

    return ModPackage(filename=path.name, size=path.stat().st_size, cars=tuple(cars))


def inspect_package_cached(path: Path) -> ModPackage:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        cached = _package_cache.get(key)
    if cached is not None:
        return cached
    package = inspect_package(path)
    with _cache_lock:
        stale = [candidate for candidate in _package_cache if candidate[0] == key[0] and candidate != key]
        for candidate in stale:
            _package_cache.pop(candidate, None)
        _package_cache[key] = package
    return package


def clear_cache(path: Path | None = None) -> None:
    with _cache_lock:
        if path is None:
            _package_cache.clear()
            _runtime_name_cache.clear()
            return
        resolved = str(path.resolve())
        for key in [candidate for candidate in _package_cache if candidate[0] == resolved]:
            _package_cache.pop(key, None)
        for key in [candidate for candidate in _runtime_name_cache if candidate[0] == resolved]:
            _runtime_name_cache.pop(key, None)


def runtime_names_by_preset(path: Path) -> dict[str, str]:
    """Map mechanical preset IDs to AC EVO runtime car names using only the package table."""
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        cached = _runtime_name_cache.get(key)
    if cached is not None:
        return dict(cached)

    mapping = {
        PureWindowsPath(entry.path).stem: _runtime_name(_car_root(entry.path))
        for entry in read_entries(path)
        if entry.path.lower().endswith(".mechanicalcarpreset")
    }
    with _cache_lock:
        stale = [candidate for candidate in _runtime_name_cache if candidate[0] == key[0] and candidate != key]
        for candidate in stale:
            _runtime_name_cache.pop(candidate, None)
        _runtime_name_cache[key] = mapping
    return dict(mapping)


def scan_mods(mods_dir: Path | None = None, official_ids: set[str] | None = None) -> tuple[ModInventoryItem, ...]:
    """Inspect top-level KSPKGs and mark preset collisions without touching other files."""
    mods_dir = mods_dir or DEFAULT_MODS_DIR
    official_ids = official_ids or set()
    try:
        paths = sorted(
            (path for path in mods_dir.iterdir() if path.is_file() and path.suffix.lower() == ".kspkg"),
            key=lambda path: path.name.casefold(),
        )
    except FileNotFoundError:
        return ()
    except OSError as exc:
        return (ModInventoryItem("", 0, (), (), "invalid", f"cannot read mod directory: {exc}"),)

    parsed: list[tuple[Path, int, ModPackage | None, str]] = []
    owners: dict[str, list[str]] = {}
    for path in paths:
        try:
            size = path.stat().st_size
            package = inspect_package_cached(path)
            error = ""
            for preset_id in package.preset_ids:
                owners.setdefault(preset_id, []).append(path.name)
        except (KspkgError, OSError) as exc:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            package = None
            error = str(exc)
        parsed.append((path, size, package, error))

    inventory: list[ModInventoryItem] = []
    for path, size, package, error in parsed:
        if package is None:
            inventory.append(ModInventoryItem(path.name, size, (), (), "invalid", error))
            continue
        # Real mods such as Manthey intentionally reuse official preset IDs. Only multiple
        # installed KSPKG owners are ambiguous; official matches are catalog overlays.
        collisions = [preset_id for preset_id in package.preset_ids if len(owners.get(preset_id, ())) > 1]
        if collisions:
            sources = ", ".join(collisions)
            error = f"mechanical preset ID conflict: {sources}"
            status = "conflict"
        else:
            error = ""
            status = "ready"
        inventory.append(
            ModInventoryItem(
                path.name,
                package.size,
                package.cars,
                package.preset_ids,
                status,
                error,
                tuple(preset_id for preset_id in package.preset_ids if preset_id in official_ids),
            )
        )
    return tuple(inventory)


def mod_car_entries(inventory: tuple[ModInventoryItem, ...]) -> list[dict]:
    cars: list[dict] = []
    for package in inventory:
        if package.status != "ready":
            continue
        for car in package.cars:
            for variant in car.variants:
                cars.append(
                    {
                        "internal_name": variant.preset_id,
                        "display_name": variant.display_name,
                        "performance_indicator": None,
                        "property_1": None,
                        "property_2": None,
                        "property_3": None,
                        "is_mod": True,
                        "mod_file": package.filename,
                        "runtime_name": car.runtime_name,
                    }
                )
    return cars
