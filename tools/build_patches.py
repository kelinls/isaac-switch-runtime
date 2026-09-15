"""为指定 Repentance NRO 生成经过原始字节保护的 IPS。"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nro_ips import encode_ips, read_module_id


EXPECTED_MODULE_ID = bytes.fromhex(
    "91C73FDD575061318D68886316AFEAC72388B2AB" + "00" * 12
)
GREED_OFFSET = 0x002FD1BC
GREED_ORIGINAL = bytes.fromhex("FD7BBEA9F30B00F9")
GREED_RECORD = (GREED_OFFSET, bytes.fromhex("E003271EC0035FD6"))
NORMAL_OFFSET = 0x0032E394
NORMAL_ORIGINAL = bytes.fromhex("85BDFF54")
NORMAL_RECORD = (NORMAL_OFFSET, bytes.fromhex("ECFDFF17"))
NORMAL_SECOND_OFFSET = 0x0032E488
NORMAL_SECOND_ORIGINAL = bytes.fromhex("E2B5FF54")
NORMAL_SECOND_RECORD = (NORMAL_SECOND_OFFSET, bytes.fromhex("AFFDFF17"))
NORMAL_RECORDS = [NORMAL_RECORD, NORMAL_SECOND_RECORD]

RELAY_TARGET_OFFSET = 0x003F8DB8
RELAY_CODE_OFFSET = 0x0068CBE0
RELAY_FALLBACK_OFFSET = RELAY_CODE_OFFSET + 0x10
RELAY_SLOT_OFFSET = RELAY_CODE_OFFSET + 0x18
RELAY_TARGET_ORIGINAL = bytes.fromhex("FF4301D1")
RELAY_CAVE_ORIGINAL = bytes(0x20)


def encode_branch(source: int, destination: int) -> bytes:
    """编码一条范围内的 ARM64 无条件 B 指令。"""
    delta = destination - source
    if delta % 4 or not -0x08000000 <= delta <= 0x07FFFFFC:
        raise ValueError("ARM64 B 跳转超出范围")
    return (0x14000000 | ((delta >> 2) & 0x03FFFFFF)).to_bytes(4, "little")


def encode_bl(source: int, destination: int) -> bytes:
    """编码一条范围内的 ARM64 BL 指令。"""
    delta = destination - source
    if delta % 4 or not -0x08000000 <= delta <= 0x07FFFFFC:
        raise ValueError("ARM64 BL 跳转超出范围")
    return (0x94000000 | ((delta >> 2) & 0x03FFFFFF)).to_bytes(4, "little")


def encode_adr(source: int, destination: int, register: int) -> bytes:
    """编码范围内的 ARM64 ADR 指令。"""
    delta = destination - source
    if not -0x100000 <= delta <= 0xFFFFF:
        raise ValueError("ARM64 ADR 跳转超出范围")
    immediate = delta & 0x1FFFFF
    instruction = (
        0x10000000
        | ((immediate & 0x3) << 29)
        | (((immediate >> 2) & 0x7FFFF) << 5)
        | register
    )
    return instruction.to_bytes(4, "little")


RELAY_TARGET_RECORD = (
    RELAY_TARGET_OFFSET,
    encode_branch(RELAY_TARGET_OFFSET, RELAY_CODE_OFFSET),
)
RELAY_CODE = bytes.fromhex(
    "D1000010"  # ADR X17, 0x68CBF8
    "30FEDFC8"  # LDAR X16, [X17]
    "500000B4"  # CBZ X16, 0x68CBF0
    "00021FD6"  # BR X16
    "FF4301D1"  # sub sp, sp, #0x50
    "72B0F517"  # B 0x3F8DBC
    "0000000000000000"
)
RELAY_RECORDS = [RELAY_TARGET_RECORD, (RELAY_CODE_OFFSET, RELAY_CODE)]

GAME_INIT_CONTEXT_OFFSET = 0x003F5A3C
GAME_INIT_CONTEXT_ORIGINAL = bytes.fromhex("E00314AA140100F9")
GAME_INIT_CALL_OFFSET = 0x003F5A44
GAME_INIT_PLT_OFFSET = 0x0067C6B0
GAME_INIT_CALL_ORIGINAL = bytes.fromhex("1B1B0A94")
GAME_OBSERVER_RELAY_CODE_OFFSET = 0x0068CC40
GAME_OBSERVER_RELAY_SLOT_OFFSET = GAME_OBSERVER_RELAY_CODE_OFFSET + 0x30
GAME_OBSERVER_RELAY_LENGTH = 0x40
GAME_OBSERVER_RELAY_CAVE_ORIGINAL = bytes(GAME_OBSERVER_RELAY_LENGTH)
EXPECTED_SOURCE_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
GAME_OBSERVER_RELAY_CODE = (
    bytes.fromhex(
        "FF4300D1"  # sub sp, sp, #0x10
        "F30300F9"  # str x19, [sp]
        "F30300AA"  # mov x19, x0
        "31010010"  # adr x17, callback slot
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, restore x0
        "00023FD6"  # blr x16
        "E00313AA"  # mov x0, x19
        "F30340F9"  # ldr x19, [sp]
        "FF430091"  # add sp, sp, #0x10
    )
    + encode_bl(GAME_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_INIT_PLT_OFFSET)
    + encode_branch(GAME_OBSERVER_RELAY_CODE_OFFSET + 0x2C, GAME_INIT_CALL_OFFSET + 4)
    + bytes(16)
)
GAME_OBSERVER_RELAY_RECORDS = [
    (
        GAME_INIT_CALL_OFFSET,
        encode_branch(GAME_INIT_CALL_OFFSET, GAME_OBSERVER_RELAY_CODE_OFFSET),
    ),
    (GAME_OBSERVER_RELAY_CODE_OFFSET, GAME_OBSERVER_RELAY_CODE),
]

GAME_UPDATE_CONTEXT_OFFSET = 0x003F9058
GAME_UPDATE_CONTEXT_ORIGINAL = bytes.fromhex("C00240F9")
GAME_UPDATE_CALL_OFFSET = 0x003F905C
GAME_UPDATE_PLT_OFFSET = 0x0067C9E0
GAME_UPDATE_CALL_ORIGINAL = bytes.fromhex("610E0A94")
GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET = 0x0068CC80
GAME_UPDATE_OBSERVER_RELAY_SLOT_OFFSET = GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x30
GAME_UPDATE_OBSERVER_RELAY_LENGTH = 0x40
GAME_UPDATE_OBSERVER_RELAY_CAVE_ORIGINAL = bytes(GAME_UPDATE_OBSERVER_RELAY_LENGTH)
GAME_UPDATE_OBSERVER_RELAY_CODE = (
    bytes.fromhex(
        "FF4300D1"  # sub sp, sp, #0x10
        "F30300F9"  # str x19, [sp]
        "F30300AA"  # mov x19, x0
        "31010010"  # adr x17, callback slot
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, restore x0
        "00023FD6"  # blr x16
        "E00313AA"  # mov x0, x19
        "F30340F9"  # ldr x19, [sp]
        "FF430091"  # add sp, sp, #0x10
    )
    + encode_bl(GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_UPDATE_PLT_OFFSET)
    + encode_branch(GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x2C, GAME_UPDATE_CALL_OFFSET + 4)
    + bytes(16)
)
GAME_UPDATE_OBSERVER_RELAY_RECORDS = [
    (
        GAME_UPDATE_CALL_OFFSET,
        encode_branch(GAME_UPDATE_CALL_OFFSET, GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET),
    ),
    (GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET, GAME_UPDATE_OBSERVER_RELAY_CODE),
]

GAME_STATE2_CONTEXT_OFFSET = 0x003F9038
GAME_STATE2_CONTEXT_ORIGINAL = bytes.fromhex("C00240F9")
GAME_STATE2_CALL_OFFSET = 0x003F903C
GAME_STATE2_PLT_OFFSET = 0x0067C9C0
GAME_STATE2_CALL_ORIGINAL = bytes.fromhex("610E0A94")
GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET = 0x0068CCC0
GAME_STATE2_OBSERVER_RELAY_SLOT_OFFSET = GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x30
GAME_STATE2_OBSERVER_RELAY_LENGTH = 0x40
GAME_STATE2_OBSERVER_RELAY_CAVE_ORIGINAL = bytes(GAME_STATE2_OBSERVER_RELAY_LENGTH)
GAME_STATE2_OBSERVER_RELAY_CODE = (
    bytes.fromhex(
        "FF4300D1"  # sub sp, sp, #0x10
        "F30300F9"  # str x19, [sp]
        "F30300AA"  # mov x19, x0
        "31010010"  # adr x17, callback slot
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, restore x0
        "00023FD6"  # blr x16
        "E00313AA"  # mov x0, x19
        "F30340F9"  # ldr x19, [sp]
        "FF430091"  # add sp, sp, #0x10
    )
    + encode_bl(GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_STATE2_PLT_OFFSET)
    + encode_branch(GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x2C, GAME_STATE2_CALL_OFFSET + 4)
    + bytes(16)
)
GAME_STATE2_OBSERVER_RELAY_RECORDS = [
    (
        GAME_STATE2_CALL_OFFSET,
        encode_branch(GAME_STATE2_CALL_OFFSET, GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET),
    ),
    (GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET, GAME_STATE2_OBSERVER_RELAY_CODE),
]

GAME_ISPAUSED_RENDER_CONTEXT_OFFSET = 0x00342998
GAME_ISPAUSED_RENDER_CONTEXT_ORIGINAL = bytes.fromhex("483B00D0084D43F9000140F9")
GAME_ISPAUSED_RENDER_CALL_OFFSET = 0x003429A4
GAME_ISPAUSED_RENDER_PLT_OFFSET = 0x00671100
GAME_ISPAUSED_RENDER_CALL_ORIGINAL = bytes.fromhex("D7B90C94")
GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET = 0x0068CD00
GAME_ISPAUSED_RENDER_OBSERVER_RELAY_SLOT_OFFSET = GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET + 0x30
GAME_ISPAUSED_RENDER_RELAY_CAVE_ORIGINAL = bytes(0x40)
GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE = (
    bytes.fromhex("FF4300D1F30300F9F30300AA3101001030FEDFC8500000B400023FD6E00313AAF30340F9FF430091")
    + encode_bl(GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_ISPAUSED_RENDER_PLT_OFFSET)
    + encode_branch(GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET + 0x2C, GAME_ISPAUSED_RENDER_CALL_OFFSET + 4)
    + bytes(16)
)
GAME_ISPAUSED_RENDER_RELAY_RECORDS = [
    (GAME_ISPAUSED_RENDER_CALL_OFFSET, encode_branch(GAME_ISPAUSED_RENDER_CALL_OFFSET, GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET)),
    (GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET, GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE),
]

LOAD_CONFIGS_RELAY_TARGET_OFFSET = 0x003F5C38
LOAD_CONFIGS_RELAY_CODE_OFFSET = 0x0068CC20
LOAD_CONFIGS_RELAY_FALLBACK_OFFSET = LOAD_CONFIGS_RELAY_CODE_OFFSET + 0x10
LOAD_CONFIGS_RELAY_SLOT_OFFSET = LOAD_CONFIGS_RELAY_CODE_OFFSET + 0x18
LOAD_CONFIGS_RELAY_TARGET_ORIGINAL = bytes.fromhex("FF4302D1")
LOAD_CONFIGS_RELAY_CAVE_ORIGINAL = bytes(0x20)
LOAD_CONFIGS_RELAY_TARGET_RECORD = (
    LOAD_CONFIGS_RELAY_TARGET_OFFSET,
    encode_branch(LOAD_CONFIGS_RELAY_TARGET_OFFSET, LOAD_CONFIGS_RELAY_CODE_OFFSET),
)
LOAD_CONFIGS_RELAY_CODE = bytes.fromhex(
    "D1000010"  # ADR X17, 0x68CC38
    "30FEDFC8"  # LDAR X16, [X17]
    "500000B4"  # CBZ X16, 0x68CC30
    "00021FD6"  # BR X16
    "FF4302D1"  # sub sp, sp, #0x90
    "02A4F517"  # B 0x3F5C3C
    "0000000000000000"
)
LOAD_CONFIGS_RELAY_RECORDS = [
    LOAD_CONFIGS_RELAY_TARGET_RECORD,
    (LOAD_CONFIGS_RELAY_CODE_OFFSET, LOAD_CONFIGS_RELAY_CODE),
]

RENDER_RELAY_TARGET_OFFSET = 0x003F9684
RENDER_RELAY_CODE_OFFSET = 0x0068CC00
RENDER_RELAY_FALLBACK_OFFSET = RENDER_RELAY_CODE_OFFSET + 0x10
RENDER_RELAY_SLOT_OFFSET = RENDER_RELAY_CODE_OFFSET + 0x18
RENDER_RELAY_TARGET_ORIGINAL = bytes.fromhex("FFC302D1")
RENDER_RELAY_CAVE_ORIGINAL = bytes(0x20)
RENDER_RELAY_TARGET_RECORD = (
    RENDER_RELAY_TARGET_OFFSET,
    encode_branch(RENDER_RELAY_TARGET_OFFSET, RENDER_RELAY_CODE_OFFSET),
)
RENDER_RELAY_CODE = (
    bytes.fromhex(
        "D1000010"  # ADR X17, 0x68CC18
        "30FEDFC8"  # LDAR X16, [X17]
        "500000B4"  # CBZ X16, 0x68CC10
        "00021FD6"  # BR X16
    )
    + RENDER_RELAY_TARGET_ORIGINAL
    + encode_branch(RENDER_RELAY_CODE_OFFSET + 0x14, RENDER_RELAY_TARGET_OFFSET + 4)
    + bytes(8)
)
RENDER_RELAY_RECORDS = [RENDER_RELAY_TARGET_RECORD, (RENDER_RELAY_CODE_OFFSET, RENDER_RELAY_CODE)]

# MC_POST_RENDER 的真实派发点：`Manager::Render` 体内最后一次 `Present` 调用（0x3F9B40）。
#
# 入口中继（RENDER_RELAY，0x3F9684）把原函数整体当子调用跑完才派发，所以派发发生在 Present
# **之后**：那一刻本帧已经上屏、帧图像队列已经清空。后果有两条，都在真机照片上看到了
# （2026-09-13，Stage149 绘制测试）：回调里画的东西进的是**下一帧**的队列，而且在那一帧里
# 排在游戏自己的图像之前，于是画在**实体（以撒）与 HUD 之下**、只压在房间地面上。
#
# 这个中继把派发插到 Present **之前**：图像在本帧入队、在本帧被 apply，既当帧可见又排在最后
# 一个被画（PC 的 POST_RENDER 覆盖层语义）。调用点上下文：x0 已经是 Present 的实参
# （`KAGE::Graphics::g_Manager`），调用之后到 `ret` 之间只用 d8 与被调用者保存寄存器，
# 所以桩里只需要额外保存/恢复 x0。
RENDER_PRESENT_CALL_OFFSET = 0x003F9B40
RENDER_PRESENT_CALL_ORIGINAL = bytes.fromhex("C0DB0994")  # 文件字节序的 `bl 0x670A40`
RENDER_PRESENT_PLT_OFFSET = 0x00670A40
RENDER_PRESENT_RELAY_CODE_OFFSET = 0x0068CF00
RENDER_PRESENT_RELAY_SLOT_OFFSET = RENDER_PRESENT_RELAY_CODE_OFFSET + 0x30
RENDER_PRESENT_RELAY_LENGTH = 0x40
RENDER_PRESENT_RELAY_CAVE_ORIGINAL = bytes(RENDER_PRESENT_RELAY_LENGTH)
RENDER_PRESENT_RELAY_CODE = (
    bytes.fromhex(
        "FF4300D1"  # sub sp, sp, #0x10
        "F30300F9"  # str x19, [sp]
        "F30300AA"  # mov x19, x0
        "31010010"  # adr x17, callback slot (+0x30)
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, restore x0
        "00023FD6"  # blr x16
        "E00313AA"  # mov x0, x19
        "F30340F9"  # ldr x19, [sp]
        "FF430091"  # add sp, sp, #0x10
    )
    + encode_bl(RENDER_PRESENT_RELAY_CODE_OFFSET + 0x28, RENDER_PRESENT_PLT_OFFSET)
    + encode_branch(RENDER_PRESENT_RELAY_CODE_OFFSET + 0x2C, RENDER_PRESENT_CALL_OFFSET + 4)
    + bytes(16)
)
RENDER_PRESENT_RELAY_RECORDS = [
    (
        RENDER_PRESENT_CALL_OFFSET,
        encode_branch(RENDER_PRESENT_CALL_OFFSET, RENDER_PRESENT_RELAY_CODE_OFFSET),
    ),
    (RENDER_PRESENT_RELAY_CODE_OFFSET, RENDER_PRESENT_RELAY_CODE),
]

# Stage48 patches the three target entries to NRO-local relays.  The relays
# preserve the original prologue instruction and return to instruction + 4;
# callback addresses are published later by Runtime through the inline slots.
STAGE48_MUSIC_PLAY_TARGET_OFFSET = 0x00427238
STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET = 0x0068CD40
STAGE48_MUSIC_PLAY_RELAY_SLOT_OFFSET = STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET + 0x38
STAGE48_MUSIC_PLAY_TARGET_ORIGINAL = bytes.fromhex("E80F1CFCFD7B01A9FD430091F65702A9")
STAGE48_MUSIC_PLAY_RELAY_TARGET_RECORD = (
    STAGE48_MUSIC_PLAY_TARGET_OFFSET,
    encode_branch(STAGE48_MUSIC_PLAY_TARGET_OFFSET, STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET),
)
STAGE48_MUSIC_PLAY_RELAY_CODE = (
    STAGE48_MUSIC_PLAY_TARGET_ORIGINAL[:4]
    + bytes.fromhex("FF8300D1E00700A9FE0B00F9E01B00BD")
    + encode_adr(STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET + 0x14, STAGE48_MUSIC_PLAY_RELAY_SLOT_OFFSET, 17)
    + bytes.fromhex("30FEDFC8500000B400023FD6E00740A9FE0B40F9E01B40BDFF830091")
    + encode_branch(STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET + 0x34, STAGE48_MUSIC_PLAY_TARGET_OFFSET + 4)
    + bytes(8)
)

STAGE48_SOUND_ACTOR_PLAY_TARGET_OFFSET = 0x00517CF4
STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET = 0x0068CD80
STAGE48_SOUND_ACTOR_PLAY_RELAY_SLOT_OFFSET = STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET + 0x30
STAGE48_SOUND_ACTOR_PLAY_TARGET_ORIGINAL = bytes.fromhex("FD7BBEA9F30B00F9FD030091F30300AA")
STAGE48_SOUND_ACTOR_PLAY_RELAY_TARGET_RECORD = (
    STAGE48_SOUND_ACTOR_PLAY_TARGET_OFFSET,
    encode_branch(STAGE48_SOUND_ACTOR_PLAY_TARGET_OFFSET, STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET),
)
STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE = (
    STAGE48_SOUND_ACTOR_PLAY_TARGET_ORIGINAL[:4]
    + bytes.fromhex("FF8300D1E00700A9")  # temporary aligned frame; save x0/x1
    + encode_adr(STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET + 0x0C, STAGE48_SOUND_ACTOR_PLAY_RELAY_SLOT_OFFSET, 17)
    + bytes.fromhex("30FEDFC8500000B400023FD6E00740A9FF830091")
    + encode_branch(STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET + 0x24, STAGE48_SOUND_ACTOR_PLAY_TARGET_OFFSET + 4)
    + bytes(8)
)

STAGE48_SOUND_ACTOR_PAUSE_TARGET_OFFSET = 0x00517D2C
STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET = 0x0068CDC0
STAGE48_SOUND_ACTOR_PAUSE_RELAY_SLOT_OFFSET = STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET + 0x30
STAGE48_SOUND_ACTOR_PAUSE_TARGET_ORIGINAL = bytes.fromhex("FD7BBEA9F30B00F9FD030091F30300AA")
STAGE48_SOUND_ACTOR_PAUSE_RELAY_TARGET_RECORD = (
    STAGE48_SOUND_ACTOR_PAUSE_TARGET_OFFSET,
    encode_branch(STAGE48_SOUND_ACTOR_PAUSE_TARGET_OFFSET, STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET),
)
STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE = (
    STAGE48_SOUND_ACTOR_PAUSE_TARGET_ORIGINAL[:4]
    + bytes.fromhex("FF8300D1E00700A9")
    + encode_adr(STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET + 0x0C, STAGE48_SOUND_ACTOR_PAUSE_RELAY_SLOT_OFFSET, 17)
    + bytes.fromhex("30FEDFC8500000B400023FD6E00740A9FF830091")
    + encode_branch(STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET + 0x24, STAGE48_SOUND_ACTOR_PAUSE_TARGET_OFFSET + 4)
    + bytes(8)
)
STAGE48_MUSIC_REPLAY_RELAY_RECORDS = [
    STAGE48_MUSIC_PLAY_RELAY_TARGET_RECORD,
    (STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET, STAGE48_MUSIC_PLAY_RELAY_CODE),
    STAGE48_SOUND_ACTOR_PLAY_RELAY_TARGET_RECORD,
    (STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET, STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE),
    STAGE48_SOUND_ACTOR_PAUSE_RELAY_TARGET_RECORD,
    (STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET, STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE),
]

# MC_PRE_GET_COLLECTIBLE 必须在 ItemPool::GetCollectible 的唯一入口分发。relay
# 先执行原版栈帧，再将参数交给 Runtime；nil 路径恢复参数并回到原函数，覆盖
# 路径恢复调用者栈帧并直接返回 Runtime 提供的 collectible type。
PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET = 0x003C6350
PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET = 0x0068CE00
PRE_GET_COLLECTIBLE_RELAY_SLOT_OFFSET = PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET + 0x58
PRE_GET_COLLECTIBLE_RELAY_TARGET_ORIGINAL = bytes.fromhex("FF0304D1E84B00FDFD7B0AA9FD830291")
PRE_GET_COLLECTIBLE_RELAY_CAVE_ORIGINAL = bytes(0x60)
PRE_GET_COLLECTIBLE_RELAY_TARGET_RECORD = (
    PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET,
    encode_branch(PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET, PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET),
)
PRE_GET_COLLECTIBLE_RELAY_CODE = (
    PRE_GET_COLLECTIBLE_RELAY_TARGET_ORIGINAL[:4]
    + bytes.fromhex(
        "FF0301D1"  # sub sp, sp, #0x40
        "E00700A9"  # stp x0, x1, [sp]
        "E20F01A9"  # stp x2, x3, [sp, #0x10]
        "E41300F9"  # str x4, [sp, #0x20]
        "FE1700F9"  # str x30, [sp, #0x28]
    )
    + encode_adr(PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET + 0x18,
                 PRE_GET_COLLECTIBLE_RELAY_SLOT_OFFSET, 17)
    + bytes.fromhex(
        "30FEDFC8"  # ldar x16, [x17]
        "100100B4"  # cbz x16, original trampoline
        "00023FD6"  # blr x16
        "11FC60D3"  # lsr x17, x0, #32
        "B10000B4"  # cbz x17, original trampoline
        "FE1740F9"  # ldr x30, [sp, #0x28]
        "FF030191"  # add sp, sp, #0x40
        "FF030491"  # add sp, sp, #0x100
        "C0035FD6"  # ret
        "E00740A9"  # ldp x0, x1, [sp]
        "E20F41A9"  # ldp x2, x3, [sp, #0x10]
        "E41340F9"  # ldr x4, [sp, #0x20]
        "FE1740F9"  # ldr x30, [sp, #0x28]
        "FF030191"  # add sp, sp, #0x40
    )
    + encode_branch(PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET + 0x54,
                    PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET + 4)
    + bytes(8)
)
PRE_GET_COLLECTIBLE_RELAY_RECORDS = [
    PRE_GET_COLLECTIBLE_RELAY_TARGET_RECORD,
    (PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET, PRE_GET_COLLECTIBLE_RELAY_CODE),
]

# Stage102 only observes after the original Level::ChangeRoom call has returned.
# The relay keeps the original Game receiver in x0, calls an optional void(Game*)
# Runtime callback, restores x0, then continues at Game::ChangeRoom + 0x74.
GAME_CHANGE_ROOM_CALL_OFFSET = 0x00354050
GAME_CHANGE_ROOM_CALL_ORIGINAL = bytes.fromhex("FC990C94")
GAME_CHANGE_ROOM_LEVEL_CHANGE_ROOM_PLT_OFFSET = 0x0067A840
GAME_CHANGE_ROOM_RELAY_CODE_OFFSET = 0x0068CE60
GAME_CHANGE_ROOM_RELAY_SLOT_OFFSET = GAME_CHANGE_ROOM_RELAY_CODE_OFFSET + 0x30
GAME_CHANGE_ROOM_RELAY_LENGTH = 0x40
GAME_CHANGE_ROOM_RELAY_CAVE_ORIGINAL = bytes(GAME_CHANGE_ROOM_RELAY_LENGTH)
GAME_CHANGE_ROOM_RELAY_TARGET_RECORD = (
    GAME_CHANGE_ROOM_CALL_OFFSET,
    encode_branch(GAME_CHANGE_ROOM_CALL_OFFSET, GAME_CHANGE_ROOM_RELAY_CODE_OFFSET),
)
GAME_CHANGE_ROOM_RELAY_CODE = (
    bytes.fromhex(
        "FF4300D1"  # sub sp, sp, #0x10
        "FE0700F9"  # str x30, [sp, #8]
    )
    + encode_bl(GAME_CHANGE_ROOM_RELAY_CODE_OFFSET + 0x08,
                GAME_CHANGE_ROOM_LEVEL_CHANGE_ROOM_PLT_OFFSET)
    + bytes.fromhex("E00313AA")  # mov x0, x19
    + encode_adr(GAME_CHANGE_ROOM_RELAY_CODE_OFFSET + 0x10,
                 GAME_CHANGE_ROOM_RELAY_SLOT_OFFSET, 17)
    + bytes.fromhex(
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, original post-call continuation
        "00023FD6"  # blr x16
        "FE0740F9"  # ldr x30, [sp, #8]
        "FF430091"  # add sp, sp, #0x10
    )
    + encode_branch(GAME_CHANGE_ROOM_RELAY_CODE_OFFSET + 0x28,
                    GAME_CHANGE_ROOM_CALL_OFFSET + 4)
    + bytes(20)
)
GAME_CHANGE_ROOM_RELAY_RECORDS = [
    GAME_CHANGE_ROOM_RELAY_TARGET_RECORD,
    (GAME_CHANGE_ROOM_RELAY_CODE_OFFSET, GAME_CHANGE_ROOM_RELAY_CODE),
]

# Stage108 observes only after Game::StartFromSavedState / Game::Start have
# returned to Manager::execute_start_game.  Both original calls are replaced by
# a small relay that preserves the Game receiver and caller return address,
# invokes the original PLT thunk, then optionally calls void(Game*, u32).
GAME_START_SAVED_CALL_OFFSET = 0x003F92A4
GAME_START_SAVED_CALL_ORIGINAL = bytes.fromhex("F30D0A94")
GAME_START_SAVED_PLT_OFFSET = 0x0067CA70
GAME_START_SAVED_RESUME_OFFSET = GAME_START_SAVED_CALL_OFFSET + 4
GAME_START_NEW_CALL_OFFSET = 0x003F93F8
GAME_START_NEW_CALL_ORIGINAL = bytes.fromhex("AE0D0A94")
GAME_START_NEW_PLT_OFFSET = 0x0067CAB0
GAME_START_NEW_RESUME_OFFSET = GAME_START_NEW_CALL_OFFSET + 4
# 2026-09-14：代码洞从 0x68CEA0 迁到 0x68CC20 —— 原位置与后来加的 render-present 中继
# （占 0x68CF00+64）**字节重叠**，两条补丁互相覆盖，游戏加载失败。
# 新位置 0x68CC20–0x68CD40 在原始 NRO 里整段为 0，且与当前 5 条已部署中继零交集。
GAME_START_SAVED_RELAY_CODE_OFFSET = 0x0068CC20
GAME_START_NEW_RELAY_CODE_OFFSET = 0x0068CC70
GAME_START_RELAY_SLOT_OFFSET = 0x0068CCC0
GAME_START_RELAY_LENGTH = 0x50
GAME_START_RELAY_CAVE_ORIGINAL = bytes(0x100)


def build_game_start_lifecycle_relay(
    code_offset: int, plt_offset: int, resume_offset: int, event_kind: int
) -> bytes:
    """Build one ABI-preserving post-Game-start relay for Stage108."""
    if event_kind not in (1, 2):
        raise ValueError("Game start lifecycle relay event kind is invalid")
    return (
        bytes.fromhex(
            "FF8300D1"  # sub sp, sp, #0x20
            "E00700A9"  # stp x0, x1, [sp]
            "FE0B00F9"  # str x30, [sp, #0x10]
        )
        + encode_bl(code_offset + 0x0C, plt_offset)
        + bytes.fromhex("E00340F9")  # ldr x0, [sp]
        + (0x52800001 | (event_kind << 5)).to_bytes(4, "little")  # mov w1, #event_kind
        + encode_adr(code_offset + 0x18, GAME_START_RELAY_SLOT_OFFSET, 17)
        + bytes.fromhex(
            "30FEDFC8"  # ldar x16, [x17]
            "500000B4"  # cbz x16, restore original callsite context
            "00023FD6"  # blr x16
            "E00740A9"  # ldp x0, x1, [sp]
            "FE0B40F9"  # ldr x30, [sp, #0x10]
            "FF830091"  # add sp, sp, #0x20
        )
        + encode_branch(code_offset + 0x34, resume_offset)
        + bytes(GAME_START_RELAY_LENGTH - 0x38)
    )


GAME_START_SAVED_RELAY_CODE = build_game_start_lifecycle_relay(
    GAME_START_SAVED_RELAY_CODE_OFFSET,
    GAME_START_SAVED_PLT_OFFSET,
    GAME_START_SAVED_RESUME_OFFSET,
    1,
)
GAME_START_NEW_RELAY_CODE = build_game_start_lifecycle_relay(
    GAME_START_NEW_RELAY_CODE_OFFSET,
    GAME_START_NEW_PLT_OFFSET,
    GAME_START_NEW_RESUME_OFFSET,
    2,
)
GAME_START_LIFECYCLE_RELAY_RECORDS = [
    (GAME_START_SAVED_CALL_OFFSET,
     encode_branch(GAME_START_SAVED_CALL_OFFSET, GAME_START_SAVED_RELAY_CODE_OFFSET)),
    (GAME_START_NEW_CALL_OFFSET,
     encode_branch(GAME_START_NEW_CALL_OFFSET, GAME_START_NEW_RELAY_CODE_OFFSET)),
    (GAME_START_SAVED_RELAY_CODE_OFFSET, GAME_START_SAVED_RELAY_CODE),
    (GAME_START_NEW_RELAY_CODE_OFFSET, GAME_START_NEW_RELAY_CODE),
]

# Stage109 supersedes the unused Stage108 cave.  Each real Game::Update restart
# call has its own relay so its original post-call Seeds cleanup resume remains
# local and no caller-address state needs to be reconstructed.
GAME_RESTART_CALL_OFFSETS = (0x00351C50, 0x00351D20, 0x00351ECC)
GAME_RESTART_CALL_ORIGINALS = (bytes.fromhex("DC7E0C94"), bytes.fromhex("A87E0C94"),
                               bytes.fromhex("3D7E0C94"))
GAME_RESTART_PLT_OFFSET = 0x006717C0
GAME_RESTART_RELAY_CODE_OFFSETS = (0x0068CEA0, 0x0068CEF0, 0x0068CF40)
GAME_RESTART_RELAY_SLOT_OFFSET = 0x0068CF90
GAME_RESTART_RELAY_LENGTH = 0x50
GAME_RESTART_RELAY_CAVE_ORIGINAL = bytes(0x100)


def build_game_restart_postcall_relay(code_offset: int, resume_offset: int, event_kind: int) -> bytes:
    """Build one ABI-preserving post-Manager::RestartGame relay for Stage109."""
    return (
        bytes.fromhex(
            "FF8300D1"  # sub sp, sp, #0x20
            "E00700A9"  # stp x0, x1, [sp]
            "FE0B00F9"  # str x30, [sp, #0x10]
        )
        + encode_bl(code_offset + 0x0C, GAME_RESTART_PLT_OFFSET)
        + bytes.fromhex("E00340F9")  # ldr x0, [sp]
        + (0x52800001 | (event_kind << 5)).to_bytes(4, "little")
        + encode_adr(code_offset + 0x18, GAME_RESTART_RELAY_SLOT_OFFSET, 17)
        + bytes.fromhex(
            "30FEDFC8"  # ldar x16, [x17]
            "500000B4"  # cbz x16, restore original callsite context
            "00023FD6"  # blr x16
            "E00740A9"  # ldp x0, x1, [sp]
            "FE0B40F9"  # ldr x30, [sp, #0x10]
            "FF830091"  # add sp, sp, #0x20
        )
        + encode_branch(code_offset + 0x34, resume_offset)
        + bytes(GAME_RESTART_RELAY_LENGTH - 0x38)
    )


GAME_RESTART_RELAY_CODES = tuple(
    build_game_restart_postcall_relay(code, call + 4, index + 1)
    for index, (call, code) in enumerate(zip(GAME_RESTART_CALL_OFFSETS, GAME_RESTART_RELAY_CODE_OFFSETS))
)
GAME_RESTART_RELAY_RECORDS = [
    *( (call, encode_branch(call, code))
       for call, code in zip(GAME_RESTART_CALL_OFFSETS, GAME_RESTART_RELAY_CODE_OFFSETS) ),
    *( (code, payload) for code, payload in zip(GAME_RESTART_RELAY_CODE_OFFSETS, GAME_RESTART_RELAY_CODES) ),
]


def require_expected_module(nro: bytes) -> None:
    module_id = read_module_id(nro)
    if module_id != EXPECTED_MODULE_ID:
        raise ValueError(
            "Repentance.nro 模块 ID 不匹配："
            f"期望 {EXPECTED_MODULE_ID.hex().upper()}，"
            f"实际 {module_id.hex().upper()}"
        )


def require_original_bytes(nro: bytes, offset: int, expected: bytes, label: str) -> None:
    actual = nro[offset : offset + len(expected)]
    if actual != expected:
        raise ValueError(
            f"{label}原始字节不匹配：期望 {expected.hex().upper()}，"
            f"实际 {actual.hex().upper()}"
        )


def require_expected_source_sha256(nro: bytes) -> None:
    actual = hashlib.sha256(nro).hexdigest()
    if actual != EXPECTED_SOURCE_SHA256:
        raise ValueError(
            "unsupported source_sha256："
            f"期望 {EXPECTED_SOURCE_SHA256}，实际 {actual}"
        )


def build_greed_patch(nro: bytes) -> bytes:
    """使 GetGreedDonationBreakChance 永远返回单精度 0.0。"""
    require_expected_module(nro)
    require_original_bytes(nro, GREED_OFFSET, GREED_ORIGINAL, "贪婪捐款机概率函数")
    return encode_ips([GREED_RECORD])


def build_normal_patch(nro: bytes) -> bytes:
    """跳过普通捐款机两个概率卡住路径，继续既有投币处理。"""
    require_expected_module(nro)
    require_original_bytes(nro, NORMAL_OFFSET, NORMAL_ORIGINAL, "普通捐款机卡住分支")
    require_original_bytes(
        nro, NORMAL_SECOND_OFFSET, NORMAL_SECOND_ORIGINAL, "普通捐款机第二卡住分支"
    )
    return encode_ips(NORMAL_RECORDS)


def build_manager_update_relay_patch(nro: bytes) -> bytes:
    """在已验证的 .text 填充区安装 Manager::Update 单指令中继。"""
    require_expected_module(nro)
    require_original_bytes(nro, RELAY_TARGET_OFFSET, RELAY_TARGET_ORIGINAL, "Manager::Update 入口")
    require_original_bytes(nro, RELAY_CODE_OFFSET, RELAY_CAVE_ORIGINAL, "中继代码洞")
    if RELAY_TARGET_RECORD[1] != encode_branch(RELAY_TARGET_OFFSET, RELAY_CODE_OFFSET):
        raise ValueError("Manager::Update 中继跳转编码不一致")
    return encode_ips(RELAY_RECORDS)


def build_manager_loadconfigs_relay_patch(nro: bytes) -> bytes:
    """在已验证的独立 .text 填充区安装 Manager::LoadConfigs 单指令中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro,
        LOAD_CONFIGS_RELAY_TARGET_OFFSET,
        LOAD_CONFIGS_RELAY_TARGET_ORIGINAL,
        "Manager::LoadConfigs 入口",
    )
    require_original_bytes(
        nro,
        LOAD_CONFIGS_RELAY_CODE_OFFSET,
        LOAD_CONFIGS_RELAY_CAVE_ORIGINAL,
        "LoadConfigs 中继代码洞",
    )
    if LOAD_CONFIGS_RELAY_TARGET_RECORD[1] != encode_branch(
        LOAD_CONFIGS_RELAY_TARGET_OFFSET, LOAD_CONFIGS_RELAY_CODE_OFFSET
    ):
        raise ValueError("Manager::LoadConfigs 中继跳转编码不一致")
    return encode_ips(LOAD_CONFIGS_RELAY_RECORDS)


