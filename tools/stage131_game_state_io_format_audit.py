"""Audit GameStateIO save/load format gates without modifying the game."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_symbols import parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
SYMBOLS = {
    "game_state_write": ("_ZN15IsaacRepentance9GameState5writeERNS_11GameStateIOE", 0x368F54),
    "game_state_read": ("_ZN15IsaacRepentance9GameState4readERNS_11GameStateIOE", 0x370F84),
    "game_state_save": ("_ZN15IsaacRepentance9GameState4SaveEv", 0x36F3CC),
    "game_state_load": ("_ZN15IsaacRepentance9GameState4LoadEPKc", 0x379224),
    "checksum_ctor": ("_ZN15IsaacRepentance8ChecksumC1ENS0_13eChecksumTypeE", 0x37DC0),
    "checksum_add": ("_ZN15IsaacRepentance8Checksum3AddEPKvm", 0x37EA8),
    "checksum_generate": ("_ZN15IsaacRepentance8Checksum8GenerateENS0_13eChecksumTypeEPN4KAGE7Filesys10StreamBaseEii", 0x38008),
}


def _u32(data: bytes, address: int) -> int:
    return int.from_bytes(data[address:address + 4], "little")


def _branch_target(data: bytes, address: int) -> int | None:
    instruction = _u32(data, address)
    if instruction & 0xFC000000 != 0x94000000:
        return None
    immediate = instruction & 0x03FFFFFF
    if immediate & (1 << 25):
        immediate -= 1 << 26
    return address + (immediate << 2)


def _calls_in_window(data: bytes, start: int, end: int, known: dict[int, str]) -> list[dict[str, str]]:
    calls = []
    for address in range(start, end, 4):
        target = _branch_target(data, address)
        if target is None:
            continue
        calls.append({"call_site": f"{address:08x}", "target": f"{target:08x}",
                      "symbol": known.get(target, "unresolved")})
    return calls


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    resolved: dict[str, int] = {}
    for key, (symbol, fallback) in SYMBOLS.items():
        value = symbols.get(symbol)
        if value is None or not value.is_defined or value.file_offset != fallback:
            raise ValueError(f"symbol mismatch for {key}")
        resolved[key] = fallback
    # Calls in the game body target PLT thunks, while dynamic symbols point to
    # the local definitions. Keep the known thunk addresses explicit and
    # version-locked so a missing symbol cannot silently look like no checksum.
    known = {value: key for key, value in resolved.items()}
    known.update({
        0x67AD50: "checksum_ctor",
        0x6712D0: "checksum_add",
        0x66FDF0: "checksum_generate",
        0x67A300: "game_state_clear",
        0x67AEC0: "game_state_read",
    })
    write_calls = _calls_in_window(data, resolved["game_state_write"], resolved["game_state_write"] + 0x1000, known)
    read_calls = _calls_in_window(data, resolved["game_state_read"], resolved["game_state_read"] + 0x1600, known)
    save_calls = _calls_in_window(data, resolved["game_state_save"], resolved["game_state_save"] + 0x220, known)
    load_calls = _calls_in_window(data, resolved["game_state_load"], resolved["game_state_load"] + 0x180, known)
    checksum_calls = [call for call in save_calls + load_calls + write_calls + read_calls
                      if call["symbol"].startswith("checksum_")]
    return {
        "schema_version": "stage131-game-state-io-format-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "symbols": {key: f"{value:08x}" for key, value in resolved.items()},
        "write_path": {"calls": write_calls, "checksum_calls": [call for call in checksum_calls if call in write_calls or call in save_calls]},
        "read_path": {"calls": read_calls, "checksum_calls": [call for call in checksum_calls if call in read_calls or call in load_calls]},
        "format_gates": {
            "append_position_proven": False,
            "length_or_checksum_update_proven": False,
            "old_save_skip_proven": False,
            "failure_rollback_proven": False,
        },
        "result": "blocked",
        "blocked_reasons": [
            "GameStateIO call order alone does not prove an append position",
            "checksum and stream length treatment for a new block are not proven",
            "legacy save compatibility and failure rollback are not proven",
        ],
        "runtime_binding_authorized": False,
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage131_game_state_io_format_audit.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
