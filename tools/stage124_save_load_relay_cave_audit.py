"""Audit a mutually exclusive code-cave candidate for Save/Load observation relays."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256


RELAY_CAVE = 0x68CEA0
RELAY_LENGTH = 0x100
SAVE_CALL = 0x3FA7B4
SAVE_RESUME = 0x3FA7B8
LOAD_CALL = 0x3FAB60
LOAD_RESUME = 0x3FAB64
SAVE_CALL_BYTES = bytes.fromhex("37090A94")
LOAD_CALL_BYTES = bytes.fromhex("68080A94")
RESERVED_RELAYS = (
    (0x68CBE0, 0x68CC00), (0x68CC00, 0x68CC20), (0x68CC20, 0x68CC40),
    (0x68CC40, 0x68CC80), (0x68CC80, 0x68CCC0), (0x68CCC0, 0x68CD00),
    (0x68CD00, 0x68CD40), (0x68CD40, 0x68CD80), (0x68CD80, 0x68CDC0),
    (0x68CDC0, 0x68CE00), (0x68CE00, 0x68CE60), (0x68CE60, 0x68CEA0),
)


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
    _require(data, SAVE_CALL, SAVE_CALL_BYTES, "Manager Save call")
    _require(data, LOAD_CALL, LOAD_CALL_BYTES, "Manager Load call")
    cave = data[RELAY_CAVE:RELAY_CAVE + RELAY_LENGTH]
    overlap = any(RELAY_CAVE < end and start < RELAY_CAVE + RELAY_LENGTH
                  for start, end in RESERVED_RELAYS)
    return {
        "schema_version": "stage124-save-load-relay-cave-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "save": {
            "call_address": f"{SAVE_CALL:08x}",
            "original_instruction": SAVE_CALL_BYTES.hex().upper(),
            "resume_target": f"{SAVE_RESUME:08x}",
            "postcall_contract": "callback may observe completion only after GameState::Save returns; no return value may be invented",
            "must_preserve_register": "caller-saved registers needed by the original tail must be saved and restored",
            "branch_reaches_cave": _branch_reachable(SAVE_CALL, RELAY_CAVE),
        },
        "load": {
            "call_address": f"{LOAD_CALL:08x}",
            "original_instruction": LOAD_CALL_BYTES.hex().upper(),
            "resume_target": f"{LOAD_RESUME:08x}",
            "postcall_contract": "callback must run after GameState::Load returns and before the original mov w23,w0",
            "must_preserve_register": "w0",
            "branch_reaches_cave": _branch_reachable(LOAD_CALL, RELAY_CAVE),
        },
        "relay_cave": {
            "offset": f"0x{RELAY_CAVE:x}",
            "length": f"0x{RELAY_LENGTH:x}",
            "zero_filled": cave == bytes(RELAY_LENGTH),
            "overlaps_current_relays": overlap,
            "mutually_exclusive_with_stage108_109": True,
            "planned_layout": "two 0x50-byte relay regions at 0x68cea0 and 0x68cef0; callback slot placement is intentionally not selected",
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "relay_bytes_not_designed_or_generated",
            "callback_register_and_simd_preservation_not_proven",
            "manager_liveness_and_postcall_timing_not_hardware_verified",
            "save_load_file_io_remains_prohibited",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage124_save_load_relay_cave_audit.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
