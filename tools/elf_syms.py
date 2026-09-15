#!/usr/bin/env python3
"""极简 ELF64 符号表转储（只依赖标准库）。

为什么需要它：真机读数（调试桩）走的是「模块基址 + 模块内偏移」，偏移必须来自
**当前部署的那份 `runtime.elf`**。本机没有 `aarch64-none-elf-nm`，容器里的 devkitPro
跑一次代价太高，而 `runtime.elf` 没被 strip（有 `.symtab`），所以直接在这里解析即可。

用法：
    python3 tools/elf_syms.py <elf> [子串...]
    python3 tools/elf_syms.py runtime/.gdbsym-artifacts/runtime.elf g_LastLuaError g_sequence
不带子串时打印全部符号（可能很大）。
"""

from __future__ import annotations

import struct
import sys


def load_sections(blob: bytes):
    (e_shoff,) = struct.unpack_from("<Q", blob, 0x28)
    (e_shentsize,) = struct.unpack_from("<H", blob, 0x3A)
    (e_shnum,) = struct.unpack_from("<H", blob, 0x3C)
    (e_shstrndx,) = struct.unpack_from("<H", blob, 0x3E)
    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, kind, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from(
            "<IIQQQQIIQQ", blob, off)
        sections.append(dict(name_off=name, type=kind, addr=addr, offset=offset,
                             size=size, link=link, entsize=entsize))
    strtab = sections[e_shstrndx]
    for section in sections:
        start = strtab["offset"] + section["name_off"]
        end = blob.index(b"\0", start)
        section["name"] = blob[start:end].decode("utf-8", "replace")
    return sections


def dump(path: str, needles: list[str]) -> int:
    blob = open(path, "rb").read()
    sections = load_sections(blob)
    by_name = {s["name"]: s for s in sections}
    symtab = by_name.get(".symtab")
    if symtab is None:
        print("no .symtab (stripped)", file=sys.stderr)
        return 2
    strtab = sections[symtab["link"]]
    count = symtab["size"] // symtab["entsize"]
    rows = []
    for i in range(count):
        off = symtab["offset"] + i * symtab["entsize"]
        name_off, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", blob, off)
        start = strtab["offset"] + name_off
        end = blob.index(b"\0", start)
        name = blob[start:end].decode("utf-8", "replace")
        rows.append((name, value, size, shndx, info))
    rows.sort(key=lambda r: r[1])
    matched = 0
    for name, value, size, shndx, info in rows:
        if needles and not any(n in name for n in needles):
            continue
        sec = ""
        for s in sections:
            if s["addr"] and s["addr"] <= value < s["addr"] + s["size"]:
                sec = s["name"]
                break
        print(f"0x{value:x}\t{size}\t{sec}\t{name}")
        matched += 1
    if needles:
        print(f"# matched {matched} / {count} symbols", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(dump(sys.argv[1], sys.argv[2:]))
