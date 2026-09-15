"""Resolve Stage75 Manager save/load PLT thunks from raw NRO relocations."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
JUMP_SLOT = 1026
MANAGER_CALLS = (
    ("SaveGameState", 0x3FA704, 0x66FCC0), ("SaveGameState", 0x3FA744, 0x66FCC0),
    ("SaveGameState", 0x3FA750, 0x67B360), ("SaveGameState", 0x3FA78C, 0x67AA20),
    ("SaveGameState", 0x3FA7A4, 0x67CC80), ("SaveGameState", 0x3FA7B4, 0x67CC90),
    ("SaveGameState", 0x3FA7C4, 0x67CCA0), ("LoadGameState", 0x3FAB1C, 0x67A300),
    ("LoadGameState", 0x3FAB30, 0x66FCC0), ("LoadGameState", 0x3FAB54, 0x671940),
    ("LoadGameState", 0x3FAB60, 0x67CD00), ("LoadGameState", 0x3FAB80, 0x671940),
    ("LoadGameState", 0x3FAB98, 0x66FB80), ("LoadGameState", 0x3FABA8, 0x670E70),
    ("LoadGameState", 0x3FABB8, 0x67CD10), ("LoadGameState", 0x3FABD4, 0x67CC80),
    ("LoadGameState", 0x3FABF4, 0x66FCC0), ("LoadGameState", 0x3FABFC, 0x67A300),
)


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _plt_got_slot(data: bytes, thunk: int) -> int:
    adrp, ldr = _u32(data, thunk), _u32(data, thunk + 4)
    if adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211:
        raise ValueError(f"unsupported PLT thunk at {thunk:08x}")
    immediate = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
    page = (thunk & ~0xFFF) + (_sign_extend(immediate, 21) << 12)
    return page + ((ldr >> 10) & 0xFFF) * 8


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    slot_symbols = {
        item.offset: symbol for symbol, items in relocations.items()
        for item in items if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0
    }
    targets = []
    for caller, call, thunk in MANAGER_CALLS:
        got_slot = _plt_got_slot(data, thunk)
        symbol = slot_symbols.get(got_slot)
        if symbol is None:
            raise ValueError(f"missing JUMP_SLOT symbol for thunk {thunk:08x}")
        targets.append({"caller": caller, "call_address": f"{call:08x}", "thunk": f"{thunk:08x}",
                        "got_slot": f"{got_slot:08x}", "symbol": symbol})
    game_edges = [item for item in targets if "IsaacRepentance4Game" in item["symbol"]]
    return {"schema_version": "stage76-manager-plt-symbols-v1", "build_id": BUILD_ID,
            "source_sha256": SOURCE_SHA256, "manager_call_targets": targets,
            "game_edges": game_edges, "status": "game_edges_found" if game_edges else "no_game_edges",
            "runtime_binding_authorized": False}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage76_manager_plt_symbols.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