def build_manager_render_relay_patch(nro: bytes) -> bytes:
    """在已验证的独立 .text 填充区安装 Manager::Render 单指令中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro, RENDER_RELAY_TARGET_OFFSET, RENDER_RELAY_TARGET_ORIGINAL, "Manager::Render 入口"
    )
    require_original_bytes(nro, RENDER_RELAY_CODE_OFFSET, RENDER_RELAY_CAVE_ORIGINAL, "Render 中继代码洞")
    if RENDER_RELAY_TARGET_RECORD[1] != encode_branch(
        RENDER_RELAY_TARGET_OFFSET, RENDER_RELAY_CODE_OFFSET
    ):
        raise ValueError("Manager::Render 中继跳转编码不一致")
    if RENDER_RELAY_CODE[RENDER_RELAY_SLOT_OFFSET - RENDER_RELAY_CODE_OFFSET:] != bytes(8):
        raise ValueError("Manager::Render 中继 callback 槽必须保持零值")
    return encode_ips(RENDER_RELAY_RECORDS)


def build_game_observer_relay_patch(nro: bytes) -> bytes:
    """在 Game 发布后的 Game::Init 调用前安装只观察对象的中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro,
        GAME_INIT_CONTEXT_OFFSET,
        GAME_INIT_CONTEXT_ORIGINAL,
        "Game::Init 发布上下文",
    )
    require_original_bytes(
        nro,
        GAME_INIT_CALL_OFFSET,
        GAME_INIT_CALL_ORIGINAL,
        "Game::Init 调用",
    )
    require_original_bytes(
        nro,
        GAME_OBSERVER_RELAY_CODE_OFFSET,
        GAME_OBSERVER_RELAY_CAVE_ORIGINAL,
        "Game observer 中继代码洞",
    )
    require_expected_source_sha256(nro)
    if GAME_OBSERVER_RELAY_CODE[GAME_OBSERVER_RELAY_SLOT_OFFSET - GAME_OBSERVER_RELAY_CODE_OFFSET:] != bytes(16):
        raise ValueError("Game observer 中继 callback 槽必须保持零值")
    if GAME_OBSERVER_RELAY_RECORDS[0][1] != encode_branch(
        GAME_INIT_CALL_OFFSET, GAME_OBSERVER_RELAY_CODE_OFFSET
    ):
        raise ValueError("Game observer 中继入口跳转编码不一致")
    if GAME_OBSERVER_RELAY_CODE[0x28:0x2C] != encode_bl(
        GAME_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_INIT_PLT_OFFSET
    ):
        raise ValueError("Game observer 中继原始 BL 编码不一致")
    return encode_ips(GAME_OBSERVER_RELAY_RECORDS)


