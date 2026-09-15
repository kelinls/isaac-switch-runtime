from dataclasses import dataclass
from pathlib import Path


TARGET_BUILD_ID = bytes.fromhex("91C73FDD575061318D68886316AFEAC72388B2AB") + bytes(12)


def layered_lua_runtime_sources(source_root: Path) -> list[Path]:
    """Host-test inputs for the Lua runtime's layered translation units."""
    source_root = Path(source_root)
    runtime_root = source_root.parent
    unit_root = runtime_root / "src"
    return [
        source_root / "lua_runtime.cpp",
        unit_root / "application" / "callback" / "callback_registry.cpp",
        unit_root / "application" / "callback" / "callback_dispatcher.cpp",
        unit_root / "interfaces" / "lua" / "api_catalog.cpp",
        unit_root / "interfaces" / "lua" / "owner_binding.cpp",
        unit_root / "interfaces" / "lua" / "mod_api.cpp",
        unit_root / "interfaces" / "lua" / "game_api.cpp",
        unit_root / "interfaces" / "lua" / "isaac_api.cpp",
        unit_root / "interfaces" / "lua" / "remaining_api.cpp",
        unit_root / "interfaces" / "lua" / "music_api.cpp",
        unit_root / "interfaces" / "lua" / "rng_api.cpp",
        unit_root / "interfaces" / "lua" / "input_api.cpp",
        unit_root / "interfaces" / "lua" / "font_api.cpp",
        unit_root / "interfaces" / "lua" / "color_api.cpp",
        unit_root / "interfaces" / "lua" / "vector_api.cpp",
        unit_root / "interfaces" / "lua" / "sprite_api.cpp",
        unit_root / "interfaces" / "lua" / "json_api.cpp",
    ]


def target_address(base: int, offset: int) -> int:
    return base + offset


def verify_bytes(expected: bytes, actual: bytes) -> bool:
    return len(expected) == len(actual) and expected == actual


def orig_after_commit(published_orig: int, commit_succeeded: bool, entry_changed: bool) -> int:
    # Once published, Orig remains callable even if commit observes a concurrent entry change.
    return published_orig


@dataclass(frozen=True)
class SyntheticModule:
    path: str
    build_id: bytes
    text_start: int = 0x7100000000
    text: bytes = b""
    text_size: int = 0
    rodata: bytes = b""


def heartbeat_frames(start: int, end: int, interval: int) -> list[int]:
    return [frame for frame in range(start, end + 1) if frame % interval == 0]


def format_event(event: str, fields: dict[str, str]) -> str:
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    return f"event={event}" + (f" {suffix}" if suffix else "")


def synthetic_module(path: str, build_id: bytes, *, valid_header: bool = True, mod0_in_rodata: bool = True) -> SyntheticModule:
    text_size = 0x100
    text = bytearray(0x60)
    mod0_offset = text_size + 0x54 if mod0_in_rodata else 0x20
    text[4:8] = (mod0_offset if valid_header else 0).to_bytes(4, "little")
    text[0x10:0x14] = (0x304F524E if valid_header else 0).to_bytes(4, "little")
    text[0x40:0x60] = build_id
    rodata = bytearray(max(len(encode_path_record(path)), 0x58))
    rodata[:len(encode_path_record(path))] = encode_path_record(path)
    if valid_header and mod0_in_rodata:
        rodata[0x54:0x58] = (0x30444F4D).to_bytes(4, "little")
    elif valid_header:
        text.extend(b"\x00" * (0x24 - len(text)))
        text[0x20:0x24] = (0x30444F4D).to_bytes(4, "little")
    return SyntheticModule(path=path, build_id=build_id, text=bytes(text), text_size=text_size, rodata=bytes(rodata))


def find_target(module: SyntheticModule, expected_build_id: bytes):
    path = read_path_record(module.rodata)
    if path is None or path.replace("\\", "/").rsplit("/", 1)[-1] != "Repentance.nrs":
        return None
    if len(module.text) < 0x60 or module.text[0x10:0x14] != b"NRO0":
        return None
    mod0_offset = int.from_bytes(module.text[4:8], "little")
    mod0_rodata_offset = mod0_offset - module.text_size
    if mod0_offset < 8 or mod0_rodata_offset < 0 or mod0_rodata_offset + 4 > len(module.rodata):
        return None
    if module.rodata[mod0_rodata_offset:mod0_rodata_offset + 4] != b"MOD0":
        return None
    if module.text[0x40:0x60] != expected_build_id:
        return None
    return module


def find_target_status(module: SyntheticModule, expected_build_id: bytes):
    path = read_path_record(module.rodata)
    if path is None or path.replace("\\", "/").rsplit("/", 1)[-1] != "Repentance.nrs":
        return "NotFound", None
    if len(module.text) < 0x60 or module.text[0x10:0x14] != b"NRO0":
        return "NotFound", None
    mod0_offset = int.from_bytes(module.text[4:8], "little")
    mod0_rodata_offset = mod0_offset - module.text_size
    if mod0_offset < 8 or mod0_rodata_offset < 0 or mod0_rodata_offset + 4 > len(module.rodata):
        return "NotFound", None
    if module.rodata[mod0_rodata_offset:mod0_rodata_offset + 4] != b"MOD0":
        return "NotFound", None
    if module.text[0x40:0x60] != expected_build_id:
        return "BuildMismatch", module
    return "Found", module


def scan_targets(modules: list[SyntheticModule], expected_build_id: bytes):
    build_mismatch = None
    for module in modules:
        status, candidate = find_target_status(module, expected_build_id)
        if status == "Found":
            return status, candidate
        if status == "BuildMismatch":
            build_mismatch = candidate
    if build_mismatch is not None:
        return "BuildMismatch", build_mismatch
    return "NotFound", None


def find_target_compatible(module: SyntheticModule, expected_build_id: bytes, target_offset: int):
    status, candidate = find_target_status(module, expected_build_id)
    if status != "Found" or candidate is None:
        return None
    return candidate if target_window_valid(candidate.text_size, target_offset) else None


def step(state: str, title_ok: bool = False, module: str | None = None, hook_ok: bool = False, *,
         title_checked: bool | None = None, fatal_failure: bool = False,
         build_mismatch: bool = False, hook_attempted: bool | None = None) -> str:
    # The legacy four-argument call means title has already been queried.
    if title_checked is None:
        title_checked = True
    if hook_attempted is None:
        hook_attempted = module is not None
    if state in ("Ready", "Disabled"):
        return state
    if fatal_failure or build_mismatch or (title_checked and not title_ok):
        return "Disabled"
    if state == "Cold":
        return "WaitingForModule" if title_checked else "Cold"
    if module is None:
        return "WaitingForModule"
    if not hook_attempted or not hook_ok:
        return "Disabled"
    return "Ready"


def encode_path_record(path: str) -> bytes:
    encoded = path.encode("ascii")
    return b"\x00" * 4 + len(encoded).to_bytes(4, "little") + encoded


def read_path_record(record: bytes):
    if len(record) < 8:
        return None
    length = int.from_bytes(record[4:8], "little")
    payload = record[8:8 + length]
    if len(payload) != length or b"\x00" in payload:
        return None
    return payload.decode("ascii")


def target_window_valid(text_size: int, offset: int, address_limit: int = (1 << 64) - 1) -> bool:
    if offset < 0 or offset % 4 != 0 or offset > address_limit - 16:
        return False
    return offset + 16 <= text_size
