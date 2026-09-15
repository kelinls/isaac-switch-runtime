"""Export version-locked Game and Manager PLT targets from the original NRO."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations
from tools.stage15_game_owner_chain import OWNER_CHAIN_ANCHORS, verify_owner_chain_nro


PLT_KEYS = (
    "game_ctor",
    "game_dtor",
    "game_init",
    "game_update",
    "game_is_paused",
    "manager_update",
)


def write_plt_targets(nro_path: Path, output_path: Path) -> dict[str, object]:
    """Write only unique, version-locked AArch64 JUMP_SLOT targets."""
    anchors = verify_owner_chain_nro(nro_path)
    build_id, relocations = parse_dynamic_relocations(nro_path.read_bytes())
    targets: dict[str, object] = {}
    for key in PLT_KEYS:
        symbol = anchors[key]["symbol"]
        slots = [entry for entry in relocations.get(symbol, [])
                 if entry.table == "jmprel" and entry.relocation_type == 1026]
        if len(slots) != 1:
            raise ValueError(f"{key}: expected one JUMP_SLOT relocation")
        targets[key] = {
            "symbol": symbol,
            "got_slot": slots[0].offset,
            "relocation_type": slots[0].relocation_type,
        }
    document: dict[str, object] = {
        "schema_version": "stage16-game-plt-targets-v1",
        "build_id": build_id,
        "source_sha256": "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a",
        "targets": targets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_plt_targets(args.nro, args.output)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
