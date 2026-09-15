"""Lock the fixed Switch dataflow used to resolve the current logical room.

This is deliberately an audit, not a Runtime binding.  It records only the
two values passed by Level::GetCurrentRoomDesc to the original resolver, so a
later hardware diagnostic can test whether that pair is stable across a room
return or a teleport without treating a RoomDescriptor pointer as identity.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from nro_symbols import parse_dynamic_relocations


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
GET_CURRENT_ROOM_DESC = 0x3DC684
GET_CURRENT_ROOM_DESC_WINDOW = bytes.fromhex("08AB82524800A0720800088B010140B9020940B99A7D0A14")
ROOM_INDEX_OFFSET = 0x21558
DIMENSION_OFFSET = 0x21560
JUMP_SLOT = 1026
GET_ROOM_BY_IDX_SYMBOL = "_ZNK15IsaacRepentance5Level12GetRoomByIdxEii"


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _branch_target(offset: int, instruction: int) -> int:
    if instruction & 0x7C000000 != 0x14000000:
        raise ValueError("GetCurrentRoomDesc resolver tail branch is not B")
    return offset + (_sign_extend(instruction & 0x03FFFFFF, 26) << 2)


def _plt_got_slot(data: bytes, thunk: int) -> int:
    if thunk < 0 or thunk + 16 > len(data):
        raise ValueError("resolver PLT thunk outside NRO")
    adrp = int.from_bytes(data[thunk:thunk + 4], "little")
    ldr = int.from_bytes(data[thunk + 4:thunk + 8], "little")
    add = int.from_bytes(data[thunk + 8:thunk + 12], "little")
    branch = int.from_bytes(data[thunk + 12:thunk + 16], "little")
    if (adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211
            or add & 0xFFC003FF != 0x91000210 or branch & 0xFFFFFC1F != 0xD61F0000):
        raise ValueError("unsupported resolver PLT thunk")
    immediate = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
    page = (thunk & ~0xFFF) + (_sign_extend(immediate, 21) << 12)
    return page + ((ldr >> 10) & 0xFFF) * 8


def _symbol_at_thunk(data: bytes, thunk: int) -> str:
    _, relocations = parse_dynamic_relocations(data)
    slots = {
        item.offset: name
        for name, items in relocations.items()
        for item in items
        if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0
    }
    symbol = slots.get(_plt_got_slot(data, thunk))
    if symbol is None:
        raise ValueError("resolver PLT thunk has no unique JUMP_SLOT symbol")
    return symbol


def audit(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    if data[0x40:0x54].hex().upper() != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    actual = data[GET_CURRENT_ROOM_DESC:GET_CURRENT_ROOM_DESC + len(GET_CURRENT_ROOM_DESC_WINDOW)]
    if actual != GET_CURRENT_ROOM_DESC_WINDOW:
        raise ValueError("GetCurrentRoomDesc instruction window mismatch")
    branch_offset = GET_CURRENT_ROOM_DESC + 0x14
    branch = int.from_bytes(data[branch_offset:branch_offset + 4], "little")
    resolver = _branch_target(branch_offset, branch)
    symbol = _symbol_at_thunk(data, resolver)
    if symbol != GET_ROOM_BY_IDX_SYMBOL:
        raise ValueError(f"unexpected GetCurrentRoomDesc resolver: {symbol}")
    return {
        "schema_version": "stage103-current-room-key-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "current_room_descriptor": {
            "entry": f"0x{GET_CURRENT_ROOM_DESC:x}",
            "instruction_window": GET_CURRENT_ROOM_DESC_WINDOW.hex().upper(),
            "receiver": "Level* in x0",
            "room_index_offset": f"0x{ROOM_INDEX_OFFSET:x}",
            "dimension_offset": f"0x{DIMENSION_OFFSET:x}",
            "argument_registers": {"room_index": "w1", "dimension": "w2"},
            "resolver_tail_branch": f"0x{resolver:x}",
            "resolver": "Level::GetRoomByIdx",
            "resolver_symbol": symbol,
            "key_candidate": "(room_index, dimension)",
        },
        "pointer_identity": "blocked_descriptor_storage_can_be_copied",
        "candidate_status": "authorized_for_read_only_hardware_stability_diagnostic_only",
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "hardware_stability_not_verified",
            "first_entry_reentry_semantics_not_verified",
            "save_restore_and_restart_reset_not_verified",
        ],
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage103_room_key_audit.py <Repentance.nro> <output-json>")
    document = audit(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")


if __name__ == "__main__":
    main()
