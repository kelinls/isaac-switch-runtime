"""Audit the conservative AAPCS contract for Save/Load observation relays."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256

SAVE_CALL = 0x3FA7B4
SAVE_POSTCALL = 0x3FA7B8
LOAD_CALL = 0x3FAB60
LOAD_POSTCALL = 0x3FAB64
SAVE_POSTCALL_BYTES = bytes.fromhex("C10240B9")
LOAD_POSTCALL_BYTES = bytes.fromhex("F703002A")
RELAY_CAVE = 0x68CEA0
RELAY_LENGTH = 0x100
SAVE_RELAY = RELAY_CAVE
LOAD_RELAY = RELAY_CAVE + 0x50
CALLBACK_SLOT = RELAY_CAVE + 0xF0
FRAME_SIZE = 0x20
SLOT_X0_X1 = (0x00, 0x10)
SLOT_X30 = (0x10, 0x18)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _branch_reachable(source: int, destination: int) -> bool:
    delta = destination - source
    return delta % 4 == 0 and -0x08000000 <= delta <= 0x07FFFFFC


def _require(data: bytes, address: int, expected: bytes, label: str) -> None:
    if data[address:address + len(expected)] != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    if data[0x40:0x54].hex().upper() != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    _require(data, SAVE_POSTCALL, SAVE_POSTCALL_BYTES, "Save post-call")
    _require(data, LOAD_POSTCALL, LOAD_POSTCALL_BYTES, "Load post-call")
    if data[RELAY_CAVE:RELAY_CAVE + RELAY_LENGTH] != bytes(RELAY_LENGTH):
        raise ValueError("relay cave is not zero-filled")
    return {
        "schema_version": "stage125-save-load-relay-abi-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "postcall_contract": {
            "save": {
                "call": f"0x{SAVE_CALL:x}",
                "resume": f"0x{SAVE_POSTCALL:x}",
                "resume_instruction": SAVE_POSTCALL_BYTES.hex().upper(),
                "return_value": "not consumed after GameState::Save; callback must not invent one",
                "branch_reaches_relay": _branch_reachable(SAVE_CALL, SAVE_RELAY),
            },
            "load": {
                "call": f"0x{LOAD_CALL:x}",
                "resume": f"0x{LOAD_POSTCALL:x}",
                "resume_instruction": LOAD_POSTCALL_BYTES.hex().upper(),
                "return_value": "GameState::Load w0 must be restored before resume",
                "branch_reaches_relay": _branch_reachable(LOAD_CALL, LOAD_RELAY),
            },
        },
        "relay_layout": {
            "cave": f"0x{RELAY_CAVE:x}",
            "length": f"0x{RELAY_LENGTH:x}",
            "save_region": f"0x{SAVE_RELAY:x}..0x{SAVE_RELAY + 0x50:x}",
            "load_region": f"0x{LOAD_RELAY:x}..0x{LOAD_RELAY + 0x50:x}",
            "callback_slot": f"0x{CALLBACK_SLOT:x}",
            "callback_slot_aligned": CALLBACK_SLOT % 8 == 0,
            "regions_do_not_overlap": not _overlap((SAVE_RELAY, SAVE_RELAY + 0x50),
                                                     (LOAD_RELAY, LOAD_RELAY + 0x50)),
            "callback_slot_outside_code": CALLBACK_SLOT >= LOAD_RELAY + 0x50,
        },
        "aapcs_contract": {
            "callback_signature": "void callback(void* manager, uint32_t kind)",
            "callback_arguments": "x0=manager, w1=kind",
            "relay_saves": ["x0", "x1", "x30"],
            "load_restores": ["w0"],
            "callback_must_preserve": ["x19-x28", "x29", "v8-v15 (lower 64 bits)"],
            "stack_frame_size": f"0x{FRAME_SIZE:x}",
            "stack_alignment": "sp remains 16-byte aligned before BLR",
            "save_ranges_do_not_overlap": not _overlap(SLOT_X0_X1, SLOT_X30),
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "relay_bytes_not_generated",
            "callback_slot_not_installed",
            "manager_liveness_not_hardware_verified",
            "file_io_and_persistence_commit_remain_prohibited",
        ],
        "next_step": "generate_and_byte-lock_read-only_save_load_observation_relays",
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage125_save_load_relay_abi_audit.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
