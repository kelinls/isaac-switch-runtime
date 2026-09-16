#!/usr/bin/env python3
"""解码 `ISAACERR` 探针的报告负载（只读崩溃报告，不碰设备）。

背景：真机文件写入通道长年无产物，探针把读数装进 `x2` 指向的 16 字负载数组，
崩溃报告的 `Stack Dump` 段精确等于 `[SP, SP+0x100)`，所以负载可以直接从报告里读出来。
用法：

    python3 tools/decode_isaacerr_payload.py crash_reports/01789205321_010021c000b6a000.log

读法（与 `runtime/source/probe/content_mount_point_probe.hpp` 的字段表一一对应）：
  * 报告里 `Stack Dump` 的基址是 `SP`；`X[02] - SP` 就是负载数组在窗口内的偏移。
  * `X[03]` = status 位（与 `payload[0]` 相同）。
  * status 的 bit9 决定负载是"文本布局"还是"普查布局"，两张表不同，不能混读。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

STACK_DUMP_HEADER = "Stack Dump:"
PAYLOAD_WORDS = 16

# status 位（`content_mount_point_probe.hpp`）。
STATUS_BITS = {
    0: "回调错误标志（CallbackErrorPending 或诊断字 [12]）",
    1: "Lua 错误信息长度 > 0",
    2: "错误信息可取（前 8 字节非零）",
    3: "update 回调进入次数 > 0",
    4: "render 回调进入次数 > 0",
    5: "回调注册表非空",
    6: "已登记回调种类掩码非零",
    7: "16 字诊断出口握手成功（返回 ISAACHR1）",
    8: "兜底口径（不是真的看到了回调错误）",
    9: "**文本布局**：payload[2..15] 是错误文本，不是普查字",
    10: "触发原因包含『运行时捕获到过 Lua 错误文本』",
}

# 普查布局的字段表。注意：这张表按**实现**（`ReportCallbackErrorSnapshot` 的赋值）写，
# 而不是按头文件注释里的编号 —— 两者对同一份数据用了不同的下标口径，实测必须以实现为准。
CENSUS_FIELDS = {
    0: "status（同 X[03]；bits32..63 = 假堆已用 KiB）",
    1: "Lua 错误信息长度",
    2: "错误信息前 8 字节",
    3: "诊断字 [3] update 回调进入次数",
    4: "诊断字 [4] render 回调进入次数",
    5: "诊断字 [5][6] 已登记回调种类掩码 id 0..63（低/高 32 位打包）",
    6: "诊断字 [7][8] 已登记回调种类掩码 id 64..127",
    7: "诊断字 [9] 有派发点的登记次数",
    8: "诊断字 [10] 无派发点的登记次数",
    9: "诊断字 [11] 清单 Mod 加载字（低 8 位=步骤，bits8..15=原因，bits16..47=观测字节）",
    10: "诊断字 [12] 回调 Lua 错误标志",
    11: "当前注册表条数（ManagedCallbackRegistry().Count()）",
    12: "Font:Load 读数（bit0 引擎接受 / bit1 名字是内容挂载点相对名 / bits8..15 长度 / bits16..47 前 4 字节）",
    13: "预留",
    14: "预留",
    15: "预留",
}

# `require` 失败代码（`lua_runtime.cpp`）。
REQUIRE_CODES = {
    19: "名字被拒/嵌套过深",
    20: "打不开（不存在）",
    21: "读失败（缓冲区装不下等）",
    22: "编译失败",
    23: "模块大于脚本缓冲区",
    # 26 已**退役**（2026-09-16 多模组加载）：`RegisterMod` 现在可以调用多次，
    # 所以运行时不再产生这个码。保留键位是为了读得懂**历史崩溃报告**里的旧值。
    26: "（已退役）RegisterMod 被调用两次",
    27: "执行失败",
}

# `Isaac.GetPlayer` 解析链的阶段码（`isaac_api.cpp` 的 `RecordEnginePlayerLookup`）。
PLAYER_STAGES = {
    0: "成功",
    1: "模块基址不可用",
    2: "Game 槽不可读或为空",
    3: "Game* 为空",
    4: "玩家向量区间不可读或不自洽（begin/end）",
    5: "玩家元素个数超过上限",
    6: "下标越界",
    7: "元素为空指针",
    8: "**元素非空但 vptr 不是 Entity_Player**（偏移或内存布局不符）",
}

MOD_LOAD_STEPS = {
    0: "带脚本加载成功",
    1: "ManifestRead 失败",
    2: "ManifestParse 失败",
    3: "PathBuild 失败",
    4: "EntryRead 失败",
    5: "纯资源 Mod（已挂载内容，无脚本）",
    16: "LuaInit 失败（原因见 bits8..15）",
}


def crashed_thread_section(text: str) -> str:
    """截到 `Crashed Thread Info` 段为止。

    重要：报告里 `Registers:` / `Stack Dump:` 各出现**多次**（每个线程一份），
    取最后一份会读到别的线程（本项目踩过一次，见 `docs/问题与回顾记录`）。报错的
    永远是崩溃线程，所以只认第一个 `Registers:` 与第一个 `Stack Dump:`。
    """
    marker = "Crashed Thread Info:"
    start = text.find(marker)
    section = text[start:] if start >= 0 else text
    # 崩溃线程的寄存器块在 `Thread Report` 段里会**再出现一次**（逐线程清单），
    # 而后面还有 5 个别的线程各带一份。取第一份即可，且必须截断到 `Thread Report` 之前。
    end = section.find("Thread Report:")
    return section[:end] if end >= 0 else section


def parse_registers(text: str) -> dict[str, int]:
    registers: dict[str, int] = {}
    for name, value in re.findall(r"^\s+(X\[\d+\]|FP|LR|SP|PC):\s+([0-9A-Fa-f]+)\s*$",
                                  crashed_thread_section(text), re.M):
        registers[name] = int(value, 16)
    return registers


def parse_stack_dump(text: str) -> dict[int, int]:
    """把崩溃线程的 `Stack Dump` 段解析成 {地址: 8 字节小端值}。

    刻意**不用正则**：本机 Python 3.14.5 上 `re.match(r"[0-9a-f]{16}", "002fa4d07c70")`
    返回 `None`（同一模式 `{16}` 量词失效，`+` 正常，`re.DEBUG` 显示模式本身编译正确），
    而这里的地址列是固定宽度的十六进制（实测 12 位，如 `002fa4d07c70`），
    按列切片比正则更稳。
    """
    lines = crashed_thread_section(text).splitlines()
    start = next(index for index, line in enumerate(lines) if STACK_DUMP_HEADER in line)
    dump: dict[int, int] = {}
    for line in lines[start + 1:]:
        if len(line) < 30 or not line.startswith(" "):
            if dump and line.strip():
                break
            continue
        address_field = line.strip().split(" ", 1)
        if len(address_field) != 2 or not 8 <= len(address_field[0]) <= 16:
            continue
        try:
            address = int(address_field[0], 16)
            raw = bytes(int(byte, 16) for byte in address_field[1].split())
        except ValueError:
            continue
        for offset in range(0, len(raw) - 7, 8):
            dump[address + offset] = int.from_bytes(raw[offset:offset + 8], "little")
    return dump


def words_at(dump: dict[int, int], base: int, count: int) -> list[int]:
    return [dump.get(base + index * 8, 0) for index in range(count)]


def printable(word: int) -> str:
    raw = word.to_bytes(8, "little")
    text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in raw)
    return f"'{text}'"


def decode(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    registers = parse_registers(text)
    dump = parse_stack_dump(text)
    sp = registers.get("SP")
    x2 = registers.get("X[02]")
    x3 = registers.get("X[03]")
    if sp is None or x2 is None:
        print(f"{path.name}: 报告里缺 SP / X[02]，无法定位负载")
        return
    offset = x2 - sp
    print(f"=== {path.name} ===")
    print(f"X[01] 魔数          : {registers.get('X[01]', 0):#x} "
          f"({printable(registers.get('X[01]', 0))})")
    print(f"SP                  : {sp:#x}")
    print(f"X[02]（负载指针）    : {x2:#x}  => SP + {offset:#x}")
    print(f"X[03]（status）      : {x3:#x}")
    print(f"X[04]（错误长度）    : {registers.get('X[04]', 0):#x}")
    if offset < 0 or offset + PAYLOAD_WORDS * 8 > 0x100:
        print(f"!! 负载窗口越界（offset {offset:#x}），报告里读不全")
    status = x3 or 0
    print("\nstatus 位：")
    for bit in sorted(STATUS_BITS):
        if status >> bit & 1:
            print(f"  bit{bit:<2} {STATUS_BITS[bit]}")
    heap_kib = status >> 32
    if heap_kib:
        print(f"  bits32..63 假堆已用 {heap_kib} KiB（{heap_kib / 1024:.1f} MiB）")
    payload = words_at(dump, x2, PAYLOAD_WORDS)
    layout = "文本布局" if status >> 9 & 1 else "普查布局"
    print(f"\n负载（{layout}，{PAYLOAD_WORDS} 字）：")
    if layout == "文本布局":
        # 文本布局：`[2..12]` = 前 88 字节，`[13..15]` = 后 24 字节（见探针头文件）。
        head = b"".join(word.to_bytes(8, "little") for word in payload[2:13])
        tail = b"".join(word.to_bytes(8, "little") for word in payload[13:16])
        raw_length = payload[1]
        head_length = min(raw_length, len(head))
        tail_bytes = 0 if raw_length <= len(head) else min(len(tail), raw_length - len(head))
        print(f"  [0] {payload[0]:#018x} status")
        print(f"  [1] {raw_length} 错误信息长度")
        print(f"  [2..12] 前 88 字节: {head[:head_length]!r}")
        if tail_bytes:
            print(f"  [13..15] 后 {tail_bytes} 字节: {tail[:tail_bytes]!r}")
        else:
            print("  [13..15] （错误文本不超过 88 字节，后缀与前面重叠）")
    else:
        for index, word in enumerate(payload):
            note = CENSUS_FIELDS.get(index, "预留")
            extra = ""
            if index in (2, 13, 14):
                extra = f"  {printable(word)}"
            print(f"  [{index:>2}] {word:#018x}  {note}{extra}")
        high = payload[5] >> 32
        low = payload[5] & 0xFFFFFFFF
        kinds = (high << 32) | low
        if kinds:
            print(f"\n已登记回调种类（id 0..63 掩码 {kinds:#x}）："
                  f"{[bit for bit in range(64) if kinds >> bit & 1]}")
        kinds_high = ((payload[6] >> 32) << 32) | (payload[6] & 0xFFFFFFFF)
        if kinds_high:
            print(f"已登记回调种类（id 64..127 掩码 {kinds_high:#x}）："
                  f"{[64 + bit for bit in range(64) if kinds_high >> bit & 1]}")
        word = payload[9]
        if word:
            step = word & 0xFF
            detail = word >> 8 & 0xFF
            observed = word >> 16 & 0xFFFFFFFF
            print(f"\n清单 Mod 加载字 {word:#x}：步骤 {step}（{MOD_LOAD_STEPS.get(step, '?')}）"
                  f" 原因 {detail} 观测字节 {observed}")
        font_word = payload[12]
        print(f"\nFont:Load 读数 {font_word:#x}："
              f"引擎接受={'是' if font_word & 1 else '否'}、"
              f"名字是挂载点相对名={'是' if font_word >> 1 & 1 else '否'}、"
              f"名字长度={font_word >> 8 & 0xFF}、"
              f"名字前 4 字节={bytes((font_word >> (16 + 8 * i)) & 0xFF for i in range(4))!r}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    for argument in argv[1:]:
        decode(Path(argument))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
