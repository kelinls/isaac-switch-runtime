"""Audit direct fixed-image access to the imported SaveDataManager global."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_symbols import parse_dynamic_relocations
from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256


MANAGER_GLOBAL = "_ZN4KAGE7Filesys17g_SaveDataManagerE"
MANAGER_GLOBAL_SLOT = 0xAAC940
GLOB_DAT = 1025


def _u32(data: bytes, address: int) -> int:
    return int.from_bytes(data[address:address + 4], "little")


def _sign_extend(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _adrp_page(instruction: int, address: int) -> tuple[int, int] | None:
    if instruction & 0x9F000000 != 0x90000000:
        return None
    immediate = ((instruction >> 5) & 0x7FFFF) << 2 | ((instruction >> 29) & 3)
    return instruction & 31, (address & ~0xFFF) + (_sign_extend(immediate, 21) << 12)


def _accesses(data: bytes, slot: int) -> tuple[list[str], list[str]]:
    text_start = _u32(data, 0x20)
    text_end = text_start + _u32(data, 0x24)
    if text_start % 4 or text_end > len(data) or text_start >= text_end:
        raise ValueError("invalid NRO text range")
    reads: set[str] = set()
    writes: set[str] = set()
    page = slot & ~0xFFF
    slot_offset = slot - page
    for adrp_address in range(text_start, text_end - 4, 4):
        adrp = _adrp_page(_u32(data, adrp_address), adrp_address)
        if adrp is None or adrp[1] != page:
            continue
        base_register = adrp[0]
        for access_address in range(adrp_address + 4, min(adrp_address + 36, text_end), 4):
            instruction = _u32(data, access_address)
            kind = instruction & 0xFFC00000
            register = instruction & 31
            if kind in (0xF9400000, 0xF9000000) and ((instruction >> 5) & 31) == base_register \
                    and ((instruction >> 10) & 0xFFF) * 8 == slot_offset:
                destination = f"{access_address:08x}"
                (reads if kind == 0xF9400000 else writes).add(destination)
                break
            # A direct call destroys caller-saved address registers.  Likewise,
            # an instruction with the same destination register starts a new value.
            if instruction & 0xFC000000 == 0x94000000 and base_register <= 18:
                break
            if register == base_register:
                break
    return sorted(reads), sorted(writes)


def export(nro_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    build_id, relocations = parse_dynamic_relocations(data)
    if build_id != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    globals_ = [item for item in relocations.get(MANAGER_GLOBAL, [])
                if item.table == "rela" and item.offset == MANAGER_GLOBAL_SLOT]
    if len(globals_) != 1 or globals_[0].relocation_type != GLOB_DAT or globals_[0].addend != 0:
        raise ValueError("SaveDataManager global relocation mismatch")
    reads, writes = _accesses(data, MANAGER_GLOBAL_SLOT)
    if not reads:
        raise ValueError("no direct SaveDataManager global reads found")
    return {
        "schema_version": "stage130-save-manager-global-access-audit-v1",
        "build_id": BUILD_ID,
        "source_sha256": SOURCE_SHA256,
        "manager_global": {
            "symbol": MANAGER_GLOBAL,
            "slot": f"{MANAGER_GLOBAL_SLOT:08x}",
            "relocation_type": "GLOB_DAT",
            "publication": "dynamic-loader relocation, not a fixed-image store",
        },
        "read_accesses": reads,
        "write_accesses": writes,
        "runtime_binding_authorized": False,
        "blocked_reasons": [
            "loader_owned_global_publication_not_runtime_owned",
            "static_no_store_result_does_not_prove_runtime_object_lifetime",
            "custom_filename_write_readback_and_commit_not_verified",
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage130_save_manager_global_access_audit.py <Repentance.nro> <output-json>")
    document = export(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={sys.argv[2]}")