def build_game_update_observer_relay_patch(nro: bytes) -> bytes:
    """在原版 Game::Update 调用前安装仅观察当帧对象的独立中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro,
        GAME_UPDATE_CONTEXT_OFFSET,
        GAME_UPDATE_CONTEXT_ORIGINAL,
        "Game::Update 参数上下文",
    )
    require_original_bytes(
        nro,
        GAME_UPDATE_CALL_OFFSET,
        GAME_UPDATE_CALL_ORIGINAL,
        "Game::Update 调用",
    )
    require_original_bytes(
        nro,
        GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET,
        GAME_UPDATE_OBSERVER_RELAY_CAVE_ORIGINAL,
        "Game update observer 中继代码洞",
    )
    require_expected_source_sha256(nro)
    if (
        GAME_UPDATE_OBSERVER_RELAY_CODE[
            GAME_UPDATE_OBSERVER_RELAY_SLOT_OFFSET - GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET:
        ]
        != bytes(16)
    ):
        raise ValueError("Game update observer 中继 callback 槽必须保持零值")
    if GAME_UPDATE_OBSERVER_RELAY_RECORDS[0][1] != encode_branch(
        GAME_UPDATE_CALL_OFFSET, GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET
    ):
        raise ValueError("Game update observer 中继入口跳转编码不一致")
    if GAME_UPDATE_OBSERVER_RELAY_CODE[0x28:0x2C] != encode_bl(
        GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_UPDATE_PLT_OFFSET
    ):
        raise ValueError("Game update observer 中继原始 BL 编码不一致")
    return encode_ips(GAME_UPDATE_OBSERVER_RELAY_RECORDS)


def build_game_state2_observer_relay_patch(nro: bytes) -> bytes:
    """在 Manager 状态 2 的原版调用前安装仅观察当前 Game 参数的中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro,
        GAME_STATE2_CONTEXT_OFFSET,
        GAME_STATE2_CONTEXT_ORIGINAL,
        "state-2 参数上下文",
    )
    require_original_bytes(
        nro,
        GAME_STATE2_CALL_OFFSET,
        GAME_STATE2_CALL_ORIGINAL,
        "state-2 原版调用",
    )
    require_original_bytes(
        nro,
        GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET,
        GAME_STATE2_OBSERVER_RELAY_CAVE_ORIGINAL,
        "state-2 observer 中继代码洞",
    )
    require_expected_source_sha256(nro)
    if (
        GAME_STATE2_OBSERVER_RELAY_CODE[
            GAME_STATE2_OBSERVER_RELAY_SLOT_OFFSET - GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET:
        ]
        != bytes(16)
    ):
        raise ValueError("state-2 observer 中继 callback 槽必须保持零值")
    if GAME_STATE2_OBSERVER_RELAY_RECORDS[0][1] != encode_branch(
        GAME_STATE2_CALL_OFFSET, GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET
    ):
        raise ValueError("state-2 observer 中继入口跳转编码不一致")
    if GAME_STATE2_OBSERVER_RELAY_CODE[0x28:0x2C] != encode_bl(
        GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_STATE2_PLT_OFFSET
    ):
        raise ValueError("state-2 observer 中继原始 BL 编码不一致")
    return encode_ips(GAME_STATE2_OBSERVER_RELAY_RECORDS)


