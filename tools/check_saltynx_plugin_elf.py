#!/usr/bin/env python3
"""Reject a SaltyNX plugin ELF that SaltyNX Core cannot load.

SaltyNX's loader (`SaltySDCore_DynamicLinkModule`) applies a plugin's *symbol*
relocations but does not apply `R_AARCH64_RELATIVE`. Any pointer that lives in
`.data` therefore keeps its raw on-disk value once the plugin is mapped, and the
first load through such a pointer branches to a near-null address.

That is not hypothetical. Build `20260910670000` of the Task 6b host plugin put a
C++ vtable in `.data` (the plugin held its resolver through a polymorphic base-class
reference, so `resolver_.Find(...)` became a virtual call). The vtable slot for
`Find` advertised the raw offset `0x60`, so the plugin died on hardware with:

    Result 0x2A8 (2168-0001), Instruction Abort, PC=0x60, LR=plugin+0x1c4

`crash_reports/01789060016_010021c000b6a000.log` on the device has that frame, and
`saltysd_core.log` stops right after SaltyNX resolved the plugin's two symbol
relocations. The proven-good plugin shape has exactly one relocation, an
`R_AARCH64_ABS64` on the single undefined symbol `SaltySDCore_FindSymbol`.

Two independent rules are enforced here, because either one alone ships a plugin
that cannot run:

1. No `R_AARCH64_RELATIVE` / `R_AARCH64_IRELATIVE` relocation, and no `.plt`.
   Both are the mechanical signature of the SaltyNX-incompatible layout.
2. No newlib allocator or reentrancy symbols in the dynamic symbol table. These
   arrive through `operator delete` -> `free`, which is what a C++ virtual
   destructor pulls in; they drag in `__malloc_av_`, `_impure_data`, `_sbrk_r` and
   a 4 KiB heap `.bss`, i.e. another 2704 bytes of `.data` full of pointers that
   rule 1 then rejects.

Exit status is 0 when the artifact is loadable and 1 when it is not, so the
build can call this directly as a gate.
"""
import argparse
import struct
import sys
from pathlib import Path

R_AARCH64_RELATIVE = 1027
R_AARCH64_IRELATIVE = 1032

FORBIDDEN_RELOCATIONS = {
    R_AARCH64_RELATIVE: "R_AARCH64_RELATIVE",
    R_AARCH64_IRELATIVE: "R_AARCH64_IRELATIVE",
}

# Symbols that only appear when the plugin pulled in newlib's allocator.
FORBIDDEN_SYMBOLS = (
    "malloc",
    "free",
    "_malloc_r",
    "_free_r",
    "_malloc_trim_r",
    "_sbrk_r",
    "__getreent",
    "__malloc_lock",
    "__malloc_unlock",
    "__malloc_av_",
    "__malloc_sbrk_base",
    "_impure_ptr",
    "_impure_data",
)


def _sections(binary: bytes) -> dict:
    section_offset = struct.unpack_from("<Q", binary, 0x28)[0]
    section_entry_size = struct.unpack_from("<H", binary, 0x3A)[0]
    section_count = struct.unpack_from("<H", binary, 0x3C)[0]
    shstr_index = struct.unpack_from("<H", binary, 0x3E)[0]
    shstr_header = section_offset + shstr_index * section_entry_size
    strings_offset, strings_size = struct.unpack_from("<QQ", binary, shstr_header + 24)
    strings = binary[strings_offset:strings_offset + strings_size]

    sections = {}
    for index in range(section_count):
        offset = section_offset + index * section_entry_size
        name_offset = struct.unpack_from("<I", binary, offset)[0]
        name_end = strings.find(b"\0", name_offset)
        name = strings[name_offset:name_end].decode("ascii")
        sections[name] = offset
    return sections


