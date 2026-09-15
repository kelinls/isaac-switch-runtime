"""Export version-locked evidence for the data-backed challenge compatibility route."""

import hashlib
import json
import sys
import xml.etree.ElementTree as element_tree
from pathlib import Path

from nro_symbols import parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"

GAME_GET_CHALLENGE_PARAMS = "_ZNK15IsaacRepentance4Game18GetChallengeParamsEv"
MANAGER_GET_CHALLENGE_PARAMS = "_ZN15IsaacRepentance7Manager18GetChallengeParamsENS_10eChallengeE"
GAME_GET_CHALLENGE_PARAMS_ENTRY = 0x34EA68
MANAGER_GET_CHALLENGE_PARAMS_ENTRY = 0x3F9D94
GAME_GET_CHALLENGE_PARAMS_GUARD = "08629F52C804A072086868B8A8000034"
MANAGER_GET_CHALLENGE_PARAMS_GUARD = "08E88C526800A072091E8052086868F8"
GAME_CHALLENGE_ENUM_OFFSET = 0x26FA88
GAME_EMBEDDED_CHALLENGE_PARAMS_FLAG_OFFSET = 0x26FB10
GAME_EMBEDDED_CHALLENGE_PARAMS_OFFSET = 0x26FB20
MANAGER_CHALLENGE_PARAMS_STRIDE = 0xF0


def _parse_challenge_index(challenges_path: Path) -> dict[str, int]:
    root = element_tree.parse(challenges_path).getroot()
    if root.tag != "challenges" or root.get("version") != "1":
        raise ValueError("unsupported challenges.xml root or version")

    index: dict[str, int] = {}
    ids: set[int] = set()
    for item in root.findall("challenge"):
        name = item.get("name")
        raw_id = item.get("id")
        if not name or raw_id is None:
            raise ValueError("challenge entry lacks name or id")
        try:
            challenge_id = int(raw_id, 10)
        except ValueError as error:
            raise ValueError("challenge id is not an integer") from error
        if challenge_id <= 0 or name in index or challenge_id in ids:
            raise ValueError("challenge names and ids must be unique positive values")
        index[name] = challenge_id
        ids.add(challenge_id)
    if not index:
        raise ValueError("challenges.xml does not contain challenges")
    return index


def _require_entry(symbols: dict[str, object], data: bytes, symbol_name: str,
                   expected_entry: int, expected_guard: str) -> None:
    symbol = symbols.get(symbol_name)
    if symbol is None or not symbol.is_defined or symbol.file_offset != expected_entry:
        raise ValueError(f"{symbol_name} does not match fixed NRO")
    actual_guard = data[expected_entry:expected_entry + 16].hex().upper()
    if actual_guard != expected_guard:
        raise ValueError(f"{symbol_name} entry guard does not match fixed NRO")


def export(nro_path: Path, challenges_path: Path, output_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    _require_entry(symbols, data, GAME_GET_CHALLENGE_PARAMS, GAME_GET_CHALLENGE_PARAMS_ENTRY,
                   GAME_GET_CHALLENGE_PARAMS_GUARD)
    _require_entry(symbols, data, MANAGER_GET_CHALLENGE_PARAMS,
                   MANAGER_GET_CHALLENGE_PARAMS_ENTRY, MANAGER_GET_CHALLENGE_PARAMS_GUARD)
    challenge_index = _parse_challenge_index(challenges_path)

    document: dict[str, object] = {
        "schema_version": "stage95-challenge-compatibility-gate-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "challenge_resource": {
            "path": str(challenges_path),
            "sha256": hashlib.sha256(challenges_path.read_bytes()).hexdigest(),
            "format": "challenges_xml_v1",
            "entry_count": len(challenge_index),
            "name_to_id": challenge_index,
            "lookup_implementation": "runtime_owned_data_index",
            "custom_mod_challenge_requirement": "resource_overlay_or_merged_index_required",
        },
        "native_state_adapter": {
            "game_get_challenge_params": {
                "symbol": GAME_GET_CHALLENGE_PARAMS,
                "entry": f"{GAME_GET_CHALLENGE_PARAMS_ENTRY:08x}",
                "entry_guard": GAME_GET_CHALLENGE_PARAMS_GUARD,
                "receiver": "const_Game_x0",
                "current_challenge_enum": {
                    "offset": f"{GAME_CHALLENGE_ENUM_OFFSET:08x}",
                    "abi": "u32_passed_in_w1_to_Manager_GetChallengeParams",
                },
                "embedded_params": {
                    "flag_offset": f"{GAME_EMBEDDED_CHALLENGE_PARAMS_FLAG_OFFSET:08x}",
                    "params_offset": f"{GAME_EMBEDDED_CHALLENGE_PARAMS_OFFSET:08x}",
                    "behavior": "nonzero_flag_returns_Game_relative_params_pointer",
                },
            },
            "manager_get_challenge_params": {
                "symbol": MANAGER_GET_CHALLENGE_PARAMS,
                "entry": f"{MANAGER_GET_CHALLENGE_PARAMS_ENTRY:08x}",
                "entry_guard": MANAGER_GET_CHALLENGE_PARAMS_GUARD,
                "abi": "Manager_x0_eChallenge_w1_returns_params_pointer_x0",
                "params_stride": MANAGER_CHALLENGE_PARAMS_STRIDE,
            },
            "game_challenge_property_route": "read_current_enum_from_existing_managed_Game_owner_chain",
        },
        "pc_api_contract": {
            "Isaac.GetChallengeIdByName": "string_to_integer",
            "Game.Challenge": "Challenge_enum_property",
        },
        "compatibility_route": {
            "per_getter_address_hunt_required": False,
            "shared_native_bridge_count": 1,
            "shared_native_bridge": "current_Game_challenge_enum",
            "runtime_binding_authorized": False,
            "next_implementation_gate": "generic_mod_resource_overlay_and_merged_challenge_index",
            "blocked_reason": "candidate_custom_challenge_name_is_not_in_base_challenges_xml",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: stage95_challenge_compatibility_gate.py <Repentance.nro> <challenges.xml> <output-json>")
    export(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
