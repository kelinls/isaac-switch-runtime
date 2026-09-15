"""Audit the real Game::Update -> Manager::RestartGame post-call boundaries."""

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
JUMP_SLOT = 1026
RESTART_SYMBOL = "_ZN15IsaacRepentance7Manager11RestartGameENS_5SeedsEbb"
RESTART_ENTRY = 0x3FA458
RESTART_PLT = 0x6717C0
GAME_UPDATE = 0x351884
POST_CALL_PATHS = (
    (0x351C50, bytes.fromhex("DC7E0C94"), 0x351C54, bytes.fromhex("A08302D1")),
    (0x351D20, bytes.fromhex("A87E0C94"), 0x351D24, bytes.fromhex("E0030091")),
    (0x351ECC, bytes.fromhex("3D7E0C94"), 0x351ED0, bytes.fromhex("E0C30191")),
)
RELAY_CAVE = 0x68CEA0
RELAY_LENGTH = 0x100
ACTIVE_RELAY_INTERVALS = (
    (0x68CBE0, 0x68CC00), (0x68CC00, 0x68CC20), (0x68CC20, 0x68CC40),
    (0x68CC40, 0x68CC80), (0x68CC80, 0x68CCC0), (0x68CCC0, 0x68CD00),
    (0x68CD00, 0x68CD40), (0x68CD40, 0x68CD80), (0x68CD80, 0x68CDC0),
    (0x68CDC0, 0x68CE00), (0x68CE00, 0x68CE60), (0x68CE60, 0x68CEA0),
)


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _branch_target(instruction: int, address: int) -> int | None:
    if instruction >> 26 != 0b100101:
        return None
    return address + (_sign_extend(instruction & 0x3FFFFFF, 26) << 2)


def _plt_got_slot(data: bytes, thunk: int) -> int | None:
    adrp = int.from_bytes(data[thunk:thunk + 4], "little")
    ldr = int.from_bytes(data[thunk + 4:thunk + 8], "little")
    if adrp & 0x9F00001F != 0x90000010 or ldr & 0xFFC003FF != 0xF9400211:
        return None
    immediate = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
    page = (thunk & ~0xFFF) + (_sign_extend(immediate, 21) << 12)
    return page + ((ldr >> 10) & 0xFFF) * 8


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and right_start < left_end


def _branch_reachable(source: int, destination: int) -> bool:
    delta = destination - source
    return delta % 4 == 0 and -0x08000000 <= delta <= 0x07FFFFFC


def audit(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID or relocation_build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    restart_symbol = symbols.get(RESTART_SYMBOL)
    if restart_symbol is None or not restart_symbol.is_defined or restart_symbol.file_offset != RESTART_ENTRY:
        raise ValueError("Manager::RestartGame symbol mismatch")
    restart_relocations = [item for item in relocations.get(RESTART_SYMBOL, [])
                           if item.table == "jmprel" and item.relocation_type == JUMP_SLOT and item.addend == 0]
    if len(restart_relocations) != 1 or _plt_got_slot(data, RESTART_PLT) != restart_relocations[0].offset:
        raise ValueError("Manager::RestartGame PLT/JUMP_SLOT mismatch")
    paths = []
    for callsite, original, resume, cleanup in POST_CALL_PATHS:
        if data[callsite:callsite + 4] != original:
            raise ValueError(f"restart original call mismatch at 0x{callsite:x}")
        target = _branch_target(int.from_bytes(original, "little"), callsite)
        if target != RESTART_PLT:
            raise ValueError(f"restart PLT target mismatch at 0x{callsite:x}")
        if data[resume:resume + 4] != cleanup:
            raise ValueError(f"restart Seeds cleanup mismatch at 0x{resume:x}")
        paths.append({
            "caller": "Game::Update",
            "caller_entry": f"0x{GAME_UPDATE:x}",
            "callsite": f"0x{callsite:x}",
            "original_instruction": original.hex().upper(),
            "plt_target": f"0x{RESTART_PLT:x}",
            "calls_restart_plt": True,
            "resume_target": f"0x{resume:x}",
            "next_instruction": cleanup.hex().upper(),
            "cleanup_call_follows": True,
            "branch_reaches_cave": _branch_reachable(callsite, RELAY_CAVE),
        })
    if RELAY_CAVE + RELAY_LENGTH > len(data):
        raise ValueError("relay cave outside NRO")
    overlaps = any(_overlaps(RELAY_CAVE, RELAY_CAVE + RELAY_LENGTH, start, end)
                   for start, end in ACTIVE_RELAY_INTERVALS)
    return {
        "schema_version": "stage109-restart-postcall-relay-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "restart_target": {
            "symbol": RESTART_SYMBOL,
            "entry": f"0x{RESTART_ENTRY:x}",
            "plt_thunk": f"0x{RESTART_PLT:x}",
            "jump_slot": f"0x{restart_relocations[0].offset:x}",
        },
        "post_call_paths": paths,
        "relay_layout": {
            "cave": f"0x{RELAY_CAVE:x}",
            "length": f"0x{RELAY_LENGTH:x}",
            "zero_filled": data[RELAY_CAVE:RELAY_CAVE + RELAY_LENGTH] == bytes(RELAY_LENGTH),
            "does_not_overlap_active_relays": not overlaps,
            "supersedes_unused_stage108_cave": True,
        },
        "runtime_hook_authorized": False,
        "next_step": "design_one_shot_read_only_restart_return_diagnostic",
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage109_restart_postcall_relay_audit.py <Repentance.nro> <output-json>")
    document = audit(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")


if __name__ == "__main__":
    main()
