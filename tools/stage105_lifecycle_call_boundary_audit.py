"""Audit fixed-NRO lifecycle call boundaries without relying on a Ghidra project."""

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
JUMP_SLOT = 1026
GAME_LIFECYCLE_ENTRIES = {
    "new_game": ("_ZN15IsaacRepentance4Game5StartENS_11ePlayerTypeENS_10eChallengeENS_5SeedsENS0_11eDifficultyE", 0x34E518),
    "saved_game": ("_ZN15IsaacRepentance4Game19StartFromSavedStateERKNS_9GameStateE", 0x34EF8C),
}
MANAGER_LIFECYCLE_ENTRIES = {
    "new_game": ("_ZN15IsaacRepentance7Manager12StartNewGameENS_11ePlayerTypeENS_10eChallengeENS_5SeedsENS_4Game11eDifficultyE", 0x3FA174),
    "saved_game": ("_ZN15IsaacRepentance7Manager14StartSavedGameEv", 0x3FA32C),
    "restart": ("_ZN15IsaacRepentance7Manager11RestartGameENS_5SeedsEbb", 0x3FA458),
}


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _plt_got_slot(data: bytes, thunk: int) -> int | None:
    adrp = int.from_bytes(data[thunk:thunk + 4], "little")
    ldr = int.from_bytes(data[thunk + 4:thunk + 8], "little")
    if adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211:
        return None
    immediate = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
    page = (thunk & ~0xFFF) + (_sign_extend(immediate, 21) << 12)
    return page + ((ldr >> 10) & 0xFFF) * 8


def _branch_target(instruction: int, address: int) -> int | None:
    if instruction >> 26 != 0b100101:
        return None
    return address + (_sign_extend(instruction & 0x3FFFFFF, 26) << 2)


def audit(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")

    defined_starts = sorted({symbol.file_offset for symbol in symbols.values()
                             if symbol.is_defined and symbol.file_offset is not None})
    target_names = {name for name, _ in GAME_LIFECYCLE_ENTRIES.values()}
    target_entries = {entry: name for name, entry in GAME_LIFECYCLE_ENTRIES.values()}
    slot_symbols = {item.offset: name for name, entries in relocations.items() for item in entries
                    if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0}

    def require(spec: tuple[str, int]) -> None:
        name, entry = spec
        symbol = symbols.get(name)
        if symbol is None or not symbol.is_defined or symbol.file_offset != entry:
            raise ValueError(f"lifecycle symbol mismatch: {name}")

    for spec in (*GAME_LIFECYCLE_ENTRIES.values(), *MANAGER_LIFECYCLE_ENTRIES.values()):
        require(spec)

    manager_calls: list[dict[str, str]] = []
    for manager_key, (manager_name, start) in MANAGER_LIFECYCLE_ENTRIES.items():
        end = next(entry for entry in defined_starts if entry > start)
        for address in range(start, end, 4):
            target = _branch_target(int.from_bytes(data[address:address + 4], "little"), address)
            if target is None:
                continue
            direct_name = target_entries.get(target)
            if direct_name in target_names:
                manager_calls.append({"manager_event": manager_key, "callsite": f"0x{address:x}",
                                      "target": direct_name, "kind": "direct"})
                continue
            slot = _plt_got_slot(data, target)
            indirect_name = slot_symbols.get(slot) if slot is not None else None
            if indirect_name in target_names:
                manager_calls.append({"manager_event": manager_key, "callsite": f"0x{address:x}",
                                      "target": indirect_name, "kind": "plt"})

    return {
        "schema_version": "stage105-lifecycle-call-boundary-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "game_lifecycle_entries": {key: {"symbol": name, "entry": f"0x{entry:x}"}
                                   for key, (name, entry) in GAME_LIFECYCLE_ENTRIES.items()},
        "manager_lifecycle_entries": {key: {"symbol": name, "entry": f"0x{entry:x}"}
                                      for key, (name, entry) in MANAGER_LIFECYCLE_ENTRIES.items()},
        "manager_to_game_lifecycle_calls": manager_calls,
        "runtime_hook_authorized": False,
        "next_step": "audit_game_entry_receivers_and_safe_post_event_boundaries",
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage105_lifecycle_call_boundary_audit.py <Repentance.nro> <output-json>")
    document = audit(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")


if __name__ == "__main__":
    main()
