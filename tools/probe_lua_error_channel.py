#!/usr/bin/env python3
"""只读探针：从设备上正在运行的以撒里取「运行时的 Lua 错误通道」与相关计数。

为什么要它：EID 大量 `pcall`/静默容忍，出问题时**不报错、只让功能静默失效**。
运行时把最后一次 Lua 错误的**整段文本**留在 `.bss`（`g_LastLuaErrorText`，256 字节）
里，接上调试桩就能直接把"哪一行、什么消息"读出来，不用再花一轮真机改脚本。

读的东西（偏移全部来自 **本地同一份 `runtime.elf` 的符号表**，见 `tools/elf_syms.py`）：

    g_LastLuaErrorLength   最后一次 Lua 错误的字节数
    g_LastLuaErrorText     最后一次 Lua 错误的整段文本
    g_CallbackError / g_ModRegistered  回调里出过错 / 模组已登记
    g_RequireFailureTotal  `require` 失败次数；配套的 Name/Code/ErrorTail 是"哪一个模块"
    g_Dispatchable / g_Unhooked        登记的回调里"有派发点 / 永远不触发"的条数

用法：
    python3 tools/probe_lua_error_channel.py            # 全自动（取 pid + 基址 + 自校验）
    python3 tools/probe_lua_error_channel.py --pid 141 --base 0x4e8a181000
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import read_device_state_via_gdb as base_tool  # noqa: E402

# 符号偏移：**必须与设备上那份模块同版本**（脚本会做身份自校验）。
SYMS = {
    'identity': 0x27cd8,
    'g_LastLuaErrorLength': 0x333c94,
    'g_LastLuaErrorText': 0x333c98,
    'g_CallbackError': 0x333d9c,
    'g_ModRegistered': 0x333da4,
    'g_RequireFailureTotal': 0x233670,
    'g_RequireLastFailureName': 0x233678,
    'g_RequireLastFailureCode': 0x233680,
    'g_RequireFirstFailureName': 0x233688,
    'g_RequireFirstFailureCode': 0x233690,
    'g_RequireErrorTail': 0x233698,
    'g_RequireFailureDetail': 0x2336a0,
    'g_Dispatchable': 0x23407b0,
    'g_Unhooked': 0x23407ac,
}
TEXT_CAPACITY = 256


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip', default='192.168.124.11')
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--elf', default=base_tool.DEFAULT_ELF)
    args = ap.parse_args()

    elf = base_tool.ROOT / args.elf
    if not elf.exists():
        print(f'找不到本地构建的 ELF：{args.elf}（偏移自校验需要它）')
        return 2

    pid = args.pid or base_tool.find_pid(args.ip)
    if not pid:
        print('没找到正在运行的 Application —— 请先启动游戏（调试桩只能附到活着的进程）。')
        return 3
    base = args.base
    if not base:
        base, _plugin, _game = base_tool.find_bases(args.ip, pid)
        if not base:
            print('定位不到运行时模块基址。可重启设备后重试，或用 --base 直接指定。')
            return 3
    print(f'进程 = {pid}；运行时模块基址 = 0x{base:x}')

    expect = base_tool.local_bytes(elf, SYMS['identity'], 16)
    actual = base_tool.read_bytes(args.ip, pid, base + SYMS['identity'], 16)
    if expect is None or len(actual) < 16 or actual != expect:
        print('偏移自校验失败：本地构建与设备上的模块不是同一版，读数不可信。')
        print(f'  本地期望: {expect.hex() if expect else "(读不到本地 ELF)"}')
        print(f'  设备实际: {actual.hex()}')
        return 4
    print('偏移自校验通过（身份函数前 16 字节与本地构建一致）')

    length = struct.unpack('<I', base_tool.read_bytes(
        args.ip, pid, base + SYMS['g_LastLuaErrorLength'], 4))[0]
    text = base_tool.read_bytes(args.ip, pid, base + SYMS['g_LastLuaErrorText'],
                                TEXT_CAPACITY).split(b'\0')[0]

    print()
    print('=== 运行时的 Lua 错误通道 ===')
    print(f'  错误文本长度 = {length}')
    if text:
        print(f'  错误文本     = {text.decode("utf-8", "replace")}')
    else:
        print('  错误文本     = （空 —— 没有记录到 Lua 错误）')

    scalars = [('g_CallbackError', '回调里出过错的标志'),
               ('g_ModRegistered', '模组已登记'),
               ('g_RequireFailureTotal', 'require 失败次数'),
               ('g_Dispatchable', '有派发点的回调登记数'),
               ('g_Unhooked', '永不触发的回调登记数')]
    values = base_tool.read_words(
        args.ip, pid, [(name, base + SYMS[name], 1) for name, _ in scalars])
    print()
    print('=== 相关计数 ===')
    for name, label in scalars:
        got = values.get(name, [])
        print(f'  {name:24} = {got[0] if got else "(读不到)"}   # {label}')

    names = base_tool.read_words(args.ip, pid, [
        ('first_name', base + SYMS['g_RequireFirstFailureName'], 1),
        ('last_name', base + SYMS['g_RequireLastFailureName'], 1),
        ('error_tail', base + SYMS['g_RequireErrorTail'], 1),
        ('first_code', base + SYMS['g_RequireFirstFailureCode'], 1),
        ('last_code', base + SYMS['g_RequireLastFailureCode'], 1),
        ('detail', base + SYMS['g_RequireFailureDetail'], 1),
    ])
    print()
    print('=== require 失败快照（8 字节的"模块名头"）===')
    for key, label in [('first_name', '第一次失败的模块名头'),
                       ('last_name', '最后一次失败的模块名头'),
                       ('error_tail', '错误尾部'),
                       ('first_code', '第一次失败代码'),
                       ('last_code', '最后一次失败代码'),
                       ('detail', 'detail')]:
        got = names.get(key, [])
        if not got:
            print(f'  {key:12} 读不到')
            continue
        value = got[0]
        raw = struct.pack('>Q', value)
        printable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
        print(f'  {key:12} = 0x{value:016x}  ascii="{printable}"   # {label}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
