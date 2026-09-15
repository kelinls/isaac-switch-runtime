#!/usr/bin/env python3
"""只读探针：把 `RoomDescriptor` 的候选字段在**真实游戏里**读出来（不改运行时）。

## 为什么是"不改运行时的内存探针"

`Clear` 与 `SafeGridIndex` 这两个字段没有语义锚点（符号表里没有访问器，`Reset()` 里
`-1` 的默认值只是线索）。按我们定的规矩，**没有语义证据不许挂名字**，所以只能靠行为验证：
在真实游戏里按"已知状态变化"去读候选偏移。

而"改运行时加一块状态面再读回来"这条路要付两笔代价：重建运行时 + 按项目纪律
**重同步读数工具的整张偏移表**（改任何编译单元都会让 bss/data 整体挪位）。
本探针绕开这两笔代价 —— 它直接读**游戏模块**的内存：`g_Game` 槽、描述符容器、
以及描述符本身的原始字节。运行时本体一行不动。

## 一次 attach 读全部（调试桩的 attach 预算有限）

探针把一次会话里需要的东西**一次性**读回来，并把**原始输出落盘**（`--out`）：
后续换解释口径时用 `--from-raw` 离线重放，不必再连设备（这是项目既有的做法）。

## 读数怎么解释（本探针只负责"取回来"）

已知的结构（来自布局表与已审计的反汇编）：

    g_Game 槽          游戏模块基址 + 0xAAC698   → Game*
    Game + 0x21510     描述符容器（vector 形态：begin/end/capacity）
    Game + 0x21550     当前 Room*
    Game + 0x21558     当前房间索引（u32）
    Game + 0x21560     当前维度（u32）
    RoomDescriptor     标量前缀 [0x00,0x74)，容器区 0x78 起
    desc + 0x10        Data（指向房间配置）
    Data + 0x08        Data.Type（房间类型，ROOM_TREASURE = 4）

于是"给字段命名"变成两件事：

* **`SafeGridIndex`**：把当前房间索引（`+0x21558`）与各候选字段比对 —— 在普通房间里
  它应当等于该房间在 13×13 网格里的下标；换个房间再读一次，跟着变的那个就是它。
  描述符**整表都读回来**，所以不需要为了换房间多烧几次 attach：换完房间再跑一次即可。
* **`Clear`**：同一房间在"敌人还活着"与"已清"两个状态下各读一次，看哪个字段 0→1 翻转。

用法见文件末尾的 `--help`；典型流程写在 `docs/`。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_device_tool():
    """复用 `read_device_state_via_gdb.py` 的会话与解析（它是这套取数的唯一实现）。"""
    path = ROOT / "tools" / "read_device_state_via_gdb.py"
    spec = importlib.util.spec_from_file_location("read_device_state_via_gdb", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEVICE = load_device_tool()

# ---- 已知偏移（全部来自已审计的反汇编 / 布局表，不是猜的）----------------------
GAME_SLOT_OFFSET = 0xAAC698        # `g_Game`（`Level::GetRoomByIdx` 里 `adrp/ldr #0x698`）
DESCRIPTOR_CONTAINER_OFFSET = 0x21510   # 描述符容器（`Level::GetRoomByIdx` 里 add x11, x0, #0x21510）
CURRENT_ROOM_POINTER_OFFSET = 0x21550   # Game → Room*
CURRENT_ROOM_INDEX_OFFSET = 0x21558
CURRENT_DIMENSION_OFFSET = 0x21560

#: 标量前缀里我们感兴趣的候选字段（布局表称"结构确定、名字未定"的那批）。
CANDIDATE_FIELD_OFFSETS = (0x00, 0x04, 0x08, 0x0C, 0x20, 0x48, 0x4C, 0x50,
                           0x58, 0x60, 0x64, 0x68, 0x6C, 0x70)

#: `RoomType` 枚举的取值 → 名字（来自 `runtime/source/program/pc_lua_enum_data.cpp` 的
#: `kRoomTypeValues`，也就是 PC 侧 Lua 的 `RoomType` 表）。
#: 为什么要有它：早先的读数把 `Type = 1` 标成"起始房间"，而 1 其实是 **`ROOM_DEFAULT`（普通房）**
#: —— 一层楼里普通房有一堆，于是那张表把四个槽位都标成了"起始房间"，是**会误导判断的错标签**。
ROOM_TYPE_NAMES = {
    0: "ROOM_NULL", 1: "ROOM_DEFAULT", 2: "ROOM_SHOP", 3: "ROOM_ERROR",
    4: "ROOM_TREASURE", 5: "ROOM_BOSS", 6: "ROOM_MINIBOSS", 7: "ROOM_SECRET",
    8: "ROOM_SUPERSECRET", 9: "ROOM_ARCADE", 10: "ROOM_CURSE", 11: "ROOM_CHALLENGE",
    12: "ROOM_LIBRARY", 13: "ROOM_SACRIFICE", 14: "ROOM_DEVIL", 15: "ROOM_ANGEL",
    16: "ROOM_DUNGEON", 17: "ROOM_BOSSRUSH", 18: "ROOM_ISAACS", 19: "ROOM_BARREN",
    20: "ROOM_CHEST", 21: "ROOM_DICE", 22: "ROOM_BLACK_MARKET", 23: "ROOM_GREED_EXIT",
    24: "ROOM_PLANETARIUM", 25: "ROOM_TELEPORTER", 26: "ROOM_TELEPORTER_EXIT",
    27: "ROOM_SECRET_EXIT", 28: "ROOM_BLUE",
}

#: 描述符至少要读这么多字节才能覆盖标量前缀（0x74）而后对齐到 0x10 边界。
DUMP_BYTES = 0x80

# ---- 描述符的实际定位方式（来自 `const Level::GetRoomByIdx` @ 0x3dc3b0 的反汇编）----
# 反汇编原文（关键五条）：
#   mov  w9, #0xa9            ; 169 = 13×13
#   madd w9, w10, w9, w1      ; 槽号表下标 = 维度 × 169 + 索引
#   add  x9, x8, w9, uxtw #2  ; 表项地址（4 字节一项）
#   ldrsw x9, [x9, #0x2d18]   ; 槽号（**有符号**：负数表示"没有这个房间"）
#   add  x8, x8, x9, lsl #8   ; 描述符 = this + 槽号 × 0x100
#   add  x8, x8, #0x18        ;          + 0x18
# ⇒ **描述符是内联在 Level 对象里的**（不是堆上的 vector），并有一张"索引→槽号"表。
# 第一版把它当成 vector 头去找，于是在 `+0x21510` 读到一堆小整数（看起来有值、其实全错）。
DESCRIPTOR_STRIDE = 0x100          # `lsl #8`
DESCRIPTOR_ARRAY_BASE = 0x18       # `add x8, x8, #0x18`
SLOT_TABLE_OFFSET = 0x2D18         # `ldrsw x9, [x9, #0x2d18]`
SLOT_ROOM_COUNT = 169              # `mov w9, #0xa9`，每个维度的房间数（13×13）
#: 槽号为负时引擎返回的"空描述符"：模块里的一个全局对象（`adrp x0, 0xabc000; add x0, x0, #0xa10`）。
EMPTY_DESCRIPTOR_MODULE_OFFSET = 0xABCA10

#: 容器起点到"当前维度"字段的距离（`0x21560 - 0x21510`）。
DIMENSION_FIELD_OFFSET = CURRENT_DIMENSION_OFFSET - DESCRIPTOR_CONTAINER_OFFSET
#: 一组 vector 头（begin/end/capacity）占 24 字节。容器很可能是"每个维度一组"：
#: 3 × 24 = 72 = 0x48，紧接着 0x50 就是当前维度 —— 数字对得上，但**这条推断要在真机上确认**
#: （读到的三组里，只有当前维度那组的 begin/end 应当是合理跨度）。原始字节会落盘，可离线复核。
VECTOR_HEAD_SIZE = 0x18
VECTOR_HEAD_COUNT = 3


#: `Level::GetRoomByIdx`（真实函数体，`0x3dc3b0`）开头两个 8 字节字。
#: 用它当指纹认游戏模块：只有 Repentance 模块里才有这段代码，且我们是从固定版本上抄下来的。
GAME_PROBE_FINGERPRINT = (0xA9017BFDD10103FF, 0xF90013F5910043FD)
GAME_PROBE_OFFSET = 0x3DC3B0
#: `Repentance.nro` 约 11 MB；映射出来的 Rx 段大小应当落在这个区间。
GAME_MODULE_SIZE_RANGE = (8 * 1024 * 1024, 16 * 1024 * 1024)


def locate_game_base_by_scan(session) -> int:
    """`monitor get info` 不给模块表时的兜底：扫映射 + 用函数指纹认游戏模块。

    为什么不复用 `read_device_state_via_gdb.locate_by_scan`：那个同时要认**我们的运行时模块**
    （靠身份函数的字节），于是必须先有一份同版本重建的本地 ELF。本探针完全不依赖运行时，
    所以这里只用游戏自己的代码指纹，少一个前提。
    """
    maps = DEVICE.parse_mappings(session.command("monitor get mappings"))
    low, high = GAME_MODULE_SIZE_RANGE
    candidates = [start for start, end, perms, kind in maps
                  if "x" in perms and low <= end - start <= high and kind != "Stack"]
    if not candidates:
        candidates = [start for start, end, perms, _kind in maps
                      if "x" in perms and GAME_PROBE_OFFSET < end - start]
    print(f"  （`monitor get info` 没给模块表，改用映射扫描：{len(candidates)} 个候选 Rx 段）")
    for start in candidates:
        words = read_words(session, [("fp", start + GAME_PROBE_OFFSET, 2)]).get("fp", [])
        if tuple(words) == GAME_PROBE_FINGERPRINT:
            return start
    return 0


def descriptor_address(game_object: int, dimension: int, room_index: int
                       ) -> tuple[int, int]:
    """返回 `(槽号表项地址, 描述符地址或 0)`。

    槽号要**先读出来**才知道描述符在哪 —— 所以调用方必须先读表项（有符号 4 字节），
    负数表示"这个索引没有房间"（引擎这时返回模块里那个空描述符）。
    """
    slot_entry = game_object + SLOT_TABLE_OFFSET + (dimension * SLOT_ROOM_COUNT + room_index) * 4
    return slot_entry, 0


def descriptor_address_from_slot(game_object: int, slot: int) -> int:
    """描述符地址 = `Level + 0x18 + 槽号 × 0x100`（槽号为负时返回 0，表示"没有"）。"""
    if slot < 0:
        return 0
    return game_object + DESCRIPTOR_ARRAY_BASE + slot * DESCRIPTOR_STRIDE


def plan_addresses(game_base: int, game_pointer: int) -> dict[str, int]:
    """三个地址各自的参照物**不同**，这里写死成一处，免得再搞错：

    * `g_Game` 槽在**模块映像**里（`game_base + 0xAAC698`）—— 它是个指针变量；
    * 描述符容器与"当前房间三件套"在**Game 对象**里（`game_pointer + 0x21510 / +0x21550`）。

    第一版把后两者也按模块基址读了，于是读到的全是**代码字节**（看起来有值、其实毫无意义），
    真机会话白跑一次。这类错误在没有设备时看不出来，所以抽成纯函数由门禁钉住。
    """
    return {
        "game_slot": game_base + GAME_SLOT_OFFSET,
        "container": game_pointer + DESCRIPTOR_CONTAINER_OFFSET,
        "current": game_pointer + CURRENT_ROOM_POINTER_OFFSET,
    }


def read_words(session, requests: list[tuple[str, int, int]]) -> dict[str, list[int]]:
    """按 `(标签, 地址, 8 字节数)` 批量读取，返回标签 → 8 字节字列表。"""
    lines: list[str] = []
    for label, address, count in requests:
        lines.append(f'echo TAG {label}\\n')
        lines.append(f'x/{count}gx 0x{address:x}')
    # `parse_words` 已经把输出按 `echo TAG <名字>` 分好组，直接按标签取即可
    # （不要按下标对位 —— 那是"看起来对、错位了也不知道"的写法）。
    values = DEVICE.parse_words(session.command('\n'.join(lines)))
    return {label: values.get(label, []) for label, _address, _count in requests}


def words_to_bytes(words: list[int]) -> bytes:
    out = bytearray()
    for word in words:
        out += word.to_bytes(8, "little")
    return bytes(out)


def u32(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 4], "little")


def u64(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 8], "little")


def guess_strides(begin: int, end: int, stride: int | None) -> list[dict[str, int]]:
    """从 begin/end 之差推 `sizeof(RoomDescriptor)` 的候选。

    容器是 `std::vector<RoomDescriptor>` 形态时，`end - begin` 就是"元素个数 × 步长"。
    不知道步长时，列出所有"8 字节对齐、且元素个数落在 1..1024"的除数，供人判断。
    """
    span = end - begin
    if span <= 0:
        return []
    candidates = []
    for step in range(8, min(span, 0x1000) + 1, 8):
        if span % step == 0 and 1 <= span // step <= 1024:
            candidates.append({"stride": step, "count": span // step})
    return candidates[:40]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default="192.168.124.11")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--game-base", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--stride", type=lambda s: int(s, 0), default=0,
                    help="描述符步长（sizeof(RoomDescriptor)）；不给则只列出候选")
    ap.add_argument("--indices", default="", help="额外要 dump 的房间索引，逗号分隔")
    ap.add_argument("--scan", type=int, default=0,
                    help="扫描前 N 个槽位的描述符（用来找『哪个槽是当前房间』；一次 attach 拿全）")
    ap.add_argument("--find", type=lambda s: int(s, 0), default=None,
                    help="在扫描结果里找这个值（默认=当前房间索引）出现在哪些槽位的哪些偏移上")
    ap.add_argument("--out", default="", help="把原始读数与解析结果落盘（JSON）")
    ap.add_argument("--note", default="", help="给这次读数起的标签（例如 clear=no / room=start）")
    ap.add_argument("--from-raw", default="", help="离线重放一次已落盘的读数（不连设备）")
    ap.add_argument("--compare", default="",
                    help="与另一份落盘读数对比（离线，不连设备）：逐槽逐偏移列出变化")
    ap.add_argument("--dry-run", action="store_true", help="只测 gdb 管道，不连设备")
    args = ap.parse_args(argv)

    if args.from_raw:
        parsed = json.loads(pathlib.Path(args.from_raw).read_text(encoding="utf-8"))
        if args.compare:
            other = json.loads(pathlib.Path(args.compare).read_text(encoding="utf-8"))
            return compare(parsed, other)
        return report(parsed)
    if args.dry_run:
        session = DEVICE.GdbSession(timeout=60)
        try:
            print(session.command("echo @@管道_ok@@").strip())
        finally:
            session.close()
        return 0

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
        game_base = args.game_base
        if not game_base:
            _module, _plugin, found_game = DEVICE.parse_bases(session.command("monitor get info"))
            game_base = found_game or 0
        if not game_base:
            game_base = locate_game_base_by_scan(session)
        if not game_base:
            print("没拿到游戏模块基址：可以用 --game-base 指定（先跑 `monitor get mappings` 自己看）。")
            return 3
        print(f"游戏模块基址 = 0x{game_base:x}")

        # ---- 第一批：模块映像里的 `g_Game` 槽（指针变量）----
        slot = read_words(session, [("game_slot", game_base + GAME_SLOT_OFFSET, 1)])
        game_pointer = slot["game_slot"][0] if slot["game_slot"] else 0
        if not game_pointer:
            print("`g_Game` 槽是 0 —— 游戏可能还没进到有 Game 对象的状态（请在游戏内再跑一次）。")
            return 3
        # ---- 第二批：`g_Game` 槽里存的**不是 Game 对象本身**，而是再一级指针 ----
        # 依据是反汇编：`adrp x21, 0xaac000; ldr x21, [x21,#0x698]` 之后还有一次 `ldr x9, [x21]`。
        # 第一版少解了这一层，拿到的其实是"持有者对象"（它 +0x21550 一带全是 0）。
        slot_owner = read_words(session, [("owner", game_pointer, 1)]).get("owner", [])
        owner = slot_owner[0] if slot_owner else 0
        game_object = owner or game_pointer
        # ---- 第三批：Level 对象里的"当前房间三件套"（**相对 Game 指针**）----
        addresses = plan_addresses(game_base, game_object)
        first = read_words(session, [("current", addresses["current"], 6)])
        current = words_to_bytes(first["current"])
        room_pointer = u64(current, 0)
        room_index = u32(current, CURRENT_ROOM_INDEX_OFFSET - CURRENT_ROOM_POINTER_OFFSET)
        dimension = u32(current, CURRENT_DIMENSION_OFFSET - CURRENT_ROOM_POINTER_OFFSET)
        print(f"`g_Game` 槽内容 = 0x{game_pointer:x}；再解一层 = 0x{game_object:x}")
        print(f"Game* = 0x{game_object:x}；当前 Room* = 0x{room_pointer:x}")
        print(f"当前房间索引 = {room_index}；维度 = {dimension}")

        # ---- 第四批：先读"索引→槽号"表项，再按槽号定位描述符（见上方常量处的反汇编）----
        slot_entry, _unused = descriptor_address(game_object, dimension, room_index)
        slot_words = read_words(session, [("slot", slot_entry, 1)]).get("slot", [])
        slot = 0
        if slot_words:
            raw = slot_words[0] & 0xFFFFFFFF
            slot = raw - (1 << 32) if raw & 0x80000000 else raw   # `ldrsw`：有符号
        descriptor = descriptor_address_from_slot(game_object, slot)
        print(f"槽号表项 Game+0x{slot_entry - game_object:x} = {slot}")
        print(f"描述符 @ Game+0x{descriptor - game_object:x}" if descriptor
              else "槽号为负 ⇒ 引擎此时返回模块里那个全局空描述符")

        dumps: dict[str, list[int]] = {}
        types: dict[str, int] = {}
        data_type = -1
        # ---- 可选：扫描前 N 个槽位。为什么要它：槽号表给出的那个"当前房间"与地面真值
        # （用户站在起始房间、网格正中 84）对不上，于是改用引擎自己的办法——**直接扫数组**，
        # 看哪个槽位的候选字段等于当前索引。一次 attach 把线索全带回来，离线慢慢解释。
        if args.scan:
            count_words = read_words(session, [("count", game_object + 0x21510, 1)])
            slot_count = count_words.get("count", [0])[0] & 0xFFFFFFFF
            scan_slots = max(1, min(args.scan, 256))
            requests = [(f"slot{i}", game_object + DESCRIPTOR_ARRAY_BASE + i * DESCRIPTOR_STRIDE,
                         DUMP_BYTES // 8) for i in range(scan_slots)]
            dumps.update(read_words(session, requests))
            print(f"数组元素计数（Game+0x21510）= {slot_count}；已扫前 {scan_slots} 个槽位")
            table = read_words(session, [("slot_table", game_object + SLOT_TABLE_OFFSET,
                                          (SLOT_ROOM_COUNT + 1) // 2)])
            table_blob = words_to_bytes(table.get("slot_table", []))
            entries = [int.from_bytes(table_blob[i*4:i*4+4], "little", signed=True)
                       for i in range(SLOT_ROOM_COUNT)]
            print(f"索引→槽号表前 16 项：{entries[:16]}")
            print(f"索引 {room_index} → 槽号 {entries[room_index] if room_index < len(entries) else '?'}")
            pending_table = entries
            # 链式读：描述符 +0x10 是 Data 指针，顺着它读 Data+0x8（Type）。
            # 那一段（0x3dc624）的引擎代码确实把 `Data.Type` 与常量 1（`ROOM_DEFAULT`）比较，
            # 但**普通房在一层楼里有很多个**，所以 Type 只能用来交叉核对
            # "这一槽确实是普通房"，**不能**用来指认哪个槽是当前房间 —— 早期版本把它当成
            # "起始房间"标记，结果把四个槽位都标上了，是会误导判断的错标签。认当前房间要看 +0x00。
            data_requests = []
            for label, words in dumps.items():
                if not label.startswith("slot"):
                    continue
                blob = words_to_bytes(words)
                pointer = u64(blob, 0x10)
                if 0x1000 < pointer < (1 << 48):
                    data_requests.append((f"type_{label}", pointer + 0x08, 1))
            if data_requests:
                typed = read_words(session, data_requests)
                for label, words in typed.items():
                    if words:
                        types[label.split("type_", 1)[1]] = words[0] & 0xFFFFFFFF
        else:
            pending_table = []
        if descriptor and not args.scan:
            reads = [("desc_current", descriptor, DUMP_BYTES // 8)]
            for piece in (args.indices or "").split(","):
                piece = piece.strip()
                if not piece:
                    continue
                other_index = int(piece, 0)
                other_entry, _ = descriptor_address(game_object, dimension, other_index)
                label = f"slot_{other_index}"
                raw_other = read_words(session, [(label, other_entry, 1)]).get(label, [0])[0]
                other_slot = raw_other & 0xFFFFFFFF
                if other_slot & 0x80000000:
                    other_slot -= 1 << 32
                other_descriptor = descriptor_address_from_slot(game_object, other_slot)
                if other_descriptor:
                    reads.append((f"desc_{other_index}", other_descriptor, DUMP_BYTES // 8))
            dumps = read_words(session, reads)
            words = dumps.get("desc_current", [])
            if words:
                blob = words_to_bytes(words)
                data_pointer = u64(blob, 0x10)
                print(f"当前描述符的 Data（+0x10）= 0x{data_pointer:x}")
                if data_pointer:
                    # 顺着 Data 读 `Data+0x8`（布局表里已 confirmed 的 Type）——
                    # 用它给"描述符定位对不对"做**独立交叉验证**：起始房间应当是普通房（Type=1）。
                    type_words = read_words(session, [("type", data_pointer + 0x08, 1)])
                    data_type = type_words.get("type", [0])[0] & 0xFFFFFFFF
                    print(f"Data.Type（Data+0x8）= {data_type}"
                          f"（1=ROOM_DEFAULT、4=ROOM_TREASURE）")
    finally:
        # **任何路径都要先 detach**：带着 attach 状态 quit，可能把游戏进程留在暂停态、
        # 甚至被 gdb 杀掉 —— 那是"为了读一次数把玩家的游戏弄死"，绝不能发生。
        if attached:
            try:
                session.command("detach")
            except Exception as error:  # noqa: BLE001 - detach 失败必须说出来
                print(f"警告：detach 失败（{error}）；若游戏卡住，重启设备即可。", file=sys.stderr)
        session.close()

    parsed = {
        "note": args.note,
        "game_base": game_base,
        "game_slot_value": game_pointer,
        "game": game_object,
        "room_pointer": room_pointer,
        "slot": slot,
        "descriptor": descriptor,
        "data_type": data_type,
        "slot_table": pending_table,
        "room_index": room_index,
        "dimension": dimension,
        "descriptors": {
            label: [
                {"offset": offset, "u32": u32(words_to_bytes(words), offset)}
                for offset in CANDIDATE_FIELD_OFFSETS
            ]
            for label, words in dumps.items() if label.startswith("desc_")
        },
        "types": types,
        "raw_words": {label: words for label, words in dumps.items()},
    }
    if args.out:
        pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.out).write_text(json.dumps(parsed, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        print(f"已落盘：{args.out}（后续可用 --from-raw 离线重放）")
    return report(parsed)


def compare(left: dict, right: dict) -> int:
    """对比两次读数：逐槽位、逐候选偏移列出**变了**的字段。

    这是给 `Clear` 定名用的手段：同一个房间在"未清/已清"两次读数里，只有它那个布尔字段会翻转。
    匹配槽位按 `+0x00`（= GridIndex）——因为槽号本身会随房间分配变化，而网格下标是稳定的。
    """
    def snapshot(parsed: dict) -> dict[int, dict[int, int]]:
        out: dict[int, dict[int, int]] = {}
        for label, words in (parsed.get("raw_words") or {}).items():
            if not label.startswith("slot") or label.startswith("slot_table"):
                continue
            blob = words_to_bytes(words)
            grid = u32(blob, 0x00)
            if grid == 0xFFFFFFFF:
                continue
            out[grid] = {offset: u32(blob, offset) for offset in CANDIDATE_FIELD_OFFSETS
                         if offset + 4 <= len(blob)}
        return out

    a, b = snapshot(left), snapshot(right)
    print(f"对比：{left.get('note') or 'A'} → {right.get('note') or 'B'}")
    print(f"  网格下标集合：A={sorted(a)[:12]}…  B={sorted(b)[:12]}…")
    changed = False
    for grid in sorted(set(a) & set(b)):
        for offset in CANDIDATE_FIELD_OFFSETS:
            before, after = a[grid].get(offset), b[grid].get(offset)
            if before is None or after is None or before == after:
                continue
            changed = True
            print(f"  房({grid}) 偏移 0x{offset:02x}：{before} → {after}")
        if a[grid].get(0x10) != b[grid].get(0x10) or a[grid].get(0x00) != b[grid].get(0x00):
            pass
    if not changed:
        print("  （两次读数在共同房间上没有字段变化）")
    print("\n解读：翻转 0↔1 的那个偏移就是 `Clear` 的候选；同时变化的其它偏移要单独解释。")
    return 0


def report(parsed: dict) -> int:
    """把一次读数渲染成人看的东西：定位链 + 候选字段（含跨槽位对照）。"""
    print(f"\n== 读数 {parsed.get('note') or ''} ==")
    print(f"当前房间索引 = {parsed['room_index']}（维度 {parsed['dimension']}）；槽号 = {parsed.get('slot')}")

    def fields(label: str) -> dict[int, int]:
        words = (parsed.get("raw_words") or {}).get(label) or []
        blob = words_to_bytes(words)
        return {offset: u32(blob, offset) for offset in CANDIDATE_FIELD_OFFSETS
                if offset + 4 <= len(blob)}

    raw = parsed.get("raw_words") or {}
    scanned = {label: fields(label) for label in raw if label.startswith("slot")
               and not label.startswith("slot_table")}
    types = parsed.get("types") or {}
    current = fields("desc_current")
    descriptor = parsed.get("descriptor") or 0

    if not scanned and not current:
        print("（这次读数没有落盘的描述符字节）")
        return 0

    if current:
        print(f"描述符 @ 0x{descriptor:x}（= Game + 0x{descriptor - parsed['game']:x}）")
        if parsed.get("data_type", -1) >= 0:
            print(f"Data.Type = {parsed['data_type']}"
                  f"（1=ROOM_DEFAULT 4=ROOM_TREASURE 5=ROOM_BOSS）")

    if scanned:
        target = parsed["room_index"]
        print(f"\n扫描到的槽位（Type 由描述符 +0x10 → Data+0x8 链式读出）：")
        print(f"{'槽位':>8} {'Type':>5} {'名称':<18} {'0x00':>6} {'0x04':>6} {'0x0c':>8} {'0x48':>6} {'0x64':>12}")
        for label in sorted(scanned, key=lambda item: int(item[4:])):
            table = scanned[label]
            kind = types.get(label, -1)
            name = ROOM_TYPE_NAMES.get(kind, "?")
            mark = ""
            if table.get(0x00) == target:
                mark = "  ← 这个槽位的 +0x00 = 当前房间索引"
            print(f"{label:>8} {kind:>5} {name:<18} {table.get(0x00, 0):>6} {table.get(0x04, 0):>6}"
                  f" {table.get(0x0C, 0):>8} {table.get(0x48, 0):>6} {table.get(0x64, 0):>12}{mark}")
        hits = [(label, offset) for label, table in scanned.items()
                for offset, value in table.items() if value == target]
        print(f"\n候选偏移里等于当前索引（{target}）的：")
        for label, offset in hits[:12]:
            print(f"   {label} 的 0x{offset:02x} = {target}")
        if not hits:
            print("   （没有：说明这批候选偏移里不含网格下标，或者当前索引不是网格下标）")

    print("\n判据提醒：")
    print(f"  * 值等于当前房间索引（{parsed['room_index']}）的候选 ⇒ GridIndex/SafeGridIndex 候选；"
          "换房间再跑一次，跟着变的那个才作数。")
    print("  * 清房间前后同一字段 0↔1 翻转 ⇒ Clear。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
