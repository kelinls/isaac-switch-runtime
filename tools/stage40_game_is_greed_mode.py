"""Write version-locked dynamic-symbol evidence for Game::IsGreedMode."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
SYMBOL = "_ZNK15IsaacRepentance4Game11IsGreedModeEv"
ENTRY = 0x350200
ENTRY_GUARD = "08058052E805A072086868B808791F12"
GOT_SLOT = 0xA9E4E0
JUMP_SLOT_RELOCATION = 1026


def write_plt_target(nro_path: Path, output_path: Path) -> dict[str, object]:
    """Authenticate the supported NRO and write its single IsGreedMode PLT target."""
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")

    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build_id")
    symbol = symbols.get(SYMBOL)
    if symbol is None or not symbol.is_defined or symbol.file_offset != ENTRY:
        raise ValueError("Game::IsGreedMode definition does not match the supported NRO")
    actual_guard = data[ENTRY:ENTRY + 16].hex().upper()
    if actual_guard != ENTRY_GUARD:
        raise ValueError("Game::IsGreedMode entry guard mismatch")

    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if relocation_build_id != BUILD_ID:
        raise ValueError("relocation table build_id mismatch")
    slots = [
        relocation for relocation in relocations.get(SYMBOL, [])
        if relocation.table == "jmprel" and relocation.relocation_type == JUMP_SLOT_RELOCATION
    ]
    if len(slots) != 1 or slots[0].offset != GOT_SLOT or slots[0].addend != 0:
        raise ValueError("Game::IsGreedMode must have one fixed JUMP_SLOT relocation")

    document: dict[str, object] = {
        "schema_version": "stage40-game-isgreedmode-plt-target-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "symbol": SYMBOL,
        "defined_entry": ENTRY,
        "entry_guard": ENTRY_GUARD,
        "got_slot": GOT_SLOT,
        "relocation_type": JUMP_SLOT_RELOCATION,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document
