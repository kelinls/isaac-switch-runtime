#!/usr/bin/env python3
"""Stage 146: audit the KAGE content-mount-point API used for mod resources.

Answers one question with static evidence: *how does the game register a content
mount point, and does a mount point pick up files that only exist in our SD
overlay tree?*  Everything here is decoded from ``Repentance.nro`` (build
``91C73FDD…``) plus its dynamic symbol and relocation tables.

Findings recorded in the output JSON:

1. ``KAGE::Filesys::g_ContentManager`` is the singleton at GOT slot ``0xAAC748``
   (resolved by name from the ``.rela`` table, not guessed).
2. ``ContentMountPointPath(char const*)`` joins its argument with
   ``ContentManager::ApplicationMountPoint()`` through
   ``KAGE::Util::Path::CreateCleanPath``, so a relative string such as
   ``isaac_mods/mods/<dir>/resources`` becomes a content-root path.
3. ``IContentManager::AddMountPoint(path const&)`` allocates a 0x30-byte
   ``ContentMountPoint``, ``strdup``s the path, and calls the virtual
   ``ContentMountPoint::Build("")``; ``Build`` tail-calls
   ``FileMap::build(mountPath, "")``.
4. ``FileMap::build`` **enumerates the directory** (``nn::fs::OpenDirectory`` /
   ``ReadDirectory`` through ``ContentManager::get_directory_entries``) and
   recurses into subdirectories with the subdirectory name as the new filter,
   inserting every file under its path *relative to the mount point*.
   No ``kage_mount_points.dat`` entry is needed for a mod mount point.
5. ``ContentMountPoint`` layout: ``+0x00`` vptr, ``+0x20`` path, ``+0x28`` AoC index.

Usage::

    python3 tools/stage146_content_mount_point_audit.py [--out <json>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import nro_disasm  # noqa: E402  (in-repo helper)
import nro_symbols  # noqa: E402

NRO = os.path.join(
    REPO_ROOT,
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]",
    "Program #0",
    "1",
    ".nro",
    "Repentance.nro",
)
DEFAULT_OUT = os.path.join(REPO_ROOT, "analysis", "stage146-content-mount-point",
                           "91C73FDD575061318D68886316AFEAC72388B2AB.json")

# (label, mangled symbol) — the offsets are read from the dynamic symbol table so
# the evidence always matches the audited build.
SYMBOLS = {
    "application_mount_point": "_ZN4KAGE7Filesys14ContentManager21ApplicationMountPointEv",
    "path_ctor_string": "_ZN4KAGE7Filesys21ContentMountPointPathC1EPKc",
    "path_ctor_index_string": "_ZN4KAGE7Filesys21ContentMountPointPathC2ERKiPKc",
    "path_dtor": "_ZN4KAGE7Filesys21ContentMountPointPathD1Ev",
    "add_mount_point": "_ZN4KAGE7Filesys15IContentManager13AddMountPointERKNS0_21ContentMountPointPathE",
    "prioritize_mount_point": "_ZN4KAGE7Filesys15IContentManager20PrioritizeMountPointERKNS0_21ContentMountPointPathE",
    "remove_mount_point": "_ZN4KAGE7Filesys15IContentManager16RemoveMountPointERKNS0_21ContentMountPointPathEb",
    "does_mount_point_exist": "_ZN4KAGE7Filesys15IContentManager19DoesMountPointExistERKNS0_21ContentMountPointPathE",
    "clear_mount_points": "_ZN4KAGE7Filesys15IContentManager16ClearMountPointsEb",
    "get_mount_points": "_ZN4KAGE7Filesys15IContentManager14GetMountPointsEv",
    "get_file_mount_point": "_ZN4KAGE7Filesys15IContentManager17GetFileMountPointEPKc",
    "get_mounted_file_path": "_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc",
    "get_directory_entries": "_ZN4KAGE7Filesys14ContentManager21get_directory_entriesEPKcRNSt3__16vectorINS0_14DirectoryEntryENS4_9allocatorIS6_EEEE",
    "file_map_build": "_ZN4KAGE7Filesys7FileMap5buildEPKcS3_",
    "file_map_build_names": "_ZN4KAGE7Filesys7FileMap5BuildEPPKcj",
    "file_map_contains": "_ZN4KAGE7Filesys7FileMap8ContainsEPKc",
    "content_mount_point_ctor": "_ZN4KAGE7Filesys15IContentManager17ContentMountPointC2ERKNS0_21ContentMountPointPathE",
    "content_mount_point_ctor_names": "_ZN4KAGE7Filesys15IContentManager17ContentMountPointC2ERKNS0_21ContentMountPointPathEPPKcj",
    "content_mount_point_build": "_ZN4KAGE7Filesys15IContentManager17ContentMountPoint5BuildEPKc",
    "rebuild_content_mount_points": "_ZN15IsaacRepentance25RebuildContentMountPointsEv",
    "content_mount_points_global": "_ZN15IsaacRepentance20g_ContentMountPointsE",
    "file_manager_hash": "_ZN4KAGE7Filesys11FileManager8HashDJB2EPKc",
    "paths_are_equal": "_ZN4KAGE4Util4Path13PathsAreEqualEPKcS3_",
    "create_clean_path": "_ZN4KAGE4Util4Path15CreateCleanPathEPKcS3_bb",
    "aoc_count_add_on_content": "_ZN2nn3aoc17CountAddOnContentEv",
    "aoc_list_add_on_content": "_ZN2nn3aoc16ListAddOnContentEPiii",
}

# Data symbols live in GOT slots filled by the loader; only the relocation table
# names them, so they are recorded as slots instead of function offsets.
DATA_SYMBOLS = {
    "singleton": "_ZN4KAGE7Filesys16g_ContentManagerE",
    "content_mount_points": "_ZN15IsaacRepentance20g_ContentMountPointsE",
}

# Instruction windows quoted verbatim into the evidence so a reviewer can see the
# decoded behaviour without re-running the disassembler.
WINDOWS = {
    "path_ctor_string": ("path_ctor_string", 0x4C, None),
    "add_mount_point": ("add_mount_point", 0x120, None),
    "file_map_build_head": ("file_map_build", 0x60, None),
    "file_map_build_recursion": ("file_map_build", 0x2C8, 0x38),
    "content_mount_point_ctor": ("content_mount_point_ctor", 0x50, None),
    "content_mount_point_vtable": (None, 0x40, None),
}

# Virtual slots of IContentManager::ContentMountPoint, resolved through the .rela
# table that fills the vtable at load time.
VTABLE = "_ZTVN4KAGE7Filesys15IContentManager17ContentMountPointE"
VTABLE_SLOTS = 10


def _guard(data: bytes, offset: int) -> str | None:
    chunk = data[offset:offset + 16]
    return chunk.hex() if len(chunk) == 16 else None


def _vtable(data: bytes, address: int, relocations: dict[int, str]) -> list[dict]:
    """Read vtable slots, naming each through the relocation applied at load."""
    slots = []
    for index in range(VTABLE_SLOTS):
        slot = address + index * 8
        target = struct.unpack_from("<Q", data, slot)[0]
        slots.append({
            "slot": index,
            "slot_offset_hex": f"0x{slot:X}",
            "relocation_target": relocations.get(slot),
            "raw_value": f"0x{target:X}",
        })
    return slots


def build_evidence() -> dict:
    with open(NRO, "rb") as handle:
        data = handle.read()
    build_id, symbols = nro_symbols.parse_dynamic_symbols(data)
    _reloc_build, grouped = nro_symbols.parse_dynamic_relocations(data)

    slot_to_name: dict[int, str] = {}
    plt_to_name: dict[int, str] = {}
    slots_by_name: dict[str, list[int]] = {}
    plt_by_name: dict[str, list[int]] = {}
    for name, items in grouped.items():
        for relocation in items:
            if relocation.table == "jmprel":
                plt_to_name.setdefault(relocation.offset, name)
                plt_by_name.setdefault(name, []).append(relocation.offset)
            else:
                # .rela slots are the data addresses the code actually loads; a
                # symbol can own several (this module's copy plus the export GOT).
                slot_to_name.setdefault(relocation.offset, name)
                slots_by_name.setdefault(name, []).append(relocation.offset)

    records: dict[str, dict] = {}
    imported: dict[str, dict] = {}
    missing: list[str] = []
    slot_of = {name: f"0x{slot:X}" for slot, name in slot_to_name.items()}
    plt_of = {name: f"0x{slot:X}" for slot, name in plt_to_name.items()}
    for label, mangled in SYMBOLS.items():
        symbol = symbols.get(mangled)
        if symbol is None or symbol.file_offset is None:
            # Not defined in this module: it is either an import reached through a
            # PLT/GOT slot, or a data symbol that only the relocation table names.
            if mangled in slot_of or mangled in plt_of:
                imported[label] = {
                    "mangled": mangled,
                    "slot": slot_of.get(mangled),
                    "plt_slot": plt_of.get(mangled),
                    "kind": "import_or_data_slot",
                }
            else:
                missing.append(label)
            continue
        offset = symbol.file_offset
        records[label] = {
            "mangled": symbol.name,
            "offset": offset,
            "offset_hex": f"0x{offset:X}",
            "guard16": _guard(data, offset),
            "imported_slot": slot_of.get(symbol.name),
            "kind": "local",
        }

    data_slots = {
        label: {
            "mangled": mangled,
            "slots": [f"0x{slot:X}" for slot in sorted(slots_by_name.get(mangled, []))],
            "note": "verified_load_slot 是代码实际读取的那一个（见 disassembly 窗口）",
            "verified_load_slot": "0xAAC748" if label == "singleton" else None,
        }
        for label, mangled in DATA_SYMBOLS.items()
    }
    singleton_slot = next(
        (slot for slot, name in slot_to_name.items()
         if name == DATA_SYMBOLS["singleton"]),
        None,
    )

    disassembly: dict[str, list[str]] = {}
    for label, (window_symbol, length, skip) in WINDOWS.items():
        if window_symbol is None:
            base = (symbols.get(VTABLE).file_offset
                    if symbols.get(VTABLE) and symbols[VTABLE].file_offset is not None
                    else None)
        else:
            base = records.get(window_symbol, {}).get("offset")
        if base is None:
            continue
        start = base + (skip or 0)
        disassembly[label] = nro_disasm.disassemble(NRO, start, length)

    vtable_address = (symbols[VTABLE].file_offset
                      if symbols.get(VTABLE) and symbols[VTABLE].file_offset is not None
                      else None)

    return {
        "stage": 146,
        "topic": "content mount point API for mod resources",
        "build_id": build_id,
        "nro": os.path.relpath(NRO, REPO_ROOT),
        "nro_sha256": hashlib.sha256(data).hexdigest(),
        "nro_size": len(data),
        "singleton": {
            "symbol": DATA_SYMBOLS["singleton"],
            "slot": f"0x{singleton_slot:X}" if singleton_slot else None,
            "load_pattern": "adrp xN, <page>; ldr xN, [xN, #<imm>]",
            "note": "read once and reused; also loaded by RebuildContentMountPoints",
        },
        "data_slots": data_slots,
        "symbols": records,
        "imports": imported,
        "missing_symbols": missing,
        "content_mount_point_vtable": {
            "symbol": VTABLE,
            "offset_hex": f"0x{vtable_address:X}" if vtable_address else None,
            "slots": _vtable(data, vtable_address, slot_to_name) if vtable_address else [],
        },
        "decoded_flow": [
            "ContentMountPointPath(char const* relative) == CreateCleanPath(relative, "
            "ApplicationMountPoint(), false, true); field +0x8 = aocIndex = -1",
            "g_ContentManager->AddMountPoint(path): if path == null return; scan the "
            "existing vectors for (aocIndex, PathsAreEqual(path)) and skip duplicates; "
            "otherwise allocate 0x30 bytes, FileMap::FileMap(), strdup(path) into +0x20, "
            "aocIndex into +0x28, call virtual slot +0x10 == ContentMountPoint::Build(\"\"), "
            "then push into the vector at +0x30",
            "ContentMountPoint::Build(char const*) tail-calls virtual slot +0x38 == "
            "FileMap::build(mountPath, argument)",
            "FileMap::build(path, filter): enumerate CreateCleanPath(filter, path) via "
            "ContentManager::get_directory_entries (nn::fs::OpenDirectory/ReadDirectory), "
            "insert each file as CreateCleanPath(name, filter) hashed with FileManager::HashDJB2, "
            "and recurse per subdirectory with build(path, subdirName)",
            "=> file keys are relative to the mount point (gfx/x.anm2, font/y.fnt)",
        ],
        "planned_call_sequence": [
            "void* manager = *reinterpret_cast<void**>(base + 0x%X);" % (singleton_slot or 0),
            "alignas(8) unsigned char path[0x18];",
            "reinterpret_cast<void(*)(void*, const char*)>(base + 0x%X)(path, "
            "\"isaac_mods/mods/<dir>/resources\");" % records.get("path_ctor_string", {}).get("offset", 0),
            "reinterpret_cast<void(*)(void*, void*)>(base + 0x%X)(manager, path);" % records.get("add_mount_point", {}).get("offset", 0),
            "reinterpret_cast<void(*)(void*)>(base + 0x%X)(path);" % records.get("path_dtor", {}).get("offset", 0),
        ],
        "open_questions": [
            "does nn::fs::OpenDirectory succeed for a rom:/ path that only exists in the "
            "Atmosphere SD overlay (LayeredFS directory merging)?",
            "is the newly appended mount point consulted before the base 'resources' "
            "mount point for same-named files, or is PrioritizeMountPoint required?",
            "when exactly to register: RebuildContentMountPoints() starts with "
            "ClearMountPoints(false), so registration must happen after it",
        ],
        "disassembly": disassembly,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--print", action="store_true", help="同时打印解码窗口")
    args = parser.parse_args(argv)

    evidence = build_evidence()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(f"写入 {os.path.relpath(args.out, REPO_ROOT)}")
    print(f"build id {evidence['build_id']}，符号 {len(evidence['symbols'])} 项，"
          f"缺失 {evidence['missing_symbols']}")
    for label, record in sorted(evidence["symbols"].items()):
        print(f"  {label:32} {record['offset_hex']:>10}  {record['guard16']}")
    if args.print:
        for label, lines in evidence["disassembly"].items():
            print(f"\n--- {label} ---")
            for line in lines:
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
