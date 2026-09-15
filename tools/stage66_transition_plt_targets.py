"""Lock transition-method JUMP_SLOT relocations for the fixed Switch NRO."""

import hashlib
import json
import sys
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols

BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
JUMP_SLOT = 1026
TARGETS = {
    "level_change_room": ("_ZN15IsaacRepentance5Level10ChangeRoomEii", 0x3D9AAC, 0xAA3388),
    "level_load_room": ("_ZN15IsaacRepentance5Level9load_roomEv", 0x3D6CB0, 0xAA3F18),
}


def export(nro_path: Path, output_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    targets: dict[str, object] = {}
    for key, (symbol_name, entry, got_slot) in TARGETS.items():
        symbol = symbols.get(symbol_name)
        if symbol is None or not symbol.is_defined or symbol.file_offset != entry:
            raise ValueError(f"{key} symbol does not match fixed NRO")
        slots = [item for item in relocations.get(symbol_name, [])
                 if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0]
        if len(slots) != 1 or slots[0].offset != got_slot:
            raise ValueError(f"{key} must have one fixed JUMP_SLOT")
        targets[key] = {"symbol": symbol_name, "entry": f"{entry:08x}", "got_slot": f"{got_slot:08x}"}
    plt_symbols = []
    for symbol_name, entries in relocations.items():
        for item in entries:
            if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0:
                symbol = symbols.get(symbol_name)
                record = {"got_slot": f"{item.offset:08x}", "symbol": symbol_name}
                if symbol is not None and symbol.is_defined and symbol.file_offset is not None:
                    record["symbol_entry"] = f"{symbol.file_offset:08x}"
                plt_symbols.append(record)
    plt_symbols.sort(key=lambda item: (item["got_slot"], item["symbol"]))
    document = {"schema_version": "stage66-transition-plt-targets-v1", "build_id": BUILD_ID,
                "source_sha256": SOURCE_SHA256, "targets": targets, "plt_symbols": plt_symbols}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage66_transition_plt_targets.py <Repentance.nro> <output-json>")
    export(Path(sys.argv[1]), Path(sys.argv[2]))
