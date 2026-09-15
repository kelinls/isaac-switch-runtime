"""Validate fixed Game and PauseScreen provenance anchors in one NRO build."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from tools.nro_symbols import parse_dynamic_symbols


TARGET_BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
TARGET_SOURCE_SHA256 = (
    "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
)

ANCHORS = {
    "game_ctor": ("_ZN15IsaacRepentance4GameC1Ev", 0x34B2DC, "FD7BBCA9F70B00F9FD030091F65702A9"),
    "game_dtor": ("_ZN15IsaacRepentance4GameD1Ev", 0x34C8FC, "FD7BBAA9FB0B00F9FD030091FA6702A9"),
    "game_init": ("_ZN15IsaacRepentance4Game4InitEv", 0x34DD50, "FFC301D1FD7B03A9FDC30091F85F04A9"),
    "game_process_input": ("_ZN15IsaacRepentance4Game12ProcessInputEv", 0x3515EC, "FD7BBCA9F70B00F9FD030091F65702A9"),
    "game_update": ("_ZN15IsaacRepentance4Game6UpdateEv", 0x351884, "FFC307D1FD7B19A9FD430691FCD300F9"),
    "game_is_paused": ("_ZNK15IsaacRepentance4Game8IsPausedEv", 0x3539DC, "FD7BBEA9F44F01A9FD03009108339F52"),
    "pause_process_input": ("_ZN15IsaacRepentance11PauseScreen12ProcessInputEv", 0x4313DC, "FD7BBAA9FC6F01A9FD030091FA6702A9"),
    "pause_show": ("_ZN15IsaacRepentance11PauseScreen4ShowEv", 0x43274C, "FF4301D1FD7B01A9FD430091F71300F9"),
    "pause_hide": ("_ZN15IsaacRepentance11PauseScreen4HideEv", 0x43296C, "FD7BBEA9F30B00F9FD030091080040B9"),
    "pause_update": ("_ZN15IsaacRepentance11PauseScreen6UpdateEv", 0x432A64, "FFC301D1FD7B01A9FD430091FC6F02A9"),
}


def verify_game_anchor_nro(nro_path: Path) -> dict[str, dict[str, object]]:
    """Verify the supported original NRO and return its ten fixed anchors."""
    return _verify_game_anchor_data(nro_path.read_bytes(), TARGET_SOURCE_SHA256)


def _verify_game_anchor_data(
    data: bytes, expected_source_sha256: str
) -> dict[str, dict[str, object]]:
    if hashlib.sha256(data).hexdigest() != expected_source_sha256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != TARGET_BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    result: dict[str, dict[str, object]] = {}
    for key, (symbol, offset, expected_guard) in ANCHORS.items():
        resolved = symbols.get(symbol)
        if resolved is None or not resolved.is_defined:
            raise ValueError(f"{key}: required dynamic symbol is missing or undefined")
        if resolved.file_offset != offset:
            raise ValueError(f"{key}: file_offset does not match")
        guard = data[offset:offset + 16].hex().upper()
        if guard != expected_guard:
            raise ValueError(f"{key}: entry guard does not match")
        result[key] = {
            "symbol": symbol,
            "file_offset": offset,
            "first_16_bytes": guard,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("nro", type=Path)
    args = parser.parse_args()
    print(verify_game_anchor_nro(args.nro))


if __name__ == "__main__":
    main()
