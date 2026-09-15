"""Strict, read-only access to the dynamic symbols in an NRO image."""

from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Symbol:
    name: str
    file_offset: int | None
    is_defined: bool


@dataclass(frozen=True)
class Relocation:
    """One AArch64 ELF RELA entry tied to its dynamic symbol."""

    table: str
    offset: int
    relocation_type: int
    addend: int


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("NRO offset is outside the file")
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        raise ValueError("NRO offset is outside the file")
    return struct.unpack_from("<Q", data, offset)[0]


def _cstring(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        raise ValueError("dynamic string is outside the file")
    end = data.find(b"\0", offset)
    if end == -1:
        raise ValueError("unterminated dynamic string")
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("dynamic symbol name is not ASCII") from error


def _dynamic_table(data: bytes) -> tuple[dict[int, int], int]:
    if len(data) < 0x54 or data[0x10:0x14] != b"NRO0":
        raise ValueError("NRO0 header not found")

    mod0_offset = _u32(data, 0x4)
    if mod0_offset + 8 > len(data) or data[mod0_offset:mod0_offset + 4] != b"MOD0":
        raise ValueError("MOD0 header not found")
    dynamic_offset = mod0_offset + _u32(data, mod0_offset + 4)
    if dynamic_offset < mod0_offset or dynamic_offset + 16 > len(data):
        raise ValueError("dynamic table is outside the file")

    dynamic: dict[int, int] = {}
    cursor = dynamic_offset
    while cursor + 16 <= len(data):
        tag = _u64(data, cursor)
        value = _u64(data, cursor + 8)
        cursor += 16
        if tag == 0:
            return dynamic, dynamic_offset
        dynamic[tag] = value
    raise ValueError("unterminated dynamic table")


def _symbol_table(data: bytes, dynamic: dict[int, int]) -> tuple[int, int, int, int]:
    hash_offset = dynamic.get(4)
    string_offset = dynamic.get(5)
    symbol_offset = dynamic.get(6)
    symbol_size = dynamic.get(11, 24)
    if hash_offset is None or string_offset is None or symbol_offset is None or symbol_size < 24:
        raise ValueError("incomplete dynamic symbol metadata")
    if hash_offset + 8 > len(data) or string_offset >= len(data) or symbol_offset >= len(data):
        raise ValueError("dynamic symbol metadata is outside the file")

    symbol_count = _u32(data, hash_offset + 4)
    if symbol_count == 0 or symbol_offset + symbol_count * symbol_size > len(data):
        raise ValueError("dynamic symbol table is outside the file")
    return string_offset, symbol_offset, symbol_size, symbol_count


def _symbol_name(data: bytes, string_offset: int, symbol_offset: int,
                 symbol_size: int, symbol_count: int, index: int) -> str:
    if index <= 0 or index >= symbol_count:
        raise ValueError("relocation references an invalid dynamic symbol")
    name_offset = _u32(data, symbol_offset + index * symbol_size)
    if name_offset == 0:
        raise ValueError("relocation references an unnamed dynamic symbol")
    return _cstring(data, string_offset + name_offset)


def parse_dynamic_symbols(data: bytes) -> tuple[str, dict[str, Symbol]]:
    """Return the build ID and named dynamic symbols from an NRO image."""
    dynamic, _ = _dynamic_table(data)
    string_offset, symbol_offset, symbol_size, symbol_count = _symbol_table(data, dynamic)

    symbols: dict[str, Symbol] = {}
    for index in range(1, symbol_count):
        entry = symbol_offset + index * symbol_size
        name_offset, _, _, section, value, _ = struct.unpack_from("<IBBHQQ", data, entry)
        if name_offset == 0:
            continue
        name = _cstring(data, string_offset + name_offset)
        if not name:
            continue
        if section == 0:
            symbols[name] = Symbol(name=name, file_offset=None, is_defined=False)
            continue
        # NRO dynamic symbols may validly name BSS/data beyond the on-disk image.
        # They cannot provide an on-disk entry guard, so this file-oriented parser skips them.
        if value >= len(data):
            continue
        symbols[name] = Symbol(name=name, file_offset=value, is_defined=True)

    return data[0x40:0x54].hex().upper(), symbols


def parse_dynamic_relocations(data: bytes) -> tuple[str, dict[str, list[Relocation]]]:
    """Return dynamic RELA/JMPREL records grouped by imported symbol name.

    NRO relocation offsets are image-relative addresses.  They identify GOT/PLT
    slots after the game loader resolves imports; they are not callable NRO
    function offsets.
    """
    dynamic, _ = _dynamic_table(data)
    string_offset, symbol_offset, symbol_size, symbol_count = _symbol_table(data, dynamic)
    tables = (("rela", 7, 8), ("jmprel", 23, 2))
    relocations: dict[str, list[Relocation]] = {}

    for table_name, offset_tag, size_tag in tables:
        offset = dynamic.get(offset_tag)
        size = dynamic.get(size_tag)
        if offset is None and size is None:
            continue
        if offset is None or size is None:
            raise ValueError(f"incomplete {table_name} relocation metadata")
        entry_size = dynamic.get(9, 24)
        if entry_size != 24 or size % entry_size != 0 or offset + size > len(data):
            raise ValueError(f"{table_name} relocation table is outside the file")
        if table_name == "jmprel" and dynamic.get(20) != 7:
            raise ValueError("JMPREL does not use RELA records")

        for cursor in range(offset, offset + size, entry_size):
            target_offset, info, addend = struct.unpack_from("<QQq", data, cursor)
            symbol_index = info >> 32
            if symbol_index == 0:
                continue
            symbol_name = _symbol_name(
                data, string_offset, symbol_offset, symbol_size, symbol_count, symbol_index,
            )
            relocations.setdefault(symbol_name, []).append(Relocation(
                table=table_name,
                offset=target_offset,
                relocation_type=info & 0xFFFFFFFF,
                addend=addend,
            ))

    return data[0x40:0x54].hex().upper(), relocations