def build_game_ispaused_render_observer_relay_patch(nro: bytes) -> bytes:
    """在原版 Render 的 Game::IsPaused 调用前安装只观察参数的中继。"""
    require_expected_module(nro)
    require_original_bytes(nro, GAME_ISPAUSED_RENDER_CONTEXT_OFFSET, GAME_ISPAUSED_RENDER_CONTEXT_ORIGINAL, "Render IsPaused 参数上下文")
    require_original_bytes(nro, GAME_ISPAUSED_RENDER_CALL_OFFSET, GAME_ISPAUSED_RENDER_CALL_ORIGINAL, "Render IsPaused 调用")
    require_original_bytes(nro, GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET, GAME_ISPAUSED_RENDER_RELAY_CAVE_ORIGINAL, "Render IsPaused observer 中继代码洞")
    require_expected_source_sha256(nro)
    if GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE[0x30:] != bytes(16):
        raise ValueError("Render IsPaused observer 中继 callback 槽必须保持零值")
    return encode_ips(GAME_ISPAUSED_RENDER_RELAY_RECORDS)


def build_stage48_music_replay_relay_patch(nro: bytes) -> bytes:
    """把 Stage48 三个观测目标改接到 NRO 内近距离 relay。"""
    require_expected_module(nro)
    require_expected_source_sha256(nro)
    targets = (
        (STAGE48_MUSIC_PLAY_TARGET_OFFSET, STAGE48_MUSIC_PLAY_TARGET_ORIGINAL, "Music::Play"),
        (STAGE48_SOUND_ACTOR_PLAY_TARGET_OFFSET, STAGE48_SOUND_ACTOR_PLAY_TARGET_ORIGINAL, "SoundActor::Play"),
        (STAGE48_SOUND_ACTOR_PAUSE_TARGET_OFFSET, STAGE48_SOUND_ACTOR_PAUSE_TARGET_ORIGINAL, "SoundActor::Pause"),
    )
    for offset, original, label in targets:
        require_original_bytes(nro, offset, original, f"Stage48 {label} 入口")
    for offset, code, label in (
        (STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET, STAGE48_MUSIC_PLAY_RELAY_CODE, "Music::Play relay"),
        (STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET, STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE, "SoundActor::Play relay"),
        (STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET, STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE, "SoundActor::Pause relay"),
    ):
        require_original_bytes(nro, offset, bytes(len(code)), label)
    for target, relay in (
        (STAGE48_MUSIC_PLAY_TARGET_OFFSET, STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET),
        (STAGE48_SOUND_ACTOR_PLAY_TARGET_OFFSET, STAGE48_SOUND_ACTOR_PLAY_RELAY_CODE_OFFSET),
        (STAGE48_SOUND_ACTOR_PAUSE_TARGET_OFFSET, STAGE48_SOUND_ACTOR_PAUSE_RELAY_CODE_OFFSET),
    ):
        if encode_branch(target, relay) != dict(STAGE48_MUSIC_REPLAY_RELAY_RECORDS)[target]:
            raise ValueError("Stage48 relay 入口跳转编码不一致")
    return encode_ips(STAGE48_MUSIC_REPLAY_RELAY_RECORDS)