def inspect(artifact: Path) -> tuple[list[str], dict]:
    """Return (problems, summary) for one plugin ELF."""
    binary = artifact.read_bytes()
    sections = _sections(binary)
    problems: list[str] = []

    relocations: list[tuple[str, int, str]] = []
    for section_name in (".rela.dyn", ".rela.plt"):
        header = sections.get(section_name)
        if header is None:
            continue
        data_offset, size = struct.unpack_from("<QQ", binary, header + 24)
        entry_size = struct.unpack_from("<Q", binary, header + 56)[0] or 24
        for index in range(size // entry_size):
            entry = data_offset + index * entry_size
            _, info, _ = struct.unpack_from("<QQq", binary, entry)
            relocations.append((section_name, info & 0xFFFFFFFF, ""))

    # Aggregate per (section, kind) so a 271-entry vtable reports one line, not 271.
    offenders: dict[tuple[str, int], int] = {}
    for section_name, kind, _ in relocations:
        if kind in FORBIDDEN_RELOCATIONS:
            offenders[(section_name, kind)] = offenders.get((section_name, kind), 0) + 1
    for (section_name, kind), count in sorted(offenders.items()):
        problems.append(
            f"{section_name} contains {count} x {FORBIDDEN_RELOCATIONS[kind]}: SaltyNX does "
            "not apply it, so the target pointer keeps its raw on-disk value and the first "
            "load through it branches to a near-null address"
        )

    if ".plt" in sections:
        problems.append(
            ".plt is present: the plugin calls an undefined symbol directly instead of "
            "binding it through a global function pointer, which adds a JUMP_SLOT relocation"
        )

    dynsym_header = sections.get(".dynsym")
    dynamic_symbols: list[str] = []
    undefined_globals: list[str] = []
    if dynsym_header is not None:
        dynsym_offset, dynsym_size = struct.unpack_from("<QQ", binary, dynsym_header + 24)
        dynsym_entry_size = struct.unpack_from("<Q", binary, dynsym_header + 56)[0] or 24
        link = struct.unpack_from("<I", binary, dynsym_header + 40)[0]
        dynstr_header = struct.unpack_from("<Q", binary, 0x28)[0] + link * struct.unpack_from(
            "<H", binary, 0x3A
        )[0]
        str_offset, str_size = struct.unpack_from("<QQ", binary, dynstr_header + 24)
        dynstr = binary[str_offset:str_offset + str_size]
        for index in range(dynsym_size // dynsym_entry_size):
            entry = dynsym_offset + index * dynsym_entry_size
            name_offset, info, _, section_index, _, _ = struct.unpack_from(
                "<IBBHQQ", binary, entry
            )
            name_end = dynstr.find(b"\0", name_offset)
            name = dynstr[name_offset:name_end].decode("ascii")
            if not name:
                continue
            dynamic_symbols.append(name)
            if (info >> 4) == 1 and section_index == 0:  # STB_GLOBAL, SHN_UNDEF
                undefined_globals.append(name)

    for forbidden in FORBIDDEN_SYMBOLS:
        if forbidden in dynamic_symbols:
            problems.append(
                f"dynamic symbol table references newlib's {forbidden}: the plugin linked "
                "the C++ allocation path (a virtual destructor does this), which drags in "
                "the allocator heap and a second batch of pointer relocations"
            )

    undefined = sorted(undefined_globals)
    if undefined != ["SaltySDCore_FindSymbol"]:
        problems.append(
            "the plugin's undefined-symbol set must be exactly ['SaltySDCore_FindSymbol'], "
            f"found {undefined}: SaltyNX resolves the symbol table, so a wider import surface "
            "is a contract change rather than a detail"
        )

    summary = {
        "path": str(artifact),
        "size": len(binary),
        "sections_present": sorted(name for name in sections if name in (".plt", ".rela.dyn", ".rela.plt")),
        "relocation_count": len(relocations),
        "relocation_kinds": sorted({kind for _, kind, _ in relocations}),
        "undefined_symbols": undefined,
    }
    return problems, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artifact", type=Path, help="SaltyNX plugin ELF to check")
    args = parser.parse_args()

    if not args.artifact.is_file():
        print(f"check_saltynx_plugin_elf: not a file: {args.artifact}", file=sys.stderr)
        return 1

    problems, summary = inspect(args.artifact)
    print(f"saltynx plugin: {summary['path']}")
    print(f"  size               {summary['size']} bytes")
    print(f"  sections           {', '.join(summary['sections_present']) or '(none)'}")
    print(f"  relocations        {summary['relocation_count']} {summary['relocation_kinds']}")
    print(f"  undefined symbols  {summary['undefined_symbols']}")
    if problems:
        print("\ncheck_saltynx_plugin_elf: REJECTED", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("check_saltynx_plugin_elf: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
