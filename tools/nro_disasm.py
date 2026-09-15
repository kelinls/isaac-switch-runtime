#!/usr/bin/env python3
"""Disassemble an address range of ``Repentance.nro`` with the Xcode LLVM tools.

The NRO file is a raw module image whose *file offsets equal runtime module
offsets* (validated by the guards recorded in ``runtime/source/runtime_constants.hpp``),
so any range can be wrapped into a minimal ELF64/AArch64 relocatable object and
handed to ``llvm-objdump``.  That keeps a real disassembler in the loop instead of
a hand-rolled decoder, while still resolving addresses against the game's own
dynamic symbol table and its inline C strings.

Usage::

    python3 tools/nro_disasm.py 0x3B3510 0x180
    python3 tools/nro_disasm.py 0x4C1700 0x120 --nro "<other path>"

Annotations added per line:

* ``bl``/``b`` targets get the dynamic symbol that starts exactly there, or the
  nearest enclosing symbol when the target lands inside a function;
* ``adrp`` + ``add``/``ldr`` pairs that point into the image get the C string that
  lives at the computed address, when one is printable.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import nro_symbols  # noqa: E402  (in-repo helper, stdlib only)

DEFAULT_NRO = os.path.join(
    REPO_ROOT,
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]",
    "Program #0",
    "1",
    ".nro",
    "Repentance.nro",
)

OBJDUMP_CANDIDATES = (
    "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/llvm-objdump",
    "/usr/bin/llvm-objdump",
    "/opt/homebrew/opt/llvm/bin/llvm-objdump",
    "llvm-objdump",
)

MACHINE_AARCH64 = 183
ET_REL = 1
SHT_PROGBITS = 1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHT_STRTAB = 3


def _find_objdump() -> str:
    for candidate in OBJDUMP_CANDIDATES:
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
        else:
            for directory in os.environ.get("PATH", "").split(os.pathsep):
                path = os.path.join(directory, candidate)
                if os.path.exists(path):
                    return path
    raise SystemExit("找不到 llvm-objdump；请安装 LLVM 或改用 Xcode 自带工具链。")


def _wrap_elf(code: bytes, base: int) -> bytes:
    """Return a minimal ET_REL ELF64 object holding ``code`` at address ``base``."""
    shstr = b"\x00.text\x00.shstrtab\x00"
    shstr_name_text = 1
    shstr_name_shstrtab = 7

    ehsize = 64
    shentsize = 64
    text_off = ehsize
    shstr_off = text_off + len(code)
    shoff = shstr_off + len(shstr)
    shnum = 3
    shstrndx = 2

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    header = ident + struct.pack(
        "<HHIQQQIHHHHHH",
        ET_REL,          # e_type
        MACHINE_AARCH64,  # e_machine
        1,               # e_version
        0,               # e_entry
        0,               # e_phoff
        shoff,           # e_shoff
        0,               # e_flags
        ehsize,          # e_ehsize
        0,               # e_phentsize
        0,               # e_phnum
        shentsize,       # e_shentsize
        shnum,           # e_shnum
        shstrndx,        # e_shstrndx
    )

    def section(name, stype, flags, addr, offset, size, align):
        return struct.pack(
            "<IIQQQQIIQQ",
            name, stype, flags, addr, offset, size, 0, 0, align, 0,
        )

    sections = (
        section(0, 0, 0, 0, 0, 0, 0)
        + section(shstr_name_text, SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR,
                  base, text_off, len(code), 4)
        + section(shstr_name_shstrtab, SHT_STRTAB, 0, 0, shstr_off, len(shstr), 1)
    )
    return header + code + shstr + sections


def _relocation_maps(nro_path: str):
    """Return ``(plt_slots, data_slots)``: GOT slot address -> imported symbol name.

    ``jmprel`` slots belong to PLT stubs (calls into imported symbols) and ``rela``
    slots hold data addresses such as global singletons.  The names come from the
    game's own relocation tables, so they are exact rather than inferred.
    """
    with open(nro_path, "rb") as handle:
        data = handle.read()
    _build_id, relocations = nro_symbols.parse_dynamic_relocations(data)
    plt_slots: dict[int, str] = {}
    data_slots: dict[int, str] = {}
    for name, items in relocations.items():
        for relocation in items:
            if relocation.table == "jmprel":
                plt_slots.setdefault(relocation.offset, name)
            else:
                data_slots.setdefault(relocation.offset, name)
    return plt_slots, data_slots


def _plt_import(data: bytes, target: int, plt_slots: dict[int, str]) -> str | None:
    """Resolve a PLT stub (``adrp x16`` / ``ldr x17`` / ``add x16`` / ``br x17``)."""
    if target + 16 > len(data):
        return None
    adrp, ldr, add, branch = struct.unpack_from("<4I", data, target)
    if (adrp & 0x9F000000) != 0x90000000 or (adrp & 0x1F) != 16:
        return None
    if (ldr & 0xFFC00000) != 0xF9400000 or (ldr & 0x1F) != 17:
        return None
    if (add & 0xFFC00000) != 0x91000000 or (add & 0x1F) != 16:
        return None
    if branch != 0xD61F0220:
        return None
    immhi = (adrp >> 5) & 0x7FFFF
    immlo = (adrp >> 29) & 0x3
    imm = (immhi << 2) | immlo
    if imm & (1 << 20):
        imm -= 1 << 21
    page = (target & ~0xFFF) + (imm << 12)
    slot = page + ((ldr >> 10) & 0xFFF) * 8
    return plt_slots.get(slot)


def _c_string(data: bytes, address: int, limit: int = 80) -> str | None:
    if not (0 <= address < len(data)):
        return None
    end = data.find(b"\x00", address, address + limit)
    if end < 0:
        return None
    raw = data[address:end]
    if len(raw) < 2:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        return None
    return text


def _symbol_lookup(symbols):
    exact: dict[int, str] = {}
    starts: list[tuple[int, str]] = []
    for name, symbol in symbols.items():
        if not getattr(symbol, "is_defined", False) or symbol.file_offset is None:
            continue
        exact.setdefault(symbol.file_offset, name)
        starts.append((symbol.file_offset, name))
    starts.sort()

    def resolve(target: int) -> str | None:
        if target in exact:
            return exact[target]
        import bisect
        index = bisect.bisect_right([start for start, _ in starts], target) - 1
        if index < 0:
            return None
        start, name = starts[index]
        offset = target - start
        if offset == 0:
            return name
        if offset <= 0x40:
            return f"{name}+0x{offset:x}"
        if offset <= 0x800:
            # Nearest preceding symbol only: mark it as uncertain, the target may
            # well be a static function that has no dynamic symbol at all.
            return f"~{name}+0x{offset:x}"
        return None

    return resolve


LINE = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(\S+)\s*(.*?)\s*$")
BRANCH = re.compile(r"^(bl|b|b\.\w+)$")
ADRP = re.compile(r"^adrp\s+(x\d+),\s+(0x[0-9a-f]+)")
ADD_WITH_IMM = re.compile(r"^add\s+(x\d+),\s+(x\d+),\s+#(0x[0-9a-f]+)")


def disassemble(nro_path: str, start: int, length: int, annotate: bool = True) -> list[str]:
    with open(nro_path, "rb") as handle:
        data = handle.read()
    _build_id, symbols = nro_symbols.parse_dynamic_symbols(data)
    resolve = _symbol_lookup(symbols) if annotate else (lambda target: None)
    plt_slots, data_slots = _relocation_maps(nro_path) if annotate else ({}, {})

    end = min(start + length, len(data))
    blob = _wrap_elf(data[start:end], start)
    with tempfile.NamedTemporaryFile(suffix=".o", delete=False) as handle:
        handle.write(blob)
        temp_path = handle.name
    try:
        objdump = _find_objdump()
        proc = subprocess.run(
            [objdump, "-d", "--triple=aarch64",
             f"--start-address={start}", f"--stop-address={end}", temp_path],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(temp_path)
    if proc.returncode != 0:
        raise SystemExit(f"llvm-objdump 失败：{proc.stderr.strip()}")

    out: list[str] = []
    pending_page: tuple[str, int] | None = None
    for line in proc.stdout.splitlines():
        match = LINE.match(line)
        if not match:
            if line.strip() and not line.startswith(temp_path):
                out.append(line)
            continue
        address = int(match.group(1), 16)
        word = match.group(2)
        mnemonic = match.group(3)
        operands = match.group(4).strip()
        note = ""

        if annotate:
            branch = BRANCH.match(mnemonic)
            if branch:
                target_match = re.search(r"#?(0x[0-9a-f]+)", operands)
                if target_match:
                    target = int(target_match.group(1), 16)
                    imported = _plt_import(data, target, plt_slots)
                    name = resolve(target)
                    if imported:
                        note = f"  ; import {imported}"
                    elif name:
                        note = f"  ; -> {name}"
            adrp = ADRP.match(f"{mnemonic} {operands}")
            if adrp:
                pending_page = (adrp.group(1), int(adrp.group(2), 0))
            elif pending_page is not None:
                register, page = pending_page
                add = ADD_WITH_IMM.match(f"{mnemonic} {operands}")
                if add and add.group(2) == register:
                    address_target = page + int(add.group(3), 0)
                    symbol = data_slots.get(address_target)
                    text = _c_string(data, address_target)
                    if symbol:
                        note = f"  ; {symbol}"
                    elif text:
                        note = f"  ; \"{text}\""
                    pending_page = None
                else:
                    load = ADD_WITH_IMM.match(f"{mnemonic} {operands}")
                    slot_match = re.match(r"^ldr\s+x\d+,\s+\[x\d+(?:,\s+#(0x[0-9a-f]+))?\]$",
                                          f"{mnemonic} {operands}")
                    if slot_match:
                        offset = int(slot_match.group(1) or "0", 16)
                        slot = page + offset
                        symbol = data_slots.get(slot)
                        if symbol:
                            note = f"  ; {symbol}"
                            pending_page = None
                        else:
                            pending_page = None
                    elif mnemonic not in ("add", "ldr", "str", "adrp"):
                        pending_page = None

        out.append(f"{address:08x}  {word}  {mnemonic:<10} {operands}{note}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", help="起始偏移，如 0x3B3510")
    parser.add_argument("length", help="字节长度，如 0x180")
    parser.add_argument("--nro", default=DEFAULT_NRO)
    parser.add_argument("--no-annotate", action="store_true")
    args = parser.parse_args(argv)

    for line in disassemble(args.nro, int(args.start, 0), int(args.length, 0),
                            annotate=not args.no_annotate):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