def build_pre_get_collectible_relay_patch(nro: bytes) -> bytes:
    """安装 MC_PRE_GET_COLLECTIBLE 的共享入口 relay，尚不发布 callback。"""
    require_expected_module(nro)
    require_expected_source_sha256(nro)
    require_original_bytes(
        nro,
        PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET,
        PRE_GET_COLLECTIBLE_RELAY_TARGET_ORIGINAL,
        "ItemPool::GetCollectible 入口",
    )
    require_original_bytes(
        nro,
        PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET,
        PRE_GET_COLLECTIBLE_RELAY_CAVE_ORIGINAL,
        "PreGetCollectible relay 代码洞",
    )
    if PRE_GET_COLLECTIBLE_RELAY_TARGET_RECORD[1] != encode_branch(
        PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET, PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET
    ):
        raise ValueError("PreGetCollectible relay 入口跳转编码不一致")
    if PRE_GET_COLLECTIBLE_RELAY_CODE[PRE_GET_COLLECTIBLE_RELAY_SLOT_OFFSET - PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET:] != bytes(8):
        raise ValueError("PreGetCollectible relay callback 槽必须保持零值")
    return encode_ips(PRE_GET_COLLECTIBLE_RELAY_RECORDS)


def build_game_change_room_relay_patch(nro: bytes) -> bytes:
    """Install the isolated post-Level::ChangeRoom read-only diagnostic relay."""
    require_expected_module(nro)
    require_expected_source_sha256(nro)
    require_original_bytes(
        nro, GAME_CHANGE_ROOM_CALL_OFFSET, GAME_CHANGE_ROOM_CALL_ORIGINAL,
        "Game::ChangeRoom Level::ChangeRoom 调用",
    )
    require_original_bytes(
        nro, GAME_CHANGE_ROOM_RELAY_CODE_OFFSET, GAME_CHANGE_ROOM_RELAY_CAVE_ORIGINAL,
        "Game::ChangeRoom 中继代码洞",
    )
    if GAME_CHANGE_ROOM_RELAY_TARGET_RECORD[1] != encode_branch(
        GAME_CHANGE_ROOM_CALL_OFFSET, GAME_CHANGE_ROOM_RELAY_CODE_OFFSET
    ):
        raise ValueError("Game::ChangeRoom relay 入口跳转编码不一致")
    if GAME_CHANGE_ROOM_RELAY_CODE[
        GAME_CHANGE_ROOM_RELAY_SLOT_OFFSET - GAME_CHANGE_ROOM_RELAY_CODE_OFFSET:
    ] != bytes(16):
        raise ValueError("Game::ChangeRoom relay callback 槽必须保持零值")
    return encode_ips(GAME_CHANGE_ROOM_RELAY_RECORDS)


