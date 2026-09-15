"""构建产物的未定义符号门禁（可在构建流程里跑，也可单独跑）。

真机事故有两个，症状完全一样（报告 `01789135138`，以及 2026-09-12 的 `01789202408`）：
某个函数的**定义**被写在条件编译块里（`#if EXL_DIAGNOSTIC_STAGE == 48`，或
`#if EXL_DIAGNOSTIC_STAGE == 13`），而**调用点**在所有构建里都编译 → 模块里留下一个
未定义符号 → 调用经 PLT 走 GOT，而该槽在加载时无人填充、保持 0 → `br x17` 直接跳到
地址 0。崩溃报告表现为 `PC=0`、`Type=Instruction Abort`、`LR` 正好指向那条
`bl …@plt` 的下一条指令 —— 从寄存器很难一眼看出是"少了一个定义"。

所以这里直接检查 ELF 的符号表：除了一份显式白名单（nnSdk 提供的 `nn::ro` 内部符号）
之外，**不允许出现任何未定义符号**。不需要真机，也不需要反汇编。

用法：
    python3 tools/check_runtime_elf_symbols.py --elf runtime/<build>/runtime.elf

退出码：0 = 通过；1 = 有未定义符号；3 = 目标不是真实 ELF（例如测试用桩工具链），跳过。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# The module links against libnx only; these come from nnSdk's own runtime and are
# resolved by the loader for every module that touches nn::ro.
ALLOWED_UNDEFINED = frozenset(
    {
        "_ZN2nn2ro6detail15g_pAutoLoadListE",
        "_ZN2nn2ro6detail35g_LookupGlobalManualFunctionPointerFnE",
        "_ZN2nn2ro6detail35g_LookupGlobalManualFunctionPointerE",
    }
)

SKIP_EXIT_CODE = 3


def elf_sections(data: bytes):
    """Yield ``(name, type, offset, size, link, entsize)`` for an ELF64 file."""
    if data[:4] != b"\x7fELF" or data[4] != 2:
        raise ValueError("不是 ELF64 文件")
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]

    def header(index):
        base = e_shoff + index * e_shentsize
        return struct.unpack_from("<IIQQQQIIQQ", data, base)

    name_off, _sh_type, _flags, _addr, str_offset, str_size, _link, _info, _align, _entsize = (
        header(e_shstrndx)
    )
    names = data[str_offset : str_offset + str_size]
    for index in range(e_shnum):
        name_offset, sh_type, _flags, _addr, offset, size, link, _info, _align, entsize = header(
            index
        )
        end = names.find(b"\x00", name_offset)
        yield names[name_offset:end].decode(), sh_type, offset, size, link, entsize


def _symbol_names(data: bytes, undefined: bool) -> list[str]:
    out: list[str] = []
    sections = list(elf_sections(data))
    for name, sh_type, offset, size, _link, entsize in sections:
        if sh_type != 2 or entsize == 0:  # SHT_SYMTAB
            continue
        string_offset, string_size = 0, 0
        for other_name, _t, other_offset, other_size, _l, _e in sections:
            if other_name == ".strtab":
                string_offset, string_size = other_offset, other_size
        strings = data[string_offset : string_offset + string_size]
        for entry in range(offset, offset + size, entsize):
            st_name, _info, _other, st_shndx, _value, _size = struct.unpack_from(
                "<IBBHQQ", data, entry
            )
            if st_name == 0:
                continue
            is_undefined = st_shndx == 0
            if is_undefined != undefined:
                continue
            end = strings.find(b"\x00", st_name)
            out.append(strings[st_name:end].decode())
    return out


def undefined_symbols(data: bytes) -> list[str]:
    return _symbol_names(data, True)


def defined_symbol_names(data: bytes) -> set[str]:
    return set(_symbol_names(data, False))


def unexpected_undefined(data: bytes) -> list[str]:
    return sorted({name for name in undefined_symbols(data) if name not in ALLOWED_UNDEFINED})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    arguments = parser.parse_args(argv)
    if not arguments.elf.is_file():
        print(f"未定义符号门禁：找不到 {arguments.elf}", file=sys.stderr)
        return SKIP_EXIT_CODE
    data = arguments.elf.read_bytes()
    try:
        unexpected = unexpected_undefined(data)
    except ValueError as error:
        # 桩工具链产出的东西不是真实 ELF（测试里会这么用）：跳过而不是报错。
        print(f"未定义符号门禁：跳过 {arguments.elf}（{error}）")
        return SKIP_EXIT_CODE
    if not unexpected:
        print(f"未定义符号门禁：通过（{arguments.elf}）")
        return 0
    print(
        "未定义符号门禁失败：模块里出现了未定义符号，调用会经 PLT 走 GOT，"
        "槽为 0 时直接跳到地址 0（真机表现为 PC=0、Instruction Abort）。"
        "检查是否有定义被条件编译排除：",
        file=sys.stderr,
    )
    for name in unexpected:
        print(f"  {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
