"""Audit isolated post-call relay prerequisites for new-game and saved-game starts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols
except ModuleNotFoundError:
    from nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
CALLS = {
    "saved_game": (0x3F92A4, bytes.fromhex("F30D0A94"), 0x3F92A8),
    "new_game": (0x3F93F8, bytes.fromhex("AE0D0A94"), 0x3F93FC),
}
RELAY_CAVE = 0x68CEA0
RELAY_LENGTH = 0x100
RESERVED_INTERVALS = (
    (0x68CBE0, 0x68CC00), (0x68CC00, 0x68CC20), (0x68CC20, 0x68CC40),
    (0x68CC40, 0x68CC80), (0x68CC80, 0x68CCC0), (0x68CCC0, 0x68CD00),
    (0x68CD00, 0x68CD40), (0x68CD40, 0x68CD80), (0x68CD80, 0x68CDC0),
    (0x68CDC0, 0x68CE00), (0x68CE00, 0x68CE60), (0x68CE60, 0x68CEA0),
)


def _branch_reachable(source: int, destination: int) -> bool:
    delta = destination - source
    return delta % 4 == 0 and -0x08000000 <= delta <= 0x07FFFFFC


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and right_start < left_end


def audit(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, _ = parse_dynamic_symbols(data)
    relocation_build_id, _ = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    if RELAY_CAVE + RELAY_LENGTH > len(data):
        raise ValueError("relay cave is outside NRO")
    paths = []
    for event, (callsite, original, resume) in CALLS.items():
        if data[callsite:callsite + 4] != original:
            raise ValueError(f"{event} original Game lifecycle call mismatch")
        paths.append({"event": event, "callsite": f"0x{callsite:x}",
                      "original_instruction": original.hex().upper(),
                      "resume_target": f"0x{resume:x}",
                      "branch_reaches_cave": _branch_reachable(callsite, RELAY_CAVE)})
    overlap = any(_overlaps(RELAY_CAVE, RELAY_CAVE + RELAY_LENGTH, start, end)
                  for start, end in RESERVED_INTERVALS)
    return {
        "schema_version": "stage107-game-start-postcall-relay-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "post_call_paths": paths,
        "relay_cave": {"offset": f"0x{RELAY_CAVE:x}", "length": f"0x{RELAY_LENGTH:x}",
                       "zero_filled": data[RELAY_CAVE:RELAY_CAVE + RELAY_LENGTH] == bytes(RELAY_LENGTH),
                       "does_not_overlap_existing_relays": not overlap},
        "runtime_hook_authorized": False,
        "next_step": "design_one_shot_read_only_lifecycle_diagnostic",
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage107_game_start_postcall_relay_audit.py <Repentance.nro> <output-json>")
    document = audit(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")


if __name__ == "__main__":
    main()