def build_game_start_lifecycle_relay_patch(nro: bytes) -> bytes:
    """Install the isolated Stage108 Game-start post-call diagnostic relays."""
    require_expected_module(nro)
    require_expected_source_sha256(nro)
    require_original_bytes(nro, GAME_START_SAVED_CALL_OFFSET, GAME_START_SAVED_CALL_ORIGINAL,
                           "Game::StartFromSavedState 调用")
    require_original_bytes(nro, GAME_START_NEW_CALL_OFFSET, GAME_START_NEW_CALL_ORIGINAL,
                           "Game::Start 调用")
    require_original_bytes(nro, GAME_START_SAVED_RELAY_CODE_OFFSET,
                           GAME_START_RELAY_CAVE_ORIGINAL,
                           "Game lifecycle 中继代码洞")
    if any(record != encode_branch(call, relay) for call, relay, record in (
        (GAME_START_SAVED_CALL_OFFSET, GAME_START_SAVED_RELAY_CODE_OFFSET,
         GAME_START_LIFECYCLE_RELAY_RECORDS[0][1]),
        (GAME_START_NEW_CALL_OFFSET, GAME_START_NEW_RELAY_CODE_OFFSET,
         GAME_START_LIFECYCLE_RELAY_RECORDS[1][1]),
    )):
        raise ValueError("Game lifecycle relay 入口跳转编码不一致")
    if nro[GAME_START_RELAY_SLOT_OFFSET:GAME_START_RELAY_SLOT_OFFSET + 8] != bytes(8):
        raise ValueError("Game lifecycle relay callback 槽必须保持零值")
    return encode_ips(GAME_START_LIFECYCLE_RELAY_RECORDS)


