"""Audit the game-owned save-manager route before any managed-state persistence probe."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_symbols import parse_dynamic_relocations
from tools.stage76_manager_plt_symbols import JUMP_SLOT, _plt_got_slot


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
SAVE_CALL = 0x36F474
LOAD_CALL = 0x379294
MANAGER_GLOBAL_SLOT = 0xAAC940
OPEN_SAVE_WRITE = "_ZN4KAGE7Filesys19SaveDataManagerBase22OpenSaveFileForWritingEPKc"
OPEN_SAVE_READ = "_ZN4KAGE7Filesys15SaveDataManager22OpenSaveFileForReadingEPKc"
OPEN_WRITE_BUFFER = "_ZN4KAGE7Filesys18BufferedFileStream9OpenWriteEPKc"
WRITE_BUFFER = "_ZN4KAGE7Filesys18BufferedStreamBase13WriteToBufferEPKcii"
COMMIT_SAVE = "_ZN4KAGE7Filesys15SaveDataManager16commit_save_dataEv"


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _branch_target(data: bytes, call_address: int) -> int:
    instruction = _u32(data, call_address)
    if instruction & 0xFC000000 != 0x94000000:
        raise ValueError(f"expected BL at {call_address:08x}")
    immediate = instruction & 0x03FFFFFF
    if immediate & (1 << 25):
        immediate -= 1 << 26
    return call_address + (immediate << 2)


def _jump_slot_symbols(relocations: dict[str, list[object]]) -> dict[int, str]:
    return {
        item.offset: symbol
        for symbol, entries in relocations.items()
        for item in entries
        if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0
    }


def _thunk_for_symbol(data: bytes, slot_symbols: dict[int, str], wanted: str) -> tuple[int, int]:
    matches: list[tuple[int, int]] = []
    for address in range(0, len(data) - 12, 4):
        try:
            slot = _plt_got_slot(data, address)
        except ValueError:
            continue
        if slot_symbols.get(slot) == wanted:
            matches.append((address, slot))
    if len(matches) != 1:
        raise ValueError(f"expected one PLT thunk for {wanted}, found {len(matches)}")
    return matches[0]


def _call_evidence(data: bytes, slot_symbols: dict[int, str], call_address: int, symbol: str) -> dict[str, str]:
    thunk, got_slot = _thunk_for_symbol(data, slot_symbols, symbol)
    if _branch_target(data, call_address) != thunk:
        raise ValueError(f"call at {call_address:08x} does not target {symbol}")
    return {"call_address": f"{call_address:08x}", "thunk": f"{thunk:08x}",
            "got_slot": f"{got_slot:08x}", "symbol": symbol}


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    slot_symbols = _jump_slot_symbols(relocations)

    # GameState::Save materializes the singleton at 0xAAC940 immediately before OpenSaveFileForWriting.
    manager_sequence = data[0x36F438:0x36F444]
    expected_sequence = bytes.fromhex("E03900B000A044F9A8090C94")
    if manager_sequence != expected_sequence:
        raise ValueError("GameState::Save save-manager provenance instruction mismatch")

    save = _call_evidence(data, slot_symbols, SAVE_CALL, OPEN_SAVE_WRITE)
    load = _call_evidence(data, slot_symbols, LOAD_CALL, OPEN_SAVE_READ)
    write_thunk, write_slot = _thunk_for_symbol(data, slot_symbols, OPEN_WRITE_BUFFER)
    buffer_thunk, buffer_slot = _thunk_for_symbol(data, slot_symbols, WRITE_BUFFER)
    commit_thunk, commit_slot = _thunk_for_symbol(data, slot_symbols, COMMIT_SAVE)
    return {
        "schema_version": "stage114-managed-save-pipeline-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "save": save,
        "load": load,
        "manager_global": {
            "slot": f"{MANAGER_GLOBAL_SLOT:08x}",
            "provenance": "GameState::Save @ 0036f438 ADRP/LDR immediately before OpenSaveFileForWriting",
            "instruction_bytes": manager_sequence.hex().upper(),
        },
        "write_support": {
            "open_write_buffer": OPEN_WRITE_BUFFER,
            "open_write_buffer_thunk": f"{write_thunk:08x}",
            "open_write_buffer_got_slot": f"{write_slot:08x}",
            "write_buffer": WRITE_BUFFER,
            "write_buffer_thunk": f"{buffer_thunk:08x}",
            "write_buffer_got_slot": f"{buffer_slot:08x}",
            "commit": COMMIT_SAVE,
            "commit_thunk": f"{commit_thunk:08x}",
            "commit_got_slot": f"{commit_slot:08x}",
        },
        "feasibility": "candidate_requires_isolated_abi_lifecycle_probe",
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "save_manager_object_lifecycle_and_abi_not_yet_runtime_verified",
            "custom_file_stream_lifecycle_not_yet_runtime_verified",
            "save_load_postcall_hook_not_yet_authorized",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage114_managed_save_pipeline.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
