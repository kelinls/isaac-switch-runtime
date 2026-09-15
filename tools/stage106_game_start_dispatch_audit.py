"""Authenticate the shared Manager dispatch that enters Game start routines."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols
except ModuleNotFoundError:
    from nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
DISPATCH = ("_ZN15IsaacRepentance7Manager18execute_start_gameEv", 0x3F9138)
START_CALLS = {
    "saved_game": ("_ZN15IsaacRepentance4Game19StartFromSavedStateERKNS_9GameStateE", 0x34EF8C, 0x3F92A4),
    "new_game": ("_ZN15IsaacRepentance4Game5StartENS_11ePlayerTypeENS_10eChallengeENS_5SeedsENS0_11eDifficultyE", 0x34E518, 0x3F93F8),
}


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _branch_target(instruction: int, address: int) -> int | None:
    if instruction >> 26 != 0b100101:
        return None
    return address + (_sign_extend(instruction & 0x3FFFFFF, 26) << 2)


def _plt_got_slot(data: bytes, thunk: int) -> int | None:
    adrp = int.from_bytes(data[thunk:thunk + 4], "little")
    ldr = int.from_bytes(data[thunk + 4:thunk + 8], "little")
    if adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211:
        return None
    immediate = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
    page = (thunk & ~0xFFF) + (_sign_extend(immediate, 21) << 12)
    return page + ((ldr >> 10) & 0xFFF) * 8


def _guard(data: bytes, offset: int) -> str:
    return data[offset:offset + 16].hex().upper()


def audit(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")

    dispatch_name, dispatch_entry = DISPATCH
    dispatch_symbol = symbols.get(dispatch_name)
    if dispatch_symbol is None or not dispatch_symbol.is_defined or dispatch_symbol.file_offset != dispatch_entry:
        raise ValueError("Manager::execute_start_game definition mismatch")

    slot_symbols = {item.offset: name for name, entries in relocations.items() for item in entries
                    if item.table == "jmprel" and item.relocation_type == 1026 and item.addend == 0}
    calls: list[dict[str, str]] = []
    for event, (name, target, callsite) in START_CALLS.items():
        symbol = symbols.get(name)
        if symbol is None or not symbol.is_defined or symbol.file_offset != target:
            raise ValueError(f"{event} Game start definition mismatch")
        instruction = int.from_bytes(data[callsite:callsite + 4], "little")
        thunk = _branch_target(instruction, callsite)
        if thunk is None or slot_symbols.get(_plt_got_slot(data, thunk)) != name:
            raise ValueError(f"{event} callsite no longer targets Game lifecycle entry")
        calls.append({
            "event": event,
            "callsite": f"0x{callsite:x}",
            "target_symbol": name,
            "target_entry": f"0x{target:x}",
            "receiver_provenance": (
                "saved_game: ldr x0,[x0] at 0x3f929c; new_game: mov x0,x20 at 0x3f93ec; "
                "both are local Manager::execute_start_game paths, not an authorized Runtime receiver"
            ),
        })

    return {
        "schema_version": "stage106-game-start-dispatch-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "dispatch": {"symbol": "Manager::execute_start_game", "entry": f"0x{dispatch_entry:x}",
                     "guard": _guard(data, dispatch_entry)},
        "game_start_calls": sorted(calls, key=lambda item: item["callsite"]),
        "runtime_hook_authorized": False,
        "next_step": "audit_post_call_control_flow_and_relay_cave_options",
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage106_game_start_dispatch_audit.py <Repentance.nro> <output-json>")
    document = audit(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")


if __name__ == "__main__":
    main()