def build_game_restart_postcall_relay_patch(nro: bytes) -> bytes:
    """Install the isolated Stage109 post-Manager::RestartGame relays."""
    require_expected_module(nro)
    require_expected_source_sha256(nro)
    for call, original in zip(GAME_RESTART_CALL_OFFSETS, GAME_RESTART_CALL_ORIGINALS):
        require_original_bytes(nro, call, original, "Game::Update RestartGame 调用")
    require_original_bytes(nro, GAME_RESTART_RELAY_CODE_OFFSETS[0],
                           GAME_RESTART_RELAY_CAVE_ORIGINAL, "Game restart 中继代码洞")
    if any(record != encode_branch(call, code) for call, code, record in (
        (call, code, GAME_RESTART_RELAY_RECORDS[index][1])
        for index, (call, code) in enumerate(zip(GAME_RESTART_CALL_OFFSETS, GAME_RESTART_RELAY_CODE_OFFSETS))
    )):
        raise ValueError("Game restart relay 入口跳转编码不一致")
    if nro[GAME_RESTART_RELAY_SLOT_OFFSET:GAME_RESTART_RELAY_SLOT_OFFSET + 8] != bytes(8):
        raise ValueError("Game restart relay callback 槽必须保持零值")
    return encode_ips(GAME_RESTART_RELAY_RECORDS)


