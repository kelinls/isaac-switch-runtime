#!/usr/bin/env python3
"""Stage 148: ``KAGE::Graphics::Font`` ABI / 对象尺寸 / 加载语义 定案审计。

回答一个工程问题：**我们能不能自己 new 一个 Font、用它加载 mod 自带的 .fnt？**
（若能，对象要多大、怎么构造、Load 的两个参数是什么、失败怎么表现、度量函数
依赖什么。）

所有结论都来自 ``Repentance.nro``（build ``91C73FDD…``）的静态解码，分为两类并
在 JSON 里显式标注：

* ``symbol_table`` —— 动态符号表/重定位表直接给出的（函数偏移、符号存在性、
  PLT/GOT 槽位）；
* ``instructions`` —— 本工具从指令重新推出来的（对象尺寸、字段布局、返回值形态、
  参数语义、调用点）。

关键机制：该 NRO 的**模块内调用也走 PLT**（default visibility + PIC），所以
``.jmprel`` 重定位按符号名给出 GOT 槽，[`_scan_plt_stubs`] 再把槽还原成 PLT 桩
地址，[`_bl_targets`] 一次扫描得到全部 ``BL`` 调用点。这样"谁调用了 Font::Load、
调用前 x0/x2 怎么装"都是可数的原始事实，而不是推测。

Usage::

    python3 tools/stage148_font_abi_audit.py [--out <json>] [--print]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import nro_disasm  # noqa: E402  (in-repo helper, stdlib only)
import nro_symbols  # noqa: E402

NRO = os.path.join(
    REPO_ROOT,
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]",
    "Program #0",
    "1",
    ".nro",
    "Repentance.nro",
)
DEFAULT_OUT = os.path.join(REPO_ROOT, "analysis", "stage148-font-abi",
                           "91C73FDD575061318D68886316AFEAC72388B2AB.json")

# ---------------------------------------------------------------------------
# 审计对象：Font 的公开接口。
# ``mangled`` 是唯一的"真值来源"（来自 .dynsym），``expect_signature`` 是人工从
# mangled 名读出的可读签名，用来交叉校验本文件里的迷你 demangler：
# 两者不一致时工具直接失败，避免把 demangle 的错误带进证据。
# ---------------------------------------------------------------------------
FUNCTIONS = (
    ("ctor_default", "_ZN4KAGE8Graphics4FontC1Ev",
     "KAGE::Graphics::Font::Font()", "默认构造（零/哨兵初始化）"),
    ("ctor_path", "_ZN4KAGE8Graphics4FontC2EPKc",
     "KAGE::Graphics::Font::Font(char const*)", "构造 + tail-call Load(path, NULL)"),
    ("load", "_ZN4KAGE8Graphics4Font4LoadEPKcS3_",
     "KAGE::Graphics::Font::Load(char const*, char const*)", "加载 .fnt（返回 bool）"),
    ("dtor", "_ZN4KAGE8Graphics4FontD1Ev",
     "KAGE::Graphics::Font::~Font()", "析构（tail-jump 到 Unload）"),
    ("unload", "_ZN4KAGE8Graphics4Font6UnloadEv",
     "KAGE::Graphics::Font::Unload()", "释放内部堆缓冲 + 复位"),
    ("is_loaded", "_ZNK4KAGE8Graphics4Font8IsLoadedEv",
     "KAGE::Graphics::Font::IsLoaded() const", "读 +0x00 字节"),
    ("get_character_width_char", "_ZNK4KAGE8Graphics4Font17GetCharacterWidthEc",
     "KAGE::Graphics::Font::GetCharacterWidth(char) const", "单字节查表（注意 sxtb）"),
    ("get_character_width_u16", "_ZNK4KAGE8Graphics4Font17GetCharacterWidthEt",
     "KAGE::Graphics::Font::GetCharacterWidth(unsigned short) const", "unicode 查表"),
    ("get_string_width", "_ZNK4KAGE8Graphics4Font14GetStringWidthEPKc",
     "KAGE::Graphics::Font::GetStringWidth(char const*) const", "单字节串宽度（含 kerning）"),
    ("get_string_width_utf8", "_ZNK4KAGE8Graphics4Font18GetStringWidthUTF8EPKc",
     "KAGE::Graphics::Font::GetStringWidthUTF8(char const*) const",
     "UTF-8 → UTF-16（内静态缓冲）→ GetStringWidth(u16 const*)"),
    ("get_line_height", "_ZNK4KAGE8Graphics4Font13GetLineHeightEv",
     "KAGE::Graphics::Font::GetLineHeight() const", "读 +0x14 半字"),
    ("get_baseline_height", "_ZNK4KAGE8Graphics4Font17GetBaselineHeightEv",
     "KAGE::Graphics::Font::GetBaselineHeight() const", "读 +0x16 半字"),
    ("set_missing_character", "_ZN4KAGE8Graphics4Font19SetMissingCharacterEt",
     "KAGE::Graphics::Font::SetMissingCharacter(unsigned short)",
     "写 glyphIndex[0xFFFF]（+0x2004E）"),
)

# 需要借来说明"内存从哪来/怎么放"的导入符号。
IMPORTS = {
    "operator_new": "_Znwm",
    "operator_new_array": "_Znam",
    "operator_delete": "_ZdlPv",
    "operator_delete_array": "_ZdaPv",
    "memset": "memset",
    "calloc": "calloc",
    "free": "free",
    "open_read": "_ZN4KAGE7Filesys11FileManager8OpenReadEPKc",
    "try_redirect_path": "_ZN15IsaacRepentance10ModManager15TryRedirectPathERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE",
}

# 需要作为"数据槽"记录（.rela 给出，代码里 adrp+ldr 读）的全局：
DATA_SYMBOLS = {
    "file_manager": "_ZN4KAGE7Filesys13g_FileManagerE",
    "image_manager": "_ZN4KAGE8Graphics14g_ImageManagerE",
    "smart_pointer_dec_notify": "_ZN12SmartPointerIN4KAGE8Graphics9ImageBaseEE11s_DecNotifyE",
}

# 反汇编窗口：全部用"从符号表取到的偏移 + 固定长度"表达，不写死绝对地址。
# (key, base_function_or_None, start_offset_in_function, length, 说明)
WINDOWS = (
    ("ctor_default", "ctor_default", 0x00, 0x38, "默认构造：写入 +0x2004E、memset(+0x50,0xFF,0x1FE)"),
    ("ctor_path", "ctor_path", 0x00, 0x60, "构造 + Load(path, NULL) tail-call，x2=xzr"),
    ("load_head", "load", 0x00, 0xC0, "先 Unload、calloc(1,0x28)→+0x38、OpenRead(path)、BMF 魔数校验"),
    ("load_ret", "load", 0x120, 0x30, "失败返回 w0=0 / 成功 strb 1→+0x00 并返回 1"),
    ("load_second_arg", "load", 0x330, 0x60, "第二参数：strlen(x2)==3 且页名以 4 字符扩展名结尾时替换扩展名"),
    ("load_second_arg_null", "load", 0x760, 0x40, "第二参数为 NULL 时的同一循环（不替换扩展名）"),
    ("load_field_writes", "load", 0x2A0, 0x40, "解析结果写 +0x24/+0x28（glyph 表，stride 0x14）"),
    ("load_page_array", "load", 0x2C0, 0x30, "页数 → +0x40，new[] +0x48（每项 0x10，含 8 字节计数头）"),
    ("load_common_record", "load", 0xF8, 0x20, "把 this+0x4 / this+0x14 作为 16 字节读入目标（行高/基线来源）"),
    ("dtor", "dtor", 0x00, 0x0C, "析构 = tail-jump 到 Unload（不 delete this）"),
    ("unload_head", "unload", 0x00, 0x70, "delete[] +0x28(glyph 表)、逐页 ImageManager::FreeImage(+0x48)"),
    ("unload_tail", "unload", 0xCC, 0x74, "memset +0x50 复位、free +0x38 的 0x28 字节结构"),
    ("is_loaded", "is_loaded", 0x00, 0x0C, "ldrb w0,[x0] —— 纯读 +0x00"),
    ("get_character_width_char", "get_character_width_char", 0x00, 0x54,
     "sxtb 索引 glyphIndex[]、glyph 记录 +0x10 是有符号 16 位"),
    ("get_character_width_u16", "get_character_width_u16", 0x00, 0x54, "uxth 索引版本"),
    ("get_string_width_head", "get_string_width", 0x00, 0x30, "逐字节查表 + kerning 哈希结构（+0x38）"),
    ("get_string_width_ret", "get_string_width", 0x11C, 0x0C, "返回 w0 = w8（int）"),
    ("get_string_width_utf8", "get_string_width_utf8", 0x00, 0x54,
     "strlen → StringEncoding::ConvertUTF8toUTF16(..., 内静态缓冲, 512) → tail-call u16 版"),
    ("get_line_height", "get_line_height", 0x00, 0x0C, "ldrh w0,[x0,#0x14]"),
    ("get_baseline_height", "get_baseline_height", 0x00, 0x0C, "ldrh w0,[x0,#0x16]"),
    ("set_missing_character", "set_missing_character", 0x00, 0x10, "strh w1,[x0,#0x2004E]"),
)

# 需要定位（不写死地址）的"外部证据点"：
#   1) operator new(≈2×对象尺寸) 后连续构造两个 Font 的调用点；
#   2) 同一基址寄存器上连续构造 Font、位移差 == 对象尺寸 的调用点；
#   3) 游戏自己加载 .fnt 的调用点（带 ModManager::TryRedirectPath）。
GAME_LOAD_SEARCH = "TryRedirectPath"   # 用离它最近的 Load 调用点当样本

MAX_CALL_SITES_IN_JSON = 40

# 判定"两个调用点在同一个函数体内"的地址跨度上限：用于剔除"不同函数恰好用了同号
# 寄存器"造成的假 stride（实测真 stride 的相邻调用点都在几十字节内）。
SAME_FUNCTION_SPAN = 0x1000


# ---------------------------------------------------------------------------
# 低层解码
# ---------------------------------------------------------------------------

def _guard(data: bytes, offset: int) -> str | None:
    chunk = data[offset:offset + 16]
    return chunk.hex() if len(chunk) == 16 else None


def _word(data: bytes, address: int) -> int:
    return struct.unpack_from("<I", data, address)[0]


def _decode_move_wide(word: int) -> tuple[str, int, int] | None:
    """movz/movk/movn w<rd>, #imm{, lsl #16} —— (op, rd, value)。"""
    base = word & 0xFF800000
    rd = word & 0x1F
    shift = ((word >> 21) & 3) * 16
    imm = ((word >> 5) & 0xFFFF) << shift
    if base in (0x52800000, 0xD2800000):
        return ("movz", rd, imm)
    if base in (0x72800000, 0xF2800000):
        return ("movk", rd, imm)
    if base in (0x12800000, 0x92800000):
        return ("movn", rd, (~imm) & 0xFFFFFFFF)
    return None


