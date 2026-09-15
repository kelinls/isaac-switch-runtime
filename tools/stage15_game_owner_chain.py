"""Validate fixed Game owner-chain anchors in the supported Switch NRO."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from tools.nro_symbols import parse_dynamic_symbols
from tools.stage14_game_provenance import (
    ANCHORS,
    TARGET_BUILD_ID,
    TARGET_SOURCE_SHA256,
)


OWNER_CHAIN_ANCHORS = {
    **ANCHORS,
    "manager_update": (
        "_ZN15IsaacRepentance7Manager6UpdateEv",
        0x3F8DB8,
        "FF4301D1FD7B01A9FD430091F71300F9",
    ),
}


def verify_owner_chain_nro(nro_path: Path) -> dict[str, dict[str, object]]:
    """Verify the supported original NRO and return fixed Stage 15 anchors."""
    return _verify_owner_chain_data(nro_path.read_bytes(), TARGET_SOURCE_SHA256)


def _verify_owner_chain_data(
    data: bytes, expected_source_sha256: str
) -> dict[str, dict[str, object]]:
    if hashlib.sha256(data).hexdigest() != expected_source_sha256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != TARGET_BUILD_ID:
        raise ValueError("unsupported NRO build ID")

    result: dict[str, dict[str, object]] = {}
    for key, (symbol, offset, expected_guard) in OWNER_CHAIN_ANCHORS.items():
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    args = parser.parse_args()
    print(verify_owner_chain_nro(args.nro))


if __name__ == "__main__":
    main()
