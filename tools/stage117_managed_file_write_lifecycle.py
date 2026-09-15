"""Audit the game-owned File wrapper used for complete write lifecycle reuse."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols
from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256


CONSTRUCT = 0x4CCBC0
OPEN_WRITE = 0x4CCE38
WRITE = 0x4CD190
CLOSE = 0x4CD00C
DESTROY = 0x4CCBFC
FILE_SIZE = 0x60

CONSTRUCT_GUARD = bytes.fromhex("FD7BBEA9F30B00F9FD030091F30300AA")
OPEN_WRITE_GUARD = bytes.fromhex("FD7BBEA9F44F01A9FD030091F40300AA")
WRITE_GUARD = bytes.fromhex("5CCB061400E000D15ACB0614")
CLOSE_GUARD = bytes.fromhex("FD7BBEA9F44F01A9FD030091F30300AA")
DESTROY_GUARD = bytes.fromhex("FD7BBEA9F44F01A9FD030091082F00B0")


def _require(data: bytes, address: int, expected: bytes, label: str) -> None:
    if data[address:address + len(expected)] != expected:
        raise ValueError(f"{label} instruction mismatch at {address:08x}")


def _symbol(symbols: dict[str, object], name: str, address: int) -> None:
    found = symbols.get(name)
    if found is None or not found.is_defined or found.file_offset != address:
        raise ValueError(f"missing or mismatched symbol {name}")


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, _ = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    _require(data, CONSTRUCT, CONSTRUCT_GUARD, "File constructor")
    _require(data, OPEN_WRITE, OPEN_WRITE_GUARD, "File OpenWrite")
    _require(data, WRITE, WRITE_GUARD, "File Write")
    _require(data, CLOSE, CLOSE_GUARD, "File Close")
    _require(data, DESTROY, DESTROY_GUARD, "File destructor")
    _symbol(symbols, "_ZN4KAGE7Filesys4File9OpenWriteEPKc", OPEN_WRITE)
    _symbol(symbols, "_ZN4KAGE7Filesys4File5WriteEPKcii", WRITE)

    return {
        "schema_version": "stage117-managed-file-write-lifecycle-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "file_object_size": f"{FILE_SIZE:08x}",
        "construct": {"address": f"{CONSTRUCT:08x}", "instruction_bytes": CONSTRUCT_GUARD.hex().upper()},
        "open_write": {
            "address": f"{OPEN_WRITE:08x}",
            "symbol": "_ZN4KAGE7Filesys4File9OpenWriteEPKc",
            "mode": "6",
            "instruction_bytes": OPEN_WRITE_GUARD.hex().upper(),
        },
        "write": {
            "address": f"{WRITE:08x}",
            "symbol": "_ZN4KAGE7Filesys4File5WriteEPKcii",
            "arguments": ["file", "payload", "item_size", "length"],
            "instruction_bytes": WRITE_GUARD.hex().upper(),
        },
        "close": {
            "address": f"{CLOSE:08x}",
            "flushes_before_close": True,
            "instruction_bytes": CLOSE_GUARD.hex().upper(),
        },
        "destroy": {"address": f"{DESTROY:08x}", "instruction_bytes": DESTROY_GUARD.hex().upper()},
        "lifecycle": ["construct", "open_write", "write", "close", "destroy"],
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "true_hardware_write_not_yet_verified",
            "custom_save_filename_isolation_not_yet_verified",
            "save_data_manager_commit_boundary_not_yet_verified",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage117_managed_file_write_lifecycle.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
