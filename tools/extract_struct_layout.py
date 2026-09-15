#!/usr/bin/env python3
"""从固定 NRO 的某个函数体里抽出"结构体字段访问序列"—— 布局表的证据来源。

## 为什么需要它

"某个字段在结构体的第几字节"是**结构体布局类 API**（字段直读）的唯一合法输入。
本项目此前是一个字段、一个字段靠人肉反汇编去追，追完把偏移写进 `runtime_constants.hpp`，
**证据本身不进仓库**（只留一句注释）。后果：别人无法复核，也没法批量。

这个工具把"追一个字段"变成"追一个函数"：给定函数与"哪个寄存器/第几个参数是结构体指针"，
它按**指令顺序**列出每一次 `LDR/STR [指针, #偏移]`，并带上：
访问方向、访问宽度、**指令地址**（这就是证据出处）。

## 为什么优先用这几类函数

同一结构体上，不同函数的"信息密度"差很多，按性价比排序：

1. **序列化**（`GameState::read_Room`/`write_Room` 这类）：**逐个字段**按宽度读写 ⇒ 一次拿到
   几乎全部字段的 (偏移, 宽度) 与出现顺序；
2. **`Reset()` / 拷贝构造 / 移动赋值**：密集写/拷全部字段 ⇒ 与序列化互相印证；
3. **语义已知的业务函数**（例如 `Level::precalc_allowed_doors`、`can_convert_to_red_treasure_room`）：
   只碰一两个字段，但**语义是确定的** ⇒ 用来给具体字段"命名"。

前两类给偏移和顺序，第三类给名字。**顺序 + 宽度都吻合才允许套名字**；对不上的只登记偏移。

## 用法

    python3 tools/extract_struct_layout.py --nro <Repentance.nro> \
        --symbol _ZN15IsaacRepentance9GameState10write_RoomERNS_19GameStateRoomConfigERKNS_14RoomDescriptorERNS_11GameStateIOE \
        --arg 2

`--arg N` 是"结构体指针是第 N 个参数"（0 起算，成员函数的 0 是 this）。工具会在函数体里
找一次 `mov xK, xN` 来确定承载它的寄存器；也可以直接用 `--reg xK` 指定。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import nro_disasm  # noqa: E402
import nro_symbols  # noqa: E402

# 访存指令 → 访问宽度（字节）。`ldr w` / `str w` 是 4 字节，`ldr x` / `str x` 是 8 字节。
_MNEMONIC_WIDTH = {
    'ldrb': 1, 'ldrsb': 1, 'strb': 1,
    'ldrh': 2, 'ldrsh': 2, 'strh': 2,
    'ldr': None, 'str': None,   # 由目标寄存器宽度决定
}

# ARM64 的三种寻址都要认，否则偏移会错：
#   [xN, #imm]      —— 普通
#   [xN, #imm]!     —— **pre-index 写回**：访问后 xN 自身也加上 imm（后续访问的真实偏移要跟着变）
#   [xN], #imm      —— post-index：本次按 xN 当前值访问，之后 xN += imm
# 第一版只认第一种，于是 `Reset()` 里 `ldr x23, [x20, #0x90]!` 之后的 `[x20, #0x8]`
# 被记成偏移 0x8，而真实偏移是 0x98（容器 begin/end）—— 这类错值会直接污染布局表。
_ACCESS = re.compile(
    r'^(?P<address>[0-9a-f]{8})\s+[0-9a-f]{8}\s+(?P<mnemonic>ldr|ldrb|ldrh|ldrsb|ldrsh|str|strb|strh)'
    r'\s+(?P<target>[wx]\d+),\s*\[(?P<base>x\d+)(?:,\s*#(?P<offset>0x[0-9a-f]+|\d+))?\]'
    r'(?P<writeback>!)?(?:,\s*#(?P<post>0x[0-9a-f]+|\d+))?'
)
# 成对访存（`ldp`/`stp`）是拷贝构造与逐字段复制的**主力**：一次搬 16 字节。
# 第一版只认单寄存器的 `ldr/str`，于是在拷贝构造上只看到 3 次访问（全是容器指针），
# 漏掉了全部标量字段 —— 报"字段很少"是假象，不是结构体真的只有几个字段。
_PAIR = re.compile(
    r'^(?P<address>[0-9a-f]{8})\s+[0-9a-f]{8}\s+(?P<mnemonic>ldp|stp)'
    r'\s+(?P<first>[wx]\d+),\s*(?P<second>[wx]\d+),\s*\[(?P<base>x\d+)'
    r'(?:,\s*#(?P<offset>0x[0-9a-f]+|\d+))?\]'
)
_MOV = re.compile(r'^[0-9a-f]{8}\s+[0-9a-f]{8}\s+mov\s+(?P<dst>x\d+),\s*(?P<src>x\d+)$')
# `mov wN, #imm`：把常量装进寄存器。它自己不是访存，但**下一条 `add` 会拿它当偏移**，
# 所以必须跟（见 `_ADD_REG` 的说明）。
_MOV_IMMEDIATE = re.compile(
    r'^[0-9a-f]{8}\s+[0-9a-f]{8}\s+mov\s+(?P<dst>[wx]\d+),\s*#(?P<imm>0x[0-9a-f]+|\d+)'
)
# `movz`/`movk`：大常量分两三条装（`mov w9,#0xf9b0` + `movk w9,#0x24,lsl#16` = 0x24f9b0）。
_MOV_WIDE = re.compile(
    r'^[0-9a-f]{8}\s+[0-9a-f]{8}\s+(?P<mnemonic>movz|movk)\s+(?P<dst>[wx]\d+),\s*'
    r'#(?P<imm>0x[0-9a-f]+|\d+)(?:,\s*lsl\s*#(?P<shift>\d+))?'
)
# `add xD, xS, xN`：偏移来自寄存器 ⇒ 与 `_MOV_IMMEDIATE` 合起来就是"结构体 + 固定偏移"。
# 为什么必须认这种形态（2026-09-15 实测）：`Room::WorldToScreenPosition` 读滚动偏移用的是
#   `mov w8, #0x1938` / `add x1, x19, x8`（把字段**地址**传给 `Vector2::operator+`），
# 全是"取地址传参"而不是 `ldr [x19,#0x1938]` ⇒ 旧版一条都提取不到，会误判成"这个函数没碰字段"。
_ADD_REG = re.compile(
    r'^[0-9a-f]{8}\s+[0-9a-f]{8}\s+add\s+(?P<dst>x\d+),\s*(?P<src>x\d+),\s*(?P<off>[wx]\d+)'
)
# `add xD, xS, #imm` / `sub`：常见于"子对象地址 = 结构体基址 + 固定偏移"。
_ADD_IMM = re.compile(
    r'^[0-9a-f]{8}\s+[0-9a-f]{8}\s+(?P<mnemonic>add|sub)\s+(?P<dst>x\d+),\s*(?P<src>x\d+),\s*#(?P<imm>0x[0-9a-f]+|\d+)$'
)
# 任何"写寄存器"的指令（目的寄存器是第一个操作数）。被它写过的跟踪寄存器要**停止跟踪**：
# 那一刻起它装的是别的东西（例如 `ldr x21, [x20, #0x90]` 装的是容器 begin 指针），
# 继续按"结构体指针 + 偏移"解释就会产生假字段。
_WRITES_REGISTER = re.compile(r'^[0-9a-f]{8}\s+[0-9a-f]{8}\s+\w+\s+(?P<dst>x\d+|w\d+)(?:,|\s|$)')
_RET = re.compile(r'\bret\b')


def _register_slot(name: str) -> str:
    """把 `w8`/`x8` 归一到同一个键：它们是同一个寄存器的不同宽度视图。

    踩过的坑（2026-09-15）：`Room::WorldToScreenPosition` 里是 `mov w8, #0x1938` 配
    `add x1, x19, x8` —— 按原样存键（`w8`）去查（`x8`）永远查不到，于是这条字段访问被静默丢掉。
    """
    return name[1:] if name[:1] in ("w", "x") else name


def parse_accesses(lines: list[str], registers: set[str]) -> list[dict[str, object]]:
    """按指令顺序抽出 `[寄存器, #偏移]` 的每一次访问（寄存器可以有一组，见 `resolve_registers`）。"""
    accesses: list[dict[str, object]] = []
    # 每个寄存器的"当前偏移基准"：写回寻址会改变它（见 `_ACCESS` 上方的说明）。
    bias: dict[str, int] = {}
    tracked = set(registers)
    # 寄存器里的常量（`mov`/`movz`/`movk` 装进去的）：`add xD, xS, xN` 要拿它当偏移。
    constants: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        # 先更新"这个寄存器现在指向哪里"，再做访存判定。
        moved = _MOV.match(stripped)
        if moved is not None:
            src, dst = moved.group('src'), moved.group('dst')
            if src in tracked:
                tracked.add(dst)
                bias[dst] = bias.get(src, 0)
            elif dst in tracked:
                tracked.discard(dst)      # 搬来的不是结构体指针了
            continue
        loaded = _MOV_IMMEDIATE.match(stripped)
        if loaded is not None:
            constants[_register_slot(loaded.group('dst'))] = int(loaded.group('imm'), 0)
            continue
        wide = _MOV_WIDE.match(stripped)
        if wide is not None:
            dst = _register_slot(wide.group('dst'))
            value = int(wide.group('imm'), 0)
            shift = int(wide.group('shift') or 0)
            if wide.group('mnemonic') == 'movz':
                constants[dst] = value << shift
            else:
                previous = constants.get(dst, 0) & ~(0xFFFF << shift)
                constants[dst] = previous | (value << shift)
            continue
        combined_reg = _ADD_REG.match(stripped)
        if combined_reg is not None:
            src, off = combined_reg.group('src'), combined_reg.group('off')
            if src in tracked and _register_slot(off) in constants:
                # 字段**地址**被算出来交给别人（例如传给 `Vector2::operator+`）。
                # 宽度在指令里看不出来（0 = 未知），由布局表的作者按类型注上。
                accesses.append({
                    'address': '0x' + stripped.split()[0],
                    'base': src,
                    'offset': bias.get(src, 0) + constants[_register_slot(off)],
                    'width': 0,
                    'direction': 'read',
                    'mnemonic': 'add',
                })
            continue
        combined = _ADD_IMM.match(stripped)
        if combined is not None:
            src, dst = combined.group('src'), combined.group('dst')
            if src in tracked:
                delta = int(combined.group('imm'), 0)
                tracked.add(dst)
                bias[dst] = bias.get(src, 0) + (delta if combined.group('mnemonic') == 'add' else -delta)
            elif dst in tracked:
                tracked.discard(dst)
            continue
        write_reg = _WRITES_REGISTER.match(stripped)
        if write_reg is not None:
            constants.pop(_register_slot(write_reg.group('dst')), None)
            if write_reg.group('dst') in tracked and not stripped.startswith('st'):
                tracked.discard(write_reg.group('dst'))
        registers = tracked
        pair = _PAIR.match(stripped)
        if pair is not None and pair.group('base') in registers:
            base_offset = int(pair.group('offset'), 0) if pair.group('offset') else 0
            width = 4 if pair.group('first').startswith('w') else 8
            direction = 'write' if pair.group('mnemonic') == 'stp' else 'read'
            for index in (0, 1):
                accesses.append({
                    'address': '0x' + pair.group('address'),
                    'base': pair.group('base'),
                    'offset': base_offset + index * width,
                    'width': width,
                    'direction': direction,
                    'mnemonic': pair.group('mnemonic'),
                })
            continue
        match = _ACCESS.match(stripped)
        if match is None:
            continue
        base = match.group('base')
        immediate = int(match.group('offset'), 0) if match.group('offset') else 0
        post = int(match.group('post'), 0) if match.group('post') else 0
        # 写回只对跟踪中的寄存器有意义；其它寄存器的写回不影响我们的偏移基准。
        if base in registers:
            mnemonic = match.group('mnemonic')
            target = match.group('target')
            width = _MNEMONIC_WIDTH[mnemonic]
            if width is None:
                width = 4 if target.startswith('w') else 8
            accesses.append({
                'address': '0x' + match.group('address'),
                'base': base,
                'offset': bias.get(base, 0) + immediate,
                'width': width,
                'direction': 'write' if mnemonic.startswith('str') else 'read',
                'mnemonic': mnemonic,
            })
        if post:
            bias[base] = bias.get(base, 0) + post
        elif match.group('writeback'):
            bias[base] = bias.get(base, 0) + immediate
    return accesses


def resolve_registers(lines: list[str], argument: int) -> set[str]:
    """返回**一组**承载结构体指针的寄存器：参数寄存器本身 + 函数开头把它搬过去的那些。

    为什么是一组、而不是一个：编译器两种写法都会用 —— 拷贝构造里既有 `mov x20, x1`（源），
    也会**直接用参数寄存器** `stp x8, x9, [x0, #0x18]`（目标）。第一版只跟踪搬过去的那一个，
    于是在拷贝构造上只看到容器区、漏掉整个标量前缀（看起来像"这个结构体没几个字段"）。

    只扫函数开头：再往后参数寄存器会被复用，把别的对象的访问算进来就会污染结论。
    交叉验证靠"多个函数 + 宽度/顺序一致"，所以这里宁可宽一点，但绝不隐藏用了哪个寄存器
    （每条访问都记 `base`，报告里看得到）。
    """
    wanted = f'x{argument}'
    registers = {wanted}
    for line in lines[:40]:
        match = _MOV.match(line.strip())
        if match and match.group('src') == wanted:
            registers.add(match.group('dst'))
    return registers


def function_lines(nro: str, start: int, limit: int = 4096) -> list[str]:
    """反汇编到函数返回（或到上限），返回指令行。"""
    lines = nro_disasm.disassemble(nro, start, limit, annotate=False)
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^[0-9a-f]{8}\s', stripped):
            if _RET.search(stripped) and body:
                body.append(stripped)
                break
            body.append(stripped)
    return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--nro', required=True)
    ap.add_argument('--symbol', help='动态符号名（用它定位函数地址）')
    ap.add_argument('--address', type=lambda value: int(value, 0), help='或直接给文件偏移')
    ap.add_argument('--arg', type=int, help='结构体指针是第几个参数（0 起算）')
    ap.add_argument('--reg', help='或直接给寄存器名（例如 x19）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    path = pathlib.Path(args.nro)
    if not path.is_file():
        print(f'找不到 NRO：{args.nro}', file=sys.stderr)
        return 2

    address = args.address
    if address is None:
        if not args.symbol:
            print('需要 --symbol 或 --address', file=sys.stderr)
            return 2
        _build_id, symbols = nro_symbols.parse_dynamic_symbols(path.read_bytes())
        symbol = symbols.get(args.symbol)
        if symbol is None or not symbol.is_defined:
            print(f'符号表里没有已定义的 {args.symbol}', file=sys.stderr)
            return 2
        address = symbol.file_offset

    lines = function_lines(str(path), address)
    if not lines:
        print(f'在 {address:#x} 反汇编不出指令', file=sys.stderr)
        return 2

    registers: set[str] = set()
    if args.reg:
        registers = {item.strip() for item in args.reg.split(',') if item.strip()}
    if not registers:
        if args.arg is None:
            print('需要 --arg 或 --reg 来说明哪个寄存器是结构体指针', file=sys.stderr)
            return 2
        registers = resolve_registers(lines, args.arg)
    if not registers:
        print('没能确定承载结构体指针的寄存器 —— 请用 --reg 明确指定', file=sys.stderr)
        return 2

    accesses = parse_accesses(lines, registers)
    if args.json:
        print(json.dumps({
            'symbol': args.symbol, 'address': f'0x{address:x}',
            'registers': sorted(registers), 'instruction_count': len(lines), 'accesses': accesses,
        }, ensure_ascii=False, indent=1))
        return 0

    reads = [a for a in accesses if a['direction'] == 'read']
    writes = [a for a in accesses if a['direction'] == 'write']
    print(f'函数 {args.symbol or hex(address)} @ {address:#x}，{len(lines)} 条指令，'
          f'结构体指针在 {",".join(sorted(registers))}')
    print(f'  字段读 {len(reads)} 次、写 {len(writes)} 次')
    print(f"{'方向':<4} {'偏移':>8} {'宽度':>4} {'寄存器':<5} 指令")
    for access in accesses:
        print(f"{access['direction']:<4} {access['offset']:>#8x} {access['width']:>4} "
              f"{access['base']:<5} {access['address']}  {access['mnemonic']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
