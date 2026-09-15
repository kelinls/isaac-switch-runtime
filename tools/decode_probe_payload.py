#!/usr/bin/env python3
"""Decode one Atmosphere crash report produced by the `ISAACERR` probe.

Reads the *first* `Registers:` / `Stack Dump:` pair inside `Crashed Thread Info`
(the report repeats them for every thread; the crashed one comes first), rebuilds
the 16-word `payload[16]` from `X[02]` and prints the field table for whichever
layout the probe used:

* **text layout** (`status` bit9) — `payload[2..12]` carries the Lua error text;
* **census layout** — the field table below.

Field tables live in `runtime/source/probe/content_mount_point_probe.cpp`; this
tool exists so that reading a report does not require re-deriving them by hand.

Usage::

    python3 tools/decode_probe_payload.py <crash-report.log> [...]
"""

from __future__ import annotations

import argparse
import re
import struct
import sys

REGISTER_RE = re.compile(r"^\s*X\[(\d+)\]:\s*([0-9a-fA-F]+)\s*$")
STACK_RE = re.compile(r"^\s*([0-9a-fA-F]{9,12})\s+((?:[0-9a-fA-F]{2} )+[0-9a-fA-F]{2})\s*$")
LAYOUT_TAG = 0x32504B41  # "AKP2"：探针自报的负载布局标记（payload[0]/[2] 的高 32 位）
STATUS_BITS = {
    0: "回调错误标志",
    1: "Lua 错误信息长度 > 0",
    2: "错误信息可取",
    3: "update 回调进入次数 > 0",
    4: "render 回调进入次数 > 0",
    5: "回调注册表非空",
    6: "已登记回调种类掩码非零",
    7: "16 字诊断出口握手成功",
    9: "**文本布局**（payload[2..12] 是错误文本）",
    10: "触发原因包含『运行时捕获到过 Lua 错误文本』",
    11: "错误文本不可信（长度 < 4）",
}


def parse_registers(text: str) -> dict[int, int]:
    start = text.find("Crashed Thread Info")
    if start < 0:
        return {}
    end = text.find("Stack Dump:", start)
    out: dict[int, int] = {}
    for line in text[start:end if end > 0 else len(text)].splitlines():
        m = REGISTER_RE.match(line)
        if m:
            out[int(m.group(1))] = int(m.group(2), 16)
    return out


def parse_stack(text: str) -> dict[int, int]:
    start = text.find("Crashed Thread Info")
    if start < 0:
        return {}
    stack_start = text.find("Stack Dump:", start)
    if stack_start < 0:
        return {}
    end = text.find("Thread Report:", stack_start)
    segment = text[stack_start:end if end > 0 else len(text)]
    memory: dict[int, int] = {}
    for line in segment.splitlines():
        m = STACK_RE.match(line)
        if not m:
            continue
        address = int(m.group(1), 16)
        for offset, byte in enumerate(bytes.fromhex(m.group(2).replace(" ", ""))):
            memory.setdefault(address + offset, byte)
    return memory


def read_words(memory: dict[int, int], base: int, count: int = 16) -> list[int] | None:
    """Read `count` words, tolerating a truncated dump.

    Atmosphere only dumps the first 0x100 bytes below/around SP, and the probe's
    payload sits at `SP + 0xb0`, so the *tail* of the payload is frequently
    outside the dump window. Treat "already seen" bytes as authoritative and
    fill the missing tail with 0xff so the caller can still tell which words are
    real (0xff repeated means "not in the dump").
    """
    if not any(base <= address < base + count * 8 for address in memory):
        return None
    raw = bytearray()
    for index in range(count * 8):
        raw.append(memory.get(base + index, 0xFF))
    return list(struct.unpack_from("<16Q", bytes(raw)))


def fields(word: int, high: int, low: int) -> int:
    width = high - low + 1
    return (word >> low) & ((1 << width) - 1)


