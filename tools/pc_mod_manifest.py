"""Read PC Isaac Mod input directories without changing their layout."""

import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import secrets
import xml.etree.ElementTree as ElementTree

from tools.png_to_pcx import PngFormatError, png_to_pcx


TITLE_ID = "010021C000B6A000"
ROMFS_MANIFEST_RELATIVE = Path("atmosphere") / "contents" / TITLE_ID / "romfs" / "isaac_mods"


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _read_metadata(metadata_path: Path) -> tuple[dict[str, object], str]:
    metadata_bytes = metadata_path.read_bytes()
    try:
        metadata_xml = metadata_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"metadata.xml must be strict UTF-8: {error}") from error
    try:
        root = ElementTree.fromstring(metadata_xml)
    except ElementTree.ParseError as error:
        raise ValueError(f"metadata.xml is malformed: {error}") from error
    if root.tag != "metadata":
        raise ValueError("metadata.xml root must be <metadata>")

    metadata: dict[str, object] = {}
    for child in root:
        if list(child):
            continue
        text = child.text or ""
        value: object = text if not child.attrib else {"attributes": dict(child.attrib), "text": text}
        previous = metadata.get(child.tag)
        if previous is None:
            metadata[child.tag] = value
        elif isinstance(previous, list):
            previous.append(value)
        else:
            metadata[child.tag] = [previous, value]

    # `<name>` is the only element a Mod cannot do without. `<id>` is the Steam
    # Workshop id: it is present in most Mods but **not all**, and real Mods that
    # ship fine on PC without it exist (`boildead/isaac-qualityonsprites-mod`, which
    # is what first hit this). Nothing in the runtime reads `metadata.xml` at all,
    # so rejecting such a Mod at packaging time would refuse a Mod the game itself
    # loads. `<directory>` is deliberately not required either: the RomFS directory
    # is the Mod's real directory name, which the loader and the manifest agree on.
    value = metadata.get("name")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metadata.xml requires non-empty <name>")
    return metadata, metadata_xml


def _inspect_mod(root: Path) -> tuple[dict[str, object], str]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Mod root must be an existing directory")

    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink() and not _inside(root, path.resolve()):
            raise ValueError(f"symlink escapes Mod root: {path.relative_to(root).as_posix()}")

    metadata_path = root / "metadata.xml"
    if not metadata_path.is_file():
        raise ValueError("metadata.xml is required")
    metadata, metadata_xml = _read_metadata(metadata_path)

    files = [path.relative_to(root).as_posix() for path in paths if path.is_file()]
    entry_script = "main.lua" if (root / "main.lua").is_file() else None
    return {
        "source_directory": root.name,
        "metadata": metadata,
        "entry_script": entry_script,
        # A PC Mod does not have to ship a script: the game mounts its `resources/`
        # and that is the whole Mod. This flag is the index-side name for that
        # shape ("资源型／无脚本"), and it is what the manifest generator keys on.
        "content_only": entry_script is None,
        "markers": {
            "disable.it": (root / "disable.it").is_file(),
            "update.it": (root / "update.it").is_file(),
        },
        "resource_directories": [name for name in ("resources", "content") if (root / name).is_dir()],
        "files": files,
    }, metadata_xml


