"""Export fixed-NRO gate evidence for the Instant Restart PC Mod."""

import hashlib
import json
import sys
from pathlib import Path

from nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"

ENTRIES = {
    "manager_is_action_triggered": (
        "_ZN15IsaacRepentance7Manager17IsActionTriggeredEjjPNS_6EntityE",
        0x3F9B7C,
    ),
    "manager_is_action_pressed": (
        "_ZN15IsaacRepentance7Manager15IsActionPressedEjjPNS_6EntityE",
        0x3F9B6C,
    ),
    "kage_is_action_triggered": (
        "_ZN4KAGE5Input11ManagerBase17IsActionTriggeredEjjPj",
        0x4F9A64,
    ),
    "kage_is_action_pressed": (
        "_ZN4KAGE5Input11ManagerBase15IsActionPressedEjjPj",
        0x4F9920,
    ),
    "console_run_command": (
        "_ZN15IsaacRepentance7Console10RunCommandERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEEPS7_PNS_13Entity_PlayerE",
        0x3D740,
    ),
    "manager_restart_game": (
        "_ZN15IsaacRepentance7Manager11RestartGameENS_5SeedsEbb",
        0x3FA458,
    ),
}


def export(nro_path: Path, output_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")

    native_entries: dict[str, object] = {}
    for key, (symbol_name, expected_offset) in ENTRIES.items():
        symbol = symbols.get(symbol_name)
        if symbol is None or not symbol.is_defined or symbol.file_offset != expected_offset:
            raise ValueError(f"{key} does not match fixed NRO")
        slots = [
            item.offset for item in relocations.get(symbol_name, [])
            if item.table == "jmprel" and item.relocation_type == 1026 and item.addend == 0
        ]
        native_entries[key] = {
            "symbol": symbol_name,
            "entry": f"{expected_offset:08x}",
            "jump_slots": [f"{slot:08x}" for slot in sorted(slots)],
        }

    exact_ascent_symbols = sorted(name for name in symbols if "IsAscent" in name)
    document: dict[str, object] = {
        "schema_version": "stage84-instant-restart-switch-gate-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "native_entries": native_entries,
        "lua_input_callback_abi": "not_present_in_switch_nro",
        "action_restart_reachability": "not_proven_by_static_nro",
        "level_is_ascent": "no_exact_dynamic_symbol" if not exact_ascent_symbols else "unexpected_exact_symbol_present",
        "exact_ascent_symbols": exact_ascent_symbols,
        "command_lua_binding": "not_proven_by_native_console_entry",
        "runtime_binding_authorized": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage84_instant_restart_switch_gate.py <Repentance.nro> <output-json>")
    export(Path(sys.argv[1]), Path(sys.argv[2]))
