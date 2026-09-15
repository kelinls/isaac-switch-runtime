"""Audit original save fragments without inferring an unsupported combined lifecycle."""

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
    COMMIT_SAVE,
    OPEN_SAVE_WRITE,
    SOURCE_SHA256,
    _call_evidence,
    _jump_slot_symbols,
)


IS_INITIALIZED = "_ZN4KAGE7Filesys19SaveDataManagerBase13IsInitializedEv"
INITIALIZATION_CALL = 0x4CB654
OPEN_WRITE_CALL = 0x4CB668
WRITE_VIRTUAL_CALL = 0x4CB6E0
FINALIZE_VIRTUAL_CALL = 0x4CCC78
COMMIT_CALL = 0x4CCC8C

# These are the original AArch64 instructions, not decoded names inferred from a decompiler.
WRITE_SEQUENCE = bytes.fromhex(
    "800240F9E10317AA22008052080040F9082140F9E303162A00013FD6"
)
FINALIZE_SEQUENCE = bytes.fromhex("680240F9E00313AA083D40F900013FD6")
COMMIT_SEQUENCE = bytes.fromhex("002F009000A044F9EDCC0694")


def _require_bytes(data: bytes, address: int, expected: bytes, label: str) -> None:
    actual = data[address:address + len(expected)]
    if actual != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    slot_symbols = _jump_slot_symbols(relocations)

    initialization_gate = _call_evidence(
        data, slot_symbols, INITIALIZATION_CALL, IS_INITIALIZED)
    open_write = _call_evidence(data, slot_symbols, OPEN_WRITE_CALL, OPEN_SAVE_WRITE)
    commit = _call_evidence(data, slot_symbols, COMMIT_CALL, COMMIT_SAVE)
    _require_bytes(data, WRITE_VIRTUAL_CALL - 0x18, WRITE_SEQUENCE, "stream write")
    _require_bytes(data, FINALIZE_VIRTUAL_CALL - 0x0C, FINALIZE_SEQUENCE, "stream finalize")
    _require_bytes(data, COMMIT_CALL - 0x08, COMMIT_SEQUENCE, "save commit")

    return {
        "schema_version": "stage115-managed-save-stream-lifecycle-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "initialization_gate": initialization_gate,
        "open_write": open_write,
        "write": {
            "virtual_call_address": f"{WRITE_VIRTUAL_CALL:08x}",
            "vtable_offset": "00000040",
            "arguments": ["stream", "payload", "item_size=1", "length"],
            "instruction_bytes": WRITE_SEQUENCE.hex().upper(),
        },
        "open_write_lifecycle": ["is_initialized", "open_write", "write"],
        "separate_finalize_commit_fragment": {
            "finalize": {
                "virtual_call_address": f"{FINALIZE_VIRTUAL_CALL:08x}",
                "vtable_offset": "00000078",
                "instruction_bytes": FINALIZE_SEQUENCE.hex().upper(),
                "semantic_name": "unknown_virtual_stream_operation",
            },
            "commit": commit | {"instruction_bytes": COMMIT_SEQUENCE.hex().upper()},
            "relation_to_openwrite_stream": "not_statically_proven",
        },
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "save_manager_and_stream_abi_not_yet_runtime_verified",
            "openwrite_stream_finalize_and_commit_link_not_statically_proven",
            "custom_file_write_and_commit_not_yet_true_hardware_verified",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage115_managed_save_stream_lifecycle.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