def inspect_mod(root: Path) -> dict[str, object]:
    """Return a transparent, JSON-ready index of one existing PC Mod directory."""
    inspected, _ = _inspect_mod(root)
    return inspected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _discover_mods(mods_root: Path) -> list[dict[str, object]]:
    mods_root = mods_root.resolve()
    if not mods_root.is_dir():
        raise ValueError("mods root must be an existing directory")

    mod_directories: list[Path] = []
    for path in mods_root.iterdir():
        if path.is_symlink() and path.is_dir():
            raise ValueError(f"top-level Mod directory symlink is not allowed: {path}")
        if path.is_dir() and not path.is_symlink():
            mod_directories.append(path)
    mod_directories.sort(key=lambda path: os.fsencode(path.name))
    mods: list[dict[str, object]] = []
    for mod_root in mod_directories:
        try:
            inspected, metadata_xml = _inspect_mod(mod_root)
            # No `main.lua` is a supported shape, not an error: the Mod is
            # resources-only and the manifest simply omits `entry`. It is still
            # skipped only if it has nothing to mount either.
            entry = inspected["entry_script"]
            if entry is None and not inspected["resource_directories"]:
                raise ValueError(
                    "a Mod without main.lua must ship resources/ or content/ to have anything to mount"
                )

            files: list[dict[str, str]] = []
            inspected_files = inspected["files"]
            if not isinstance(inspected_files, list):
                raise ValueError("invalid file list")
            for relative_path in sorted(inspected_files, key=os.fsencode):
                if not isinstance(relative_path, str):
                    raise ValueError("invalid file path")
                file_path = Path(relative_path)
                if file_path.is_absolute() or ".." in file_path.parts:
                    raise ValueError(f"unsafe file path: {relative_path}")
                files.append({"path": relative_path, "sha256": _sha256(mod_root / file_path)})

            source_directory = inspected["source_directory"]
            metadata = inspected["metadata"]
            markers = inspected["markers"]
            content_only = inspected["content_only"]
            if (
                not isinstance(source_directory, str)
                or not isinstance(metadata, dict)
                or not isinstance(metadata_xml, str)
                or not isinstance(markers, dict)
                or not isinstance(content_only, bool)
            ):
                raise ValueError("invalid Mod model")
            version = metadata.get("version")
            mods.append(
                {
                    "directory": source_directory,
                    "metadata": metadata,
                    "metadata_xml": metadata_xml,
                    "version": version if isinstance(version, str) else None,
                    "entry": entry,
                    # Index-side marker: "资源型（无脚本）". `entry is None` is the
                    # same statement; this names it for readers of the index.
                    "content_only": content_only,
                    "enabled": not bool(markers.get("disable.it")),
                    "markers": markers,
                    "files": files,
                }
            )
        except (OSError, ValueError) as error:
            raise ValueError(f"direct Mod directory {mod_root}: {error}") from error
    return mods


def discover_mods(mods_root: Path) -> list[dict[str, object]]:
    """Return a stable, read-only model of every direct PC Mod directory."""
    return _discover_mods(mods_root)


