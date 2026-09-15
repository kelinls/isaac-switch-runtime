#!/usr/bin/env python3
"""批次 11–13 的真机验收探针：**一次挂载**同时取 A / B 两个通道并当场对照。

## 两个通道分别是什么

* **A 通道**＝Lua 报出来的值。设备上 EID 的 `main.lua` 里追加了一段临时探针
  （`dist/eid-verify-batch11-13-20260916/added-snippet.lua`），进游戏若干帧后用 `error()`
  把一行 `[b13] …` 抛出来；运行时把它记进 `g_LastLuaErrorText`（**256 字节，超了就截断** ——
  2026-09-16 实测被截断过，所以探针必须写得短）。本探针从**我们的运行时模块**里读回这行。
* **B 通道**＝**引擎内存的独立读数**：`g_Game` 槽 → Game → 当前 Room → 网格实体表 → 实体字段，
  以及 Game 内嵌的 `ItemPool` 里的药丸表。完全不经过我们的 Lua 层，两边对上了才能说
  "这条 API 报的是引擎的真值"，而不是"它跟它自己一致"。

## ★ 为什么同时读**两条**解析链（2026-09-16 的教训）

`g_Game` 槽本身是个**指针变量**，链上有两种可能：

    链 1：Level/Game = *(模块 + 0xAAC698)          —— 解一层（槽值本身）
    链 2：Level/Game = *(*(模块 + 0xAAC698))       —— 再解一层

2026-09-16 实测两条链读出来的**不是同一个对象**（链 1 的房间索引 84、链 2 是 71），
而"哪条是引擎真正在用的那个"当时没有定论。所以本探针**两条都读**，再用 A 通道的
`idx`/`stage`/`stype` 去判定哪条与运行时一致 —— 不猜，量出来。

## 一次性读回来的地址

    g_Game 槽              游戏模块基址 + 0xAAC698  → 指针变量（两条链的起点）
    Level(=Game) + 0x00     关卡 eLevelStage（u32）      + 0x04 eStageType（u32）
    Level(=Game) + 0x21550  当前 Room*                   + 0x21558 房间索引（u32）
    Room + 0x30             网格实体表（13×13 = 169 项 `GridEntity*`）
    ★ 实体的字段要**分两批**读：先读表里的**槽值**（那才是实体地址），再去读那个地址上的
      `+0x10` variant / `+0x18` type / `+0x30` RNG（16 字节、种子在头 4 字节）。
      2026-09-16 第一版把"槽的地址"当成了实体地址，读回一堆数组元素当成字段 ——
      这正是 `probe_room_descriptor.py` 注释里警告过的同一类错误。
    Game + 0x242C0          ItemPool（**内嵌对象**，加法不是解引用）
    ItemPool + 0xa2c        药丸效果表（15 × u32）      + 0xa68 药丸"已识别"表（15 × u8）

## 判定口径

* `stage`/`stype`/`pills` —— 与"被判定为真链"的那套引擎读数逐值相等；
* `alt`/`pre` —— 与 PC 契约推出的布尔一致（`stype != 0`；`stage == 9 且 (stype & ~1) == 4`）；
* `g<k>` 的 `variant`/`type`/`seed` —— 与那个下标上的实体字段逐值相等；
* `g<k>` 的 `next` —— 与 `seed` 不同（快照真被推进过），**而引擎那边的种子必须仍是 `seed`**
  （`0x0E010054` 那条已登记偏离的可执行判据）；
* `pillgiant` —— 巨大药丸标志（+2048）问出来的 15 位必须与不带标志的完全相同；
* `rngrt` —— `SetSeed(4242,35)` 之后 `GetSeed` 必须读回 4242；
* `goob`/`pilloor`/`pillneg` —— 只报告不判定（引擎侧本就没有定义）。

用法：

    # 真机（先启动游戏到游戏内；本命令自己找进程与两个模块基址）
    python3 tools/probe_batch13_anchors.py --out dist/batch13-anchors.json --note room=start

    # 离线重放一次已落盘的读数（不连设备、不再烧 attach 预算）
    python3 tools/probe_batch13_anchors.py --from-raw dist/batch13-anchors.json

退出码：0 全部硬判据通过；1 有硬判据不通过；3 环境/读数不足（没进程、没基址、Lua 那行没读到）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_device_tool():
    """复用 `read_device_state_via_gdb.py` 的会话、解析与偏移同步（那套取数的唯一实现）。"""
    path = ROOT / "tools" / "read_device_state_via_gdb.py"
    spec = importlib.util.spec_from_file_location("read_device_state_via_gdb", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEVICE = load_device_tool()

# ---- 已知偏移（全部来自已审计的反汇编 / `runtime_constants.hpp`，不是猜的）------------
GAME_SLOT_OFFSET = 0xAAC698             # 游戏模块里的 `g_Game`（指针变量）
LEVEL_STAGE_OFFSET = 0x00               # Level == Game（Level 内嵌在 Game 起始处）
LEVEL_STAGE_TYPE_OFFSET = 0x04
CURRENT_ROOM_POINTER_OFFSET = 0x21550
CURRENT_ROOM_INDEX_OFFSET = 0x21558
ROOM_GRID_TABLE_OFFSET = 0x30
GRID_SLOT_COUNT = 169                   # 13 × 13（`Room::GetGridIndex` 的乘数）
GRID_ENTITY_VARIANT_OFFSET = 0x10
GRID_ENTITY_TYPE_OFFSET = 0x18
GRID_ENTITY_RNG_OFFSET = 0x30
GRID_ENTITY_DUMP_WORDS = 36             # +0x00..+0x11f：够在对象里搜 Lua 报的值
#: 实体 dump 的字节数（与上面的字数一致）。
GRID_ENTITY_DUMP_BYTES = GRID_ENTITY_DUMP_WORDS * 8
ITEM_POOL_IN_GAME_OFFSET = 0x242C0      # Game 内嵌 ItemPool（`game_observer.cpp` 用的就是这个）
PILL_EFFECT_OFFSET = 0xA2C
PILL_IDENTIFIED_OFFSET = 0xA68
PILL_COLOR_COUNT = 15                   # IsaacDocs `enums/PillColor.md` 的 NUM_PILLS

STAGE_PRE_ASCENT = 9
STAGE_TYPE_REPENTANCE_ERA_MASKED = 0x4

#: 每个链最多 dump 几个非空格子上的实体（3 个足够对照，且省输出）。
MAX_GRID_ENTITIES = 3
#: 链的编号 → 人话（写进报告，免得看数字猜）。
CHAIN_LABELS = {
    1: "链 1：槽值本身（解一层）",
    2: "链 2：槽值再解一层",
}


def chain_addresses(game_base: int, owner: int, entity_pointers: dict[int, int],
                    item_pool: int) -> dict[str, tuple[int, int]]:
    """返回某条链上要读的 `标签 → (地址, 8 字节字数)`。

    ⚠ `entity_pointers` 是**从网格表的槽里读出来的实体地址**（不是槽的地址）——
    第一版在这里踩过：把槽地址当实体地址，于是 `+0x10`/`+0x18` 读到的其实是数组里的
    下一个槽，整批"实体字段"全是垃圾。纯函数，门禁测试直接钉住这条。
    """
    plan: dict[str, tuple[int, int]] = {
        "head": (owner + LEVEL_STAGE_OFFSET, 1),                 # 关卡 + 关卡类型
        "current": (owner + CURRENT_ROOM_POINTER_OFFSET, 2),     # Room* + 房间索引 + 维度
        "item_pool": (item_pool + ITEM_POOL_IN_GAME_OFFSET, 0),  # 只作登记，真正读下面两张表
        "pill_effect": (item_pool + ITEM_POOL_IN_GAME_OFFSET + PILL_EFFECT_OFFSET, 8),
        "pill_bits": (item_pool + ITEM_POOL_IN_GAME_OFFSET + PILL_IDENTIFIED_OFFSET, 2),
    }
    for index, pointer in entity_pointers.items():
        plan[f"entity{index}"] = (pointer, GRID_ENTITY_DUMP_WORDS)
    return plan


def words_to_bytes(words: list[int]) -> bytes:
    out = bytearray()
    for word in words:
        out += int(word).to_bytes(8, "little")
    return bytes(out)


def u32(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 4], "little")


def u64(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 8], "little")


LUA_LINE = re.compile(r"\[b13\]\s*(?P<body>.*)", re.S)


NUMERIC_PREFIX = re.compile(r"^[-0-9A-Fa-fx/]+")


def sanitize_value(value: str) -> str:
    """削掉"粘在数值尾巴上的旧文本"。

    运行时的 Lua 错误文本缓冲是 256 字节且**不清零**：新的错误更短时，后面会留着上一次那条
    更长错误的尾巴（2026-09-16 实测：`0x20F013A7s/modconfig.lua'…`）。所以以数字开头的值
    只取开头那段合法的数字/`/`，其余丢掉；`true`/`false`/`ERR:…` 这类原样保留。
    """
    if not value or not value[0].isdigit() and value[0] != "-":
        return value
    match = NUMERIC_PREFIX.match(value)
    return match.group(0) if match else value


def parse_lua_report(text: str) -> dict[str, str]:
    """从 Lua 错误文本里取出 `[b13] k=v k=v …` 那一行（值可能是 `ERR:…`）。"""
    match = LUA_LINE.search(text)
    if match is None:
        return {}
    body = match.group("body").replace("\n", " ").strip()
    fields: dict[str, str] = {}
    for piece in re.split(r"\s+(?=[A-Za-z][A-Za-z0-9_]*=)", body):
        if "=" not in piece:
            continue
        key, _, value = piece.partition("=")
        fields[key.strip()] = sanitize_value(value.strip())
    return fields


def parse_int(text: str) -> int:
    """探针报出来的整数：`0x…` 是十六进制（v2 一律带前缀），纯数字按十进制，
    只有 `A–F` 的旧格式（v1）按十六进制兜底。"""
    text = text.strip()
    if text.lower().lstrip("-").startswith("0x"):
        return int(text, 16)
    return int(text, 16) if re.search(r"[A-Fa-f]", text) else int(text, 10)


#: A 通道的两种键名：v1 探针用长键名（`idx/stage/stype`），v2 探针为了塞进运行时那 256 字节的
#: 错误文本上限改用短键名（`i/s/t/P/G0`）。判定逻辑只认归一之后的那一套。
KEY_ALIASES = {
    "idx": ("idx", "i"),
    "stage": ("stage", "s"),
    "stype": ("stype", "t"),
    "alt": ("alt", "a"),
    "pre": ("pre", "p"),
    "pills": ("pills", "P"),
    "rngrt": ("rngrt", "R"),
    "pillgiant": ("pillgiant",),
    "pilloor": ("pilloor", "O"),
    "pillneg": ("pillneg", "N"),
    "goob": ("goob",),
}


def canonical_fields(fields: dict[str, str]) -> dict[str, object]:
    """把探针的两种键名归一成一套，并把 `0/1` 压成布尔。"""
    out: dict[str, object] = {}

    def take(*names: str) -> str | None:
        for name in names:
            if fields.get(name) is not None:
                return fields[name]
        return None

    for canonical, names in KEY_ALIASES.items():
        value = take(*names)
        if value is None:
            continue
        if canonical in ("alt", "pre"):
            out[canonical] = value if value in ("true", "false") else ("true" if value == "1" else "false")
        elif canonical == "pilloor":
            # v2 探针直接报"越界问出来是不是 true"（0 = 符合预期），v1 报原始布尔
            out["pilloor_false"] = (value == "0") if value in ("0", "1") else (value == "false")
        elif canonical == "pillneg":
            out["pillneg_false"] = (value == "0") if value in ("0", "1") else (value == "false")
        else:
            out[canonical] = value
    # v2 探针用一个 0/1 表示"巨大药丸标志那一遍与不带标志那一遍是否完全相同"
    if fields.get("X") is not None:
        out["pillgiant_matches"] = fields["X"] == "1"
    for key, value in fields.items():
        match = re.fullmatch(r"[gG](\d+)", key)
        if match is not None:
            out[f"g{match.group(1)}"] = value
    return out


def as_bool(text: str) -> bool | None:
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def chain_from_words(prefix: str, words: dict[str, list[int]]) -> dict:
    """把某一个前缀（`c1`/`c2`）下的原始字重建出这条链的读数。"""
    head = words_to_bytes(words.get(f"{prefix}_head", []))
    current = words_to_bytes(words.get(f"{prefix}_current", []))
    grid = words_to_bytes(words.get(f"{prefix}_grid", []))
    pill_bits = words_to_bytes(words.get(f"{prefix}_pill_bits", []))
    entities: dict[int, dict] = {}
    for label, blob_words in words.items():
        if not label.startswith(f"{prefix}_entity"):
            continue
        suffix = label[len(f"{prefix}_entity"):]
        if not suffix.isdigit():
            continue
        index = int(suffix)
        blob = words_to_bytes(blob_words)
        entities[index] = {
            "index": index,
            "dump": blob,
            "pointer": u64(blob, 0),
            "variant": u32(blob, GRID_ENTITY_VARIANT_OFFSET),
            "type": u32(blob, GRID_ENTITY_TYPE_OFFSET),
            "seed": u32(blob, GRID_ENTITY_RNG_OFFSET),
        }
    non_empty: list[int] = []
    if grid:
        for index in range(GRID_SLOT_COUNT):
            if u64(grid, index * 8) != 0:
                non_empty.append(index)
    for index, record in entities.items():
        slot_pointer = u64(grid, index * 8) if grid else 0
        if slot_pointer:
            record["pointer"] = slot_pointer
    return {
        "stage": u32(head, LEVEL_STAGE_OFFSET) if head else 0,
        "stage_type": u32(head, LEVEL_STAGE_TYPE_OFFSET) if head else 0,
        "room": u64(current, 0) if current else 0,
        "room_index": u32(current, CURRENT_ROOM_INDEX_OFFSET - CURRENT_ROOM_POINTER_OFFSET)
        if current else 0,
        "non_empty_cells": non_empty,
        "pointers": {index: u64(grid, index * 8) for index in non_empty},
        "pills": "".join("1" if byte else "0" for byte in pill_bits[:PILL_COLOR_COUNT]),
        "entities": entities,
    }


def chains_from_raw(raw: dict) -> dict[int, dict]:
    words = raw["raw_words"]
    chains = {}
    for number, prefix in ((1, "c1"), (2, "c2")):
        if any(label.startswith(prefix) for label in words):
            chains[number] = chain_from_words(prefix, words)
    return chains


def expected_from_chain(chain: dict) -> dict[str, object]:
    """真链推出的期望值（A 通道必须与它们逐条对上）。"""
    stage = chain["stage"]
    stage_type = chain["stage_type"]
    expected: dict[str, object] = {
        "stage": stage,
        "stype": stage_type,
        "alt": stage_type != 0,
        "pre": stage == STAGE_PRE_ASCENT
        and (stage_type & ~1) == STAGE_TYPE_REPENTANCE_ERA_MASKED,
        "pills": chain["pills"],
    }
    for position, index in enumerate(sorted(chain["entities"])):
        expected[f"g{position}"] = chain["entities"][index]
    return expected


def pick_chain(chains: dict[int, dict], canonical: dict[str, object]) -> tuple[int | None, str]:
    """用 A 通道的值判定"哪条链是引擎真正在用的那个对象"。

    判据按可信度排序：房间索引（84 与 71 这种差别最能分辨两个对象）→ 关卡 → 关卡类型。

    ⚠ 2026-09-16 的教训：A 通道是**报的那一刻**的值。第一次真机读数里 A 报的是起始房间
    （索引 84），而人已经走进了另一个房间（71），于是"两条链都对不上" —— 那不是链错，
    是**两次取数不在同一时刻**。所以探针 v2 每 300 帧重报一次，读数前也不要换房间。
    """
    if not canonical:
        return None, "A 通道没有读数 ⇒ 无法判定哪条链是真的"
    index_wanted = canonical.get("idx")
    stage_wanted = canonical.get("stage")
    type_wanted = canonical.get("stype")
    match: list[int] = []
    for number, chain in chains.items():
        try:
            index_ok = index_wanted is not None and int(str(index_wanted)) == chain["room_index"]
        except ValueError:
            index_ok = False
        stage_ok = stage_wanted is not None and str(stage_wanted) == str(chain["stage"])
        type_ok = type_wanted is not None and str(type_wanted) == str(chain["stage_type"])
        if index_ok or (stage_ok and type_ok):
            match.append(number)
    if len(match) == 1:
        return match[0], f"与 A 通道一致（{CHAIN_LABELS[match[0]]}）"
    if len(match) > 1:
        return None, f"两条链都和 A 通道对得上（{match}）—— 这一轮无法区分"
    return None, ("两条链都与 A 通道不一致 ⇒ 要么解析链错了，要么**两次取数不在同一时刻**"
                  "（A 通道是报的那一刻的值：进游戏后换过房间就会这样）")


def compare(canonical: dict[str, object], chain: dict) -> tuple[list[dict], list[dict]]:
    """把 A 通道（归一后）的 Lua 值逐条对照真链的引擎读数。"""
    checks: list[dict] = []
    notes: list[dict] = []
    expected = expected_from_chain(chain)

    def check(key: str, lua, wanted, note: str) -> None:
        if lua is None:
            checks.append({"key": key, "lua": None, "engine": wanted, "ok": False,
                           "why": f"{note}（A 通道没有这个键 —— 可能被 256 字节截断了）"})
            return
        if isinstance(wanted, bool):
            if isinstance(lua, bool):
                ok = lua == wanted
            else:
                text = str(lua)
                ok = (as_bool(text) == wanted) if text in ("true", "false") \
                    else (text == ("true" if wanted else "false"))
        elif isinstance(wanted, int):
            try:
                ok = parse_int(str(lua)) == wanted
            except ValueError:
                ok = False
        else:
            ok = str(lua) == str(wanted)
        checks.append({"key": key, "lua": lua, "engine": wanted, "ok": ok, "why": note})

    for key, note in (("stage", "A 通道的关卡值与引擎内存逐值相等"),
                      ("stype", "A 通道的关卡类型与引擎内存逐值相等"),
                      ("pills", "A 通道的 15 位药丸识别标志与引擎内存逐字节相等"),
                      ("alt", "PC 契约推出的布尔必须与 Lua 一致"),
                      ("pre", "PC 契约推出的布尔必须与 Lua 一致")):
        check(key, canonical.get(key), expected[key], note)

    reported = [int(key[1:]) for key in canonical
                if re.fullmatch(r"g\d+", key) and str(canonical[key]).count("/") == 4]
    reported_count = (max(reported) + 1) if reported else 0
    for key, record in sorted(expected.items()):
        if not key.startswith("g") or not key[1:].isdigit():
            continue
        raw = canonical.get(key)
        if raw is None or (int(key[1:]) >= reported_count and reported_count):
            # 探针故意只报前两格（为了塞进 256 字节），第三格我们仍然读了 —— 它只用于取证，
            # 不该算成"A 通道缺了这一格"。
            notes.append({"key": key, "lua": raw,
                          "why": "只报告不判定（探针只报前两格；这一格的引擎读数用作取证）"})
            continue
        pieces = str(raw).split("/")
        if len(pieces) != 5:
            checks.append({"key": key, "lua": raw, "engine": record, "ok": False,
                           "why": "A 通道这一格不是 5 段（下标/variant/type/种子/推进后的种子）"})
            continue
        index, variant, gtype, seed, after = pieces
        try:
            index_ok = parse_int(index) == record["index"]
            variant_ok = parse_int(variant) == record["variant"]
            type_ok = parse_int(gtype) == record["type"]
            seed_ok = parse_int(seed) == record["seed"]
            advanced_ok = parse_int(after) != parse_int(seed)
        except ValueError:
            index_ok = variant_ok = type_ok = seed_ok = advanced_ok = False
        parts = {
            "index": index_ok,
            "variant": variant_ok,
            "type": type_ok,
            "seed": seed_ok,
            # 快照必须真被推进过（否则"引擎没变"就成了废话）
            "advanced": advanced_ok,
            # ★ 引擎那边的种子必须一个字节都没动（已登记偏离的可执行判据）
            "engine_untouched": seed_ok,
        }
        checks.append({"key": key, "lua": raw, "engine": record, "ok": all(parts.values()),
                       "why": str(parts)})

    check("rngrt", canonical.get("rngrt"), 4242, "SetSeed(4242,35) 之后 GetSeed 必须读回 4242")
    if "pillgiant_matches" in canonical:
        check("pillgiant", canonical["pillgiant_matches"], True,
              "巨大药丸标志（+2048）问出来的 15 位必须与不带标志的完全相同")
    else:
        check("pillgiant", canonical.get("pillgiant"), canonical.get("pills"),
              "巨大药丸标志（+2048）必须与不带标志的 15 位完全相同")
    for key in ("pilloor", "pillneg"):
        if f"{key}_false" in canonical:
            check(key, canonical[f"{key}_false"], True,
                  "掩码后越界/负颜色必须返回 false（不能抛错、也不能返回 true）")
        else:
            check(key, canonical.get(key), "false",
                  "掩码后越界/负颜色必须返回 false（不能抛错、也不能返回 true）")

    for key in ("goob",):
        if canonical.get(key) is not None:
            notes.append({"key": key, "lua": canonical[key],
                          "why": "只报告不判定（越界下标在引擎/PC 侧本就没有定义）"})
    return checks, notes


def forensic_lines(canonical: dict[str, object], chain: dict) -> list[str]:
    """取证：把 A 通道报的 variant/type/种子在**实体对象的原始字节**里搜一遍。

    为什么需要它：如果 `+0x10`/`+0x18`/`+0x30` 这三个偏移判断错了，两边的值会"看起来都对不上"，
    而人很容易把这解释成"引擎那边还没刷新"。搜一遍就知道 Lua 报的那个值到底落在对象的哪个
    偏移上 —— 偏移对了它就出现在 +0x10 / +0x18 / +0x30。
    """
    lines: list[str] = []
    ordered = sorted(chain["entities"])
    for position, index in enumerate(ordered):
        raw = canonical.get(f"g{position}")
        record = chain["entities"][index]
        blob = record.get("dump") or b""
        if raw is None or not blob:
            continue
        pieces = str(raw).split("/")
        if len(pieces) != 5:
            continue
        try:
            wanted = {
                "variant": parse_int(pieces[1]),
                "type": parse_int(pieces[2]),
                "seed": parse_int(pieces[3]),
            }
        except ValueError:
            continue
        found: dict[str, list[int]] = {}
        for name, value in wanted.items():
            offsets = [offset for offset in range(0, max(0, len(blob) - 3), 4)
                       if int.from_bytes(blob[offset:offset + 4], "little") == value]
            found[name] = offsets
        expect = {"variant": GRID_ENTITY_VARIANT_OFFSET, "type": GRID_ENTITY_TYPE_OFFSET,
                  "seed": GRID_ENTITY_RNG_OFFSET}
        marks = []
        for name, value in wanted.items():
            offsets = found[name]
            if not offsets:
                marks.append(f"{name}=0x{value:X} 在该对象里**没有**")
            else:
                hit = "✓" if expect[name] in offsets else f"（不在 +0x{expect[name]:x}）"
                marks.append(f"{name}=0x{value:X} 出现在 "
                             f"{[hex(off) for off in offsets[:4]]}{hit}")
        lines.append(f"  网格 #{index}（A 通道第 {position} 格）：" + "；".join(marks))
    return lines


def print_chain(number: int, chain: dict) -> None:
    label = CHAIN_LABELS.get(number, f"链 {number}")
    print(f"  {label}")
    print(f"    Level(=Game) 对象 = 0x{chain.get('owner', 0):x}；"
          f"Room* = 0x{chain['room']:x}")
    print(f"    +0x00 关卡 = {chain['stage']}；+0x04 关卡类型 = {chain['stage_type']}；"
          f"+0x21558 房间索引 = {chain['room_index']}")
    print(f"    Room+0x30 表里非空格子数 = {len(chain['non_empty_cells'])}"
          f"（前几个：{chain['non_empty_cells'][:6]}）")
    print(f"    ItemPool+0xa68 药丸识别位 = {chain['pills']}")
    for index, record in sorted(chain["entities"].items()):
        print(f"    网格 #{index}: 实体 0x{record['pointer']:x} variant={record['variant']} "
              f"type={record['type']} RNG 种子={record['seed']}")


def report(raw: dict) -> int:
    """打印 A/B 两通道的对照结果。返回退出码。"""
    base = raw.get("base", 0)
    chains = chains_from_raw(raw)
    text = raw.get("lua_error_text", "")
    fields = parse_lua_report(text)
    canonical = canonical_fields(fields)

    print("== B 通道：引擎内存独立读数（两条解析链都读）==")
    for number in sorted(chains):
        print_chain(number, chains[number])

    print("== A 通道：Lua 报出来的值 ==")
    if not fields:
        print(f"  没读到 `[b13]` 那一行。错误文本前 200 字符：{text[:200]!r}")
        return 3
    for key, value in fields.items():
        print(f"  {key} = {value}")
    if len(text) >= 250:
        print("  ⚠ 错误文本已经顶到 256 字节上限 ⇒ 后面几个键大概被截断了（探针要写短些）")

    number, why = pick_chain(chains, canonical)
    print(f"== 哪条链是真的 ==\n  {why}")
    if number is None:
        print("结论：**不通过**（无法判定真链，API 的结论先搁置）")
        return 1
    chain = chains[number]
    chain["owner"] = raw.get(f"c{number}_owner", 0)

    checks, notes = compare(canonical, chain)
    print("== 逐条对照（对所有硬判据）==")
    failures = 0
    for item in checks:
        mark = "✓" if item["ok"] else "✗"
        if not item["ok"]:
            failures += 1
        print(f"  {mark} {item['key']}: Lua={item['lua']} / 引擎={item['engine']} —— {item['why']}")
    for item in notes:
        print(f"  · {item['key']}: {item['lua']} —— {item['why']}")

    lines = forensic_lines(canonical, chain)
    if lines:
        print("== 取证：A 通道报的值在实体对象里落在哪个偏移 ==")
        for line in lines:
            print(line)

    anchors_ok = raw.get("anchors_ok")
    print(f"  身份自校验："
          f"{'通过（三个代码锚点与本地构建逐字节一致）' if anchors_ok else '未通过'}")
    print(f"  base = 0x{base:x}")
    if failures or not anchors_ok:
        print(f"结论：**不通过**（{failures} 条硬判据没过）")
        return 1
    print("结论：通过（A/B 两通道逐条一致）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default="192.168.124.11")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0, help="运行时模块基址")
    ap.add_argument("--game-base", type=lambda s: int(s, 0), default=0, help="游戏模块基址")
    ap.add_argument("--elf", default=DEVICE.DEFAULT_ELF)
    ap.add_argument("--out", default="", help="把原始读数与解析结果落盘（JSON）")
    ap.add_argument("--note", default="", help="给这次读数起的标签")
    ap.add_argument("--from-raw", default="", help="离线重放一次已落盘的读数（不连设备）")
    ap.add_argument("--dry-run", action="store_true", help="只测 gdb 管道，不连设备")
    args = ap.parse_args(argv)

    if args.from_raw:
        return report(json.loads(pathlib.Path(args.from_raw).read_text(encoding="utf-8")))

    if args.dry_run:
        session = DEVICE.GdbSession(timeout=60)
        try:
            print(session.command("echo @@管道_ok@@").strip())
        finally:
            session.close()
        return 0

    elf = pathlib.Path(args.elf)
    if not elf.is_absolute():
        elf = ROOT / elf
    # `sync_syms_from_elf()` 是**原地刷新** `DEVICE.SYMS` 并返回"没解析到的符号"。
    missing = DEVICE.sync_syms_from_elf(elf, verbose=True)
    syms = DEVICE.SYMS
    needed = ("identity", "exl_main", "manifest_install", "g_LastLuaErrorLength",
              "g_LastLuaErrorText")
    absent = [key for key in needed if key not in syms]
    if absent:
        print(f"本地 ELF 里没解析到这些符号，读数无法进行：{absent}（其余未解析：{missing}）")
        return 3

    session = DEVICE.GdbSession()
    attached = False
    try:
        session.command(f"target extended-remote {args.ip}:22225")
        pid = args.pid or DEVICE.parse_processes(session.command("info os processes")) or 0
        if not pid:
            print("没找到正在运行的 Application —— 请先启动游戏到游戏内。")
            return 3
        print(f"进程 = {pid}（本次只 attach 一次）")
        session.command(f"attach {pid}")
        attached = True

        base = args.base
        game_base = args.game_base
        monitor_text = ""
        if not base or not game_base:
            monitor_text = session.command("monitor get info")
            module, _plugin, found_game = DEVICE.parse_bases(monitor_text)
            base = base or (module or 0)
            game_base = game_base or (found_game or 0)
        if not base or not game_base:
            # `monitor get info` 在某些固件/时机下不给模块表（2026-09-16 实测：返回只有一个 "P"）。
            # 这时按**内容指纹**扫可执行段兜底，并把 monitor 原文打出来 ——
            # 否则一次 attach 白烧，连"为什么没认出来"都看不到。
            if monitor_text:
                head = "\n".join(monitor_text.splitlines()[:24])
                print(f"  monitor get info 原文（前 24 行）：\n{head}")
            scan_base, scan_game = DEVICE.locate_by_scan(session, elf)
            base = base or scan_base or 0
            game_base = game_base or scan_game or 0
        if not base or not game_base:
            print("没拿到模块基址 —— 可用 --base/--game-base 指定。")
            return 3
        print(f"运行时模块基址 = 0x{base:x}；游戏模块基址 = 0x{game_base:x}")

        raw_words: dict[str, list[int]] = {}

        def read(requests: list[tuple[str, int, int]]) -> None:
            requests = [item for item in requests if item[2] > 0]
            if not requests:
                return
            lines: list[str] = []
            for label, address, count in requests:
                lines.append(f'echo TAG {label}\\n')
                lines.append(f'x/{count}gx 0x{address:x}')
            values = DEVICE.parse_words(session.command("\n".join(lines)))
            for label, _address, _count in requests:
                raw_words[label] = values.get(label, [])

        def first(label: str, index: int = 0) -> int:
            words = raw_words.get(label) or []
            return words[index] if len(words) > index else 0

        # ---- 第一批：运行时侧的身份锚点 + Lua 错误文本（A 通道）----
        read([
            ("anchors", base + syms["identity"], 2),
            ("anchor_exl_main", base + syms["exl_main"], 2),
            ("anchor_manifest", base + syms["manifest_install"], 2),
            ("lua_error_text", base + syms["g_LastLuaErrorText"], 32),
        ])
        anchors_ok = True
        for name, label in (("identity", "anchors"), ("exl_main", "anchor_exl_main"),
                            ("manifest_install", "anchor_manifest")):
            expect = DEVICE.local_bytes(elf, syms[name], 16)
            got = words_to_bytes(raw_words.get(label, [])[:2])
            if expect is None or got != expect:
                anchors_ok = False
                print(f"  锚点不符：{name} 设备 {got.hex()} / 本地 "
                      f"{expect.hex() if expect else '(读不到)'}")
        lua_text = words_to_bytes(raw_words.get("lua_error_text", [])).split(b"\x00", 1)[0]
        lua_text = lua_text.decode("utf-8", errors="replace")

        # ---- 第二批：`g_Game` 槽 → 两条链的对象各读一次 ----
        read([("game_slot", game_base + GAME_SLOT_OFFSET, 1)])
        owner1 = first("game_slot")
        if not owner1:
            print("`g_Game` 槽是 0 —— 游戏还没进到有 Game 对象的状态（请进到游戏内再跑）。")
            return 3
        read([("c1_owner_probe", owner1, 1)])
        owner2 = first("c1_owner_probe")
        raw_words["c1_owner"] = [owner1]
        raw_words["c2_owner"] = [owner2]
        print(f"g_Game 槽内容 = 0x{owner1:x}；再解一层 = 0x{owner2:x}")
        read([("c1_head", owner1 + LEVEL_STAGE_OFFSET, 1),
              ("c1_current", owner1 + CURRENT_ROOM_POINTER_OFFSET, 2),
              ("c2_head", owner2 + LEVEL_STAGE_OFFSET, 1) if owner2 else
              ("c2_head", owner1, 0),
              ("c2_current", owner2 + CURRENT_ROOM_POINTER_OFFSET, 2) if owner2 else
              ("c2_current", owner1, 0)])

        # ---- 第三批：每条链的网格表 + 表里前几个实体的**真实地址**上的字段 ----
        for number, owner in ((1, owner1), (2, owner2)):
            prefix = f"c{number}"
            if not owner:
                continue
            current = words_to_bytes(raw_words.get(f"{prefix}_current", []))
            room = u64(current, 0) if current else 0
            if not room:
                continue
            read([(f"{prefix}_grid", room + ROOM_GRID_TABLE_OFFSET, GRID_SLOT_COUNT)])
            grid = words_to_bytes(raw_words.get(f"{prefix}_grid", []))
            occupied = []
            for index in range(GRID_SLOT_COUNT):
                if u64(grid, index * 8) != 0:
                    occupied.append((index, u64(grid, index * 8)))
                if len(occupied) >= MAX_GRID_ENTITIES:
                    break
            print(f"  {CHAIN_LABELS[number]}：Room* = 0x{room:x}；"
                  f"表里非空格子（前 {MAX_GRID_ENTITIES} 个）= "
                  f"{[index for index, _ in occupied]}；实体地址 = "
                  f"{[hex(pointer) for _, pointer in occupied]}")
            if occupied:
                plan = chain_addresses(game_base, owner, dict(occupied), owner)
                read([(f"{prefix}_{label}", address, count)
                      for label, (address, count) in plan.items()
                      if label.startswith("entity")])

        # ---- 第四批：两条链各自的 ItemPool 药丸表 ----
        for number, owner in ((1, owner1), (2, owner2)):
            if not owner:
                continue
            prefix = f"c{number}"
            read([(f"{prefix}_pill_effect",
                   owner + ITEM_POOL_IN_GAME_OFFSET + PILL_EFFECT_OFFSET, 8),
                  (f"{prefix}_pill_bits",
                   owner + ITEM_POOL_IN_GAME_OFFSET + PILL_IDENTIFIED_OFFSET, 2)])

        session.command("detach")
        attached = False
    finally:
        if attached:
            try:
                session.command("detach")
            except Exception:
                pass
        session.close()

    raw = {
        "note": args.note,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": base,
        "game_base": game_base,
        "anchors_ok": anchors_ok,
        "lua_error_text": lua_text,
        "raw_words": raw_words,
    }
    out_path = pathlib.Path(args.out) if args.out else (
        ROOT / "dist" / f"batch13-anchors-{time.strftime('%Y%m%d-%H%M%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"原始读数已落盘：{out_path}（可用 --from-raw 离线重放）")
    return report(raw)


if __name__ == "__main__":
    sys.exit(main())
