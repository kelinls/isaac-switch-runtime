"""Audit the fixed Switch NRO contract for a read-only Game::ChangeRoom relay."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from nro_symbols import parse_dynamic_relocations


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
JUMP_SLOT = 1026
CALLSITE = 0x354050
CALLSITE_ORIGINAL = bytes.fromhex("FC990C94")
POST_CALL = 0x354054
POST_CALL_ORIGINAL = bytes.fromhex("E00313AA")
TAIL_CALL = 0x354068
TAIL_CALL_ORIGINAL = bytes.fromhex("AE750C14")
RELAY_CAVE = 0x68CE60
RELAY_LENGTH = 0x40
RELAY_SLOT = RELAY_CAVE + 0x30
LEVEL_CHANGE_ROOM_THUNK = 0x67A840
LEVEL_CHANGE_ROOM_SYMBOL = "_ZN15IsaacRepentance5Level10ChangeRoomEii"
LEVEL_UPDATE_THUNK = 0x671720
LEVEL_UPDATE_SYMBOL = "_ZN15IsaacRepentance5Level6UpdateEv"
RESERVED_RELAY_INTERVALS = (
    (0x68CBE0, 0x68CC00),
    (0x68CC00, 0x68CC20),
    (0x68CC20, 0x68CC40),
    (0x68CC40, 0x68CC80),
    (0x68CC80, 0x68CCC0),
    (0x68CCC0, 0x68CD00),
    (0x68CD00, 0x68CD40),
    (0x68CD40, 0x68CE00),
    (0x68CE00, 0x68CE60),
)


def _require(data: bytes, offset: int, expected: bytes, label: str) -> None:
    actual = data[offset:offset + len(expected)]
    if actual != expected:
        raise ValueError(f"{label} mismatch at {offset:#x}: expected={expected.hex()} actual={actual.hex()}")


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _plt_got_slot(data: bytes, thunk: int) -> int:
    adrp = int.from_bytes(data[thunk:thunk + 4], "little")
    ldr = int.from_bytes(data[thunk + 4:thunk + 8], "little")
    if adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211:
        raise ValueError(f"unsupported PLT thunk at {thunk:08x}")
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
    slot = _plt_got_slot(data, thunk)
    symbol = slots.get(slot)
    if symbol is None:
        raise ValueError(f"missing JUMP_SLOT target for PLT thunk {thunk:#x}")
    return symbol


def _overlaps(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


def audit(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    if data[0x40:0x54].hex().upper() != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    _require(data, CALLSITE, CALLSITE_ORIGINAL, "Game::ChangeRoom Level::ChangeRoom call")
    _require(data, POST_CALL, POST_CALL_ORIGINAL, "Game::ChangeRoom post-call Game receiver restore")
    _require(data, TAIL_CALL, TAIL_CALL_ORIGINAL, "Game::ChangeRoom Level::Update tail call")
    cave = data[RELAY_CAVE:RELAY_CAVE + RELAY_LENGTH]
    if len(cave) != RELAY_LENGTH:
        raise ValueError("relay cave exceeds source NRO")
    if RELAY_SLOT % 8:
        raise ValueError("relay callback slot is not naturally aligned")
    level_change_symbol = _symbol_at_thunk(data, LEVEL_CHANGE_ROOM_THUNK)
    level_update_symbol = _symbol_at_thunk(data, LEVEL_UPDATE_THUNK)
    if level_change_symbol != LEVEL_CHANGE_ROOM_SYMBOL:
        raise ValueError("unexpected Game::ChangeRoom callee")
    if level_update_symbol != LEVEL_UPDATE_SYMBOL:
        raise ValueError("unexpected Game::ChangeRoom tail callee")
    overlap = any(
        _overlaps(RELAY_CAVE, RELAY_CAVE + RELAY_LENGTH, start, end)
        for start, end in RESERVED_RELAY_INTERVALS
    )
    return {
        "schema_version": "stage101-change-room-post-call-relay-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "callsite": {
            "function": "Game::ChangeRoom",
            "offset": f"0x{CALLSITE:x}",
            "original_instruction": CALLSITE_ORIGINAL.hex().upper(),
            "callee": "Level::ChangeRoom",
            "plt_thunk": f"0x{LEVEL_CHANGE_ROOM_THUNK:x}",
            "symbol": level_change_symbol,
        },
        "post_call": {
            "offset": f"0x{POST_CALL:x}",
            "next_instruction": "mov x0, x19",
            "game_receiver_restored": data[POST_CALL:POST_CALL + 4] == POST_CALL_ORIGINAL,
            "tail_call_offset": f"0x{TAIL_CALL:x}",
            "tail_calls_level_update": data[TAIL_CALL:TAIL_CALL + 4] == TAIL_CALL_ORIGINAL,
            "tail_plt_thunk": f"0x{LEVEL_UPDATE_THUNK:x}",
            "tail_symbol": level_update_symbol,
        },
        "relay": {
            "cave_offset": f"0x{RELAY_CAVE:x}",
            "length": f"0x{RELAY_LENGTH:x}",
            "slot_offset": f"0x{RELAY_SLOT:x}",
            "cave_is_zero_filled": cave == bytes(RELAY_LENGTH),
            "does_not_overlap_existing_relays": not overlap,
            "callback_abi": "void(Game*)",
            "preserves_original_level_call": True,
            "returns_to_original_post_call": True,
        },
        "runtime_binding_authorized": False,
        "next_step": "build_read_only_diagnostic_only",
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage101_change_room_relay_audit.py <Repentance.nro> <output-json>")
    document = audit(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")


if __name__ == "__main__":
    main()