def _input_fingerprint(mods: list[dict[str, object]]) -> bytes:
    """Serialize the complete discovery model for exact snapshot comparison."""
    return json.dumps(mods, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _revalidate_input_snapshot(mods_root: Path, expected_fingerprint: bytes) -> None:
    current_fingerprint = _input_fingerprint(_discover_mods(mods_root))
    if current_fingerprint != expected_fingerprint:
        raise ValueError(f"PC Mod input changed during synchronization: {mods_root}")


def _safe_posix_relative_path(value: object, description: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError(f"{description} must be a string")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError(f"unsafe {description}: {value}")
    return path


def _manifest_mod(mod: dict[str, object]) -> dict[str, object]:
    directory_path = _safe_posix_relative_path(mod.get("directory"), "Mod directory")
    if len(directory_path.parts) != 1:
        raise ValueError(f"unsafe Mod directory: {directory_path.as_posix()}")
    entry = mod.get("entry")
    if entry is not None:
        entry_path = _safe_posix_relative_path(entry, "entry path")
    files = mod.get("files")
    if not isinstance(files, list):
        raise ValueError(f"invalid file list: {directory_path.as_posix()}")

    manifest_files: list[dict[str, str]] = []
    for file in files:
        if not isinstance(file, dict):
            raise ValueError(f"invalid file record: {directory_path.as_posix()}")
        file_path = _safe_posix_relative_path(file.get("path"), "file path")
        sha256 = file.get("sha256")
        if not isinstance(sha256, str):
            raise ValueError(f"invalid sha256: {directory_path.as_posix()}/{file_path.as_posix()}")
        manifest_files.append(
            {
                "path": (PurePosixPath("mods") / directory_path / file_path).as_posix(),
                "sha256": sha256,
            }
        )

    manifest_mod = dict(mod)
    if entry is None:
        # Resource-only Mod: **no** `entry` key at all. The loader reads a missing
        # entry as "mount the content, initialize no Lua"; writing `null` instead
        # would be rejected by the on-device parser, which only accepts the key
        # when its value is a string.
        manifest_mod.pop("entry", None)
    else:
        manifest_mod["entry"] = (PurePosixPath("mods") / directory_path / entry_path).as_posix()
    manifest_mod["files"] = manifest_files
    return manifest_mod


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _validate_input_output_separation(mods_root: Path, output_root: Path) -> None:
    target = (output_root.absolute() / ROMFS_MANIFEST_RELATIVE).resolve()
    if _inside(mods_root, target) or _inside(target, mods_root):
        raise ValueError(f"PC mods root and fixed RomFS target overlap: {mods_root} / {target}")


def _validate_directory(path: Path, description: str, *, create: bool) -> None:
    if path.is_symlink():
        raise ValueError(f"{description} must not be a symlink: {path}")
    if _path_exists(path):
        if not path.is_dir():
            raise ValueError(f"{description} must be a directory: {path}")
    elif create:
        path.mkdir()
    else:
        raise ValueError(f"{description} changed before publish: {path}")


def _romfs_manifest_root(output_root: Path, *, create: bool = True) -> Path:
    """Return the fixed target after rejecting symlinks inside the output tree."""
    output_root = output_root.absolute()
    _validate_directory(output_root, "output root", create=create)

    current = output_root
    for part in ROMFS_MANIFEST_RELATIVE.parent.parts:
        current /= part
        _validate_directory(current, "RomFS publication directory", create=create)

    target = current / ROMFS_MANIFEST_RELATIVE.name
    if target.is_symlink():
        raise ValueError(f"RomFS manifest target must not be a symlink: {target}")
    if _path_exists(target) and not target.is_dir():
        raise ValueError(f"RomFS manifest target must be a directory: {target}")
    return target


def _create_unique_staging_directory(parent: Path, target_name: str) -> Path:
    for _ in range(100):
        staging = parent / f".{target_name}.staging.{secrets.token_hex(8)}"
        try:
            staging.mkdir(mode=0o700)
            return staging
        except FileExistsError:
            continue
    raise FileExistsError(f"could not create unique {target_name} staging directory")


def _unique_backup_path(parent: Path, target_name: str) -> Path:
    for _ in range(100):
        backup = parent / f".{target_name}.backup.{secrets.token_hex(8)}"
        if not _path_exists(backup):
            return backup
    raise FileExistsError(f"could not choose unique {target_name} backup path")


def _copy_file(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("rb") as source, destination_path.open("xb") as destination:
        for block in iter(lambda: source.read(65536), b""):
            destination.write(block)


def _sha256_file_at(path: Path) -> str:
    return _sha256(path)


def _remove_staging_tree(path: Path) -> None:
    """Remove only the staging path created by this invocation."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.exists():
        return
    for child in path.iterdir():
        _remove_staging_tree(child)
    path.rmdir()


def _publish_staged_directory(staging: Path, target: Path, output_root: Path) -> None:
    # Concurrent mutation by another process or file manager is unsupported.
    validated_target = _romfs_manifest_root(output_root, create=False)
    if validated_target != target:
        raise ValueError("RomFS manifest target changed before publish")

    backup: Path | None = None
    if target.exists():
        backup = _unique_backup_path(target.parent, target.name)
        target.replace(backup)
    try:
        staging.replace(target)
    except Exception:
        if backup is not None:
            backup.replace(target)
        raise


def write_romfs_manifest(mods_root: Path, output_root: Path) -> Path:
    """Synchronize Mods into the tool-owned ``isaac_mods`` subtree.

    The target subtree must not contain user-managed files. Concurrent changes by
    another synchronizer, process, or file manager are outside this API's contract.
    Existing symlinks in the configured output chain or target are rejected.
    """
    mods_root = mods_root.resolve()
    _validate_input_output_separation(mods_root, output_root)
    mods = discover_mods(mods_root)
    input_fingerprint = _input_fingerprint(mods)
    manifest_mods = [_manifest_mod(mod) for mod in mods]
    _revalidate_input_snapshot(mods_root, input_fingerprint)
    target = _romfs_manifest_root(output_root)
    staging = _create_unique_staging_directory(target.parent, target.name)

    try:
        generated_entries: list[dict[str, object]] = []
        for mod, manifest_mod in zip(mods, manifest_mods, strict=True):
            directory = _safe_posix_relative_path(mod.get("directory"), "Mod directory")
            files = mod.get("files")
            manifest_files = manifest_mod["files"]
            if not isinstance(files, list) or not isinstance(manifest_files, list):
                raise ValueError(f"invalid file list: {directory.as_posix()}")
            for file, manifest_file in zip(files, manifest_files, strict=True):
                if not isinstance(file, dict) or not isinstance(manifest_file, dict):
                    raise ValueError(f"invalid file record: {directory.as_posix()}")
                file_path = _safe_posix_relative_path(file.get("path"), "file path")
                expected_sha256 = file.get("sha256")
                output_path = manifest_file.get("path")
                if not isinstance(expected_sha256, str) or not isinstance(output_path, str):
                    raise ValueError(f"invalid file record: {directory.as_posix()}/{file_path.as_posix()}")

                source_path = mods_root / Path(directory.as_posix()) / Path(file_path.as_posix())
                destination_path = _safe_posix_relative_path(output_path, "output path")
                staged_file = staging.joinpath(*destination_path.parts)
                _copy_file(source_path, staged_file)
                if _sha256_file_at(staged_file) != expected_sha256:
                    raise ValueError(f"sha256 mismatch: {directory.as_posix()}/{file_path.as_posix()}")
                # Switch 版引擎按 `.pcx` 找贴图（`Manager::LoadImage` 会把 `.png` 改写掉），而解码器
                # 是按扩展名挑的：真机验证"只改请求名"不足以让画面出图，**磁盘上要有真实 `.pcx`**。
                # 因此这里给 `gfx/` 下的 PNG **额外**写一份同名 `.pcx`（原 PNG 保留：字体等按 `.png` 引用）。
                if (
                    file_path.suffix.lower() == ".png"
                    and "gfx" in file_path.parts[:-1]
                    # Mod 自己已经带了同名 `.pcx` 就不要再生成/覆盖（源文件优先）。
                    and not source_path.with_suffix(".pcx").exists()
                ):
                    try:
                        pcx_bytes = png_to_pcx(staged_file.read_bytes())
                    except (PngFormatError, OSError, ValueError):
                        pcx_bytes = None  # 形态不支持就跳过：Mod 仍能加载，只是这张贴图在 Switch 上不显示
                    if pcx_bytes is not None:
                        staged_pcx = staged_file.with_suffix(".pcx")
                        staged_pcx.write_bytes(pcx_bytes)
                        # 生成的 `.pcx` 也进信息列表（带 `generated` 标记）：设备上它丢了或坏了，
                        # 加载器的校验阶段就会报出来，而不是等到"贴图不显示"再查。
                        generated_entries.append(
                            {
                                "path": str(PurePosixPath(output_path).with_suffix(".pcx")),
                                "sha256": hashlib.sha256(pcx_bytes).hexdigest(),
                                "generated": True,
                            }
                        )

        if generated_entries:
            manifest_mods[0]["files"] = list(manifest_mods[0]["files"]) + generated_entries

        manifest_bytes = (
            json.dumps({"schema_version": 1, "mods": manifest_mods}, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        (staging / "manifest.json").write_bytes(manifest_bytes)
        _revalidate_input_snapshot(mods_root, input_fingerprint)
        _publish_staged_directory(staging, target, output_root)
    finally:
        _remove_staging_tree(staging)

    return target / "manifest.json"
