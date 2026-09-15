"""Verify the real GameState::Save receiver and BufferedFileStream close chain."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols
from tools.stage114_managed_save_pipeline import (
    BUILD_ID,
    OPEN_SAVE_WRITE,
    SOURCE_SHA256,
    _call_evidence,
    _jump_slot_symbols,
)


GAME_STATE_SAVE = 0x36F3CC
OPEN_WRITE_CALL = 0x36F474
SAVE_RESULT_STORE = 0x36F478
OWNER_RECOVERY = 0x36F4B8
CLOSE_VIRTUAL = 0x36F4D0
OPEN_SAVE_WRITE_ENTRY = 0x4CC278
PARENT_DIRECTORY_CALL = 0x4CC2C0
CREATE_PARENT_DIRECTORIES = "_ZN4KAGE7Filesys19SaveDataManagerBase25create_parent_directoriesEPKc"
BUFFERED_CLOSE = "_ZN4KAGE7Filesys18BufferedFileStream5CloseEv"

SAVE_GUARD = bytes.fromhex("FF4301D1FD7B03A9FDC30091F44F04A9")
OPEN_RESULT_SEQUENCE = bytes.fromhex("E03900B000A044F9A3090C94801E00F9C00200B4")
OWNER_RECOVERY_SEQUENCE = bytes.fromhex("881E40F909E100D11F0100F1E003899A")
CLOSE_CALL_SEQUENCE = bytes.fromhex("080040F9083540F900013FD6")
PARENT_DIRECTORY_SEQUENCE = bytes.fromhex("E00314AAE10313AA18CF0694C0020036")


def _require(data: bytes, address: int, expected: bytes, label: str) -> None:
    if data[address:address + len(expected)] != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def _symbol(symbols: dict[str, object], name: str, address: int) -> None:
    symbol = symbols.get(name)
    if symbol is None or not symbol.is_defined or symbol.file_offset != address:
        raise ValueError(f"missing or mismatched symbol {name}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, relocations = parse_dynamic_relocations(data)
    symbol_build_id, symbols = parse_dynamic_symbols(data)
    if build_id != BUILD_ID or symbol_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    _symbol(symbols, OPEN_SAVE_WRITE, OPEN_SAVE_WRITE_ENTRY)
    _symbol(symbols, CREATE_PARENT_DIRECTORIES, 0x4CC32C)
    _symbol(symbols, BUFFERED_CLOSE, 0x4CD7E8)
    _require(data, GAME_STATE_SAVE, SAVE_GUARD, "GameState Save entry")
    _require(data, OPEN_WRITE_CALL - 0x08, OPEN_RESULT_SEQUENCE, "GameState Save OpenWrite")
    _require(data, OWNER_RECOVERY, OWNER_RECOVERY_SEQUENCE, "GameState Save owner recovery")
    _require(data, CLOSE_VIRTUAL - 0x08, CLOSE_CALL_SEQUENCE, "GameState Save Close virtual call")
    _require(data, PARENT_DIRECTORY_CALL - 0x08, PARENT_DIRECTORY_SEQUENCE, "OpenWrite parent directory")
    slots = _jump_slot_symbols(relocations)
    open_write = _call_evidence(data, slots, OPEN_WRITE_CALL, OPEN_SAVE_WRITE)
    parent = _call_evidence(data, slots, PARENT_DIRECTORY_CALL, CREATE_PARENT_DIRECTORIES)
    return {
        "schema_version": "stage121-save-manager-real-lifecycle-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "save_manager_openwrite": {
            "entry": f"{OPEN_SAVE_WRITE_ENTRY:08x}",
            "parent_directory_call": f"{PARENT_DIRECTORY_CALL:08x}",
            "parent_directory": parent,
            "returned_interface": "BufferedFileStream + 0x38",
        },
        "game_state_save": {
            "entry": f"{GAME_STATE_SAVE:08x}",
            "entry_guard": SAVE_GUARD.hex().upper(),
            "open_write_call": f"{OPEN_WRITE_CALL:08x}",
            "open_write": open_write,
            "open_result_slot": "owner + 0x38",
            "owner_recovery": "load [owner + 0x38] - 0x38",
            "owner_recovery_instruction_bytes": OWNER_RECOVERY_SEQUENCE.hex().upper(),
            "close_virtual_call": f"{CLOSE_VIRTUAL:08x}",
            "close_virtual_offset": "00000068",
            "close_target": "KAGE::Filesys::BufferedFileStream::Close",
            "close_instruction_bytes": CLOSE_CALL_SEQUENCE.hex().upper(),
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "runtime_manager_receiver_and_safe_save_hook_not_verified",
            "custom_filename_and_true_hardware_write_not_verified",
            "save_load_timing_and_readback_not_verified",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage121_save_manager_real_lifecycle.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
