"""Write version-locked direct NRO evidence for Starterr's native dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
JUMP_SLOT_RELOCATION = 1026

TARGETS = {
    "item_pool_get_collectible": {
        "symbol": "_ZN15IsaacRepentance8ItemPool14GetCollectibleENS0_13eItemPoolTypeEjjNS_16eCollectibleTypeE",
        "entry": 0x3C6350,
        "guard": "FF0304D1E84B00FDFD7B0AA9FD830291",
        "got_slot": 0xA9EBE8,
    },
    "rng_constructor": {
        "symbol": "_ZN15IsaacRepentance3RNGC1Ejj",
        "entry": 0x44E360,
        "guard": "FD7BBEA9F44F01A9FD030091F403022A",
        "got_slot": 0xA9DE78,
    },
    "rng_set_seed": {
        "symbol": "_ZN15IsaacRepentance3RNG7SetSeedEjj",
        "entry": 0x44E3C0,
        "guard": "010000B9E83200F0086541F989018052",
        "got_slot": 0xA9E420,
    },
    "rng_next": {
        "symbol": "_ZN15IsaacRepentance3RNG4NextEv",
        "entry": 0x44E464,
        "guard": "FD7BBEA9F30B00F9FD030091080040B9",
        "got_slot": 0xA9DEB0,
    },
}

MISSING_PC_GETTERS = {
    "game_get_room": "_ZN15IsaacRepentance4Game7GetRoomEv",
    "game_get_level": "_ZN15IsaacRepentance4Game8GetLevelEv",
    "game_get_item_pool": "_ZN15IsaacRepentance4Game11GetItemPoolEv",
    "game_get_treasure_room_visit_count": "_ZN15IsaacRepentance4Game25GetTreasureRoomVisitCountEv",
    "room_get_type": "_ZNK15IsaacRepentance4Room7GetTypeEv",
    "level_get_stage": "_ZNK15IsaacRepentance5Level8GetStageEv",
}


def _target_evidence(data: bytes, symbols: dict, relocations: dict, key: str) -> dict[str, object]:
    target = TARGETS[key]
    symbol = symbols.get(target["symbol"])
    if symbol is None or not symbol.is_defined or symbol.file_offset != target["entry"]:
        raise ValueError(f"{key} dynamic symbol does not match the supported NRO")
    actual_guard = data[target["entry"]:target["entry"] + 16].hex().upper()
    if actual_guard != target["guard"]:
        raise ValueError(f"{key} entry guard mismatch")
    slots = [
        relocation
        for relocation in relocations.get(target["symbol"], [])
        if relocation.table == "jmprel"
        and relocation.relocation_type == JUMP_SLOT_RELOCATION
        and relocation.addend == 0
    ]
    if len(slots) != 1 or slots[0].offset != target["got_slot"]:
        raise ValueError(f"{key} must have one fixed JUMP_SLOT relocation")
    return {
        "symbol": target["symbol"],
        "entry": target["entry"],
        "entry_guard": target["guard"],
        "got_slot": target["got_slot"],
        "relocation_type": JUMP_SLOT_RELOCATION,
    }


def write_plt_targets(nro_path: Path, output_path: Path) -> dict[str, object]:
    """Authenticate direct dependencies and record absent PC getter symbols."""
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build_id")
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if relocation_build_id != BUILD_ID:
        raise ValueError("relocation table build_id mismatch")

    missing = {}
    for key, symbol_name in MISSING_PC_GETTERS.items():
        if symbol_name in symbols:
            raise ValueError(f"{key} unexpectedly has a dynamic symbol")
        missing[key] = symbol_name

    document: dict[str, object] = {
        "schema_version": "starterr-direct-native-evidence-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "targets": {
            key: _target_evidence(data, symbols, relocations, key) for key in TARGETS
        },
        "missing_pc_getters": missing,
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "game_room_level_item_pool_getters_lack_unique_dynamic_entries",
            "mc_pre_get_collectible_trigger_and_override_path_unproven",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document