# Stage 148: Mod 内容挂载点重建中继。
#
# 目标函数 `IsaacRepentance::RebuildContentMountPoints()`（0x3B3510）开头就是
# `ClearMountPoints(false)` 并重建整张挂载点表，所以 Mod 的 `resources/` 挂载点会
# 随内容重载一起消失，必须在它每次返回后重新注册。
#
# 为什么补这一条 `ret`（0x3B36F4）而不是各调用点：
#   * 函数体内**只有一条** `ret`（实测），全部早退分支（0x3B3564/0x3B35A4/
#     0x3B35AC/0x3B36C0）都汇聚到它，因此补一处即覆盖全部返回路径；
#   * 4 个调用点里 `Manager::SetLanguage+0x40` 是 `b` 尾调用，补调用点在那条路上
#     无解；
#   * 序言（`sub sp,#0x90` / `stp` / `add x29`）完全不碰，所以没有覆盖序言导致
#     栈不平衡的风险；到 `ret` 时 x19/x20/x21/sp/x29/x30 都已恢复。
#
# 桩只保存 `x0`（返回值）与 `x30`（返回地址）：`blr` 会覆盖 `x30`，所以必须自己
# 保存并在回调返回后恢复；`x0..x18` 是 caller-saved，实测 4 个调用点在调用后都会
# 重写它们，不读旧值。
REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET = 0x003B36F4
REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET = 0x0068CFA0
REBUILD_MOUNT_POINTS_RELAY_SLOT_OFFSET = REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET + 0x28
REBUILD_MOUNT_POINTS_RELAY_TARGET_ORIGINAL = bytes.fromhex("C0035FD6E02340F905F10A94E8034039")
REBUILD_MOUNT_POINTS_RELAY_CAVE_ORIGINAL = bytes(0x30)
REBUILD_MOUNT_POINTS_RELAY_TARGET_RECORD = (
    REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET,
    encode_branch(REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET,
                  REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET),
)
REBUILD_MOUNT_POINTS_RELAY_CODE = (
    bytes.fromhex("FF8300D1E07B00A9")  # sub sp, sp, #0x20 ; stp x0, x30, [sp]
    + encode_adr(REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET + 0x08,
                 REBUILD_MOUNT_POINTS_RELAY_SLOT_OFFSET, 17)  # adr x17, <slot>
    + bytes.fromhex("30FEDFC8")  # ldar x16, [x17]
    + bytes.fromhex("500000B4")  # cbz x16, <restore>  (skip the call when unpublished)
    + bytes.fromhex("00023FD6")  # blr x16
    + bytes.fromhex("E07B40A9FF830091C0035FD6")  # ldp x0,x30,[sp] ; add sp,sp,#0x20 ; ret
    + bytes(4)                   # padding to the slot
    + bytes(8)                   # callback slot, published by the Runtime
)
REBUILD_MOUNT_POINTS_RELAY_RECORDS = [
    REBUILD_MOUNT_POINTS_RELAY_TARGET_RECORD,
    (REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET, REBUILD_MOUNT_POINTS_RELAY_CODE),
]


def build_rebuild_mount_points_relay_patch(nro: bytes) -> bytes:
    """在 RebuildContentMountPoints() 的唯一 `ret` 上安装返回后中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro,
        REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET,
        REBUILD_MOUNT_POINTS_RELAY_TARGET_ORIGINAL,
        "RebuildContentMountPoints 唯一 ret",
    )
    require_original_bytes(
        nro, REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET,
        REBUILD_MOUNT_POINTS_RELAY_CAVE_ORIGINAL, "挂载点重建中继代码洞",
    )
    if REBUILD_MOUNT_POINTS_RELAY_TARGET_RECORD[1] != encode_branch(
            REBUILD_MOUNT_POINTS_RELAY_TARGET_OFFSET,
            REBUILD_MOUNT_POINTS_RELAY_CODE_OFFSET):
        raise ValueError("挂载点重建中继跳转编码不一致")
    if len(REBUILD_MOUNT_POINTS_RELAY_CODE) != 0x30:
        raise ValueError("挂载点重建中继记录长度必须为 0x30")
    return encode_ips(REBUILD_MOUNT_POINTS_RELAY_RECORDS)


def build_render_present_relay_patch(nro: bytes) -> bytes:
    """在 `Manager::Render` 体内最后一次 `Present` 调用前安装派发中继。"""
    require_expected_module(nro)
    require_original_bytes(
        nro,
        RENDER_PRESENT_CALL_OFFSET,
        RENDER_PRESENT_CALL_ORIGINAL,
        "Manager::Render 内 Present 调用",
    )
    require_original_bytes(
        nro,
        RENDER_PRESENT_RELAY_CODE_OFFSET,
        RENDER_PRESENT_RELAY_CAVE_ORIGINAL,
        "Present 前派发中继代码洞",
    )
    require_expected_source_sha256(nro)
    if RENDER_PRESENT_RELAY_CODE[
        RENDER_PRESENT_RELAY_SLOT_OFFSET - RENDER_PRESENT_RELAY_CODE_OFFSET:
    ] != bytes(16):
        raise ValueError("Present 前派发中继 callback 槽必须保持零值")
    if RENDER_PRESENT_RELAY_RECORDS[0][1] != encode_branch(
        RENDER_PRESENT_CALL_OFFSET, RENDER_PRESENT_RELAY_CODE_OFFSET
    ):
        raise ValueError("Present 前派发中继入口跳转编码不一致")
    if RENDER_PRESENT_RELAY_CODE[0x28:0x2C] != encode_bl(
        RENDER_PRESENT_RELAY_CODE_OFFSET + 0x28, RENDER_PRESENT_PLT_OFFSET
    ):
        raise ValueError("Present 前派发中继原始 BL 编码不一致")
    if len(RENDER_PRESENT_RELAY_CODE) != RENDER_PRESENT_RELAY_LENGTH:
        raise ValueError("Present 前派发中继记录长度必须为 0x40")
    return encode_ips(RENDER_PRESENT_RELAY_RECORDS)




def main() -> None:
    parser = argparse.ArgumentParser(description="生成以撒忏悔版捐款机 IPS 补丁")
    parser.add_argument(
        "--kind",
        choices=[
            "greed",
            "normal",
            "relay",
            "render-relay",
            "loadconfigs-relay",
            "game-observer-relay",
            "game-update-observer-relay",
            "game-state2-observer-relay",
            "game-ispaused-render-observer-relay",
            "stage48-music-replay-relay",
            "pre-get-collectible-relay",
            "change-room-relay",
            "lifecycle-relay",
            "restart-relay",
            "rebuild-mount-points-relay",
            "render-present-relay",
        ],
        required=True,
    )
    parser.add_argument("--nro", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    nro = args.nro.read_bytes()
    if args.kind == "greed":
        patch = build_greed_patch(nro)
        label = "未知"
        label = "贪婪捐款机"
    elif args.kind == "normal":
        patch = build_normal_patch(nro)
        label = "普通捐款机"
    elif args.kind == "relay":
        patch = build_manager_update_relay_patch(nro)
        label = "Manager::Update 中继"
    elif args.kind == "render-relay":
        patch = build_manager_render_relay_patch(nro)
        label = "Manager::Render 中继"
    elif args.kind == "game-observer-relay":
        patch = build_game_observer_relay_patch(nro)
        label = "Game observer 中继"
    elif args.kind == "game-update-observer-relay":
        patch = build_game_update_observer_relay_patch(nro)
        label = "Game update observer 中继"
    elif args.kind == "game-state2-observer-relay":
        patch = build_game_state2_observer_relay_patch(nro)
        label = "Game state-2 observer 中继"
    elif args.kind == "game-ispaused-render-observer-relay":
        patch = build_game_ispaused_render_observer_relay_patch(nro)
        label = "Render IsPaused observer 中继"
    elif args.kind == "stage48-music-replay-relay":
        patch = build_stage48_music_replay_relay_patch(nro)
        label = "Stage48 Music replay 中继"
    elif args.kind == "pre-get-collectible-relay":
        patch = build_pre_get_collectible_relay_patch(nro)
        label = "PreGetCollectible 中继"
    elif args.kind == "change-room-relay":
        patch = build_game_change_room_relay_patch(nro)
        label = "Game::ChangeRoom 中继"
    elif args.kind == "lifecycle-relay":
        patch = build_game_start_lifecycle_relay_patch(nro)
        label = "Game lifecycle 中继"
    elif args.kind == "render-present-relay":
        patch = build_render_present_relay_patch(nro)
        label = "Render Present 前派发中继"
    elif args.kind == "rebuild-mount-points-relay":
        patch = build_rebuild_mount_points_relay_patch(nro)
        label = "Mod 内容挂载点重建中继"
    elif args.kind == "restart-relay":
        patch = build_game_restart_postcall_relay_patch(nro)
        label = "Game restart 中继"
    else:
        patch = build_manager_loadconfigs_relay_patch(nro)
        label = "Manager::LoadConfigs 中继"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patch)
    print(f"已生成{label}补丁：{args.output}")


if __name__ == "__main__":
    main()
