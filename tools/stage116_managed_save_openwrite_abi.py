"""Verify the original SaveDataManager OpenWrite result and stream receivers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256
from tools.nro_symbols import parse_dynamic_relocations


# Original GameState::Save instructions; the return comes from the PLT call at +0x38.
OPEN_WRITE_RESULT_SEQUENCE = bytes.fromhex(
    "002F00B000A044F9E10315AA26990694"
    "800200F9A00200B5"
)
# Original non-null write path, including x0 reload from the local stream slot.
WRITE_RECEIVER_SEQUENCE = bytes.fromhex(
    "800240F9E10317AA22008052080040F9082140F9E303162A00013FD6"
)
# Commit path uses x19 as the stream object for the +0x78 virtual operation.
FINALIZE_RECEIVER_SEQUENCE = bytes.fromhex("680240F9E00313AA083D40F900013FD6")


def _require(data: bytes, address: int, expected: bytes, label: str) -> None:
    if data[address:address + len(expected)] != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, _ = parse_dynamic_relocations(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")

    _require(data, 0x4CB65C, OPEN_WRITE_RESULT_SEQUENCE, "open_write result")
    _require(data, 0x4CB6C8, WRITE_RECEIVER_SEQUENCE, "write receiver")
    _require(data, 0x4CCC6C, FINALIZE_RECEIVER_SEQUENCE, "finalize receiver")
    return {
        "schema_version": "stage116-managed-save-openwrite-abi-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "open_write": {
            "call_address": "004cb668",
            "return_register": "x0",
            "saved_to": "GameState.local_stream_slot_[x20+0x0]",
            "null_check": "cbnz_x0_to_write_path",
            "instruction_bytes": OPEN_WRITE_RESULT_SEQUENCE.hex().upper(),
        },
        "write_receiver": "load_[x20+0x0]_into_x0",
        "write": {
            "virtual_call_address": "004cb6e0",
            "vtable_offset": "00000040",
            "instruction_bytes": WRITE_RECEIVER_SEQUENCE.hex().upper(),
        },
        "finalize_receiver": "x19_stream_object",
        "finalize": {
            "virtual_call_address": "004ccc78",
            "vtable_offset": "00000078",
            "instruction_bytes": FINALIZE_RECEIVER_SEQUENCE.hex().upper(),
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "manager_receiver_and_stream_result_abi_not_yet_runtime_verified",
            "finalize_virtual_method_semantic_name_not_statically_proven",
            "write_commit_sequence_not_yet_true_hardware_verified",
        ],
    }
