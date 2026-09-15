#!/usr/bin/env python3
"""只读探针：从设备上正在运行的以撒里取「运行时的 Lua 错误通道」与 `require` 失败快照。

为什么要它：EID 大量 `pcall`/静默容忍，出问题时**不报错、只让功能静默失效**。
运行时把最后一次 Lua 错误的**整段文本**留在 `.bss`（`g_LastLuaErrorText`，256 字节）
里，接上调试桩就能直接把"哪一行、什么消息"读出来，不用再花一轮真机改脚本。
它也是**新增 Lua API 的真机验证通道**：让模组脚本里 `error("<要验证的值>")`，
文本就会被记在这里，读回来即可与设备内存里的原始数据对照。

读的东西（偏移**每次都问本地 `runtime.elf` 的符号表**，见 `tools/elf_syms.py`）：

    g_LastLuaErrorLength   最后一次 Lua 错误的字节数
    g_LastLuaErrorText     最后一次 Lua 错误的整段文本
    g_CallbackError        回调里出过错的粘性标志
    g_ModRegistered        模组是否登记成功（Lua 真的跑起来了）
    g_RequireFailureTotal  `require` 失败次数；配套的 Name/Code/ErrorTail 是"哪一个模块"
    g_Dispatchable / g_Unhooked  登记的回调里"有派发点 / 永远不触发"的条数

用法：
    python3 tools/probe_lua_error_channel.py                      # 全自动（取进程 + 基址 + 自校验）
    python3 tools/probe_lua_error_channel.py --base 0x4e8a181000   # 已知基址时少读一轮

★ 2026-09-15 重写：旧版调用的 `find_pid`/`find_bases`/`read_bytes`/`read_words` 在
`read_device_state_via_gdb.py` 里**根本不存在**（那是更早版本的接口），因此这个工具早就跑不起来。
现在改为直接用该工具现成的 `GdbSession`：**一次 attach 读完**，用完 `detach`
（调试桩每次开机只可靠支撑很少几次 attach，见 `docs/问题与解决记录.md`）。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import read_device_state_via_gdb as base_tool  # noqa: E402

#: 本探针要读的键（偏移由 `base_tool.sync_syms_from_elf()` 从本地 ELF 解析后填进 `SYMS`）。
WANTED = ('g_LastLuaErrorLength', 'g_LastLuaErrorText', 'g_CallbackError',
          'g_ModRegistered', 'g_RequireFailureTotal', 'g_RequireLastFailureName',
          'g_RequireLastFailureCode', 'g_RequireFirstFailureName',
          'g_RequireFirstFailureCode', 'g_RequireErrorTail', 'g_RequireFailureDetail',
          'g_Dispatchable', 'g_Unhooked')
TEXT_CAPACITY = 256
SCALARS = (('g_CallbackError', '回调里出过错的粘性标志（1 = 出过）'),
           ('g_ModRegistered', '模组已登记（1 = Lua 真的跑起来了）'),
           ('g_RequireFailureTotal', '`require` 失败次数'),
           ('g_Dispatchable', '有派发点的回调登记数'),
           ('g_Unhooked', '永不触发的回调登记数'))
WORD_SNAPSHOT = (('g_RequireFirstFailureName', '第一次失败的模块名头'),
                 ('g_RequireLastFailureName', '最后一次失败的模块名头'),
                 ('g_RequireErrorTail', '错误尾部'),
                 ('g_RequireFirstFailureCode', '第一次失败代码'),
                 ('g_RequireLastFailureCode', '最后一次失败代码'),
                 ('g_RequireFailureDetail', 'detail'))


def ascii_of(word: int) -> str:
    """把 8 字节的"模块名头"按大端读成可打印串（运行时就是这么打包的）。"""
    return ''.join(chr(b) if 32 <= b < 127 else '.' for b in struct.pack('>Q', word))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--ip', default='192.168.124.11')
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0, help='运行时模块基址')
    ap.add_argument('--elf', default=base_tool.DEFAULT_ELF)
    args = ap.parse_args(argv)

    elf = ROOT / args.elf
    if not elf.exists():
        print(f'找不到本地构建的 ELF：{args.elf}（偏移要从它的符号表来）')
        return 2

    missing = base_tool.sync_syms_from_elf(elf, verbose=True)
    absent = [key for key in WANTED if key not in base_tool.SYMS]
    if absent:
        print(f'⚠ 本地 ELF 里没有这些符号，读数会缺项：{", ".join(absent)}')
    del missing

    session = base_tool.GdbSession()
    try:
        session.command(f'target extended-remote {args.ip}:22225')
        pid = args.pid or (base_tool.parse_processes(session.command('info os processes')) or 0)
        if not pid:
            print('没找到正在运行的 Application —— 请先启动游戏（调试桩只能附到活着的进程）。')
            return 3
        print(f'进程 = {pid}（本会话只 attach 这一次）')
        session.command(f'attach {pid}')

        base = args.base
        if not base:
            base, _plugin, _game = base_tool.parse_bases(session.command('monitor get info'))
            base = base or 0
        if not base:
            # `monitor get info` 在"刚开机第一次挂载"也可能返回空：用映射 + 身份指纹兜底。
            base, _game = base_tool.locate_by_scan(session, elf)
            base = base or 0
        if not base:
            print('定位不到运行时模块基址。可重启设备后重试，或用 --base 直接指定。')
            return 3
        print(f'运行时模块基址 = 0x{base:x}')

        # 版本核对：身份函数头 16 字节必须与本地这份构建逐字节相同，否则偏移全都不可信。
        expect = base_tool.local_bytes(elf, base_tool.SYMS['identity'], 16)
        requests = [('identity', base + base_tool.SYMS['identity'], 2)]
        requests += [(key, base + base_tool.SYMS[key], 1) for key in WANTED
                     if key != 'g_LastLuaErrorText']
        requests += [('error_text', base + base_tool.SYMS['g_LastLuaErrorText'],
                      TEXT_CAPACITY // 8)]
        lines = []
        for label, address, count in requests:
            lines.append(f'echo TAG {label}\\n')
            lines.append(f'x/{count}gx 0x{address:x}')
        values = base_tool.parse_words(session.command('\n'.join(lines)))
        session.command('detach')
    finally:
        session.close()

    actual = b''.join(v.to_bytes(8, 'little') for v in (values.get('identity') or [])[:2])
    if expect is None or len(actual) < 16 or actual != expect:
        print('偏移自校验失败：本地构建与设备上的模块不是同一版，读数不可信。')
        print(f'  本地期望: {expect.hex() if expect else "(读不到本地 ELF)"}')
        print(f'  设备实际: {actual.hex()}')
        return 4
    print('偏移自校验通过（身份函数前 16 字节与本地构建一致）')

    def word(key: str) -> int | None:
        got = values.get(key) or []
        return got[0] if got else None

    length = word('g_LastLuaErrorLength')
    blob = b''.join(v.to_bytes(8, 'little') for v in (values.get('error_text') or []))
    text = blob[:TEXT_CAPACITY].split(b'\0', 1)[0]

    print()
    print('=== 运行时的 Lua 错误通道 ===')
    print(f'  错误文本长度 = {length & 0xFFFFFFFF if length is not None else "(读不到)"}')
    if text:
        print('  错误文本     = ' + text.decode('utf-8', 'replace'))
    else:
        print('  错误文本     = （空 —— 没有记录到 Lua 错误）')

    print()
    print('=== 相关计数 ===')
    for key, label in SCALARS:
        value = word(key)
        shown = '(读不到)' if value is None else (value & 0xFF if key == 'g_ModRegistered'
                                                 else (value & 0xFFFFFFFF))
        print(f'  {key:24} = {shown}   # {label}')

    print()
    print('=== require 失败快照（8 字节的"模块名头"，按大端看字符）===')
    for key, label in WORD_SNAPSHOT:
        value = word(key)
        if value is None:
            print(f'  {key:26} 读不到')
            continue
        print(f'  {key:26} = 0x{value:016x}  ascii="{ascii_of(value)}"   # {label}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
