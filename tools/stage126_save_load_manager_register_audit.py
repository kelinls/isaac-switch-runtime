"""Audit manager register liveness at the Save/Load callsites."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256

SAVE_CALL = 0x3FA7B4
LOAD_CALL = 0x3FAB60
SAVE_CALL_BYTES = bytes.fromhex("37090A94")
LOAD_CALL_BYTES = bytes.fromhex("68080A94")
SAVE_MANAGER_SETUP = (0x3FA6DC, bytes.fromhex("F30300AA"), "x19")
LOAD_MANAGER_SETUP = (0x3FAB10, bytes.fromhex("F40300AA"), "x20")
SAVE_PRECALL = (0x3FA7AC, bytes.fromhex("E00314AA"), "x20", "GameState*")
LOAD_PRECALL = (0x3FAB5C, bytes.fromhex("E00313AA"), "x19", "GameState*")


def _require(data: bytes, address: int, expected: bytes, label: str) -> None:
    if data[address:address + len(expected)] != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    if data[0x40:0x54].hex().upper() != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    for address, expected, label in (
        (SAVE_CALL, SAVE_CALL_BYTES, "Save call"), (LOAD_CALL, LOAD_CALL_BYTES, "Load call"),
        (SAVE_MANAGER_SETUP[0], SAVE_MANAGER_SETUP[1], "Save manager setup"),
        (LOAD_MANAGER_SETUP[0], LOAD_MANAGER_SETUP[1], "Load manager setup"),
        (SAVE_PRECALL[0], SAVE_PRECALL[1], "Save precall receiver"),
        (LOAD_PRECALL[0], LOAD_PRECALL[1], "Load precall receiver"),
    ):
        _require(data, address, expected, label)
    return {
        "schema_version": "stage126-save-load-manager-register-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "save": {"manager_register": "x19", "manager_type": "IsaacRepentance::Manager*",
                  "not_save_data_manager": True, "manager_setup": "0x3fa6dc",
                  "call_receiver_register": "x20", "call_receiver_role": "GameState*",
                  "manager_is_distinct_from_x0": True, "manager_is_callee_saved": True,
                  "postcall_can_reuse_manager_register": True},
        "load": {"manager_register": "x20", "manager_type": "IsaacRepentance::Manager*",
                 "not_save_data_manager": True, "manager_setup": "0x3fab10",
                 "call_receiver_register": "x19", "call_receiver_role": "GameState*",
                 "manager_is_distinct_from_x0": True, "manager_is_callee_saved": True,
                 "postcall_can_reuse_manager_register": True},
        "runtime_binding_authorized": False,
        "blocked_reasons": ["manager_pointer_liveness_only_static", "relay_bytes_not_generated",
                            "callback_slot_not_installed", "save_load_commit_and_readback_unverified"],
        "next_step": "generate_byte_locked_read_only_save_load_relays_using_x19_x20_manager_registers",
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage126_save_load_manager_register_audit.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
