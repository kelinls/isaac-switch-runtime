"""Write version-locked PLT evidence for Music::Update."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
SYMBOL = "_ZN15IsaacRepentance5Music6UpdateEv"
ENTRY = 0x426C58
ENTRY_GUARD = "ED33B96DEB2B016DE923026DFD7B03A9"
JUMP_SLOT_RELOCATION = 1026
MUSIC_CONSTRUCTOR_SYMBOL = "_ZN15IsaacRepentance5MusicC1Ev"
MUSIC_CONSTRUCTOR_ENTRY = 0x423FBC
MUSIC_CONSTRUCTOR_GUARD = "FD7BBDA9F50B00F9FD030091F44F02A9"
MUSIC_DESTRUCTOR_SYMBOL = "_ZN15IsaacRepentance5MusicD1Ev"
MUSIC_DESTRUCTOR_ENTRY = 0x42417C
MUSIC_DESTRUCTOR_GUARD = "FF8302D1FD7B04A9FD030191FC6F05A9"


def write_plt_target(nro_path: Path, output_path: Path) -> dict[str, object]:
    """Authenticate Music::Update and record its version-locked JUMP_SLOT."""
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")

    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build_id")
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if relocation_build_id != BUILD_ID:
        raise ValueError("relocation table build_id mismatch")

    def version_locked_symbol(name: str, entry: int, guard: str) -> dict[str, object]:
        symbol = symbols.get(name)
        if symbol is None or not symbol.is_defined or symbol.file_offset != entry:
            raise ValueError(f"{name} definition does not match the supported NRO")
        if data[entry:entry + 16].hex().upper() != guard:
            raise ValueError(f"{name} entry guard mismatch")
        slots = [
            relocation for relocation in relocations.get(name, [])
            if relocation.table == "jmprel"
            and relocation.relocation_type == JUMP_SLOT_RELOCATION
            and relocation.addend == 0
        ]
        if len(slots) != 1:
            raise ValueError(f"{name} must have one JUMP_SLOT relocation")
        return {
            "symbol": name,
            "defined_entry": entry,
            "entry_guard": guard,
            "got_slot": slots[0].offset,
            "relocation_type": JUMP_SLOT_RELOCATION,
            "jump_slot_count": len(slots),
        }

    update = version_locked_symbol(SYMBOL, ENTRY, ENTRY_GUARD)
    constructor = version_locked_symbol(
        MUSIC_CONSTRUCTOR_SYMBOL, MUSIC_CONSTRUCTOR_ENTRY, MUSIC_CONSTRUCTOR_GUARD
    )
    destructor = version_locked_symbol(
        MUSIC_DESTRUCTOR_SYMBOL, MUSIC_DESTRUCTOR_ENTRY, MUSIC_DESTRUCTOR_GUARD
    )

    document: dict[str, object] = {
        "schema_version": "stage41-music-update-plt-target-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        **update,
        "music_constructor": constructor,
        "music_destructor": destructor,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return document
