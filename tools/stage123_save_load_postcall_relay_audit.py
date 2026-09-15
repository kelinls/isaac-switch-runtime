"""Audit Manager save/load post-call sites without producing a relay or patch."""

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
    SOURCE_SHA256,
    _call_evidence,
    _jump_slot_symbols,
)


MANAGER_SAVE = 0x3FA658
MANAGER_LOAD = 0x3FAAF4
SAVE_CALL = 0x3FA7B4
SAVE_POSTCALL = 0x3FA7B8
LOAD_CALL = 0x3FAB60
LOAD_POSTCALL = 0x3FAB64
GAMESTATE_SAVE = "_ZN15IsaacRepentance9GameState4SaveEv"
GAMESTATE_LOAD = "_ZN15IsaacRepentance9GameState4LoadEPKc"
SAVE_CONTEXT = bytes.fromhex("C10240B973620091")
LOAD_CONTEXT = bytes.fromhex("E1030091E00313AA68080A94F703002A")


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
    _require(data, SAVE_POSTCALL, SAVE_CONTEXT, "Manager Save post-call")
    _require(data, LOAD_CALL - 8, LOAD_CONTEXT, "Manager Load call and return")
    slots = _jump_slot_symbols(relocations)
    return {
        "schema_version": "stage123-save-load-postcall-relay-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "save": {
            "manager_entry": f"{MANAGER_SAVE:08x}",
            "call": _call_evidence(data, slots, SAVE_CALL, GAMESTATE_SAVE),
            "call_address": f"{SAVE_CALL:08x}",
            "postcall": f"{SAVE_POSTCALL:08x}",
            "postcall_instruction": "ldr w1, [x22]",
            "return_value_use": "w0 has already been consumed before GameState::Save; post-call sequence does not consume a GameState::Save return value",
        },
        "load": {
            "manager_entry": f"{MANAGER_LOAD:08x}",
            "call": _call_evidence(data, slots, LOAD_CALL, GAMESTATE_LOAD),
            "call_address": f"{LOAD_CALL:08x}",
            "postcall": f"{LOAD_POSTCALL:08x}",
            "postcall_instruction": "mov w23, w0",
            "return_value_use": "w0 copied to w23",
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "no_version_locked_unused_relay_cave_selected",
            "postcall_register_preservation_contract_not_designed",
            "manager_liveness_and_observation_callback_scope_not_hardware_verified",
            "file_io_remains_prohibited",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage123_save_load_postcall_relay_audit.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