def as_float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def report(path: str) -> int:
    text = open(path, encoding="utf-8", errors="replace").read()
    registers = parse_registers(text)
    memory = parse_stack(text)
    print("=" * 78)
    print(path)
    if not registers:
        print("  没有 Crashed Thread Info / 寄存器段，无法解码")
        return 1
    payload_address = registers.get(2, 0)
    status = registers.get(3, 0)
    error_length = registers.get(4, 0)
    print(f"  X[02] payload = {payload_address:#x}   X[03] status = {status:#018x}"
          f"   X[04] = {error_length}")
    words = read_words(memory, payload_address)
    if words is None:
        print("  负载窗口越界（报告里的 Stack Dump 读不全）")
        return 1
    present = sum(1 for index in range(16) if payload_address + index * 8 in memory)
    if present < 16:
        print(f"  ⚠ 报告只 dump 到 payload[{present}]，后面的字按 0xff 填充"
              f"（读到的 65535 / 0xff… 都是这个填充值，不是真读数）")
    if fields(status, 9, 9):
        # 布局判定只看 `payload[2]` 的高 32 位（自描述标记）。**不要看 [0]**：它的高 32 位
        # 是堆用量（曾经误判过一次）。
        untrustworthy = bool(fields(status, 11, 11))
        # ★ 布局标记的**权威位置是 `payload[2]` 的高 32 位**：探针在文本布局里写的是
        # `payload[2] = tag | (texelCalls<<32) | ...`，也就是说 tag 落在**低** 32 位、
        # 高 32 位是取色调用次数。解码器早先只看高 32 位，导致把每一份新报告都判成"旧布局"
        # （真机报告 `01789227137` 就因此把 `GetItemConfig` 探针误读成 lastApi）。
        # 现在两个半边都认，并打印原始字以便核对。
        tagged = (fields(words[2], 31, 0) == LAYOUT_TAG) or \
                 (fields(words[2], 63, 32) == LAYOUT_TAG)
        print(f"  payload[2] = {words[2]:#018x}"
              f"  (低32={fields(words[2], 31, 0):#010x} 高32={fields(words[2], 63, 32):#010x})"
              f"  {'✓ 当前布局' if tagged else '✗ 不是当前布局（下面的读数无效）'}")
        if untrustworthy:
            print("  ⚠ status bit11 = 错误文本过短（< 4 字节）⇒ [5] 里是**残留内容**，不是本次消息")
        if tagged:
            print(f"  [2]↑ Sprite:GetTexel 引擎调用次数 = {fields(words[2], 63, 32)}"
                  f"  （标签在低 32 位，其余读数见探针源码布局）")
            print(f"  [3]  Level:GetCurses 原始位 = {fields(words[3], 15, 0):#06x}"
                  f"  合成标志 = {fields(words[3], 23, 16):#04x}"
                  f"  permanent = {fields(words[3], 31, 24):#04x}"
                  f"  banned = {fields(words[3], 39, 32):#04x}")
            print(f"       诅咒读数次数 = {fields(words[3], 47, 40)}"
                  f"  Pickup.Touched 读取次数 = {fields(words[3], 55, 48)}"
                  f"  为真次数 = {fields(words[3], 63, 56)}")
            # [0] 高位：`Sprite:ReplaceSpritesheet` 探针（第六轮）。它回答"这个函数到底
            # 收到了什么参数"——真机连续两轮停在这里，而三条失败路径返回的都是 nullptr。
            replace_len = fields(words[0], 31, 12)
            replace_head = fields(words[0], 55, 32)
            replace_calls = fields(words[0], 63, 56)
            head_bytes = bytes((replace_head >> (8 * i)) & 0xFF for i in range(3))
            len_text = "“不是字符串”" if replace_len == 0xFFFFF else str(replace_len)
            print(f"  [0]↑ ReplaceSpritesheet 调用次数 = {replace_calls}"
                  f"  文件名长度 = {len_text}  前 3 字节 = {head_bytes!r}")
            # [4] = 最后调用的 API（第七轮）；[5] = 错误文本最后 8 字节
            last_bit = fields(words[4], 31, 0)
            last_calls = fields(words[4], 63, 32)
            gic = fields(words[3], 63, 32)
            if gic:
                print(f"  [3]↑ Isaac.GetItemConfig 调用 {fields(gic, 31, 8)} 次"
                      f"  链路解析成功={bool(fields(gic, 0, 0))}"
                      f"  向量可读={bool(fields(gic, 1, 1))}"
                      f"  因向量未建立而返回对象={bool(fields(gic, 2, 2))}"
                      f"  因参数非法而返回 nil={bool(fields(gic, 3, 3))}")
            print(f"  [4]  最后调用的 API 掩码位 = "
                  f"{'（还没有任何调用）' if last_bit == 0xFFFFFFFF else last_bit}"
                  f"  主掩码调用总次数 = {last_calls}")
            tail = int(words[5]).to_bytes(8, "little")
            print(f"  [5]  错误文本**最后 8 字节**: {tail.decode('utf-8', errors='replace')!r}")
            calls = fields(words[2], 47, 32)
            if calls == 0:
                print("  ⇒ 判读：EID 没有走到像素比对（GetTexel 一次未调）")
            else:
                print(f"  ⇒ 判读：GetTexel 被调到 {calls} 次（78 次 = 完整跑完 39 个采样点；"
                      f"6 次 = 第 1 个点就判不同并提前返回）")
        else:
            head = b"".join(int(w).to_bytes(8, "little") for w in words[2:10])
            print(f"  错误文本（旧布局，[2..] 起）: {head.decode('utf-8', errors='replace')!r}")
        return 0

    print("  —— 普查布局 ——")
    print(f"  [1]  EID.isHidden = {fields(words[1], 15, 0)}"
          f"  读取状态 = {fields(words[1], 31, 16)}")
    print(f"  [2]  CountEnemies = {fields(words[2], 15, 0)}"
          f"  IsPaused = {fields(words[2], 31, 16)}")
    print(f"  [3]  HideInBattle = {fields(words[3], 0, 0)}"
          f"  状态 = {fields(words[3], 15, 8)}"
          f"  RefreshRate = {fields(words[3], 31, 16)}"
          f"  GameRenderCount 状态 = {fields(words[3], 39, 32)}"
          f"  该字段>0 = {fields(words[3], 40, 40)}")
    print(f"  [6]  diagnostics[7] = {fields(words[6], 31, 0):#x}"
          f"  diagnostics[8] = {fields(words[6], 63, 32):#x}")
    print(f"  [7]  diagnostics[9] = {words[7]:#x}")
    print(f"  [8]  diagnostics[10] = {words[8]:#x}")
    print(f"  [9]  diagnostics[11] = {words[9]:#x}")
    print(f"  [10] diagnostics[12] = {words[10]:#x}")
    print(f"  [11] registryCount = {words[11]}")
    print(f"  [12] Font:Load 次数 = {fields(words[12], 15, 0)}"
          f"  结果 = {fields(words[12], 16, 16)}"
          f"  IsLoaded 次数 = {fields(words[12], 31, 17)}"
          f"  结果 = {fields(words[12], 32, 32)}")
    print(f"       Pickup.Touched 读取次数 = {fields(words[12], 47, 32)}"
          f"  为真次数 = {fields(words[12], 55, 48)}"
          f"  最近原始字节 = {fields(words[12], 63, 56)}")
    print(f"  [13] 绘制 alpha(‰) = {fields(words[13], 15, 0)}"
          f"  ScaleX(‰) = {fields(words[13], 31, 16)}")
    print(f"       Sprite:GetTexel 引擎调用次数 = {fields(words[13], 47, 32)}"
          f"  落值次数 = {fields(words[13], 55, 48)}"
          f"  落值原因 = {fields(words[13], 63, 56)}")
    print(f"  [14] Level:GetCurses 原始位 = {fields(words[14], 31, 0):#x}"
          f"  合成标志 = {fields(words[14], 39, 32):#x}"
          f"  permanent = {fields(words[14], 47, 40):#x}"
          f"  banned = {fields(words[14], 55, 48):#x}"
          f"  读数次数 = {fields(words[14], 63, 56)}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", help="crash report .log files")
    args = parser.parse_args(argv)
    status = 0
    for path in args.reports:
        status |= report(path)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