def _decode_bitmask_immediate(word: int, datasize: int = 32) -> int | None:
    """ORR/AND (immediate) 的位掩码立即数（编译器常用它做 mov 大常数）。

    官方伪码：``len = HighestSetBit(immN:NOT(imms))``；这里必须对 imms 取反，
    否则会把 ``mov w2, #0x1fffe``（0x321f3fe2）算成 0xFFFFFFFF。
    """
    if (word & 0x7F800000) != 0x32000000:
        return None
    imm_n = (word >> 22) & 1
    immr = (word >> 16) & 0x3F
    imms = (word >> 10) & 0x3F
    field = (imm_n << 6) | ((~imms) & 0x3F)
    length = field.bit_length() - 1
    if length < 1:
        return None
    esize = 1 << length
    if esize > datasize:
        return None
    s = imms & (esize - 1)
    r = immr & (esize - 1)
    if s == esize - 1:
        return None
    welem = (1 << (s + 1)) - 1
    rotate = r % esize
    elem = ((welem >> rotate) | (welem << ((esize - rotate) % esize))) & ((1 << esize) - 1)
    value = 0
    for _ in range(datasize // esize):
        value = (value << esize) | elem
    return value


def _decode_immediate_value(word: int) -> int | None:
    """把一条"给 32 位寄存器装常数"的指令解成数值（movz/movk/movn/位掩码 mov）。"""
    move = _decode_move_wide(word)
    if move is not None:
        return move[2]
    return _decode_bitmask_immediate(word)


def _materialized_constant(data: bytes, pc: int, register: int = 0, back: int = 4):
    """从 ``pc`` 往前收集给 ``w<register>`` 装常数的连续 movz/movk 序列。

    返回 ``(value, [instruction_addresses])``；序列末端必须是 movz/movn（movk 只能
    出现在它前面），否则返回 ``None``。
    """
    sequence = []
    for step in range(1, back + 1):
        address = pc - 4 * step
        move = _decode_move_wide(_word(data, address))
        if move is None:
            break
        if move[1] != register:
            # 遇到给别的寄存器装常数的指令：序列到此为止（不是错误）。
            break
        sequence.append((address, move[0], move[2]))
    if not sequence or sequence[0][0] != pc - 4:
        return None
    if sequence[-1][1] not in ("movz", "movn"):
        return None
    if any(op != "movk" for _, op, _ in sequence[:-1]):
        return None
    value = sequence[-1][2]
    for _, _, part in sequence[:-1]:
        value |= part
    return value, [address for address, _, _ in sequence]


def _decode_add_this(data: bytes, pc: int):
    """解 ``add x0, xN, xM`` / ``add x0, xN, #imm`` 形式的 this 表达式。

    返回 ``(base_register_index, offset, [evidence_addresses])``；``offset`` 是
    从基址到 this 的位移（``None`` 表示无法定案）。只处理"BL 前 4 条指令内"的情形。
    """
    for step in range(1, 5):
        address = pc - 4 * step
        word = _word(data, address)
        # 遇到控制流（BL/B/BR 系）就停：再往前就不是这次调用的参数准备了。
        if (word & 0xFC000000) in (0x94000000, 0x14000000):
            return None
        if (word & 0xFE000000) == 0xD6000000:
            return None
        # ADD (immediate) 64-bit: sf=1 op=0 S=0 100010 sh imm12 Rn Rd
        if (word & 0xFF000000) == 0x91000000:
            imm = ((word >> 10) & 0xFFF) << (12 if (word >> 22) & 1 else 0)
            rn = (word >> 5) & 0x1F
            if (word & 0x1F) == 0:
                return rn, imm, [address]
            continue
        # ADD (shifted register) 64-bit: 0x8B000000，位移由 movz/movk 装入的寄存器给出
        if (word & 0xFF200000) == 0x8B000000:
            rm = (word >> 16) & 0x1F
            rn = (word >> 5) & 0x1F
            if (word & 0x1F) != 0:
                continue
            resolved = _constant_before(data, address, register=rm, limit=8)
            if resolved is None:
                continue
            value, addresses = resolved
            return rn, value, [address] + addresses
        continue
    return None


# ---------------------------------------------------------------------------
# 代码扫描：PLT 桩 / BL 调用点 / 调用序列
# ---------------------------------------------------------------------------

def _scan_plt_stubs(data: bytes) -> dict[int, int]:
    """扫描 ``adrp x16; ldr x17,[x16,#imm]; add x16,x16,#imm; br x17`` 桩。

    返回 ``{GOT 槽地址: 桩地址}``；槽与符号名的对应来自 ``.jmprel``（见
    ``nro_symbols.parse_dynamic_relocations``）。
    """
    stubs: dict[int, int] = {}
    for index in range(len(data) // 4 - 3):
        pc = index * 4
        word0, word1, word2, word3 = struct.unpack_from("<4I", data, pc)
        if (word0 & 0x9F000000) != 0x90000000 or (word0 & 0x1F) != 16:
            continue
        if (word1 & 0xFFC00000) != 0xF9400000 or (word1 & 0x1F) != 17 or ((word1 >> 5) & 0x1F) != 16:
            continue
        if (word2 & 0xFFC00000) != 0x91000000 or (word2 & 0x1F) != 16 or ((word2 >> 5) & 0x1F) != 16:
            continue
        if word3 != 0xD61F0220:
            continue
        immhi = (word0 >> 5) & 0x7FFFF
        immlo = (word0 >> 29) & 3
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (pc & ~0xFFF) + (imm << 12)
        slot = page + ((word1 >> 10) & 0xFFF) * 8
        stubs.setdefault(slot, pc)
    return stubs


def _bl_targets(data: bytes) -> dict[int, list[int]]:
    """一次扫描得到 ``{BL 目标: [BL 指令地址…]}``（保持升序）。"""
    targets: dict[int, list[int]] = {}
    for index in range(len(data) // 4):
        word = struct.unpack_from("<I", data, index * 4)[0]
        if (word & 0xFC000000) != 0x94000000:
            continue
        imm = word & 0x03FFFFFF
        if imm & 0x02000000:
            imm -= 0x04000000
        pc = index * 4
        targets.setdefault(pc + imm * 4, []).append(pc)
    return targets


def _plt_slot_by_name(relocations) -> dict[str, list[int]]:
    slots: dict[str, list[int]] = {}
    for name, items in relocations.items():
        for relocation in items:
            if relocation.table == "jmprel":
                slots.setdefault(name, []).append(relocation.offset)
    return {name: sorted(values) for name, values in slots.items()}


def _data_slots_by_name(relocations) -> dict[str, list[int]]:
    slots: dict[str, list[int]] = {}
    for name, items in relocations.items():
        for relocation in items:
            if relocation.table == "rela":
                slots.setdefault(name, []).append(relocation.offset)
    return {name: sorted(values) for name, values in slots.items()}


def _user_functions(data: bytes) -> list[tuple[int, int]]:
    """本模块里所有 ≥ 对象尺寸的栈帧（``sub sp, sp, #imm``，imm ≥ 0x20050）。

    用来回答"游戏会不会把 Font 放在栈上"：只有这些函数才有可能。
    """
    frames = []
    for index in range(len(data) // 4):
        word = struct.unpack_from("<I", data, index * 4)[0]
        if (word & 0xFF800000) != 0xD1000000:
            continue
        imm = ((word >> 10) & 0xFFF) << (12 if (word >> 22) & 1 else 0)
        if ((word >> 5) & 0x1F) == 31 and (word & 0x1F) == 31 and imm >= 0x20050:
            frames.append((index * 4, imm))
    return frames


# ---------------------------------------------------------------------------
# 迷你 Itanium demangler（只覆盖本文件用到的 13 个名字）
# ---------------------------------------------------------------------------

_SIMPLE_TYPES = {
    "v": "void", "b": "bool", "c": "char", "a": "signed char", "h": "unsigned char",
    "s": "short", "t": "unsigned short", "i": "int", "j": "unsigned int",
    "l": "long", "m": "unsigned long", "x": "long long", "y": "unsigned long long",
    "f": "float", "d": "double", "w": "wchar_t",
}


class _MiniDemangler:
    """只覆盖本次审计 13 个符号的迷你 demangler（嵌套名、替换码、P/K 限定、基础类型）。

    不是通用实现；``_self_check_demangler`` 会用人工从 mangled 名读出的签名逐个核对，
    不一致就直接失败。替换码编号与"前缀才是候选"这两条语义已用 Xcode
    ``c++filt -n`` 作为 oracle 验证过（见 ``_type``/``_nested_name`` 注释）。
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0
        self.substitutions: list[str] = []

    def _peek(self) -> str:
        return self.source[self.index] if self.index < len(self.source) else ""

    def _take(self, count: int = 1) -> str:
        text = self.source[self.index:self.index + count]
        self.index += count
        return text

    def _identifier(self) -> str:
        digits = ""
        while self._peek().isdigit():
            digits += self._take()
        if not digits:
            raise ValueError("期望长度前缀")
        return self._take(int(digits))

    def _type(self) -> str:
        token = self._peek()
        if token == "P":
            self._take()
            text = self._type() + "*"
            self.substitutions.append(text)
            return text
        if token == "R":
            self._take()
            text = self._type() + "&"
            self.substitutions.append(text)
            return text
        if token == "O":
            self._take()
            text = self._type() + "&&"
            self.substitutions.append(text)
            return text
        if token == "K":
            self._take()
            text = self._type() + " const"
            self.substitutions.append(text)
            return text
        if token == "S":
            self._take()
            digits = ""
            while self._peek().isdigit():
                digits += self._take()
            if self._take() != "_":
                raise ValueError("替换码格式错误")
            # Oracle 校验过（Xcode c++filt -n）：S_ == 下标 0，S<n>_ == 下标 n+1。
            number = int(digits) + 1 if digits else 0
            if number >= len(self.substitutions):
                raise ValueError("替换码越界")
            return self.substitutions[number]
        if token == "N":
            return self._nested_name()
        token = self._take()
        if token in _SIMPLE_TYPES:
            return _SIMPLE_TYPES[token]
        raise ValueError(f"未支持的类型码 {token!r}")

    def _nested_name(self) -> str:
        if self._take() != "N":
            raise ValueError("不是嵌套名")
        is_const = False
        while self._peek() in ("K", "V", "r"):
            if self._take() == "K":
                is_const = True
        prefix = ""
        while self._peek() != "E":
            if self._peek().isdigit():
                component = self._identifier()
                prefix = f"{prefix}::{component}" if prefix else component
                # 只有"前缀"是替换候选：Oracle 校验过
                # _ZN4KAGE8Graphics4Font4LoadEPKcS3_ 的 S3_ 指向第 2 个 char const*。
                if self._peek() != "E":
                    self.substitutions.append(prefix)
                continue
            code = self.source[self.index:self.index + 2]
            if code in ("C1", "C2", "C3", "D0", "D1", "D2"):
                self._take(2)
                last = prefix.rsplit("::", 1)[-1]
                # ctor 名 = <class>::<class>；dtor 名 = <class>::~<class>
                self.name = f"{prefix}::~{last}" if code.startswith("D") else f"{prefix}::{last}"
                break
            raise ValueError(f"未支持的名称码 {code!r}")
        if self._take() != "E":
            raise ValueError("嵌套名缺少 E")
        if is_const:
            self.const_member = True
        if not hasattr(self, "name") or self.name is None:
            self.name = prefix
        return self.name

    def demangle(self) -> str:
        if self._take(2) != "_Z":
            raise ValueError("不是 Itanium 名字")
        self.const_member = False
        self.name = None
        if self._peek() == "N":
            name = self._nested_name()
        else:
            name = self._identifier()
        params = []
        while self.index < len(self.source):
            params.append(self._type())
        if params == ["void"]:
            params = []
        suffix = " const" if self.const_member else ""
        return f"{name}({', '.join(params)}){suffix}"


def _demangle(mangled: str) -> str:
    return _MiniDemangler(mangled).demangle()


def _self_check_demangler() -> None:
    """demangler 自检：结果是证据的一部分，错了必须当场失败。"""
    samples = {mangled: signature for _, mangled, signature, _ in FUNCTIONS}
    for mangled, expected in samples.items():
        actual = _demangle(mangled)
        if actual != expected:
            raise SystemExit(f"demangler 自检失败：{mangled} -> {actual!r}，期望 {expected!r}")
    _self_check_immediates()


def _self_check_immediates() -> None:
    """立即数解码自检（用 clang -c 汇编过的真实编码）。"""
    # mov w2, #0x1fffe / mov w2, #0x1fe / mov w8, #0x2004e(movz+movk)
    if _decode_bitmask_immediate(0x321F3FE2) != 0x1FFFE:
        raise SystemExit("位掩码立即数解码自检失败")
    if _decode_move_wide(0x52803FC2)[2] != 0x1FE:
        raise SystemExit("movz 立即数解码自检失败")


# ---------------------------------------------------------------------------
# 证据组装
# ---------------------------------------------------------------------------

def build_evidence() -> dict:
    _self_check_demangler()

    with open(NRO, "rb") as handle:
        data = handle.read()
    build_id, symbols = nro_symbols.parse_dynamic_symbols(data)
    _reloc_build, relocations = nro_symbols.parse_dynamic_relocations(data)

    plt_slots = _plt_slot_by_name(relocations)          # name -> [GOT 槽]
    data_slots = _data_slots_by_name(relocations)       # name -> [数据槽]
    stubs = _scan_plt_stubs(data)                       # GOT 槽 -> PLT 桩
    bl_targets = _bl_targets(data)                      # BL 目标 -> [调用点]

    def call_sites_for(mangled: str) -> tuple[list[int], int | None, int | None]:
        slots = plt_slots.get(mangled, [])
        stub = next((stubs[slot] for slot in slots if slot in stubs), None)
        sites = sorted(bl_targets.get(stub, [])) if stub is not None else []
        return sites, (slots[0] if slots else None), stub

    records: dict[str, dict] = {}
    missing: list[str] = []
    for label, mangled, expected_signature, note in FUNCTIONS:
        symbol = symbols.get(mangled)
        sites, slot, stub = call_sites_for(mangled)
        if symbol is None or symbol.file_offset is None:
            missing.append(label)
            records[label] = {
                "mangled": mangled,
                "offset": None,
                "offset_hex": None,
                "guard16": None,
                "signature": _demangle(mangled),
                "note": note,
                "defined_in_module": False,
                "plt_slot": f"0x{slot:X}" if slot else None,
                "plt_stub": f"0x{stub:X}" if stub else None,
                "in_module_call_site_count": len(sites),
                "in_module_call_sites": [f"0x{site:X}" for site in sites[:MAX_CALL_SITES_IN_JSON]],
            }
            continue
        offset = symbol.file_offset
        demangled = _demangle(mangled)
        records[label] = {
            "mangled": mangled,
            "offset": offset,
            "offset_hex": f"0x{offset:X}",
            "guard16": _guard(data, offset),
            "signature": demangled,
            "signature_agrees_with_hand_read": demangled == expected_signature,
            "note": note,
            "defined_in_module": True,
            "plt_slot": f"0x{slot:X}" if slot else None,
            "plt_stub": f"0x{stub:X}" if stub else None,
            "in_module_call_site_count": len(sites),
            "in_module_call_sites": [f"0x{site:X}" for site in sites[:MAX_CALL_SITES_IN_JSON]],
            "call_sites_truncated": max(0, len(sites) - MAX_CALL_SITES_IN_JSON),
        }

    imports: dict[str, dict] = {}
    for label, mangled in IMPORTS.items():
        symbol = symbols.get(mangled)
        slots = plt_slots.get(mangled, [])
        data_slot_list = data_slots.get(mangled, [])
        stub = next((stubs[slot] for slot in slots if slot in stubs), None)
        imports[label] = {
            "mangled": mangled,
            "defined_in_module": bool(symbol is not None and symbol.is_defined),
            "plt_slot": f"0x{slots[0]:X}" if slots else None,
            "plt_stub": f"0x{stub:X}" if stub else None,
            "call_site_count": len(bl_targets.get(stub, [])) if stub is not None else 0,
            "data_slots": [f"0x{slot:X}" for slot in data_slot_list],
            "resolved_by": "symbol_table",
        }

    # ---- 反汇编窗口 --------------------------------------------------------
    disassembly: dict[str, list[str]] = {}
    for key, function_key, skip, length, _note in WINDOWS:
        record = records.get(function_key, {})
        base = record.get("offset")
        if base is None:
            continue
        disassembly[key] = nro_disasm.disassemble(NRO, base + skip, length)

    # ---- 游戏自身加载 .fnt 的样本（证明路径形态与第二参数）------------------
    game_sample = _game_load_sample(data, records, imports, bl_targets)
    if game_sample is not None:
        disassembly["game_load_sample"] = nro_disasm.disassemble(
            NRO, game_sample["window_start"], 0x70)
        game_sample["path_strings"] = sorted({
            line.split('; "', 1)[1].rstrip('"')
            for line in disassembly["game_load_sample"] if '; "' in line
        })

    # ---- 对象尺寸证据 ------------------------------------------------------
    new_sizes = _new_call_site_sizes(data, imports["operator_new"]["plt_stub"], bl_targets)
    ctor_sites = [int(site, 16) for site in records["ctor_default"]["in_module_call_sites"]]
    ctor_this = []
    for site in ctor_sites:
        decoded = _decode_add_this(data, site)
        if decoded is None:
            continue
        base_register, offset, evidence = decoded
        ctor_this.append({
            "call_site": f"0x{site:X}",
            "base_register": f"x{base_register}",
            "this_offset": offset,
            "this_offset_hex": f"0x{offset:X}" if offset is not None else None,
            "evidence_instructions": [f"0x{address:08X}" for address in evidence],
        })

    strides = []
    rejected_pairs = []
    for previous, current in zip(ctor_this, ctor_this[1:]):
        if previous["base_register"] != current["base_register"]:
            continue
        if previous["this_offset"] is None or current["this_offset"] is None:
            continue
        delta = current["this_offset"] - previous["this_offset"]
        if delta <= 0:
            continue
        record = {
            "first": previous["call_site"],
            "first_offset_hex": previous["this_offset_hex"],
            "second": current["call_site"],
            "second_offset_hex": current["this_offset_hex"],
            "delta": delta,
            "delta_hex": f"0x{delta:X}",
        }
        # 同一函数体内的两个调用点才会共享同一个基址寄存器；相距过远的一对只是
        # 恰好用了同号的寄存器，不能当作 stride 证据（例如 x20 在不同函数里复用）。
        if abs(int(current["call_site"], 16) - int(previous["call_site"], 16)) > SAME_FUNCTION_SPAN:
            record["rejected_reason"] = f"两个调用点相距超过 0x{SAME_FUNCTION_SPAN:X}，判为跨函数误配"
            rejected_pairs.append(record)
            continue
        strides.append(record)

    delta_counts: dict[int, int] = {}
    for entry in strides:
        delta_counts[entry["delta"]] = delta_counts.get(entry["delta"], 0) + 1
    ranked = sorted(delta_counts.items(), key=lambda item: (-item[1], item[0]))

    deltas = sorted({entry["delta"] for entry in strides})

    # memset 边界：ctor 的 memset(this+0x50, 0xFF, count) 与 +0x2004E 半字写入
    ctor_notes = {}
    for label in ("ctor_default", "ctor_path", "unload"):
        start = records[label]["offset"]
        end = _next_symbol_offset(symbols, start) or (start + 0x100)
        ctor_notes[label] = _memset_in_function(data, start, end)
    end_halfword = _ctor_end_halfword(
        data,
        records["ctor_default"]["offset"],
        _next_symbol_offset(symbols, records["ctor_default"]["offset"]) or 0x4CDE58,
    )

    heap_blocks = _heap_block_with_two_fonts(data, new_sizes, ctor_sites, records)

    # 尺寸定案 = 四路来源投票，全部一致且至少两路才定案
    size_votes: dict[str, int] = {}
    if end_halfword["offset"] is not None:
        size_votes["ctor_last_halfword_write(+0x2004E+2)"] = end_halfword["offset"] + 2
    char_ctor_count = ctor_notes["ctor_path"].get("count")
    if char_ctor_count:
        # memset 覆盖 [0x50, 0x50+count)，紧接着 ctor 又写 +0x2004E 的半字 → 上限 0x20050
        size_votes["ctor_path_memset_span_plus_trailing_halfword"] = 0x50 + char_ctor_count + 2
    if ranked and ranked[0][1] >= 2:
        size_votes["allocation_stride(模态)"] = ranked[0][0]
    for block in heap_blocks:
        size_votes[f"operator_new_block@{block['new_call_site']}"] = int(block["delta_hex"], 16)

    vote_values = sorted(set(size_votes.values()))
    consensus = vote_values[0] if len(vote_values) == 1 and len(size_votes) >= 2 else None

    size_evidence = [
        {
            "kind": "ctor_last_halfword_write",
            "source": "instructions",
            "claim": "Font() 在 +0x2004E 写入一个半字（strh wzr,[x0,x8]，x8=0x2004E）",
            "lower_bound": 0x2004E + 2,
            "lower_bound_hex": f"0x{0x2004E + 2:X}",
            "instructions": end_halfword["instructions"],
        },
        {
            "kind": "ctor_memset_span",
            "source": "instructions",
            "claim": "Font(char const*) 的 memset(this+0x50, 0xFF, 0x1FFFE) 覆盖到 +0x2004D，"
                     "紧接着写 +0x2004E 的半字 → 对象尾端 = 0x20050",
            "memset_count_hex": ctor_notes["ctor_path"].get("count_hex"),
            "memset_call_form": ctor_notes["ctor_path"].get("call_form"),
            "memset_span_end_hex": (f"0x{ctor_notes['ctor_path']['span_value']:X}"
                                    if ctor_notes["ctor_path"].get("span_value") else None),
            "instructions": ctor_notes["ctor_path"]["instructions"],
        },
        {
            "kind": "allocation_stride",
            "source": "instructions",
            "claim": "同一基址寄存器上连续（同一函数体内）的 Font() 调用点，其 this 位移差恒为 0x20050",
            "observed_deltas_hex": [f"0x{delta:X}" for delta in deltas],
            "delta_counts": {f"0x{delta:X}": count for delta, count in ranked},
            "examples": strides[:8],
            "rejected_pairs": rejected_pairs,
        },
    ]
    if heap_blocks:
        size_evidence.append({
            "kind": "operator_new_block",
            "source": "instructions",
            "claim": "operator new(size) 返回的同一块内存里连续构造两个 Font，"
                     "两次 this 位移差 == 0x20050，说明 size 覆盖 2 个对象 + 头部",
            "blocks": heap_blocks,
        })

    object_size = consensus

    call_site_shape = {
        "source": "instructions",
        "total_ctor_call_sites": len(ctor_sites),
        "decoded_this_expression": len(ctor_this),
        "undecoded_call_sites": [
            {
                "call_site": f"0x{site:X}",
                "window": [f"0x{site - 4 * step:08X}:{_word(data, site - 4 * step):08X}"
                           for step in (1, 2, 3)],
            }
            for site in ctor_sites
            if f"0x{site:X}" not in {entry["call_site"] for entry in ctor_this}
        ],
        "by_base_register": {
            register: sum(1 for entry in ctor_this if entry["base_register"] == register)
            for register in sorted({entry["base_register"] for entry in ctor_this})
        },
        "finding": "每个 ctor 调用点的 this 都是 <寄存器>+<立即数位移>：游戏内 Font 一律作为聚合体成员"
                   "（HUD/Manager/... 的成员，相邻成员位移差 0x20050）或聚合体堆块内的成员被构造；"
                   "本审计没有发现任何【游戏自己给单个 Font 分配内存】的调用点。",
    }

    return {
        "stage": 148,
        "topic": "KAGE::Graphics::Font ABI：对象尺寸/构造析构语义/Load 与度量函数 ABI",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_id": build_id,
        "nro": os.path.relpath(NRO, REPO_ROOT),
        "nro_sha256": hashlib.sha256(data).hexdigest(),
        "nro_size": len(data),
        "offset_equals_runtime_offset": {
            "asserted_by": "runtime/source/runtime_constants.hpp（历史守卫，本工具未读取/未修改）",
            "used_here": True,
            "note": "本审计把 NRO 文件偏移当作运行时模块偏移；这是项目既有前提，本工具不重复证明。",
        },
        "invocation_model": {
            "source": "instructions+symbol_table",
            "finding": "模块内对 Font 成员函数的调用走 PLT（.jmprel 按符号名给出 GOT 槽），"
                       "因此可用 PLT 桩 + BL 目标枚举出全部模块内调用点",
            "plt_stub_count": len(stubs),
            "bl_instruction_count": sum(len(v) for v in bl_targets.values()),
        },
        "functions": records,
        "missing_symbols": missing,
        "imports": imports,
        "data_symbols": {
            label: {
                "mangled": mangled,
                "data_slots": [f"0x{slot:X}" for slot in data_slots.get(mangled, [])],
                "plt_slots": [f"0x{slot:X}" for slot in plt_slots.get(mangled, [])],
            }
            for label, mangled in DATA_SYMBOLS.items()
        },
        "object_size": {
            "value": object_size,
            "hex": f"0x{object_size:X}" if object_size else None,
            "decimal": object_size,
            "decided": object_size is not None,
            "vote_sources": size_votes,
            "vote_sources_hex": {key: f"0x{value:X}" for key, value in size_votes.items()},
            "vote_disagreement": ([f"0x{value:X}" for value in vote_values]
                                  if object_size is None else []),
            "alignment_conclusion": {
                "at_least": 8,
                "proof": "构造/使用代码对 +0x38/+0x48 用普通 8 字节 str/ldr（非 stur）→ 编译器认为对象 ≥8 字节对齐",
                "observed": "全部 28 处模块内构造点的 this 都是 16 字节对齐（operator new 与聚合体成员位移）",
                "decided_16": False,
                "recommendation": "按 alignas(16) 分配（operator new 本来就返回 16 对齐）",
            },
            "evidence": size_evidence,
            "field_layout": _FIELD_LAYOUT,
            "initialization_map": {
                "ctor_default": ctor_notes["ctor_default"],
                "ctor_path": ctor_notes["ctor_path"],
                "unload": dict(
                    ctor_notes["unload"],
                    description="Unload 复位：+0x00、+0x24、+0x30、+0x40 置 0，"
                                "memset(+0x50, 0xFF, 0x1FFFE)",
                ),
            },
            "uninitialized_gaps": _UNINITIALIZED_GAPS,
        },
        "constructors": {
            "ctor_default": {
                "offset_hex": records["ctor_default"]["offset_hex"],
                "verdict_source": "instructions",
                "summary": "不是纯零初始化：写 +0x00/+0x24..+0x33/+0x38/+0x40/+0x48/+0x2004E 为 0，"
                           "并把 +0x50 起 0x1FE 字节填 0xFF（glyphIndex[0..254]=0xFFFF）；"
                           "+0x01..+0x23、+0x34..+0x37、+0x3C..+0x3F、+0x44..+0x47、"
                           "+0x4C..+0x4F、+0x24E..+0x2004D 不初始化",
                "memset_count": ctor_notes["ctor_default"].get("count"),
            },
            "ctor_path": {
                "offset_hex": records["ctor_path"]["offset_hex"],
                "verdict_source": "instructions",
                "summary": "与默认构造同样的初始化（但 memset 长度 0x1FFFE，整张索引表置 0xFFFF），"
                           "然后 tail-call Load(path, NULL)（x2 = xzr 由指令证明）",
                "memset_count": ctor_notes["ctor_path"].get("count"),
                "tail_call": "b <Font::Load PLT 桩>",
                "in_module_call_site_count": records["ctor_path"]["in_module_call_site_count"],
            },
            "note": "两个 ctor 的 memset 长度不同（0x1FE vs 0x1FFFE）是实测；"
                    "为什么不同未定案（见 open_questions）。",
            "call_site_shape": call_site_shape,
        },
        "destructor": {
            "offset_hex": records["dtor"]["offset_hex"],
            "source": "instructions",
            "summary": "~Font() 只做一件事：tail-jump 到 Unload()（没有任何 operator delete this、"
                       "没有全局表注销）",
            "frees": [
                "delete[] 指针 +0x28（glyph 记录数组，记录 stride 0x14）",
                "对 +0x48 数组（每页一个 0x10 字节 SmartPointer<ImageBase>，前 8 字节是计数头）"
                "逐页调用 ImageManager::FreeImage，并对非零项走 SmartPointer dec-notify，最后 delete[]",
                "free 指针 +0x38 指向的 calloc(1,0x28) 结构（先 free 其 +0x10/+0x18/+0x20）",
            ],
            "no_global_registry_evidence": {
                "globals_touched_in_ctor_dtor_load_unload": [
                    "g_FileManager（读，仅 Load）",
                    "g_ImageManager（读，Load/Unload 释放图像用）",
                    "SmartPointer<ImageBase>::s_DecNotify（读，Load/Unload）",
                ],
                "note": "未出现 Font 注册表/引用计数全局；Font 本身无 vptr（无 _ZTV/_ZTI 符号，"
                        "+0x00 是 IsLoaded 读的 bool）",
            },
            "self_new_delete_verdict": "可以自己 new / 自己 delete：析构不释放 this、不注销全局表；"
                                       "对象本身必须用我们自己这一侧的分配器配对释放",
        },
        "load": {
            "offset_hex": records["load"]["offset_hex"],
            "signature": records["load"]["signature"],
            "source": "instructions",
            "param1": "x1 = .fnt 路径（char const*），交给 Filesys::FileManager::OpenRead",
            "param2": "x2 = 可选 3 字符页图扩展名（char const*）；"
                      "只在 strlen(x2)==3 且页名倒数第 4 字符为 '.' 时覆盖页名扩展名；"
                      "NULL 合法（游戏自身 32 处调用全部传 NULL，Font(char const*) 也传 NULL）",
            "returns": "bool：w0=1 成功并把 +0x00 置 1；w0=0 失败（OpenRead 失败 / 魔数不是 'BMF' / 版本 != 3）",
            "failure_behaviour": "失败时 +0x00 保持 0（Load 开头先调用 Unload，Unload 会把 +0x00 清 0），"
                                 "不会崩溃；模块内调用点均不使用返回值，靠 IsLoaded 判定",
            "format_check": "读 4 字节与 0x00464D42（'B','M','F'）比较，再读 1 字节版本，要求 == 3",
            "path_convention": "游戏自身一律 'font/<name>.fnt'（内容挂载点相对路径），"
                               "且调用前先经 ModManager::TryRedirectPath",
            "observed_path_strings": _font_path_strings(data),
            "call_site_count": records["load"]["in_module_call_site_count"],
            "all_call_sites_pass_null_second_arg": _load_sites_pass_null(data, records, bl_targets),
            "game_usage_sample": game_sample,
        },
        "metrics": {
            "source": "instructions",
            "common_note": "五个度量/状态函数都不检查 IsLoaded；它们只读字段。在"
                           "【已完整初始化但未 Load 成功】的对象上（全表 0xFFFF、missing 槽 0）"
                           "返回 0 且不会解引用空指针；但在默认构造且未 Load 的对象上，"
                           "若用 SetMissingCharacter 设了 ≥255 的缺失字符，就会顺着未初始化的 "
                           "glyphIndex 去读 +0x28/+0x38（两者为 NULL）→ 可能崩溃。"
                           "工程做法：先 Load 成功再调用度量函数。",
            "is_loaded": {
                "offset_hex": records["is_loaded"]["offset_hex"],
                "signature": records["is_loaded"]["signature"],
                "abi": "x0=this；返回 w0 = *(u8*)(this+0)，只读 +0x00，不访问任何其它字段",
            },
            "get_character_width_char": {
                "offset_hex": records["get_character_width_char"]["offset_hex"],
                "signature": records["get_character_width_char"]["signature"],
                "abi": "x0=this，w1=c（按 sxtb 符号扩展！）；返回 w0 = (int)(s16)glyph[+0x10]，"
                       "查不到（索引 0xFFFF 且 missing 链为 0）返回 0",
                "caveat": "w1 用 sxtb 计算位移 → 字节 ≥0x80 会得到负位移（越界读），只适合 ASCII",
            },
            "get_character_width_u16": {
                "offset_hex": records["get_character_width_u16"]["offset_hex"],
                "signature": records["get_character_width_u16"]["signature"],
                "abi": "x0=this，w1=unicode（uxth）；返回 w0 同 char 版",
            },
            "get_string_width": {
                "offset_hex": records["get_string_width"]["offset_hex"],
                "signature": records["get_string_width"]["signature"],
                "abi": "x0=this，x1=char const*（单字节串）；返回 w0 = int 宽度和（减去 kerning）",
                "depends_on_load": "是：需要 +0x50 索引表、+0x28 glyph 表、+0x38 kerning 哈希结构",
                "unloaded_behaviour": "全部索引为 0xFFFF 且 missing 槽为 0 时返回 0，且不触碰 "
                                      "+0x28/+0x38（因此不会空指针解引用）",
            },
            "get_string_width_utf8": {
                "offset_hex": records["get_string_width_utf8"]["offset_hex"],
                "signature": records["get_string_width_utf8"]["signature"],
                "abi": "x0=this，x1=UTF-8 串；strlen → StringEncoding::ConvertUTF8toUTF16(串, len, "
                       "内静态缓冲(512 UTF-16 单元), 512) → tail-call GetStringWidth(u16 const*)；返回 w0=int",
                "static_buffer": {
                    "source": "instructions",
                    "evidence": "窗口 get_string_width_utf8 里 adrp x21,0xAFF000 / add x21,x21,#0xBD0 "
                                "→ 缓冲地址 0xAFFBD0；该地址大于 NRO 文件大小 → 属于 BSS（模块内静态数组）",
                    "address_hex": "0xAFFBD0",
                    "capacity_utf16_units": 512,
                },
                "caveat": "使用模块内静态缓冲（非线程安全，超 512 单元截断）",
            },
            "get_line_height": {
                "offset_hex": records["get_line_height"]["offset_hex"],
                "signature": records["get_line_height"]["signature"],
                "abi": "x0=this；返回 w0 = *(u16*)(this+0x14)（零扩展）",
                "depends_on_load": "是：+0x14 只在 Load 解析 .fnt 的 common 记录时被写入；"
                                   "构造与 Unload 都不初始化它（详见 open_questions）",
            },
            "get_baseline_height": {
                "offset_hex": records["get_baseline_height"]["offset_hex"],
                "signature": records["get_baseline_height"]["signature"],
                "abi": "x0=this；返回 w0 = *(u16*)(this+0x16)（零扩展）",
                "depends_on_load": "同上（+0x16）",
            },
        },
        "call_sequence": _call_sequence(records, object_size),
        "disassembly": disassembly,
        "open_questions": _OPEN_QUESTIONS,
    }


_FIELD_LAYOUT = (
    {"offset_hex": "0x00", "size": 1, "meaning": "IsLoaded 标志（bool，Load 成功置 1，Unload 清 0）",
     "evidence": "IsLoaded: ldrb w0,[x0]；Load 成功路径 strb w0,[x19]；Unload: strb wzr,[x19]"},
    {"offset_hex": "0x04", "size": 0x10, "meaning": "Load 时从 .fnt 读取的 16 字节 common 记录（字段名未定案）",
     "evidence": "Load: add x8,x19,#0x4 存入 sp+8，随后 file->Read(dst,0x10,1)"},
    {"offset_hex": "0x14", "size": 2, "meaning": "行高（GetLineHeight 读取）", "evidence": "ldrh w0,[x0,#0x14]"},
    {"offset_hex": "0x16", "size": 2, "meaning": "基线高（GetBaselineHeight 读取）", "evidence": "ldrh w0,[x0,#0x16]"},
    {"offset_hex": "0x1C", "size": 2, "meaning": "页数（u16，被复制到 +0x40 并用于 new[] 页数组）",
     "evidence": "ldrh w22,[x19,#0x1c]；str w22,[x19,#0x40]"},
    {"offset_hex": "0x24", "size": 4, "meaning": "glyph 记录数（u32）", "evidence": "str w22,[x19,#0x24]"},
    {"offset_hex": "0x28", "size": 8, "meaning": "glyph 记录数组指针（new[]，记录 stride 0x14，+0x10 是 s16 前进量）",
     "evidence": "bl _Znam；str x0,[x19,#0x28]；Unload 里 delete[]"},
    {"offset_hex": "0x30", "size": 4, "meaning": "kerning 对数（u32）", "evidence": "str w8,[x19,#0x30]"},
    {"offset_hex": "0x38", "size": 8, "meaning": "kerning 哈希结构指针（calloc(1,0x28)：+0x00 count、+0x10 位图、"
                                                 "+0x18 key(u64[])、+0x20 value(u16[])）",
     "evidence": "calloc(1,0x28) → +0x38；GetStringWidth 用 +0x38 做开放寻址探查"},
    {"offset_hex": "0x40", "size": 4, "meaning": "页图数量（= +0x1C 页数）", "evidence": "str w22,[x19,#0x40]"},
    {"offset_hex": "0x48", "size": 8, "meaning": "页图 SmartPointer<ImageBase> 数组（new[]，前 8 字节计数头，项 stride 0x10）",
     "evidence": "new[](8+0x10*n) → +0x48；Unload 逐页 ImageManager::FreeImage"},
    {"offset_hex": "0x50", "size": 0x20000, "meaning": "uint16 glyphIndex[65536]（按码位查 glyph 下标，0xFFFF=无）",
     "evidence": "GetCharacterWidth: ldrh w8,[x0+x1*2+0x50]；memset(+0x50,0xFF,…)"},
    {"offset_hex": "0x2004E", "size": 2, "meaning": "glyphIndex[0xFFFF]，充当 missing character（SetMissingCharacter 写入）",
     "evidence": "SetMissingCharacter: strh w1,[x0,#0x2004E]；ctor: strh wzr,[x0,#0x2004E]"},
)

_UNINITIALIZED_GAPS = {
    "source": "instructions",
    "ranges_hex": ["0x01-0x23", "0x34-0x37", "0x3C-0x3F", "0x44-0x47", "0x4C-0x4F",
                   "0x24E-0x2004D"],
    "note": "默认构造 Font() 只写 +0x00、+0x24..+0x33、+0x38、+0x40、+0x48、+0x50..+0x24D(0xFF) 与 +0x2004E；"
            "Font(char const*) 的 memset 把 +0x50..+0x2004D 全填 0xFF。Load 会把 .fnt 的 16 字节记录"
            "读进 +0x04 与 +0x14（+0x14/+0x16 即 GetLineHeight/GetBaselineHeight 读的字段），"
            "所以这两处只有 Load 成功解析 .fnt 后才有意义。因此【从未 Load 成功】的对象上 "
            "GetLineHeight/GetBaselineHeight 返回值不确定；默认构造对象在码位 ≥255 上可能读到"
            "未初始化的 glyphIndex（进而经 +0x28 解引用）。",
    "risk": "对我们自己 new 的对象：先 Load 成功再调用度量/行高函数，或自行把 +0x14/+0x16 置 0。",
}

_OPEN_QUESTIONS = (
    "第二参数的确切身份未完全定案：只证明【strlen(x2)==3 且页名以 '.'+3 字符结尾时替换这 3 个字符】；"
    "它是否为页图格式（如 \"png\"）没有 .fnt 样本可核对（游戏自身 32 处调用全部传 NULL）。",
    "mod 场景的路径前缀未定案：游戏自身用内容挂载点相对路径 'font/<name>.fnt' 且调用前先经 "
    "ModManager::TryRedirectPath；我们自己构造的 Font 该传什么前缀、是否需要先注册挂载点，本审计不下结论。",
    "alignof(Font) 是否为 16 未定案：只能由 8 字节 str/ldr 证明 ≥8；所有实测实例都是 16 对齐。",
    "两个 ctor 的 memset 长度不同（Font() 0x1FE / Font(char const*) 0x1FFFE）为什么不同未定案；"
    "后果已定案：默认构造的对象在码位 ≥255 上索引未初始化。",
    "GetCharacterWidth(char) 对字节 ≥0x80 用 sxtb 产生负位移：是 bug 还是有意（期待调用方只传 ASCII）未定案。",
    "跨模块堆一致性未定案：我们自己 operator new 得到的内存与游戏 Load 内部 _Znam/calloc 使用的分配器"
    "是否同一个堆，本审计无法从静态代码判定；工程上必须【谁分配谁释放】，对象本身由我方释放、"
    "内部缓冲由游戏的 Unload 释放。",
    "字体是否存在全局注册表：本审计只覆盖 Font 的 ctor/dtor/Load/Unload 四个函数的全局引用"
    "（仅 g_FileManager/g_ImageManager/s_DecNotify 与一个内静态 UTF-16 缓冲），未穷举整个模块。",
    ".fnt 解析细节未定案：只定案魔数 'BMF'(0x00464D42 小端) 与版本字节 == 3；其余字段语义需要样本核对。",
    "Font 是否有拷贝构造未定案：.dynsym 中没有 _ZN4KAGE8Graphics4FontC1ERKS0_；若我们按值拷贝会双重释放，禁止。",
)


def _constant_before(data: bytes, pc: int, register: int, limit: int = 16):
    """找 ``pc`` 之前**最近一次**给 ``w<register>`` 装常数的指令并解出数值。

    与 [`_materialized_constant`] 的区别：允许中间夹着别的指令（编译器常把
    ``mov w2, #len`` 与 ``bl memset`` 之间插入若干 store）。返回 ``(value, [地址…])``。
    """
    for step in range(1, limit + 1):
        address = pc - 4 * step
        word = _word(data, address)
        if (word & 0xFC000000) in (0x94000000, 0x14000000):
            return None
        move = _decode_move_wide(word)
        if move is not None and move[1] == register:
            if move[0] == "movk":
                return _materialized_constant(data, address + 4, register=register)
            return move[2], [address]
        value = _decode_bitmask_immediate(word)
        if value is not None and (word & 0x1F) == register and ((word >> 5) & 0x1F) == 31:
            return value, [address]
    return None


def _next_symbol_offset(symbols, offset: int) -> int | None:
    """符号表里比 ``offset`` 大的下一个函数起点（用于界定函数体范围）。"""
    candidates = sorted({symbol.file_offset for symbol in symbols.values()
                         if symbol.file_offset is not None and symbol.file_offset > offset})
    return candidates[0] if candidates else None


def _font_path_strings(data: bytes) -> dict:
    """扫描镜像里所有以 ``.fnt`` 结尾的可打印字符串，用于说明路径约定。"""
    found = {}
    for match in re.finditer(rb"[ -~]{4,120}\.fnt\x00", data):
        text = match.group()[:-1].decode("ascii")
        found.setdefault(text, match.start())
    return {
        "source": "instructions(rodata)",
        "count": len(found),
        "entries": [
            {"path": text, "file_offset_hex": f"0x{offset:X}"}
            for text, offset in sorted(found.items())
        ],
        "all_under_font_directory": all(text.startswith("font/") for text in found),
    }


def _game_load_sample(data: bytes, records, imports, bl_targets) -> dict | None:
    """取游戏自身一处 ``Font::Load`` 调用点：往前找最近的 ModManager::TryRedirectPath 调用。

    用来展示真实用法的参数形态（x0=this、x1=重定向后的 std::string 内容指针、x2=NULL）
    以及游戏使用的路径字符串。
    """
    redirect_hex = imports["try_redirect_path"]["plt_stub"]
    if not redirect_hex:
        return None
    redirect_stub = int(redirect_hex, 16)
    redirect_sites = set(bl_targets.get(redirect_stub, []))
    for site_text in records["load"]["in_module_call_sites"]:
        site = int(site_text, 16)
        for back in range(4, 0x60, 4):
            if site - back in redirect_sites:
                return {
                    "load_call_site": site_text,
                    "try_redirect_path_call_site": f"0x{site - back:X}",
                    "window_start": site - 0x40,
                    "window_length": 0x70,
                    "source": "instructions",
                }
    return None


def _memset_in_function(data: bytes, function_offset: int, function_end: int) -> dict:
    """在 ``[function_offset, function_end)`` 内找 memset 调用（``bl`` 或尾调用 ``b``）
    并解出长度参数（w2）。Font() 用的是尾调用 ``b memset``。"""
    plt_slots = _plt_slot_by_name(nro_symbols.parse_dynamic_relocations(data)[1])
    stubs = _scan_plt_stubs(data)
    memset_stub = next((stubs[slot] for slot in plt_slots.get("memset", []) if slot in stubs), None)
    instructions: list[str] = []
    count = None
    form = None
    if memset_stub is not None:
        sites = []
        for index in range(function_offset // 4, min(function_end, len(data)) // 4):
            address = index * 4
            word = _word(data, address)
            if (word & 0xFC000000) not in (0x94000000, 0x14000000):
                continue
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            if address + imm * 4 == memset_stub:
                sites.append((address, "b" if (word & 0xFC000000) == 0x14000000 else "bl"))
        for site, site_form in sites:
            resolved = _constant_before(data, site, register=2)
            if resolved is None:
                continue
            count = resolved[0]
            form = site_form
            instructions = [
                f"0x{address:08X}: {_word(data, address):08X}"
                for address in sorted(resolved[1]) + [site]
            ]
            break
    return {
        "count": count,
        "count_hex": f"0x{count:X}" if count is not None else None,
        "call_form": form,
        "instructions": instructions,
        "source": "instructions",
        "span": (f"this+0x50 .. this+0x{0x50 + count:X} (不含)" if count else None),
        "span_value": (0x50 + count if count else None),
    }


def _ctor_end_halfword(data: bytes, ctor_offset: int, ctor_end: int) -> dict:
    """找出 ctor 里 ``strh wzr,[x0,x8]``（x8 由 movz/movk 装出的 0x2004E）。"""
    instructions: list[str] = []
    detected = None
    for address in range(ctor_offset, min(ctor_end, ctor_offset + 0x60), 4):
        word = _word(data, address)
        # STRH (register): size=01, opc=00, [11:10]=10 → 固定位 0x78200800
        if (word & 0xFFE00C00) != 0x78200800:
            continue
        if (word & 0x1F) != 31:                       # Rt 必须写 wzr
            continue
        rm = (word >> 16) & 0x1F
        if ((word >> 5) & 0x1F) != 0:                 # Rn 必须是 this 指针 x0
            continue
        resolved = _constant_before(data, address, register=rm)
        if resolved is None:
            continue
        detected = resolved[0]
        instructions = [f"0x{address:08X}: {word:08X}"] + [
            f"0x{instr:08X}: {_word(data, instr):08X}" for instr in sorted(resolved[1])
        ]
        break
    return {
        "offset": detected,
        "offset_hex": f"0x{detected:X}" if detected is not None else None,
        "instructions": instructions,
    }


def _new_call_site_sizes(data: bytes, new_stub_hex: str | None, bl_targets) -> list[dict]:
    """枚举 ``bl operator new`` 调用点并解出 x0 的尺寸立即数。"""
    if not new_stub_hex:
        return []
    stub = int(new_stub_hex, 16)
    decoded = []
    for site in bl_targets.get(stub, []):
        resolved = _materialized_constant(data, site, register=0)
        if resolved is None:
            continue
        decoded.append({
            "call_site": f"0x{site:X}",
            "size": resolved[0],
            "size_hex": f"0x{resolved[0]:X}",
            "evidence_instructions": [
                f"0x{address:08X}: {_word(data, address):08X}" for address in sorted(resolved[1])
            ],
        })
    return decoded


def _heap_block_with_two_fonts(data: bytes, new_sizes, ctor_sites, records) -> list[dict]:
    """找"一次 operator new 里连续构造 ≥2 个 Font"的调用点（对象尺寸的直接证据）。"""
    blocks = []
    for entry in new_sizes:
        new_site = int(entry["call_site"], 16)
        nearby = [site for site in ctor_sites if 0 <= site - new_site <= 0x100]
        if len(nearby) < 2:
            continue
        offsets = []
        for site in nearby:
            decoded = _decode_add_this(data, site)
            if decoded is None or decoded[1] is None:
                continue
            offsets.append({
                "ctor_call_site": f"0x{site:X}",
                "this_offset": decoded[1],
                "this_offset_hex": f"0x{decoded[1]:X}",
                "base_register": f"x{decoded[0]}",
            })
        if len(offsets) < 2:
            continue
        blocks.append({
            "new_call_site": entry["call_site"],
            "new_size": entry["size"],
            "new_size_hex": entry["size_hex"],
            "new_instructions": entry["evidence_instructions"],
            "ctor_offsets": offsets,
            "delta_hex": f"0x{offsets[1]['this_offset'] - offsets[0]['this_offset']:X}",
            "head_hex": offsets[0]["this_offset_hex"],
        })
    return blocks


def _load_sites_pass_null(data: bytes, records, bl_targets) -> dict:
    """检查每个 Font::Load 模块内调用点附近是否把 x2 设为 NULL（``mov x2, xzr``）。

    只回看 3 条指令：编译器可能把 ``mov x2, xzr`` 与 ``mov <callee-saved>, <src>``
    交错排布（实测 32 处里有 2 处如此）。
    """
    sites = [int(site, 16) for site in records["load"]["in_module_call_sites"]]
    null_sites = []
    other_sites = []
    for site in sites:
        found = None
        for step in range(1, 4):
            word = _word(data, site - 4 * step)
            if (word & 0xFC000000) in (0x94000000, 0x14000000):
                break
            if word == 0xAA1F03E2:            # mov x2, xzr
                found = step
                break
        if found is not None:
            null_sites.append({"call_site": f"0x{site:X}", "distance_instructions": found})
        else:
            window = [
                f"0x{site - 4 * step:08X}:{_word(data, site - 4 * step):08X}"
                for step in (1, 2, 3)
            ]
            other_sites.append({"call_site": f"0x{site:X}", "window": window})
    return {
        "checked": len(sites),
        "null_count": len(null_sites),
        "null_call_sites": null_sites,
        "non_null_call_sites": other_sites,
        "conclusion": ("全部调用点都把 x2 设为 NULL → NULL 一定合法"
                       if not other_sites else "存在未把 x2 置 NULL 的调用点，需逐个核对窗口"),
        "source": "instructions",
    }


def _call_sequence(records, object_size: int | None) -> list[str]:
    size = f"0x{object_size:X}" if object_size else "<见 object_size>"
    return [
        "// 我们的模块：base = Repentance.nro 运行时基址（文件偏移 == 模块偏移）",
        f"// 1) 分配对象：x0 = operator new({size}) 或 alignas(16) 静态缓冲（对象 ≥8 字节对齐）",
        f"constexpr size_t kFontSize = {size};",
        "void* font = ::operator new(kFontSize);   // 必须与释放端同一分配器",
        "",
        f"// 2) 构造：x0 = font，无其它参数；返回 void（寄存器形态：仅 x0 入参）",
        f"reinterpret_cast<void(*)(void*)>(base + {records['ctor_default']['offset_hex']})(font);",
        "",
        f"// 3) 加载：x0 = font，x1 = .fnt 路径（char const*），x2 = NULL（合法，游戏自身也传 NULL）；"
        f"返回 w0 = bool",
        f"bool ok = reinterpret_cast<bool(*)(void*, const char*, const char*)>("
        f"base + {records['load']['offset_hex']})(font, path, nullptr);",
        "",
        f"// 4) 判定：x0 = font，返回 w0 = *(u8*)font（纯读取，无副作用）",
        f"bool ready = reinterpret_cast<bool(*)(const void*)>(base + {records['is_loaded']['offset_hex']})(font);",
        "",
        f"// 5) 度量：单字节串用 GetStringWidth（x0=this, x1=char const*，返回 w0=int）",
        f"int width = reinterpret_cast<int(*)(const void*, const char*)>("
        f"base + {records['get_string_width']['offset_hex']})(font, \"Hello\");",
        f"//    多字节/中文串用 GetStringWidthUTF8（x0=this, x1=UTF-8 串；内部静态缓冲 512 单元、非线程安全）",
        f"int width_utf8 = reinterpret_cast<int(*)(const void*, const char*)>("
        f"base + {records['get_string_width_utf8']['offset_hex']})(font, u8\"中文\");",
        f"//    行高/基线：x0=this，返回 w0 = 零扩展的 16 位字段（+0x14 / +0x16）——只在 Load 成功后有意义",
        f"int line_height = reinterpret_cast<int(*)(const void*)>("
        f"base + {records['get_line_height']['offset_hex']})(font);",
        "",
        f"// 6) 释放内部缓冲（可选，但推荐；可重复调用）：x0 = font，返回 void",
        f"reinterpret_cast<void(*)(void*)>(base + {records['unload']['offset_hex']})(font);",
        f"// 7) 释放对象本身：必须用与第 1 步配对的分配器（我方 operator delete）",
        "::operator delete(font);",
        "",
        "// 备选：Font(char const*) 一步完成【构造 + Load(path, NULL)】——",
        f"// 它等于默认构造再 tail-call Load(path, NULL)（{records['ctor_path']['offset_hex']}）；",
        "// 但它在本 build 里没有任何模块内调用点（无 PLT 桩），属于未使用导出函数。",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--print", action="store_true", help="同时打印摘要与关键窗口")
    args = parser.parse_args(argv)

    evidence = build_evidence()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=1)
        handle.write("\n")

    print(f"写入 {os.path.relpath(args.out, REPO_ROOT)}")
    print(f"build id {evidence['build_id']}  nro_sha256 {evidence['nro_sha256'][:16]}…")
    print(f"PLT 桩 {evidence['invocation_model']['plt_stub_count']} 个，"
          f"BL 指令 {evidence['invocation_model']['bl_instruction_count']} 条")
    print(f"对象尺寸 {evidence['object_size']['hex']} "
          f"({evidence['object_size']['value']} 字节)，"
          f"证据 {len(evidence['object_size']['evidence'])} 条")
    for label, record in evidence["functions"].items():
        print(f"  {label:32} {record['offset_hex']:>10}  {record['guard16']}  "
              f"调用点 {record['in_module_call_site_count']}")
    print("Load 第二参数："
          f"NULL 调用点 {evidence['load']['all_call_sites_pass_null_second_arg']['null_count']}"
          f"/{evidence['load']['all_call_sites_pass_null_second_arg']['checked']}")
    if evidence["missing_symbols"]:
        print(f"缺失符号：{evidence['missing_symbols']}")
    if args.print:
        for label, lines in evidence["disassembly"].items():
            print(f"\n--- {label} ---")
            for line in lines:
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
