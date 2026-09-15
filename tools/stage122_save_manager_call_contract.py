"""Audit the original Save/Load call contract without authorizing Runtime reuse."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_symbols import parse_dynamic_relocations
from tools.stage114_managed_save_pipeline import (
    BUILD_ID,
    MANAGER_GLOBAL_SLOT,
    OPEN_SAVE_READ,
    OPEN_SAVE_WRITE,
    SOURCE_SHA256,
    _call_evidence,
    _jump_slot_symbols,
)


GAME_STATE_SAVE = 0x36F3CC
GAME_STATE_LOAD = 0x379224
SAVE_OPEN_WRITE_CALL = 0x36F474
LOAD_OPEN_READ_CALL = 0x379294
SAVE_MANAGER_LOAD = 0x36F46C
LOAD_MANAGER_LOAD = 0x37928C
SAVE_MANAGER_SEQUENCE = bytes.fromhex("E03900B000A044F9A3090C94")
LOAD_MANAGER_SEQUENCE = bytes.fromhex("803900F000A044F90BE20B94")


def _require(data: bytes, address: int, expected: bytes, label: str) -> None:
    if data[address:address + len(expected)] != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    _require(data, SAVE_MANAGER_LOAD, SAVE_MANAGER_SEQUENCE, "GameState Save manager")
    _require(data, LOAD_MANAGER_LOAD, LOAD_MANAGER_SEQUENCE, "GameState Load manager")
    slots = _jump_slot_symbols(relocations)
    return {
        "schema_version": "stage122-save-manager-call-contract-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "manager_global": {
            "slot": f"{MANAGER_GLOBAL_SLOT:08x}",
            "save_sequence": SAVE_MANAGER_SEQUENCE.hex().upper(),
            "load_sequence": LOAD_MANAGER_SEQUENCE.hex().upper(),
            "contract": "both original GameState operations materialize x0 from the same global slot immediately before their file-manager call",
        },
        "save": {
            "entry": f"{GAME_STATE_SAVE:08x}",
            "open_write": _call_evidence(data, slots, SAVE_OPEN_WRITE_CALL, OPEN_SAVE_WRITE),
            "filename_argument": "x1 is selected from GameState-owned string storage before x0 is loaded from manager global",
        },
        "load": {
            "entry": f"{GAME_STATE_LOAD:08x}",
            "open_read": _call_evidence(data, slots, LOAD_OPEN_READ_CALL, OPEN_SAVE_READ),
            "filename_argument": "x1 is selected from GameState-owned string storage before x0 is loaded from manager global",
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "global_manager_lifecycle_not_runtime_verified",
            "custom_relative_filename_provenance_not_verified",
            "write_readback_and_save_load_hook_timing_not_verified",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage122_save_manager_call_contract.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
