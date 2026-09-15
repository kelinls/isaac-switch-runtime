"""Write version-locked anchor evidence consumed by Stage 15 Ghidra analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.stage14_game_provenance import TARGET_BUILD_ID, TARGET_SOURCE_SHA256
from tools.stage15_game_owner_chain import verify_owner_chain_nro


def write_owner_chain_anchor_evidence(
    nro_path: Path, output_path: Path
) -> dict[str, object]:
    """Validate the NRO formally and write deterministic Stage 15 anchor evidence."""
    document: dict[str, object] = {
        "schema_version": "stage15-owner-chain-anchors-v1",
        "build_id": TARGET_BUILD_ID,
        "source_sha256": TARGET_SOURCE_SHA256,
        "anchors": verify_owner_chain_nro(nro_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_owner_chain_anchor_evidence(args.nro, args.output)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
