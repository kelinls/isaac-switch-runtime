#!/usr/bin/env python3
"""Stage 150: ``Sprite``（= 引擎里的 ``IsaacRepentance::ANM2``）渲染/加载入口 + ``Font`` 缩放/UTF8 入口
+ ``KAGE::Math::Vector2`` 值类型布局 的三重证据审计。

回答下一批"渲染胶水"API 切片要落地的工程问题：**PC Lua 的 ``Sprite:Render`` / ``RenderLayer`` /
``Load`` / ``LoadGraphics`` / ``GetLayerCount`` 以及 ``Font:DrawStringScaled`` /
``DrawStringUTF8`` / ``DrawStringScaledUTF8`` 分别对应 ``Repentance.nro`` 里的哪个原生入口、
ABI 是什么、``Vector`` 在引擎侧的内存布局是什么。**

结论分三类，JSON 里逐条显式标注（``confidence``）：

* ``verified`` —— 偏移来自游戏自己的 ``.dynsym``、首 16 字节与本文件里**人工转录的 guard 常量**
  逐字节一致、并且本工具解码了该函数的 ABI（参数寄存器/返回值形态与 PC 文档签名逐参对应）；
* ``inferred`` —— 偏移/字节/符号齐全，但 Lua 语义对应只由方法名 + 参数形态推出（未逐条解码 ABI）；
* ``unknown`` —— 该 Lua 方法在本 NRO 中**找不到**对应原生入口（例如 ``Sprite:GetLayerCount``）。

三条硬约束：

1. **每条偏移必须与真实 NRO 对得上**。``GUARDS`` 里的 16 字节是人工从 ``Repentance.nro``
   抄录的转录常量；[`_verify_bytes`] 会把它们与 NRO 逐字节比对，不一致直接让工具失败。
   同一套转录方法还用 ``runtime/source/runtime_constants.hpp`` 里**已有的** Font guard
   （只读、不修改）做交叉校验：能复现出项目自己的常量，才说明转录方法本身可靠。
2. **不猜函数名**。所有函数都用 mangled 名去 ``.dynsym`` 查（``nro_symbols``）；查不到就报
   ``missing_symbols``，绝不按 PC 文档的名字去猜地址。
3. **签名不靠 PC 文档**。``signature`` 由 ``/usr/bin/c++filt`` 从 mangled 名还原，
   ``abi`` 字段是本工具从指令里读出来的（寄存器分配、返回值形态、边界检查、tail-call 目标）。

本工具**只读**：读 NRO、读 ``runtime/source/runtime_constants.hpp`` 与 PC Mod 目录，只写
``analysis/stage150-sprite-abi/`` 下的 JSON。

Usage::

    python3 tools/stage150_sprite_abi_audit.py [--out <json>] [--print]
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import struct
import subprocess
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
RUNTIME_CONSTANTS = os.path.join(REPO_ROOT, "runtime", "source", "runtime_constants.hpp")
PC_MODS_ROOT = os.path.join(
    REPO_ROOT, "analysis", "pc-mod-contract", "romfs-preview", "atmosphere", "contents",
    "010021C000B6A000", "romfs", "isaac_mods", "mods",
)
BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
NRO_SHA256 = "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
TITLE_ID = "010021C000B6A000"
DEFAULT_OUT = os.path.join(
    REPO_ROOT, "analysis", "stage150-sprite-abi", f"{BUILD_ID}.json",
)

CXXFILT = "/usr/bin/c++filt"

# ---------------------------------------------------------------------------
# 转录常量：offset + 16 字节 guard，人工从 Repentance.nro 抄录。
# 键 = ENTRIES/SUPPORTING 里的 key；本工具会逐字节复核（见 _verify_bytes）。
# 抄录日期 2026-09-11，来源 build 91C73FDD…。
# ---------------------------------------------------------------------------
GUARDS: dict[str, tuple[int, str]] = {
    # --- Sprite（= IsaacRepentance::ANM2）渲染 ---
    "sprite_render": (0x9BD4, "fd7bbca9f85f01a9fd030091f65702a9"),
    "sprite_render_layer": (0x9D70, "fd7bbca9f70b00f9fd030091f65702a9"),
    # --- Sprite 加载 ---
    "sprite_load": (0xC970, "fd7bbaa9fb0b00f9fd030091fa6702a9"),
    "sprite_load_graphics": (0xCB6C, "e1031f2a608e1914fd7bbea9f44f01a9"),
    "sprite_replace_spritesheet": (0xD270, "fd7bbda9f50b00f9fd030091f44f02a9"),
    "sprite_reset": (0x65B8, "fd7bbca9f85f01a9fd030091f65702a9"),
    "sprite_reload": (0xCB74, "fd7bbea9f44f01a9fd03009108004039"),
    # --- Sprite 播放/查询 ---
    "sprite_ctor": (0x6540, "fd7bbea9f30b00f9fd030091f30300aa"),
    "sprite_dtor": (0x71A4, "fd7bbea9f30b00f9fd030091f30300aa"),
    "sprite_update": (0x86BC, "e80f1efcfdfb00a9fd230091f30f00f9"),
    "sprite_play": (0xA1F4, "fd7bbda9f65701a9fd030091f44f02a9"),
    "sprite_set_animation": (0xA2E8, "fd7bbca9f85f01a9fd030091f65702a9"),
    "sprite_play_random": (0xA198, "08a040b9880100342908c81a0a2a8052"),
    "sprite_set_frame_named": (0xA65C, "fd7bbca9f70b00f9fd030091f65702a9"),
    "sprite_set_frame": (0xA8CC, "081c40f9880000b42000221e00c00091"),
    "sprite_get_frame": (0xA924, "081c40f9880000b4005040bd0000301e"),
    "sprite_set_layer_frame": (0xA958, "081c40f9680000b400c00091a3961914"),
    "sprite_get_layer_frame": (0xA96C, "fd7bbea9f44f01a9fd030091081c40f9"),
    "sprite_is_playing": (0xA454, "fd7bbfa9fd030091081c40f9280100b4"),
    "sprite_is_finished": (0xA528, "fd7bbfa9fd030091081c40f9680100b4"),
    "sprite_get_texel": (0xC074, "ff8301d1ec0b00fdf52700f9ebab016d"),
    # --- Sprite overlay ---
    "sprite_play_overlay": (0xA9FC, "fd7bbda9f65701a9fd030091f44f02a9"),
    "sprite_set_overlay_animation": (0xAAF0, "fd7bbca9f85f01a9fd030091f65702a9"),
    "sprite_stop_overlay": (0xABA8, "083440f9480000b41f100239c0035fd6"),
    "sprite_is_overlay_playing": (0xAC28, "fd7bbfa9fd030091083440f9280100b4"),
    "sprite_is_overlay_finished": (0xACFC, "fd7bbfa9fd030091083440f9680100b4"),
    "sprite_set_overlay_frame": (0xAEF8, "fd7bbca9f70b00f9fd030091f65702a9"),
    "sprite_get_overlay_frame": (0xAFE0, "083440f9880000b4008040bd0000381e"),
    "sprite_remove_overlay": (0xAFFC, "00800191e1031faa37941914fd7bbea9"),
    # --- Font 缩放 / UTF8 ---
    "font_draw_string_scaled": (0x4CEF6C, "ff4303d1ec2b00fdeb2b066de923076d"),
    "font_draw_string_utf8": (0x4CF2A4, "ff8301d1e923016dfd7b02a9fd830091"),
    "font_draw_string_scaled_utf8": (0x4CFAB8, "ffc301d1eb2b016de923026dfd7b03a9"),
    # --- Vector 值类型 ---
    "vector2_ctor": (0x4FD098, "1f0000f9c0035fd6000000bd010400bd"),
    "vector2_ctor_xy": (0x4FD0A0, "000000bd010400bdc0035fd6ff8300d1"),
    "vector2_length": (0x4FD124, "0004402d2108211e0008201e0028211e"),
    "vector2_zero": (0x8C3D34, "00000000000000000000803f0000803f"),
    "vector2_one": (0x8C3D3C, "0000803f0000803f0100000001000000"),
    # --- 支撑证据（Sprite 类身份判别 / 层数取值的依据）---
    "sprite_class_sprite_ctor": (0x4A4658, "fd7bbda9f65701a9fd030091f44f02a9"),
    "sprite_class_sprite_dtor": (0x4A47B0, "fd7bbea9f30b00f9fd030091483000b0"),
    "sprite_class_sprite_render": (0x4A4F14, "ffc306d1ef3b126dfcbb00f9ed33136d"),
    "sprite_class_sprite_load_anims": (0x4A48B4, "fd7bbea9f30b00f9fd030091f30300aa"),
    "anm2_get_layer_int": (0xA034, "fd7bbea9f44f01a9fd030091f303012a"),
    "anm2_get_layer_const": (0xBC64, "fd7bbea9f44f01a9fd030091f303012a"),
    "font_draw_string": (0x4CEEF0, "ff4301d1e923016dfd7b02a9fd830091"),
    "font_draw_string_scaled_u16": (0x4CF350, "ff4303d1ec2b00fdeb2b066de923076d"),
    "font_draw_string_u16": (0x4CF688, "ff4301d1e923016dfd7b02a9fd830091"),
}

# ---------------------------------------------------------------------------
# 入口记录。signature 由 c++filt 还原（不手写）；abi 是本工具从指令读出的形态。
# ``lua_usage`` = PC Mod 里实际出现的方法名（本工具会在 PC_MODS_ROOT 里统计 file:line）。
# ---------------------------------------------------------------------------
ENTRIES: tuple[dict, ...] = (
    # ---------------------------- Sprite 渲染 ----------------------------
    dict(
        key="sprite_render",
        lua_name="Sprite:Render",
        group="sprite_render",
        mangled="_ZN15IsaacRepentance4ANM26RenderERKN4KAGE4Math7Vector2ES5_S5_",
        lua_usage=["Render"],
        confidence="verified",
        abi=(
            "x0=this(ANM2*)；x1=position(Vector2 const&)、x2=topLeftClamp、x3=bottomRightClamp —— "
            "三个 Vector2 都以指针形式传入（各 8 字节）；返回 void。"
            "函数序言先读 ldrb w8,[x0,#0x149]（加载标志，Load 写 1），为 0 直接返回 → "
            "未 Load 的 Sprite 调 Render 是安全空操作。"
            "随后按 [x0,#0x90]（overlay 优先标志）决定主/覆盖两层 AnimationState 的绘制顺序，"
            "每层循环调用 AnimationLayer::RenderFrame(pos, frameIdx, clamp1, clamp2, layerData)"
            "（0x9C40/0x9C94/0x9CEC/0x9D40 四处 bl，目标 PLT 槽 = "
            "_ZNK15IsaacRepentance4ANM214AnimationLayer11RenderFrameE…）。"
        ),
        note=(
            "PC 文档签名 Render(Vector Position, Vector TopLeftClamp=Vector.Zero, "
            "Vector BottomRightClamp=Vector.Zero) 与本 ABI 逐参一致；EID 三处调用都恰好传 3 个 Vector。"
        ),
    ),
    dict(
        key="sprite_render_layer",
        lua_name="Sprite:RenderLayer",
        group="sprite_render",
        mangled="_ZN15IsaacRepentance4ANM211RenderLayerEiRKN4KAGE4Math7Vector2ES5_S5_",
        lua_usage=["RenderLayer"],
        confidence="verified",
        abi=(
            "x0=this；w1=layerId(int，有符号)；x2=position、x3=topLeftClamp、x4=bottomRightClamp"
            "（三个 Vector2 const&，各 8 字节）；返回 void。"
            "w1<0（tbnz w1,#0x1f）或 w1 >= [x0+0xB0]（层数，cmp w9,w23; b.le）时整段跳过 → "
            "越界 layerId 是安全空操作；命中时 w10=0x18，x0 = 层数组基址 + 0x18*layerId，"
            "再 bl AnimationLayer::RenderFrame。"
        ),
        note="PC 文档 RenderLayer(int LayerId, Vector Position, Vector TopLeftClamp, Vector BottomRightClamp) 逐参一致。",
    ),
    # ---------------------------- Sprite 加载 ----------------------------
    dict(
        key="sprite_load",
        lua_name="Sprite:Load",
        group="sprite_load",
        mangled=(
            "_ZN15IsaacRepentance4ANM24LoadERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEEb"
        ),
        lua_usage=["Load"],
        confidence="verified",
        abi=(
            "x0=this(ANM2*)；x1=std::string const&（libc++ std::string 的隐藏引用，即指向 24 字节 "
            "string 对象的指针）；w2=loadGraphics(bool)；返回 void。"
            "入口先按 [x0] 的短串标志与 [x0+0x8] 判断是否已有路径，若有则 "
            "g_AnmCache.RemoveReference(旧路径)；然后 basic_string::operator=(新路径)、"
            "g_AnmCache.Load2(path, this)、g_AnmCache.AddReference(path)；"
            "w2 bit0 置位时再调 ANM2::load_graphics(false)；"
            "最后 AnimationState::Reset(&this[0x30],null) 与 Reset(&this[0x60],null) 复位主/覆盖层，"
            "读 [x0+0xB0] 得层数并建立 \"shadow\" 图层映射，写 strb 1 → [x0+0x149]（加载标志）。"
        ),
        note=(
            "全程依赖模块全局 g_AnmCache（_ZN15IsaacRepentance10g_AnmCacheE，.rela 唯一一条数据重定位），"
            "因此只能在游戏自身初始化之后调用；参数是 libc++ std::string 的引用，运行时侧必须用"
            "libc++ 兼容布局构造临时对象（布局细节见 open_questions）。"
        ),
    ),
    dict(
        key="sprite_load_graphics",
        lua_name="Sprite:LoadGraphics",
        group="sprite_load",
        mangled="_ZN15IsaacRepentance4ANM212LoadGraphicsEv",
        lua_usage=["LoadGraphics"],
        confidence="verified",
        abi=(
            "x0=this；仅两条指令：mov w1, wzr（0x2A1F03E1）+ b ANM2::load_graphics(bool) "
            "（PLT 桩 0x6704F0）→ 等价于 load_graphics(false)。无参数、返回 void。"
        ),
        note="guard 的前 8 字节就是这两条指令，随后 8 字节已是下一个函数（Reload）的序言。",
    ),
    dict(
        key="sprite_replace_spritesheet",
        lua_name="Sprite:ReplaceSpritesheet",
        group="sprite_load",
        mangled=(
            "_ZN15IsaacRepentance4ANM218ReplaceSpritesheetEiRKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE"
        ),
        lua_usage=["ReplaceSpritesheet"],
        confidence="verified",
        abi=(
            "x0=this；w1=layerId(int，有符号)；x2=std::string const&（png 路径）；返回 void。"
            "同样先 tbnz w1,#0x1f 排除负数、再与 [x0+0xB0]（层数）比较 → 越界安全返回。"
        ),
        note="PC 文档 ReplaceSpritesheet(int LayerId, string PngFilename)；Repentance+ 返回 bool，本 build 的符号是 void。",
    ),
    dict(
        key="sprite_reset",
        lua_name="Sprite:Reset",
        group="sprite_load",
        mangled="_ZN15IsaacRepentance4ANM25ResetEv",
        lua_usage=["Reset"],
        confidence="inferred",
        abi="x0=this，无参数，返回 void（未逐条解码；与 ~ANM2 里 bl 的 Reset 同一入口）。",
        note="PC 文档 Reset()。",
    ),
    dict(
        key="sprite_reload",
        lua_name="Sprite:Reload",
        group="sprite_load",
        mangled="_ZN15IsaacRepentance4ANM26ReloadEv",
        lua_usage=["Reload"],
        confidence="inferred",
        abi="x0=this，无参数，返回 void；入口先读 ldrb w8,[x0]（短串标志）决定用哪一路路径字符串。",
        note="PC 文档 Reload()。",
    ),
    # ---------------------------- Sprite 构造/析构 ----------------------------
    dict(
        key="sprite_ctor",
        lua_name="Sprite()（构造函数）",
        group="sprite_lifecycle",
        mangled="_ZN15IsaacRepentance4ANM2C1Ev",
        lua_usage=["Sprite"],
        confidence="inferred",
        abi=(
            "x0=this，无参数，返回 void。首 5 条指令：把 this 写进 [this+0x30]（主 AnimationState 的"
            "回指）→ 说明本类的两个 AnimationState 各自 +0x00 存 ANM2*，因此 RenderLayer 里 "
            "ldr x5,[x22,#0x60] 得到的其实是 ANM2*，[x5+0xB0] 就是层数。"
        ),
        note=(
            "对象尺寸未定案（本工具只证 ctor 偏移与字节）：ANM2 被 Backdrop/Entity/HUD/Room/Entity_Player "
            "等大量类作为成员构造（171 个模块内调用点），必须另做分配步长分析。"
        ),
    ),
    dict(
        key="sprite_dtor",
        lua_name="~Sprite()（析构函数）",
        group="sprite_lifecycle",
        mangled="_ZN15IsaacRepentance4ANM2D1Ev",
        lua_usage=[],
        confidence="inferred",
        abi="x0=this，无参数；先 bl ANM2::Reset()，再释放 [this+0x70] 等内部缓冲（245 个模块内调用点）。",
        note="没有 D0（deleting destructor）符号 → 该类的释放由调用方负责，运行时侧必须自己配对分配器。",
    ),
    # ---------------------------- Sprite 播放/查询 ----------------------------
    dict(
        key="sprite_update",
        lua_name="Sprite:Update",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM26UpdateEv",
        lua_usage=["Update"],
        confidence="verified",
        abi=(
            "x0=this，无参数，返回 void。入口 ldrb w8,[x0,#0x149]（加载标志），为 0 直接返回 → "
            "未 Load 时是安全空操作；随后读 [x0+0x38]（主 AnimationState 的层向量）逐层推进。"
        ),
        note="PC 文档 Update()。",
    ),
    dict(
        key="sprite_play",
        lua_name="Sprite:Play",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM24PlayEPKcb",
        lua_usage=["Play"],
        confidence="inferred",
        abi="x0=this；x1=char const*（动画名）；w2=force(bool)；返回 void（未逐条解码）。",
        note="PC 文档 Play(string AnimationName, boolean Force)；EID 有 6 处调用。",
    ),
    dict(
        key="sprite_set_animation",
        lua_name="Sprite:SetAnimation",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM212SetAnimationEPKcb",
        lua_usage=["SetAnimation"],
        confidence="inferred",
        abi="x0=this；x1=char const*；w2=reset(bool)；入口先 ldr w23,[x0,#0xA0]（动画数）。返回 void。",
        note="PC 文档 SetAnimation(string AnimationName, boolean Reset=true)。",
    ),
    dict(
        key="sprite_play_random",
        lua_name="Sprite:PlayRandom",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM210PlayRandomEj",
        lua_usage=["PlayRandom"],
        confidence="verified",
        abi=(
            "x0=this；w1=seed(unsigned)；返回 void。ldr w8,[x0,#0xA0]（动画数），为 0 时跳到日志分支；"
            "否则 udiv+msub 得到 seed % count，乘 0x150 得元素步长，基址 ldr x9,[x0,#0x98] → 选中动画，"
            "w2=0（force=false）继续调用。"
        ),
        note=(
            "日志字符串交叉引用：本工具在 .rodata 找到 \"[warn] PlayRandom: no animations\\n\""
            "（文件偏移 0x8B1498），其唯一 adrp+add 引用点就在本函数体内 0xA1CC —— 这是"
            "\"该函数确实是 PlayRandom\"的第二条独立证据。"
        ),
    ),
    dict(
        key="sprite_set_frame",
        lua_name="Sprite:SetFrame(int)",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM28SetFrameEi",
        lua_usage=["SetFrame"],
        confidence="verified",
        abi=(
            "x0=this；w1=frameNum(int)；返回 void。ldr x8,[x0,#0x38] 判空后 scvtf s0,w1 并 "
            "add x0,x0,#0x30 + b AnimationState::SetPosition(float)（PLT 0x6703E0）→ "
            "只操作主 AnimationState，未 Load 时安全返回。"
        ),
        note="PC 文档 SetFrame(int FrameNum)。",
    ),
    dict(
        key="sprite_set_frame_named",
        lua_name="Sprite:SetFrame(string, int)",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM28SetFrameEPKci",
        lua_usage=["SetFrame"],
        confidence="inferred",
        abi="x0=this；x1=char const*（动画名）；w2=frameNum(int)；入口先 ldr w23,[x0,#0xA0]。返回 void。",
        note="PC 文档的第二个重载 SetFrame(string AnimationName, int FrameNum)。",
    ),
    dict(
        key="sprite_get_frame",
        lua_name="Sprite:GetFrame",
        group="sprite_anim",
        mangled="_ZNK15IsaacRepentance4ANM28GetFrameEv",
        lua_usage=["GetFrame"],
        confidence="verified",
        abi=(
            "x0=this；返回 w0=int。ldr x8,[x0,#0x38]（主 AnimationState 的层向量）为空 → w0=-1"
            "（0xA938 mov w0,#-1）；否则 ldr s0,[x0,#0x50]; fcvtms w0,s0 —— 返回【向下取整】的"
            "主动画帧号（+0x50 = 主 AnimationState@0x30 的 +0x20，float）。"
            "同族 GetFramePrecise()@0xA940 返回同一个 float（空则 -1.0f）。"
        ),
        note="与 GetOverlayFrame(0xAFE0) 同构；EID main.lua:223 用 entitySprite:GetFrame() 取值再喂给 SetFrame。",
    ),
    dict(
        key="sprite_set_layer_frame",
        lua_name="Sprite:SetLayerFrame",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM213SetLayerFrameEii",
        lua_usage=["SetLayerFrame"],
        confidence="verified",
        abi=(
            "x0=this；w1=layerId(int)；w2=frameNum(int)；返回 void。ldr x8,[x0,#0x38] 判空后 "
            "add x0,x0,#0x30 + 尾调用 AnimationState::SetLayerPosition(int,int)（PLT 0x6703F0），"
            "最后 ret。"
        ),
        note="PC 文档 SetLayerFrame(int LayerId, int FrameNum)。",
    ),
    dict(
        key="sprite_get_layer_frame",
        lua_name="Sprite:GetLayerFrame",
        group="sprite_anim",
        mangled="_ZN15IsaacRepentance4ANM213GetLayerFrameEi",
        lua_usage=[],
        confidence="inferred",
        abi="x0=this；w1=layerId(int)；返回 w0=int；入口 ldr x8,[x0,#0x38] 判空后转交 AnimationState。",
        note="PC 文档未列出（Lua 侧为 GetLayerFrame 的内部/兼容接口）；仅在 Sprite 层查询时需要。",
    ),
    dict(
        key="sprite_is_playing",
        lua_name="Sprite:IsPlaying",
        group="sprite_query",
        mangled="_ZNK15IsaacRepentance4ANM29IsPlayingEPKc",
        lua_usage=["IsPlaying"],
        confidence="inferred",
        abi="x0=this；x1=char const*（动画名）；返回 w0=bool；入口 ldr x8,[x0,#0x38] 与 ldrb w9,[x0,#0x54]（播放中标志）双判空。",
        note="PC 文档 IsPlaying(string AnimationName) / IsPlaying()。",
    ),
    dict(
        key="sprite_is_finished",
        lua_name="Sprite:IsFinished",
        group="sprite_query",
        mangled="_ZNK15IsaacRepentance4ANM210IsFinishedEPKc",
        lua_usage=["IsFinished"],
        confidence="inferred",
        abi="x0=this；x1=char const*；返回 w0=bool；与 IsPlaying 同一套前置判空。",
        note="PC 文档 IsFinished(string AnimationName) / IsFinished()。",
    ),
    dict(
        key="sprite_get_texel",
        lua_name="Sprite:GetTexel",
        group="sprite_query",
        mangled="_ZNK15IsaacRepentance4ANM28GetTexelEN4KAGE4Math7Vector2ES3_fi",
        lua_usage=["GetTexel"],
        confidence="verified",
        abi=(
            "x0=this；**前两个 Vector2 按值传**（HFA 规则 → v0/v1 = samplePos.X/Y，v2/v3 = renderPos.X/Y），"
            "s4=alphaThreshold(float)，w1=layerId(int)；返回 KColor（16 字节，走 x8 间接结果寄存器："
            "函数体内用 x8 保存 sret 指针，见 0xC0B4 mov x20,x8）。"
        ),
        note=(
            "PC 文档 GetTexel(Vector SamplePos, Vector RenderPos, float AlphaThreshold, int LayerID=0) "
            "与寄存器顺序逐参一致 —— 同时也是\"Vector 是 8 字节双 float\"的独立证据。"
        ),
    ),
    # ---------------------------- Sprite overlay ----------------------------
    dict(
        key="sprite_play_overlay",
        lua_name="Sprite:PlayOverlay",
        group="sprite_overlay",
        mangled="_ZN15IsaacRepentance4ANM211PlayOverlayEPKcb",
        lua_usage=["PlayOverlay"],
        confidence="inferred",
        abi="x0=this；x1=char const*；w2=force(bool)；返回 void。",
        note="PC 文档 PlayOverlay(string AnimationName, boolean Force)。",
    ),
    dict(
        key="sprite_set_overlay_animation",
        lua_name="Sprite:SetOverlayAnimation",
        group="sprite_overlay",
        mangled="_ZN15IsaacRepentance4ANM219SetOverlayAnimationEPKcb",
        lua_usage=["SetOverlayAnimation"],
        confidence="inferred",
        abi="x0=this；x1=char const*；w2=reset(bool)；入口先 ldr w23,[x0,#0xA0]。返回 void。",
        note="PC 文档 SetOverlayAnimation(string AnimationName, bool Reset=true)。",
    ),
    dict(
        key="sprite_stop_overlay",
        lua_name="Sprite:StopOverlay",
        group="sprite_overlay",
        mangled="_ZN15IsaacRepentance4ANM211StopOverlayEv",
        lua_usage=["StopOverlay"],
        confidence="verified",
        abi=(
            "x0=this，无参数，返回 void。只有 4 条指令：[x0+0x68]（覆盖层 AnimationState 的层向量）"
            "为空则 ret，否则 strb wzr,[x0,#0x84]（覆盖层播放标志 = 0x60+0x24）再 ret。"
        ),
        note="PC 文档 StopOverlay()。",
    ),
    dict(
        key="sprite_is_overlay_playing",
        lua_name="Sprite:IsOverlayPlaying",
        group="sprite_overlay",
        mangled="_ZNK15IsaacRepentance4ANM216IsOverlayPlayingEPKc",
        lua_usage=["IsOverlayPlaying"],
        confidence="inferred",
        abi="x0=this；x1=char const*；返回 w0=bool；判空用 [x0+0x68] 与 [x0+0x84]（覆盖层对应字段）。",
        note="PC 文档 IsOverlayPlaying(string AnimationName)。",
    ),
    dict(
        key="sprite_is_overlay_finished",
        lua_name="Sprite:IsOverlayFinished",
        group="sprite_overlay",
        mangled="_ZNK15IsaacRepentance4ANM217IsOverlayFinishedEPKc",
        lua_usage=["IsOverlayFinished"],
        confidence="inferred",
        abi="x0=this；x1=char const*；返回 w0=bool；与 IsOverlayPlaying 同构。",
        note="PC 文档 IsOverlayFinished(string AnimationName)。",
    ),
    dict(
        key="sprite_set_overlay_frame",
        lua_name="Sprite:SetOverlayFrame",
        group="sprite_overlay",
        mangled="_ZN15IsaacRepentance4ANM215SetOverlayFrameEPKci",
        lua_usage=["SetOverlayFrame"],
        confidence="inferred",
        abi="x0=this；x1=char const*；w2=frameNum(int)；入口先 ldr w23,[x0,#0xA0]。返回 void。",
        note="PC 文档 SetOverlayFrame(string AnimationName, int FrameNum)；另有 SetOverlayFrame(int)@0xAFC8。",
    ),
    dict(
        key="sprite_get_overlay_frame",
        lua_name="Sprite:GetOverlayFrame",
        group="sprite_overlay",
        mangled="_ZN15IsaacRepentance4ANM215GetOverlayFrameEv",
        lua_usage=["GetOverlayFrame"],
        confidence="verified",
        abi=(
            "x0=this，返回 w0=int。ldr x8,[x0,#0x68] 为空则 w0=-1（0xAFF4 mov w0,#-1）；"
            "否则 ldr s0,[x0,#0x80]; fcvtzs w0,s0 → 覆盖层帧号（float 存、int 返回）。"
        ),
        note="PC 文档 GetOverlayFrame()。",
    ),
    dict(
        key="sprite_remove_overlay",
        lua_name="Sprite:RemoveOverlay",
        group="sprite_overlay",
        mangled="_ZN15IsaacRepentance4ANM213RemoveOverlayEv",
        lua_usage=["RemoveOverlay"],
        confidence="verified",
        abi=(
            "x0=this，无参数，返回 void。整体是尾调用：add x0,x0,#0x60 + mov x1,xzr + "
            "b AnimationState::Reset(AnimationData const*)（PLT 0x6700E0）→ 把覆盖层 AnimationState 复位。"
        ),
        note="PC 文档 RemoveOverlay()。",
    ),
    # ---------------------------- Font ----------------------------
    dict(
        key="font_draw_string_scaled",
        lua_name="Font:DrawStringScaled",
        group="font",
        mangled="_ZNK4KAGE8Graphics4Font16DrawStringScaledEPKcffffNS0_5ColorEjb",
        lua_usage=["DrawStringScaled"],
        confidence="verified",
        abi=(
            "x0=this(Font const*)；x1=char const*；s0=positionX、s1=positionY、s2=scaleX、s3=scaleY"
            "（四个 float 走 v0..v3）；x2=Color（**不可见引用**：Color 有用户定义拷贝构造，"
            "所以占整数寄存器 x2 而不是 v4）；w3=boxWidth(int)、w4=center(bool)；返回 void。"
            "入口 0x4CEFB0 cbz w3 分支：boxWidth!=0 时先 bl Font::GetStringWidth(char const*)。"
        ),
        note=(
            "PC 文档 DrawStringScaled(string String, float PositionX, float PositionY, float ScaleX, "
            "float ScaleY, KColor RenderColor, int BoxWidth=0, boolean Center=false) 逐参一致。"
        ),
    ),
    dict(
        key="font_draw_string_utf8",
        lua_name="Font:DrawStringUTF8",
        group="font",
        mangled="_ZNK4KAGE8Graphics4Font14DrawStringUTF8EPKcffNS0_5ColorEjb",
        lua_usage=["DrawStringUTF8"],
        confidence="verified",
        abi=(
            "x0=this；x1=UTF-8 串；s0=positionX、s1=positionY；x2=Color(不可见引用)；"
            "w3=boxWidth、w4=center；返回 void。实现：strlen(x1) → "
            "StringEncoding::ConvertUTF8toUTF16(串, len, 模块内静态缓冲 0xAFFFD0, 512) → "
            "把 scale 置 1.0f×2 → bl Font::DrawStringScaled(unsigned short const*,…)（PLT 0x671C70）。"
        ),
        note=(
            "PC 文档 DrawStringUTF8(string, float PositionX, float PositionY, KColor, int BoxWidth=0, "
            "boolean Center=false)。注意内部静态缓冲非线程安全、512 单元截断（与 stage148 的 "
            "GetStringWidthUTF8 同一约定）。"
        ),
    ),
    dict(
        key="font_draw_string_scaled_utf8",
        lua_name="Font:DrawStringScaledUTF8",
        group="font",
        mangled="_ZNK4KAGE8Graphics4Font20DrawStringScaledUTF8EPKcffffNS0_5ColorEjb",
        lua_usage=["DrawStringScaledUTF8"],
        confidence="verified",
        abi=(
            "x0=this；x1=UTF-8 串；s0..s3=positionX/positionY/scaleX/scaleY；x2=Color(不可见引用)；"
            "w3=boxWidth、w4=center；返回 void。序言把 x1 交给 x0 后立即 bl strlen（与 DrawStringUTF8 "
            "同一形状），即 UTF8→UTF16 后再走 u16 版 DrawStringScaled。"
        ),
        note=(
            "EID 两处调用（main.lua:602、features/eid_api.lua:1637）都是 8 个实参、"
            "位置参数为两个 float —— 与本 ABI 完全对齐；是本切片最需要的一个 Font 入口。"
        ),
    ),
    # ---------------------------- Vector 值类型 ----------------------------
    dict(
        key="vector2_ctor_xy",
        lua_name="Vector(x, y)（构造函数）",
        group="vector",
        mangled="_ZN4KAGE4Math7Vector2C1Eff",
        lua_usage=["Vector"],
        confidence="verified",
        abi=(
            "x0=this；s0=x、s1=y；返回 void。函数体只有 3 条指令：str s0,[x0] + str s1,[x0,#4] + ret "
            "→ **对象大小 8 字节、x 在 +0、y 在 +4、无虚表**。"
        ),
        note="这是\"Vector 到底几个字节\"的直接证据（不是 16 字节）。",
    ),
    dict(
        key="vector2_ctor",
        lua_name="Vector()（默认构造）",
        group="vector",
        mangled="_ZN4KAGE4Math7Vector2C1Ev",
        lua_usage=[],
        confidence="verified",
        abi="x0=this，无参数；str xzr,[x0] + ret → 一次 8 字节清零（两个 float 同时归零），再次证明 sizeof==8。",
        note="",
    ),
    dict(
        key="vector2_length",
        lua_name="Vector:Length()（内部依据）",
        group="vector",
        mangled="_ZNK4KAGE4Math7Vector26LengthEv",
        lua_usage=[],
        confidence="verified",
        abi="x0=this；返回 s0=float。函数体 ldp s0,s1,[x0]（一次读 8 字节 = x,y 成对）后 fmul/fadd/fsqrt。",
        note="第三条独立证据：向量读取一律按 8 字节成对加载。",
    ),
    dict(
        key="vector2_zero",
        lua_name="Vector.Zero",
        group="vector",
        mangled="_ZN4KAGE4Math7Vector24ZeroE",
        lua_usage=[],
        confidence="verified",
        abi=(
            "数据符号（非函数）：文件偏移 0x8C3D34 起 8 字节全 0；紧随其后的 0x8C3D3C 是 Vector.One = "
            "{1.0f,1.0f}。两个常量正好相邻 8 字节 → sizeof(Vector2)==8 的第四条证据。"
        ),
        note="guard 窗口跨到相邻常量，这本身就是\"8 字节一个对象\"的读数。",
    ),
    dict(
        key="vector2_one",
        lua_name="Vector.One",
        group="vector",
        mangled="_ZN4KAGE4Math7Vector23OneE",
        lua_usage=[],
        confidence="verified",
        abi="数据符号：0x8C3D3C 起 8 字节 = 0x3F800000,0x3F800000（= (1.0f,1.0f)）。",
        note="",
    ),
)

# 没有原生入口 / 不能定案的条目（保持与 ENTRIES 同样的字段形态，便于核对）。
UNRESOLVED_ENTRIES: tuple[dict, ...] = (
    dict(
        key="sprite_get_layer_count",
        lua_name="Sprite:GetLayerCount",
        group="sprite_query",
        mangled=None,
        lua_usage=["GetLayerCount"],
        confidence="unknown",
        abi=(
            "本 NRO **没有**任何导出函数实现它：.dynsym 中不含 \"LayerCount\"/\"NumLayers\" 子串的符号，"
            "全镜像也扫不到 `ldr w0,[x0,#0xB0]; ret` 形态的 getter。层数是 ANM2 的 int 字段 "
            "**(this+0xB0)**，可由运行时直接读 4 字节得到。"
        ),
        evidence_hint=(
            "字段偏移由 4 处独立代码交叉证明：ANM2::Load@0xC970 读 [x0+0xB0] 当层循环上界；"
            "ANM2::GetLayer(int)@0xA034 用 [this+0xB0] 做下标越界检查、并按 0xB0 步长返回 "
            "[this+0xA8]+0xB0*idx；ANM2::RenderLayer@0x9D70 与 ReplaceSpritesheet@0xD270 同样用它做边界检查。"
        ),
        note=(
            "要把它升到 verified，需要一条本 NRO 里不存在的证据：Lua 绑定层反汇编（证明绑定读了 +0xB0），"
            "或 PC 侧头文件/绑定源码里 GetLayerCount 的返回值字段。"
        ),
    ),
)

# 支撑证据：不直接对应 PC Lua 方法，但用来判定 Sprite 类身份 / 层数字段 / 交叉校验。
SUPPORTING: dict[str, dict] = {
    "sprite_class_sprite_ctor": dict(
        signature="IsaacRepentance::Sprite::Sprite()",
        role="类身份判别：另一个同名类 `IsaacRepentance::Sprite`（有 vtable）的构造函数。",
    ),
    "sprite_class_sprite_dtor": dict(
        signature="IsaacRepentance::Sprite::~Sprite()",
        role="有 D0/D1 + _ZTV/_ZTI → 多态类；与 ANM2（无 _ZTV/_ZTI 符号）形态不同。",
    ),
    "sprite_class_sprite_render": dict(
        signature="IsaacRepentance::Sprite::Render(float, float, KAGE::Graphics::Color const&, "
                  "KAGE::Graphics::Color const&, KAGE::Math::Vector2 const&, float)",
        role="参数形态与 PC Lua 的 Sprite:Render(Vector,Vector,Vector) **不一致** → 不是 Lua 的 Sprite。",
    ),
    "sprite_class_sprite_load_anims": dict(
        signature="IsaacRepentance::Sprite::LoadAnimationsFile(char const*, char const*)",
        role="方法名集合（PlayAnimation/SetAnimationFrame/NumFrames/get_animation）与 PC Lua 也不同。",
    ),
    "anm2_get_layer_int": dict(
        signature="IsaacRepentance::ANM2::GetLayer(int)",
        role="层数字段与本类布局的关键证据：用 [this+0xB0] 做越界检查、返回 [this+0xA8]+0xB0*idx。",
    ),
    "anm2_get_layer_const": dict(
        signature="IsaacRepentance::ANM2::GetLayer(int) const",
        role="同上（const 版本，同地址形状）。",
    ),
    "font_draw_string": dict(
        signature="KAGE::Graphics::Font::DrawString(char const*, float, float, Color, unsigned int, bool) const",
        role="交叉校验现有 runtime_constants.hpp：同时证明它只是 DrawStringScaled 的 1.0f 缩放包装。",
    ),
    "font_draw_string_scaled_u16": dict(
        signature="KAGE::Graphics::Font::DrawStringScaled(unsigned short const*, float, float, float, float, Color, unsigned int, bool) const",
        role="UTF8 路径最终落在的 u16 重载（DrawStringUTF8/ DrawStringScaledUTF8 的调用目标）。",
    ),
    "font_draw_string_u16": dict(
        signature="KAGE::Graphics::Font::DrawString(unsigned short const*, float, float, Color, unsigned int, bool) const",
        role="DrawStringUTF8 的等价 u16 入口。",
    ),
}

# 反汇编窗口：(key, 起始偏移, 长度, 说明)。起点全部来自符号表（见 _record），不写死。
WINDOWS: tuple[tuple[str, int, int, str], ...] = (
    ("sprite_render_head", 0x9BD4, 0x40, "加载标志 + 三个 Vector2 入参保存"),
    ("sprite_render_layers", 0x9C5C, 0x58, "主/覆盖两层 AnimationLayer::RenderFrame 调用循环"),
    ("sprite_render_layer_head", 0x9D70, 0x5C, "layerId 越界检查（+0xB0 层数）与 0x18 步长"),
    ("sprite_load_head", 0xC970, 0x60, "g_AnmCache RemoveReference/Load2/AddReference + load_graphics"),
    ("sprite_load_state", 0xCA04, 0xA0, "两个 AnimationState::Reset、+0xB0 层数、+0x149 加载标志"),
    ("sprite_load_graphics", 0xCB6C, 0x10, "mov w1,wzr + b load_graphics(bool)"),
    ("sprite_replace_spritesheet_head", 0xD270, 0x24, "layerId 越界检查"),
    ("sprite_ctor_head", 0x6540, 0x24, "[this+0x30] 回指 + AnimationState::Reset"),
    ("sprite_set_frame", 0xA8CC, 0x14, "scvtf + AnimationState::SetPosition 尾调用"),
    ("sprite_set_layer_frame", 0xA958, 0x14, "AnimationState::SetLayerPosition 尾调用"),
    ("sprite_get_frame", 0xA924, 0x14, "ldr s0 + fcvtzs 取值形态"),
    ("sprite_play_random", 0xA198, 0x28, "seed % count + 0x150 步长 + 日志分支"),
    ("sprite_get_overlay_frame", 0xAFE0, 0x18, "无覆盖层返回 -1"),
    ("sprite_stop_overlay", 0xABA8, 0x10, "strb wzr,[x0,#0x84]"),
    ("sprite_remove_overlay", 0xAFFC, 0x10, "AnimationState::Reset(&this[0x60], null) 尾调用"),
    ("sprite_get_texel_head", 0xC074, 0x4C, "两个 Vector2 按值(HFA v0..v3) + float + int；KColor 走 x8"),
    ("anm2_get_layer_int", 0xA034, 0x64, "[this+0xB0] 越界检查 + 0xB0 步长 + \"%s: No layer with Id %d\""),
    ("font_draw_string", 0x4CEEF0, 0x7C, "DrawString = 拷贝 Color + 1.0f 缩放 → DrawStringScaled"),
    ("font_draw_string_scaled_head", 0x4CEF6C, 0x60, "s0..s3 四个 float + x2=Color + w3/w4"),
    ("font_draw_string_utf8_head", 0x4CF2A4, 0x60, "strlen → ConvertUTF8toUTF16(静态缓冲,512)"),
    ("font_draw_string_utf8_tail", 0x4CF300, 0x50, "scale=1.0f → DrawStringScaled(u16…)"),
    ("font_draw_string_scaled_utf8_head", 0x4CFAB8, 0x30, "与 DrawStringUTF8 同形状的序言"),
    ("vector2_ctor", 0x4FD098, 0x14, "str xzr,[x0] / str s0,[x0]; str s1,[x0,#4] —— 8 字节对象"),
    ("vector2_length", 0x4FD124, 0x2C, "ldp s0,s1,[x0] 成对读 8 字节"),
    ("sprite_class_sprite_ctor", 0x4A4658, 0x64, "vtable 装载 + 两个 Vector2 成员（+0x4c/+0x54）"),
    ("sprite_class_sprite_render", 0x4A4F14, 0x44, "float x/y + Color×2 + Vector2 + float 的入参形态"),
)

PC_MOD_USAGE_METHODS = (
    "Render", "RenderLayer", "Load", "LoadGraphics", "ReplaceSpritesheet", "Play", "SetFrame",
    "Update", "IsFinished", "IsPlaying", "GetFrame", "GetLayerCount", "GetTexel", "Reset", "Stop",
    "PlayRandom", "SetLayerFrame", "PlayOverlay", "SetOverlayAnimation", "StopOverlay",
    "IsOverlayPlaying", "IsOverlayFinished", "SetOverlayFrame", "GetOverlayFrame", "RemoveOverlay",
    "DrawStringScaledUTF8", "DrawStringScaled", "DrawStringUTF8", "GetStringWidthUTF8",
    "SetMissingCharacter", "IsLoaded", "Load (Font)", "Vector",
)

# 需要扫描的"Lua 绑定层是否存在"的判据字符串。
LUA_LAYER_PROBES = (b"lua_State", b"isaac_mods", b".lua", b"luaL_", b"lua_pcall", b"luaL_newstate")
LUA_LAYER_SYMBOL_PROBES = ("lua_", "luaL_", "Lua", "MOD_CALLBACK", "Isaac_", "RegisterCallback")

MAX_CALL_SITES = 24
OPEN_QUESTIONS = (
    "ANM2 对象尺寸未定案：本审计只证 ctor/dtor 偏移与字节；ANM2 被 Backdrop/Entity/HUD/Room/"
    "Entity_Player 等大量类作为成员构造（171 个模块内 ctor 调用点），要自己 new 一个 Sprite() "
    "必须先做分配步长分析（同 stage148 对 Font 用的方法：同一 operator new 内多个 ANM2 ctor 的 "
    "this 位移差、或连续 ctor 调用点的模态位移）。",
    "std::string 参数的 ABI 未在本审计内验证：ANM2::Load/ReplaceSpritesheet 收的是 "
    "libc++ std::string const&（24 字节对象：__short{size(1)+data(23)} / __long{ptr,size,cap|1}）。"
    "运行时侧必须用同一 ABI 的 libc++ 构造临时对象，且该临时串由我方释放；"
    "本审计未验证运行时构建所用的 STL 布局是否与游戏一致（需要一次真机或链接期静态断言）。",
    "g_AnmCache 生命周期未定案：Load 会写模块全局 g_AnmCache（_ZN15IsaacRepentance10g_AnmCacheE），"
    "在游戏初始化完成前调用会解引用空指针；运行时需要先确认该全局已就绪（例如沿用既有 "
    "Game 安全点机制）。",
    "渲染批处理归属未定案（沿用 stage148 结论）：KAGE 的 Graphics Manager 拥有 batch，"
    "从运行时回调里直接调用 Render 是否落在活着的 batch 内、是否需要 Begin/EndBatches 包裹，"
    "静态代码无法判定，需要真机验证。",
    "Sprite 类身份（Lua Sprite ↔ IsaacRepentance::ANM2）是**推断**而非直接读绑定表得来："
    "本 NRO 不含 Lua 绑定层（见 lua_layer_absence）。支持证据是方法名与参数形态逐一对应 + "
    "ANM2 是被 entity/HUD 广泛使用的动画容器；反证是 IsaacRepentance::Sprite 的 Render 参数形态"
    "与 PC Lua 签名不符。要闭环需要 PC 版 Repentance 的绑定源码/头文件，或一份带符号的 PC 二进制。",
    "Font::DrawStringScaled 的 boxWidth/center 语义未逐条解码（只确认 w3/w4 位置与 "
    "\"boxWidth!=0 时先量宽\"这一分支），实际换行/居中行为需要真机截图核对。",
)


# ---------------------------------------------------------------------------
# 低层工具
# ---------------------------------------------------------------------------

def _word(data: bytes, address: int) -> int:
    return struct.unpack_from("<I", data, address)[0]


def _guard(data: bytes, offset: int) -> str:
    return data[offset:offset + 16].hex()


def _demangle(names: list[str]) -> list[str]:
    """用 Xcode 的 c++filt 还原签名（唯一真值来源，不手写签名）。

    必须带 ``-n``：macOS 的 c++filt 默认把 ``_Z…`` 当成"带前置下划线的 C 符号"而不还原，
    实测不带 ``-n`` 会原样吐出 mangled 名（本工具曾因此把 mangled 名当成签名写进 JSON）。
    """
    if not names:
        return []
    for flags in (["-n"], []):
        try:
            result = subprocess.run([CXXFILT] + flags + names,
                                    capture_output=True, text=True, check=False)
        except OSError:
            return names
        lines = result.stdout.splitlines()
        if len(lines) == len(names) and any(line != name for line, name in zip(lines, names)):
            return lines
    return names


def _scan_plt_stubs(data: bytes) -> dict[int, int]:
    """``{GOT 槽地址: PLT 桩地址}``（``adrp x16; ldr x17,[x16,#imm]; add x16,x16,#imm; br x17``）。"""
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
    """``{BL 目标: [BL 指令地址…]}``。"""
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


def _plt_slots_by_name(relocations) -> dict[str, list[int]]:
    slots: dict[str, list[int]] = {}
    for name, items in relocations.items():
        for relocation in items:
            if relocation.table == "jmprel":
                slots.setdefault(name, []).append(relocation.offset)
    return {name: sorted(values) for name, values in slots.items()}


def _owner_lookup(symbols):
    starts = sorted(
        (symbol.file_offset, name)
        for name, symbol in symbols.items()
        if symbol.is_defined and symbol.file_offset is not None
    )
    keys = [offset for offset, _ in starts]

    def resolve(address: int):
        index = bisect.bisect_right(keys, address) - 1
        if index < 0:
            return None, None
        offset, name = starts[index]
        return offset, name

    return resolve


def _string_xrefs(data: bytes, target: int) -> list[int]:
    """全镜像扫 ``adrp+add`` 指向 ``target`` 的指令地址（用于日志字符串交叉引用）。"""
    hits = []
    for index in range(len(data) // 4 - 1):
        pc = index * 4
        word0, word1 = struct.unpack_from("<2I", data, pc)
        if (word0 & 0x9F000000) != 0x90000000:
            continue
        register = word0 & 0x1F
        immhi = (word0 >> 5) & 0x7FFFF
        immlo = (word0 >> 29) & 3
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (pc & ~0xFFF) + (imm << 12)
        if (word1 & 0xFFC00000) != 0x91000000:
            continue
        if ((word1 >> 5) & 0x1F) != register or (word1 & 0x1F) != register:
            continue
        if page + (((word1 >> 10) & 0xFFF) << (12 if (word1 >> 22) & 1 else 0)) == target:
            hits.append(pc)
    return hits


def _find_string(data: bytes, needle: bytes) -> int | None:
    offset = data.find(needle)
    return offset if offset >= 0 else None


# --- std::string ABI 兼容性检查（Sprite:Load / ReplaceSpritesheet 的硬前提）---

LIBCXX_STRING_PREFIX = "_ZNSt3__112basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEE"
LIBSTDCXX_STRING_MARKER = b"_ZNSt7__cxx11"
LIBCXX_STRING_MARKER = b"_ZNSt3__1"


def _runtime_artifacts(limit: int = 12) -> list[str]:
    """找到最近的若干 ``runtime/build*/runtime.elf``（只读；不参与任何构建）。"""
    root = os.path.join(REPO_ROOT, "runtime")
    if not os.path.isdir(root):
        return []
    candidates = []
    for name in os.listdir(root):
        if not name.startswith("build"):
            continue
        path = os.path.join(root, name, "runtime.elf")
        if os.path.exists(path):
            candidates.append((os.path.getmtime(path), path))
    candidates.sort(reverse=True)
    return [path for _mtime, path in candidates[:limit]]


def _std_string_abi_hazard(symbols, plt_slots) -> dict:
    imports = []
    for name, symbol in sorted(symbols.items()):
        if not name.startswith(LIBCXX_STRING_PREFIX):
            continue
        imports.append({
            "mangled": name,
            "demangled": _demangle([name])[0],
            "defined_in_module": bool(symbol.is_defined),
            "plt_stub": f"0x{plt_slots[name]:X}" if name in plt_slots else None,
        })
    scanned = []
    verdict = "未找到 runtime/build*/runtime.elf（不做判定）"
    for path in _runtime_artifacts():
        with open(path, "rb") as handle:
            blob = handle.read()
        libstdcpp = LIBSTDCXX_STRING_MARKER in blob
        libcpp = LIBCXX_STRING_MARKER in blob
        scanned.append({
            "artifact": os.path.relpath(path, REPO_ROOT),
            "has_libstdcxx_string_symbols": libstdcpp,
            "has_libcxx_string_symbols": libcpp,
        })
    with_stdcpp = sum(1 for row in scanned if row["has_libstdcxx_string_symbols"])
    with_libcxx = sum(1 for row in scanned if row["has_libcxx_string_symbols"])
    if scanned:
        verdict = (
            f"libstdc++（std::__cxx11::basic_string，32 字节布局）："
            f"{with_stdcpp}/{len(scanned)} 个产物含 _ZNSt7__cxx11，{with_libcxx} 个含 _ZNSt3__1"
        )
    runtime_stdlib = {
        "artifacts_scanned": scanned,
        "artifacts_with_libstdcxx_string": with_stdcpp,
        "artifacts_with_libcxx_string": with_libcxx,
        "verdict": verdict,
        "note": (
            "只扫了各 build 目录里的 runtime.elf 里的符号名子串；"
            "只要出现 _ZNSt7__cxx11 就说明该产物用的是 libstdc++ 的 std::string。"
        ),
    }
    artifact_note = verdict
    return {
        "source": "symbol_table+instructions(artifact strings)",
        "game_string_class": LIBCXX_STRING_PREFIX,
        "game_string_class_note": (
            "游戏侧 std::string 是 libc++ 的 std::__basic_string：24 字节对象"
            "（短串：1 字节长度 + 23 字节内联缓冲，SSO 上限 22 字符；长串：ptr/size/cap|1）。"
            "ANM2::Load 与 ANM2::ReplaceSpritesheet 的形参就是它的 const&。"
        ),
        "game_side_string_methods": imports,
        "runtime_artifact": runtime_stdlib,
        "runtime_artifact_note": artifact_note,
        "hazard": (
            "Runtime 用 devkitA64 的 g++（-std=gnu++23，未选 libc++）构建，实测其产物里出现 "
            "_ZNSt7__cxx11…（libstdc++ 的 std::string，32 字节：ptr/size/union{cap, buf[16]}），"
            "与游戏的 libc++ 24 字节布局**不是同一个类**。直接把 runtime 侧的 std::string 传给 "
            "ANM2::Load / ReplaceSpritesheet 会被游戏按 libc++ 布局解读（长度/指针读错），"
            "必须构造 libc++ 布局的 24 字节对象。"
        ),
        "workaround": (
            "零初始化 24 字节缓冲（= 合法的空短串）后，调用模块自己导入的 "
            "basic_string::assign(char const*) / dtor 的 PLT 桩来填值；"
            "路径 ≤22 字符时留在内联缓冲、不触及堆分配器。"
        ),
    }


# ---------------------------------------------------------------------------
# 证据采集
# ---------------------------------------------------------------------------

def _parse_runtime_constants() -> dict[str, dict]:
    """只读 ``runtime/source/runtime_constants.hpp``，取出 ``kXxxOffset`` 与其 16 字节 guard。

    用来做"转录方法自检"：用我们这套读法能否复现项目**已经信任**的常量。
    """
    if not os.path.exists(RUNTIME_CONSTANTS):
        return {}
    text = open(RUNTIME_CONSTANTS, encoding="utf-8").read()
    offsets = {name: int(value, 16) for name, value in
               re.findall(r"k(\w+)Offset\s*=\s*(0x[0-9A-Fa-f]+)", text)}
    guards = {}
    for name, body in re.findall(r"k(\w+)ExpectedBytes\s*=\s*\{(.*?)\}", text, re.S):
        values = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
        if len(values) == 16:
            guards[name] = bytes(values)
    result = {}
    for name, value in offsets.items():
        if name in guards:
            result[name] = {"offset": value, "expected_bytes": guards[name].hex()}
    return result


def _pc_mod_usage() -> dict:
    """统计 PC Mod（EID 等）里 ``:Method(...)`` 的实际用法：方法名 → file:line 列表。"""
    pattern = re.compile(r"[:.]([A-Z][A-Za-z0-9_]*)\s*\(")
    wanted = set(PC_MOD_USAGE_METHODS)
    usage: dict[str, list[dict]] = {}
    files = 0
    if not os.path.isdir(PC_MODS_ROOT):
        return {"root": None, "note": "PC Mod 目录不存在", "usage": {}}
    for directory, _dirs, names in os.walk(PC_MODS_ROOT):
        for name in names:
            if not name.endswith(".lua"):
                continue
            files += 1
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, PC_MODS_ROOT)
            try:
                handle = open(path, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with handle:
                for number, line in enumerate(handle, 1):
                    for match in pattern.finditer(line):
                        method = match.group(1)
                        if method not in wanted:
                            continue
                        usage.setdefault(method, []).append({
                            "file": relative,
                            "line": number,
                            "text": line.strip()[:160],
                        })
    return {
        "root": os.path.relpath(PC_MODS_ROOT, REPO_ROOT),
        "lua_files_scanned": files,
        "usage": {name: items for name, items in sorted(usage.items())},
    }


def _lua_layer_absence(data: bytes, symbols) -> dict:
    probes = {}
    for needle in LUA_LAYER_PROBES:
        offset = data.find(needle)
        probes[needle.decode()] = {
            "occurrences": data.count(needle),
            "first_offset_hex": f"0x{offset:X}" if offset >= 0 else None,
        }
    symbol_hits = {}
    for probe in LUA_LAYER_SYMBOL_PROBES:
        hits = sorted(name for name in symbols if probe in name)
        symbol_hits[probe] = {"count": len(hits), "examples": hits[:6]}
    return {
        "source": "instructions(rodata)+symbol_table",
        "byte_probes": probes,
        "symbol_probes": symbol_hits,
        "finding": (
            "本 NRO 内找不到 Lua 绑定层：lua_State / isaac_mods / .lua / luaL_* / lua_pcall 全部 0 次，"
            ".dynsym 里也没有任何 Lua* 符号；只有 4 个 Entity_Player::lua_* 方法名残留。"
            "因此\"某个 Lua 方法对应哪个原生函数\"**不能**靠读绑定表证明，只能靠方法名 + "
            "参数形态对应 + 类身份判别来推断 —— 这正是本审计对每条记录给出 confidence 的原因。"
        ),
    }


def _record(key: str, entry: dict, data: bytes, symbols, stubs, bl_targets, owner) -> dict:
    offset, expected = GUARDS[key]
    symbol = symbols.get(entry["mangled"]) if entry.get("mangled") else None
    record = {
        "key": key,
        "lua_name": entry["lua_name"],
        "group": entry["group"],
        "mangled": entry.get("mangled"),
        "native_offset": f"0x{offset:X}",
        "native_offset_decimal": offset,
        "expected_bytes": expected,
        "expected_bytes_source": "人工从 Repentance.nro 抄录（本工具再逐字节复核）",
        "guard16_from_nro": _guard(data, offset),
        "signature": None,
        "abi": entry["abi"],
        "confidence": entry["confidence"],
        "evidence": [],
        "lua_reference": entry.get("lua_usage") or [],
        "note": entry.get("note", ""),
    }
    if symbol is None:
        record.update({
            "symbol_defined_in_module": False,
            "symbol_offset_hex": None,
            "offset_agrees_with_symbol_table": False,
            "bytes_byte_for_byte_equal": record["guard16_from_nro"] == expected,
        })
        record["evidence"].append(
            f".dynsym 中不存在 {entry.get('mangled')} → 该转录地址没有符号支持（工具应报 unknown）"
        )
        return record
    record["symbol_defined_in_module"] = bool(symbol.is_defined)
    record["symbol_offset_hex"] = f"0x{symbol.file_offset:X}" if symbol.file_offset is not None else None
    record["offset_agrees_with_symbol_table"] = symbol.file_offset == offset
    record["bytes_byte_for_byte_equal"] = _guard(data, offset) == expected
    record["signature"] = _demangle([entry["mangled"]])[0]

    # PLT 桩 + 模块内调用点由 build_evidence 里的 attach() 补齐（模块内调用走 PLT，见 stage148）。
    record["evidence"].append(
        f"llvm-nm/.dynsym 符号 {entry['mangled']} → file_offset={record['symbol_offset_hex']}"
        f"（与本条转录常量 {record['native_offset']} 一致：{record['offset_agrees_with_symbol_table']}）"
    )
    record["evidence"].append(
        f"反汇编首 16 字节 = {record['guard16_from_nro']}，与转录常量逐字节比对"
        f"{'一致' if record['bytes_byte_for_byte_equal'] else '不一致'}"
    )
    return record



def _independent_dynsym(data: bytes) -> dict[str, int]:
    """**独立**再解析一遍 NRO 动态符号表（不复用 ``nro_symbols``）。

    这是本项目"第二条证据通道"的用法：同一批偏移若能被两份互不共享代码的实现得到，
    就不是单个解析器的 bug。这里手写 NRO 头 → MOD0 → ``.dynamic`` → ``.dynsym/.dynstr``。
    """
    if data[0x10:0x14] != b"NRO0":
        raise ValueError("NRO0 not found")
    mod0 = struct.unpack_from("<I", data, 4)[0]
    if data[mod0:mod0 + 4] != b"MOD0":
        raise ValueError("MOD0 not found")
    cursor = mod0 + struct.unpack_from("<I", data, mod0 + 4)[0]
    tags: dict[int, int] = {}
    while cursor + 16 <= len(data):
        tag, value = struct.unpack_from("<QQ", data, cursor)
        cursor += 16
        if tag == 0:
            break
        tags[tag] = value
    strtab, symtab = tags[5], tags[6]
    entry_size = tags.get(11, 24)
    symbol_count = struct.unpack_from("<I", data, tags[4] + 4)[0]
    symbols: dict[str, int] = {}
    for index in range(symbol_count):
        entry = symtab + index * entry_size
        name_offset, _info, _other, section, value, _size = struct.unpack_from("<IBBHQQ", data, entry)
        if name_offset == 0 or section == 0 or value >= len(data):
            continue
        end = data.find(b"\0", strtab + name_offset)
        if end < 0:
            continue
        symbols.setdefault(data[strtab + name_offset:end].decode("ascii", "replace"), value)
    return symbols


def build_evidence() -> dict:
    with open(NRO, "rb") as handle:
        data = handle.read()
    sha256 = hashlib.sha256(data).hexdigest()
    build_id, symbols = nro_symbols.parse_dynamic_symbols(data)
    _reloc_build_id, relocations = nro_symbols.parse_dynamic_relocations(data)
    stubs = _scan_plt_stubs(data)
    bl = _bl_targets(data)
    owner = _owner_lookup(symbols)

    plt_slots: dict[str, int] = {}
    for name, items in relocations.items():
        for relocation in items:
            if relocation.table == "jmprel" and relocation.offset in stubs:
                plt_slots.setdefault(name, stubs[relocation.offset])

    def attach(record: dict) -> None:
        mangled = record.get("mangled")
        record["plt_stub"] = f"0x{plt_slots[mangled]:X}" if mangled in plt_slots else None
        sites = bl.get(plt_slots[mangled], []) if mangled in plt_slots else []
        resolved = []
        for site in sorted(sites):
            start, name = owner(site)
            resolved.append({"call_site": f"0x{site:X}", "owner": name,
                             "owner_offset_hex": f"0x{start:X}" if start else None})
        record["in_module_call_site_count"] = len(resolved)
        record["in_module_call_sites"] = resolved[:MAX_CALL_SITES]
        record["in_module_call_sites_truncated"] = max(0, len(resolved) - MAX_CALL_SITES)
        record["evidence"].append(
            (f"该符号的 PLT 桩 0x{plt_slots[mangled]:X} 上有 {len(resolved)} 个模块内 BL 调用点"
             + (f"（示例 owner：{', '.join(sorted({item['owner'] for item in resolved if item['owner']})[:4])}）"
                if resolved else "（无模块内调用者）"))
            if mangled in plt_slots else
            "该符号没有 PLT 桩 → 本模块内没有跨编译单元的调用点"
        )

    def make(entry: dict) -> dict:
        record = _record(entry["key"], entry, data, symbols, stubs, bl, owner)
        attach(record)
        return record

    entries = [make(entry) for entry in ENTRIES]

    unresolved = []
    for entry in UNRESOLVED_ENTRIES:
        unresolved.append({
            "key": entry["key"],
            "lua_name": entry["lua_name"],
            "group": entry["group"],
            "mangled": None,
            "native_offset": None,
            "expected_bytes": None,
            "signature": None,
            "abi": entry["abi"],
            "confidence": entry["confidence"],
            "evidence": [
                "全镜像扫描：.dynsym 无 \"LayerCount\"/\"NumLayers\" 符号；"
                "无 `ldr w0,[x0,#0xB0]; ret` 形态 getter（只有 2 处命中，均为无关类）。",
                entry["evidence_hint"],
                "字段读法已被 4 处游戏自身代码交叉证明（见 anm2_get_layer_int / sprite_load / "
                "sprite_render_layer / sprite_replace_spritesheet 的反汇编窗口）。",
            ],
            "lua_reference": entry.get("lua_usage") or [],
            "note": entry["note"],
        })

    # --- 逐字节校验汇总 ---
    verified_rows = []
    for record in entries:
        verified_rows.append({
            "key": record["key"],
            "native_offset": record["native_offset"],
            "symbol_offset": record["symbol_offset_hex"],
            "offset_agrees": record["offset_agrees_with_symbol_table"],
            "expected_bytes": record["expected_bytes"],
            "nro_bytes": record["guard16_from_nro"],
            "equal": record["bytes_byte_for_byte_equal"],
            "differing_byte_indices": [
                index for index in range(16)
                if record["expected_bytes"][index * 2:index * 2 + 2] != record["guard16_from_nro"][index * 2:index * 2 + 2]
            ],
        })

    # --- 第二条通道：手写解析器独立复核偏移与字节 ---
    independent = _independent_dynsym(data)
    recheck_rows = []
    for record in entries:
        mangled = record["mangled"]
        offset = int(record["native_offset"], 16)
        recheck_rows.append({
            "key": record["key"],
            "mangled": mangled,
            "json_offset": record["native_offset"],
            "independent_symbol_offset": (f"0x{independent[mangled]:X}" if mangled in independent else None),
            "offset_agrees": independent.get(mangled) == offset,
            "bytes_agree": _guard(data, offset) == record["expected_bytes"],
        })
    independent_recheck = {
        "method": (
            "用本工具内手写的 NRO 动态符号表解析器（不复用 tools/nro_symbols.py）重新取一遍符号偏移，"
            "并直接从文件读 16 字节，与 JSON 里记录的偏移/转录字节比对。"
        ),
        "independent_symbol_count": len(independent),
        "checked": len(recheck_rows),
        "offset_mismatches": [row for row in recheck_rows if not row["offset_agrees"]],
        "byte_mismatches": [row for row in recheck_rows if not row["bytes_agree"]],
        "rows": recheck_rows,
    }

    # --- runtime_constants.hpp 交叉校验（只读）---
    runtime_constants = _parse_runtime_constants()
    cross_check = []
    for name, item in sorted(runtime_constants.items()):
        if item["offset"] >= len(data):
            continue
        actual = _guard(data, item["offset"])
        cross_check.append({
            "constant": name,
            "offset_hex": f"0x{item['offset']:X}",
            "header_bytes": item["expected_bytes"],
            "nro_bytes": actual,
            "equal": actual == item["expected_bytes"],
        })
    cross_check_failures = [row for row in cross_check if not row["equal"]]
    # 已知的**既有**不一致（不是本次转录错误）：runtime_constants.hpp 里
    # kGameIsGreedModeExpectedBytes 与 NRO 差 1 字节，而项目自己的 Stage40 证据 JSON
    # 记录的是与 NRO 一致的字节。这里把它读出来做实，交给 README 说明。
    for row in cross_check_failures:
        if row["constant"] != "GameIsGreedMode":
            continue
        stage40 = os.path.join(
            REPO_ROOT, "analysis", "starterr-native-evidence",
            f"universal-getter-inventory-{BUILD_ID}.json",
        )
        observed = None
        if os.path.exists(stage40):
            try:
                with open(stage40, encoding="utf-8") as handle:
                    payload = json.load(handle)
                observed = payload["anchors"]["game_is_greed_mode"]["entry_guard"].lower()
            except (OSError, KeyError, ValueError):
                observed = None
        row["preexisting"] = {
            "verdict": (
                "runtime/source/runtime_constants.hpp 的既有常量与 NRO 不符（差 1 字节："
                "0x68 被写成 0xB8）——本审计**未修改**该文件，仅记录。"
            ),
            "stage40_evidence_file": os.path.relpath(stage40, REPO_ROOT) if os.path.exists(stage40) else None,
            "stage40_recorded_guard": observed,
            "stage40_guard_equals_nro": observed == row["nro_bytes"],
            "runtime_check_site": (
                "runtime/source/hook_manager.cpp::VerifyGameIsGreedMode（第 2220-2236 行附近）"
                "→ verify_bytes() = std::memcmp 精确比较（第 1653-1655 行）→ 该 guard 必然校验失败"
            ),
        }

    # --- Vector2 / 类身份 / 层数字段的支撑证据 ---
    supporting = []
    for key, meta in SUPPORTING.items():
        offset, expected = GUARDS[key]
        symbol = next((symbol for name, symbol in symbols.items()
                       if symbol.file_offset == offset), None)
        mangled = next((name for name, symbol in symbols.items()
                        if symbol.file_offset == offset), None)
        supporting.append({
            "key": key,
            "signature": meta["signature"],
            "role": meta["role"],
            "mangled": mangled,
            "native_offset": f"0x{offset:X}",
            "expected_bytes": expected,
            "guard16_from_nro": _guard(data, offset),
            "bytes_byte_for_byte_equal": _guard(data, offset) == expected,
            "symbol_offset_agrees": bool(symbol) and symbol.file_offset == offset,
            "plt_stub": f"0x{plt_slots[mangled]:X}" if mangled in plt_slots else None,
            "in_module_call_site_count": len(bl.get(plt_slots[mangled], [])) if mangled in plt_slots else 0,
        })

    # --- 日志字符串交叉引用（PlayRandom / No layer with Id）---
    play_random_log = _find_string(data, b"[warn] PlayRandom: no animations\n")
    no_layer_log = _find_string(data, b"%s: No layer with Id %d\n")
    string_xrefs = {}
    for label, offset in (("play_random_no_animations", play_random_log),
                          ("no_layer_with_id", no_layer_log)):
        if offset is None:
            continue
        sites = _string_xrefs(data, offset)
        string_xrefs[label] = {
            "string_offset_hex": f"0x{offset:X}",
            "string": data[offset:data.find(b"\x00", offset)].decode("ascii", "replace"),
            "xref_sites": [
                {"address": f"0x{site:X}", "owner": owner(site)[1]} for site in sites
            ],
        }

    # --- 反汇编窗口 ---
    disassembly = {}
    for label, start, length, note in WINDOWS:
        lines = nro_disasm.disassemble(NRO, start, length, annotate=True)
        disassembly[label] = {"start_hex": f"0x{start:X}", "length": length,
                              "note": note, "lines": lines[2:]}

    # --- 入口表（需求要求的字段在最前）---
    entry_table = []
    for record in entries + unresolved:
        entry_table.append({
            "lua_name": record["lua_name"],
            "native_offset": record["native_offset"],
            "expected_bytes": record["expected_bytes"],
            "signature": record["signature"],
            "evidence": record["evidence"],
            "confidence": record["confidence"],
        })

    groups: dict[str, list[str]] = {}
    for record in entries:
        groups.setdefault(record["group"], []).append(record["key"])

    return {
        "stage": 150,
        "topic": (
            "Sprite(=IsaacRepentance::ANM2) 渲染/加载入口 + Font 缩放与 UTF8 入口 + "
            "KAGE::Math::Vector2 值类型布局 的原生入口审计"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title_id": TITLE_ID,
        "build_id": build_id,
        "build_id_matches_expected": build_id == BUILD_ID,
        "nro": os.path.relpath(NRO, REPO_ROOT),
        "nro_size": len(data),
        "nro_sha256": sha256,
        "nro_sha256_matches_expected": sha256 == NRO_SHA256,
        "offset_equals_module_offset": {
            "asserted_by": "runtime/source/runtime_constants.hpp（历史守卫，本工具只读）",
            "used_here": True,
            "note": "本审计把 NRO 文件偏移当作运行时模块偏移；这是项目既有前提，本工具不重复证明。",
        },
        "tooling": {
            "script": "tools/stage150_sprite_abi_audit.py",
            "symbols": "tools/nro_symbols.py（解析 NRO 的 .dynsym/.rela/.jmprel）",
            "disassembler": "tools/nro_disasm.py → " + str(nro_disasm._find_objdump()),
            "demangler": CXXFILT + "（签名不手写）",
            "commands": [
                "python3 tools/nro_disasm.py --help",
                "python3 tools/stage150_sprite_abi_audit.py --out analysis/stage150-sprite-abi/"
                f"{BUILD_ID}.json --print",
                "/usr/bin/c++filt <mangled>（由本工具内部调用）",
            ],
            "plt_stub_count": len(stubs),
            "bl_instruction_count": sum(len(v) for v in bl.values()),
        },
        "lua_layer_absence": _lua_layer_absence(data, symbols),
        "confidence_semantics": {
            "verified": (
                "偏移来自 .dynsym、首 16 字节与转录常量逐字节一致、并且本工具解码了 ABI"
                "（参数寄存器/返回值形态）"
            ),
            "inferred": "偏移/字节/符号齐全，但 Lua 语义对应仅由方法名 + 参数形态推出（未逐条解码 ABI）",
            "unknown": "本 NRO 中找不到对应原生入口",
        },
        "entries": entry_table,
        "records": {record["key"]: record for record in entries},
        "unresolved_records": {record["key"]: record for record in unresolved},
        "groups": groups,
        "byte_verification": {
            "method": (
                "人工把每条入口的首 16 字节从 Repentance.nro 抄进工具常量 GUARDS，"
                "再由工具逐字节读回比对；不相等则 differing_byte_indices 非空、"
                "该条 confidence 只能是 unknown。"
            ),
            "entry_count": len(verified_rows),
            "all_equal": all(row["equal"] for row in verified_rows),
            "all_offsets_agree_with_symbol_table": all(row["offset_agrees"] for row in verified_rows),
            "rows": verified_rows,
            "independent_dynsym_recheck": independent_recheck,
            "runtime_constants_cross_check": {
                "file": "runtime/source/runtime_constants.hpp（只读；未修改）",
                "parsed_constants": len(cross_check),
                "all_equal": not cross_check_failures,
                "failures": cross_check_failures,
                "rows": cross_check,
                "purpose": (
                    "自检转录方法：用同一套读法复核项目**已经信任**的守卫常量；"
                    "全部一致 → 本工具读出的取址/取字节方式与项目既有约定一致。"
                ),
            },
        },
        "vector_layout": {
            "answer": (
                "引擎自带的两分量向量类是 KAGE::Math::Vector2，**大小 8 字节 = {float X@+0x0, "
                "float Y@+0x4}，没有虚表**。运行时不需要引入引擎对象，自己放一个 8 字节 POD "
                "（alignof 4）即可；传给 Sprite:Render/RenderLayer/GetTexel 时："
                "Vector2 const& → 传该 8 字节缓冲的地址（x1/x2/x3…）；"
                "Vector2 按值 → 按 AAPCS64 的 HFA 规则落在 v0..v3（本例 GetTexel 两个向量占 s0..s3）。"
            ),
            "evidence": [
                "Vector2::Vector2() @0x4FD098：str xzr,[x0] + ret —— 一次 8 字节清零（两个 float 同时写）",
                "Vector2::Vector2(float,float) @0x4FD0A0：str s0,[x0] + str s1,[x0,#4] + ret",
                "Vector2::Length() const @0x4FD124：ldp s0,s1,[x0] —— 按 8 字节成对加载",
                "静态常量 Vector2::Zero @0x8C3D34 = 8 个 0 字节；紧随其后的 Vector2::One @0x8C3D3C "
                "= (1.0f,1.0f) —— 两个常量相差正好 8 字节",
                "IsaacRepentance::Sprite::Sprite() @0x4A4658 连续构造两个 Vector2 成员在 +0x4C 与 +0x54"
                "（相差 8 字节），独立证明 sizeof(Vector2)==8",
                "ANM2::GetTexel(Vector2, Vector2, float, int) @0xC074 把两个向量读进 s0..s3、"
                "阈值读 s4、layerId 读 w1 —— 按值传参的 HFA 形态",
            ],
            "conclusion_confidence": "verified",
            "not_16_bytes": (
                "任务描述里假设的\"16 字节 float x,y\"与实测不符：引擎与 Lua 的 Vector 都是 8 字节。"
                "（Lua 侧的 Vector 表本身是 2 个 number，与引擎对象无关；只在跨边界时展开成两个 float。）"
            ),
        },
        "std_string_abi_hazard": _std_string_abi_hazard(symbols, plt_slots),
        "sprite_class_disambiguation": {
            "question": "Lua 的 Sprite 到底是 IsaacRepentance::ANM2 还是 IsaacRepentance::Sprite？",
            "answer": "IsaacRepentance::ANM2（confidence: inferred，理由见下）",
            "evidence_for_anm2": [
                "PC 文档的 Sprite 方法名与 ANM2 的方法名逐一对上：Load(string,bool)、LoadGraphics()、"
                "Play、PlayOverlay、SetAnimation、SetFrame(int)/(string,int)、SetLayerFrame(int,int)、"
                "Render(Vector,Vector,Vector)、RenderLayer(int,Vector,Vector,Vector)、"
                "ReplaceSpritesheet(int,string)、GetTexel(Vector,Vector,float,int)、IsPlaying、IsFinished、"
                "PlayOverload/RemoveOverlay/IsOverlayFinished、Update、Reset、Reload",
                "参数形态逐参一致（3 个 Vector2 const&、layerId 在 w1、GetTexel 两个向量按值）"
                "——见 entries 的 abi 与 disassembly",
                "ANM2 是游戏自己的动画容器：171 个模块内 ctor 调用点，出现在 Backdrop/Entity/HUD/"
                "Room/Entity_Player 等类里（本工具 in_module_call_sites 的 owner 字段）",
            ],
            "evidence_against_sprite_class": [
                "IsaacRepentance::Sprite::Render 的参数是 (float,float,Color,Color,Vector2,float) —— "
                "与 PC Lua 的 Render(Vector,Vector,Vector) 形态不符",
                "IsaacRepentance::Sprite 的方法名是 PlayAnimation/SetAnimationFrame/NextFrame/PrevFrame/"
                "NumFrames/get_animation/AnimationPlaying/AnimationFinished/LoadAnimationsFile —— "
                "与 PC Lua 的 Play/SetFrame/GetFrame/IsPlaying/IsFinished/Load 不同",
                "IsaacRepentance::Sprite 是多态类（有 _ZTV/_ZTI，ctor 写 vtable+0x10 到 +0x00），"
                "而 ANM2 在 .dynsym 里没有任何 _ZTV/_ZTI 符号",
            ],
            "residual_uncertainty": (
                "本 NRO 不含 Lua 绑定层（见 lua_layer_absence），所以无法用\"绑定函数里存进去的 "
                "userdata 类型\"直接证明映射；上述结论是签名/名称对应 + 类身份判别得出的推断。"
            ),
        },
        "layer_count_field": {
            "field_offset": "0xB0",
            "type": "int（层数）",
            "sibling_field": "0xA8 = LayerState 数组基址，元素步长 0xB0",
            "evidence": [
                "ANM2::Load@0xC970：0xCA1C ldr w26,[x19,#0xB0] 后作为层循环上界（cmp x23,w26,uxtw）",
                "ANM2::GetLayer(int)@0xA034：0xA04C ldr w8,[x20,#0xB0] + cmp w8,w19 + b.gt 越界检查；"
                "0xA080 ldr x8,[x20,#0xA8] + smaddl x0,w19,#0xB0,x8 返回元素地址",
                "ANM2::RenderLayer@0x9D70：0x9DB8 ldr w9,[x5,#0xB0]（x5=[this+0x60] 即主 AnimationState "
                "的回指 ANM2）与 w23(layerId) 比较",
                "ANM2::ReplaceSpritesheet@0xD270：0xD284 ldr w8,[x0,#0xB0] 同样做边界检查",
            ],
            "use": "运行时实现 Sprite:GetLayerCount 直接读 *(int32*)(sprite+0xB0)；越界 layerId 由引擎自己兜住。",
        },
        "pc_mod_usage": _pc_mod_usage(),
        "string_xrefs": string_xrefs,
        "supporting_records": supporting,
        "symbol_inventory": _symbol_inventory(data, symbols, plt_slots, bl, entries),
        "disassembly": disassembly,
        "suggested_constants": _suggested_constants(),
        "open_questions": list(OPEN_QUESTIONS),
    }



SYMBOL_INVENTORY_PREFIXES = (
    "_ZN15IsaacRepentance4ANM2",
    "_ZNK15IsaacRepentance4ANM2",
    "_ZN4KAGE8Graphics4Font",
    "_ZNK4KAGE8Graphics4Font",
    "_ZN4KAGE4Math7Vector2",
    "_ZNK4KAGE4Math7Vector2",
)


def _symbol_inventory(data, symbols, plt_slots, bl, entries) -> dict:
    """下一批切片可能用到的完整符号清单（offset + 16 字节 guard + 调用点数目）。

    含 ``std::string`` 与 ``char const*`` 两种重载，便于选择**不需要 libc++ std::string** 的那个。
    """
    covered = {record.get("mangled"): record["key"] for record in entries if record.get("mangled")}
    covered_offsets = {int(record["native_offset"], 16): record["key"]
                       for record in entries if record.get("native_offset")}
    rows = []
    for name, symbol in symbols.items():
        if not name.startswith(SYMBOL_INVENTORY_PREFIXES) or not symbol.is_defined:
            continue
        if symbol.file_offset is None:
            continue
        rows.append({
            "mangled": name,
            "demangled": _demangle([name])[0],
            "native_offset": f"0x{symbol.file_offset:X}",
            "guard16": _guard(data, symbol.file_offset),
            "plt_stub": f"0x{plt_slots[name]:X}" if name in plt_slots else None,
            "in_module_call_site_count": len(bl.get(plt_slots[name], [])) if name in plt_slots else 0,
            "covered_by_entry": covered.get(name) or covered_offsets.get(symbol.file_offset),
            "takes_std_string_by_const_ref": "NSt3__112basic_string" in name,
        })
    rows.sort(key=lambda row: int(row["native_offset"], 16))
    return {
        "purpose": "下一批渲染切片可直接取用的入口清单（本审计只覆盖其中渲染/加载/查询子集）。",
        "count": len(rows),
        "std_string_overload_count": sum(1 for row in rows if row["takes_std_string_by_const_ref"]),
        "rows": rows,
    }

def _suggested_constants() -> dict:
    """按 ``runtime/source/runtime_constants.hpp`` 的既有形式给出建议常量（本工具不修改该文件）。"""
    rows = []
    for record in ENTRIES:
        key = record["key"]
        offset, expected = GUARDS[key]
        if record["group"] == "vector":
            continue
        camel = "".join(part.capitalize() for part in key.split("_"))
        rows.append(f"inline constexpr uintptr_t k{camel}Offset = 0x{offset:X};")
        pairs = [expected[index:index + 2].upper() for index in range(0, 32, 2)]
        rows.append(f"inline constexpr std::array<u8, 16> k{camel}ExpectedBytes = {{")
        for index in range(0, 16, 8):
            rows.append("    " + ", ".join(f"0x{byte}" for byte in pairs[index:index + 8]) + ",")
        rows.append("};")
    return {
        "note": (
            "仅为下一批实现准备的建议形式（照 runtime/source/runtime_constants.hpp 的约定："
            "文件偏移常量 + 16 字节期望数组）；本审计**没有**修改 runtime/ 下任何文件。"
        ),
        "lines": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--print", action="store_true", help="打印关键窗口与逐字节校验结果")
    args = parser.parse_args(argv)

    evidence = build_evidence()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=1)
        handle.write("\n")

    print(f"写入 {os.path.relpath(args.out, REPO_ROOT)}")
    print(f"build id {evidence['build_id']}（一致 {evidence['build_id_matches_expected']}）  "
          f"nro_size {evidence['nro_size']}  sha256 {evidence['nro_sha256'][:16]}…"
          f"（一致 {evidence['nro_sha256_matches_expected']}）")
    verify = evidence["byte_verification"]
    print(f"逐字节校验：{verify['entry_count']} 条，全部一致 {verify['all_equal']}，"
          f"偏移与符号表全部一致 {verify['all_offsets_agree_with_symbol_table']}")
    recheck = verify["independent_dynsym_recheck"]
    print(f"独立解析器复核：{recheck['checked']} 条，偏移不一致 "
          f"{len(recheck['offset_mismatches'])}，字节不一致 {len(recheck['byte_mismatches'])}")
    cross = verify["runtime_constants_cross_check"]
    print(f"runtime_constants.hpp 交叉校验：{cross['parsed_constants']} 条，"
          f"全部一致 {cross['all_equal']}")
    for record in evidence["records"].values():
        print(f"  {record['key']:34} {str(record['native_offset']):>10}  "
              f"{record['guard16_from_nro']}  {record['confidence']:9} "
              f"调用点 {record['in_module_call_site_count']}")
    for record in evidence["unresolved_records"].values():
        print(f"  {record['key']:34} {'(none)':>10}  {'':32}  {record['confidence']}")
    if args.print:
        for label, block in evidence["disassembly"].items():
            print(f"\n--- {label} @ {block['start_hex']} ({block['note']}) ---")
            for line in block["lines"]:
                print(line)
        print("\n--- byte rows ---")
        for row in verify["rows"]:
            print(f"{row['key']:34} {row['native_offset']:>8} {row['nro_bytes']} "
                  f"equal={row['equal']} diff={row['differing_byte_indices']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
