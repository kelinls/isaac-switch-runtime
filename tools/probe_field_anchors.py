#!/usr/bin/env python3
"""**两通道字段验收探针（声明式）** —— 换批次只改一张规格表，不改代码。

## 它解决什么

批次 11–13 的验收做法是"一次 attach 同时取两个通道"：

* **A 通道** = 模组（EID）在游戏内报出来的值 —— 探针脚本用 `error()` 抛一行，运行时把它记进
  `g_LastLuaErrorText`；
* **B 通道** = 引擎内存的**独立读数** —— 从 `g_Game` 槽沿指针链找到对象，按已知偏移读字段；
* 两边逐值比对，不一致就判红；再把 A 的值拿到**对象原始字节**里搜落点（"取证搜索"，
  证明那个值只出现在我们声称的那个偏移上）。

那套东西写死在 `tools/probe_batch13_anchors.py` 里（**换批次要改常量**，台账里一直挂着这条待办）。
这个文件把它拆成两层：

* **引擎**（本文件）：attach 一次、自校验身份锚点、按规格解析指针链、读字段、比对、取证搜索、
  原始读数落盘 + `--from-raw` 离线重放；
* **规格**（`tools/probe_specs/<批次>.json`，纯数据）：链怎么走、每条链 dump 多少字节、
  要读哪些字段（偏移/宽度/有无符号/对应 A 通道的哪个键）。

## 用法

    # 真机取数（游戏要跑着；一次 attach 读完）
    python3 tools/probe_field_anchors.py --spec tools/probe_specs/fields2.json \
        --out dist/fields2-anchors.json --note "字段缺口第二批"
    # 离线重放（改解码/看结论，不再连设备）
    python3 tools/probe_field_anchors.py --from-raw dist/fields2-anchors.json

退出码：0 全部一致；1 有判红；3 环境问题（没进程/拿不到基址/规格不合法）。

## 规格里链的走法（`steps`，按顺序求值，`reader` 是注入点便于门禁假读）

| step | 含义 |
| --- | --- |
| `["slot_ptr", off]` | 读**模块**+off 处的 8 字节 = 某个全局变量的**地址**（不是它的值） |
| `["deref_ptr"]` | 读当前地址处的 8 字节，当成新地址（解一层指针） |
| `["add", off]` | 当前地址 + off（内嵌对象用加法，不是解引用） |
| `["load_ptr", off]` | 读当前地址+off 处的 8 字节，当成新地址 |
| `["vector_elem", i]` | `begin` 处第 i 个元素（步长 8）→ 新地址 |
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import struct
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

#: 身份锚点：设备上这三个函数的头 16 字节必须与本地 ELF 逐字节相同，
#: 否则"设备上是不是这份构建"就没被证明，读数不能当本次证据。
ANCHOR_SYMBOLS = ("identity", "exl_main", "manifest_install")
#: A 通道需要的符号（运行时把 Lua 错误文本记在这两处）。
LUA_SYMBOLS = ("g_LastLuaErrorLength", "g_LastLuaErrorText")
#: `g_LastLuaErrorText` 的容量（运行时按 256 字节记，见教训清单）。
LUA_TEXT_WORDS = 32

#: A 通道的行格式：`[tag] k=v k=v …`（tag 由规格给，避免和别的探针混）。
LUA_LINE_RE = re.compile(r"\[(?P<tag>[A-Za-z0-9_]+)\]\s*(?P<body>.*)", re.S)
#: 键名允许**数字开头**：探针脚本为了省那 256 字节会把键写成 `1q`（第 1 个条目的 Quality）
#: 这种压缩形式（实测：写全称 `sad_onion_quality=` 几次就把缓冲写满并被截断）。
PAIR_RE = re.compile(r"([A-Za-z0-9_]+)\s*=\s*(\S+)")


# ----------------------------------------------------------------------------------
# 纯函数层（门禁直接钉这些；不碰设备）
# ----------------------------------------------------------------------------------
def parse_lua_fields(text: str, tag: str) -> dict[str, str]:
    """从运行时记录的 Lua 错误文本里取 `[tag] k=v …` 的键值对。

    ⚠ 两个实测坑（都属于"读数工具骗人"）：
      1. 那块缓冲**256 字节且不清零** —— 新错误更短时**尾巴上留着上一次更长的文本**
         ⇒ 只认**最后一个 `[tag]` 标记**之后、到行尾为止的那一段
         （实现上不能用 `\\[tag\\].*` 配 DOTALL：那样第一个匹配就会把后面所有内容一起吞掉，
         实测就是这么把上一次的键值当成这一次读出来的）；
      2. 只有**最后一次**错误会被保留 ⇒ 探针要一次把要报的值都写完（并控制长度）。
    """
    if not text:
        return {}
    marker = f"[{tag}]"
    index = text.rfind(marker)
    if index < 0:
        return {}
    body = text[index + len(marker):]
    body = body.split("\n", 1)[0]                  # 只认这一行
    body = body.split("[", 1)[0]                   # 撞到下一个标记就停
    return {key: value for key, value in PAIR_RE.findall(body)}


def parse_float(token: str):
    """把 A 通道报出来的**浮点**文本解析成 float（`4.25` / `-0.5`）；解析不了返回 None。

    与 `parse_number` 同一套"只取前导"的防粘尾规则（缓冲区不清零）。规格里凡是
    `"kind": "float"` 的字段都走这条路。
    """
    token = token.strip().rstrip(",")
    match = re.match(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", token)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_number(token: str):
    """把 `0x…` / 十进制 / `true`/`false` 解析成整数；解析不了返回 None。

    ⚠ `%X` 这类格式化会打出**没有 `0x` 前缀的十六进制**，看起来像十进制 ——
    所以规格与探针脚本**一律带 `0x` 前缀**，这里也只按带前缀的当十六进制。
    """
    token = token.strip().rstrip(",")
    if token in ("true", "false"):
        return 1 if token == "true" else 0
    # ⚠ 缓冲区不清零 ⇒ 值的尾巴可能粘着上一次错误的文本（实测读到 `0x0o file 'rom:…'`：
    #   我们的 `0x0` 后面紧跟残留的 `o`）⇒ 只取**前导**的数字串，后面的尾巴一律丢掉。
    prefix = re.match(r"[-+]?(0[xX][0-9A-Fa-f]+|[0-9]+)", token)
    token = prefix.group(0) if prefix else token
    try:
        if token.lower().startswith(("0x", "-0x")):
            return int(token, 16)
        return int(token, 10)
    except ValueError:
        return None


def truncate_width(word: int, width: int, signed: bool) -> int:
    """把 `x/1gx` 读来的 8 字节按字段宽度截断。

    ⚠ 读 4 字节变量必须截断：`x/1gx` 会把**相邻变量**一起读进来（实测把 `FailureDetail`
    读成 `0x3_0000_0005`）。有符号按符号扩展，无符号零扩展。
    """
    if width == 8:
        value = word & 0xFFFFFFFFFFFFFFFF
    elif width == 4:
        value = word & 0xFFFFFFFF
    elif width == 2:
        value = word & 0xFFFF
    elif width == 1:
        value = word & 0xFF
    else:
        raise ValueError(f"不支持的字段宽度 {width}")
    if signed and width < 8 and value & (1 << (width * 8 - 1)):
        value -= 1 << (width * 8)
    return value


def resolve_chain(reader, module_base: int, steps: list, game_base: int = 0) -> int:
    """按规格走一条链，返回对象地址；任何一步读不到就返回 0。

    `reader(address) -> int`：读该地址处的 8 字节（0 表示读不到）；门禁注入假读数即可离线测。

    ⚠ **两种槽别混**：全局对象的槽（`g_Game` `0xAAC698`、`g_Manager` `0xAAC648`）在
    **游戏模块**里，而运行时自己的全局在**我们的模块**里 —— 用错基址会读出漂亮的垃圾。
      * `slot_ptr`：**运行时模块** + off（运行时自己的全局）；
      * `game_slot_ptr`：**游戏模块** + off（游戏自己的全局，解一层才是对象）。
    """
    current = 0
    for index, step in enumerate(steps):
        kind = step[0]
        if kind == "slot_ptr":
            current = reader(module_base + int(step[1], 0))
        elif kind == "game_slot_ptr":
            current = reader(game_base + int(step[1], 0)) if game_base else 0
        elif kind == "deref_ptr":
            current = reader(current) if current else 0
        elif kind == "add":
            current = current + int(step[1], 0) if current else 0
        elif kind == "load_ptr":
            current = reader(current + int(step[1], 0)) if current else 0
        elif kind == "vector_elem":
            current = reader(current + int(step[1]) * 8) if current else 0
        else:
            raise ValueError(f"规格里第 {index} 步的 kind 不认识：{kind}")
        if not current:
            return 0
    return current


def compare_rows(lua: dict[str, str], engine: dict[str, int], targets: list) -> tuple[list, list]:
    """逐条比对 A/B 两通道。返回 `(通过, 判红)`，每条都是带说明的 dict。"""
    passed, failed = [], []
    for target in targets:
        name = target["name"]
        key = target.get("lua_key", "")
        engine_value = engine.get(name)
        is_float = target.get("kind") == "float"
        if key:
            lua_value = parse_float(lua.get(key, "")) if is_float else parse_number(lua.get(key, ""))
        else:
            lua_value = None
        row = {"name": name, "chain": target.get("chain", ""), "offset": target.get("offset", ""),
               "lua_key": key, "lua": lua_value, "engine": engine_value}
        if key and lua_value is None:
            row["why"] = f"A 通道没报 `{key}`（探针脚本没跑 / 被 256 字节截断 / 键名写错）"
            failed.append(row)
        elif engine_value is None:
            row["why"] = "B 通道读不到（链走断了或字段在对象外）"
            failed.append(row)
        elif not key:
            passed.append(row)                     # 只登记、不比对（规格里没给 lua_key）
        elif is_float and abs(float(lua_value) - float(engine_value)) > 1e-3:
            # 浮点走**容差**（1e-3）：A 通道报的是十进制文本，逐位相等没有意义；
            # 但字段读错位置/读错对象时差值必然远大于 1e-3，判别力不受影响。
            row["why"] = "两通道**不一致**（超出浮点容差 1e-3）"
            failed.append(row)
        elif not is_float and lua_value != engine_value:
            row["why"] = "两通道**不一致**"
            failed.append(row)
        else:
            passed.append(row)
    return passed, failed


def forensic_search(blob: bytes, value: int, width: int) -> list[int]:
    """在对象的原始字节里搜这个值出现在哪些偏移 —— "取证搜索"。

    这不是"我们读它也读同一处"的自证：只有在**对象里唯一的落点**就是规格声称的那个偏移时，
    才说明读对了地方。
    """
    needle_masks = {4: 0xFFFFFFFF, 2: 0xFFFF, 1: 0xFF, 8: 0xFFFFFFFFFFFFFFFF}
    mask = needle_masks[width]
    wanted = value & mask
    hits = []
    for offset in range(0, max(0, len(blob) - width + 1)):
        if int.from_bytes(blob[offset:offset + width], "little") & mask == wanted:
            hits.append(offset)
    return hits


def load_spec(path: pathlib.Path) -> dict:
    """读规格并做最小校验 —— 规格是数据，坏了要在**连设备之前**就吵。"""
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("schema") != "isaac-probe-spec/1":
        raise ValueError(f"规格 schema 不认识：{spec.get('schema')!r}")
    if not spec.get("tag"):
        raise ValueError("规格缺 `tag`（A 通道的行标签）")
    chains = spec.get("chains") or {}
    if not chains:
        raise ValueError("规格里没有任何链")
    for name, chain in chains.items():
        if not chain.get("steps"):
            raise ValueError(f"链 {name} 没有 steps")
        if not chain.get("dump_bytes"):
            raise ValueError(f"链 {name} 没有 dump_bytes")
    for target in spec.get("targets") or []:
        if target.get("chain") not in chains:
            raise ValueError(f"字段 {target.get('name')} 指向不存在的链 {target.get('chain')!r}")
        if int(target.get("width", 0)) not in (1, 2, 4, 8):
            raise ValueError(f"字段 {target.get('name')} 的宽度不合法")
        # `kind: "float"`（2026-09-16 加）：引擎里就是 IEEE-754 的字段（例如
        # `EntityPlayer.Damage/MoveSpeed/MaxFireDelay/TearRange` 四个属性都是 float）。
        # 只允许 4/8 字节；比对时按**容差**而不是逐位相等（A 通道报的是十进制文本）。
        if target.get("kind") == "float" and int(target.get("width", 0)) not in (4, 8):
            raise ValueError(f"字段 {target.get('name')} 标了 float 但宽度不是 4/8")
    return spec


def words_to_bytes(words: list[int]) -> bytes:
    out = bytearray()
    for word in words:
        out += int(word).to_bytes(8, "little")
    return bytes(out)


# ----------------------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------------------
def build_engine_values(raw: dict, spec: dict) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """从原始读数里算 B 通道的值。返回 `(值, 链地址, 链 dump 字数)`。"""
    chains = raw.get("chains") or {}
    values: dict[str, int] = {}
    addresses: dict[str, int] = {}
    for name, chain in chains.items():
        addresses[name] = int(chain.get("address") or 0)
    for target in spec["targets"]:
        chain = chains.get(target["chain"]) or {}
        offset = int(str(target["offset"]), 0)
        words = chain.get("reads", {}).get(str(offset)) or chain.get("reads", {}).get(offset)
        if not words:
            continue
        if target.get("kind") == "float":
            # IEEE-754：把 4/8 字节原样解释成浮点（不是截位整数）。
            width = int(target["width"])
            values[target["name"]] = struct.unpack("<f" if width == 4 else "<d",
                                                   int(words[0]).to_bytes(8, "little")[:width])[0]
            continue
        values[target["name"]] = truncate_width(int(words[0]), int(target["width"]),
                                                bool(target.get("signed")))
    return values, addresses, {name: len(chain.get("dump_words") or [])
                               for name, chain in chains.items()}


def report(raw: dict, spec: dict) -> int:
    """离线重放：拿原始读数打分（改解码不用再连设备）。"""
    lua = parse_lua_fields(raw.get("lua_error_text", ""), spec["tag"])
    engine, addresses, _ = build_engine_values(raw, spec)
    passed, failed = compare_rows(lua, engine, spec["targets"])

    print(f"批次：{spec.get('batch', '?')}（规格 {spec.get('spec_path', '?')}）")
    print(f"身份锚点自校验：{'通过' if raw.get('anchors_ok') else '**不通过**（设备上可能不是这份构建）'}")
    print(f"A 通道原文：{raw.get('lua_error_text', '')!r}")
    print(f"链地址：{ {k: hex(v) for k, v in addresses.items()} }")
    print(f"逐条对照：通过 {len(passed)} 条、判红 {len(failed)} 条")
    for row in spec["targets"]:
        hit = next((item for item in passed + failed if item["name"] == row["name"]), None)
        if hit is None:
            print(f"  ⏭  {row['name']:18} 没读到（链断或规格里没标 lua_key）")
            continue
        mark = "✅" if hit in passed else "❌"
        print(f"  {mark} {row['name']:18} 链={hit['chain']:6} +{hit['offset']:>6}  "
              f"A={hit['lua']}  B={hit['engine']}"
              + (f"   ← {hit['why']}" if hit in failed else ""))

    # 取证搜索：A 报的值在对象字节里的落点，与规格声称的偏移对照。
    print("\n取证搜索（A 通道的值在对象原始字节里的落点）：")
    for target in spec["targets"]:
        chain = (raw.get("chains") or {}).get(target["chain"]) or {}
        blob = words_to_bytes(chain.get("dump_words") or [])
        if not blob:
            continue
        lua_value = parse_number(lua.get(target.get("lua_key", ""), "")) if target.get("lua_key") else None
        if lua_value is None:
            continue
        hits = forensic_search(blob, lua_value, int(target["width"]))
        claimed = int(str(target["offset"]), 0)
        verdict = "落点含声称的偏移 ✅" if claimed in hits else "**落点不含声称的偏移 ❌**"
        if len(hits) > 8:
            # 值很小（尤其 0）时对象里到处都是它 ⇒ 这条检查对**这个值**没有区分力，
            # 老实说出来，而不是打一行几百个偏移看着像"查过了"。
            shown = "、".join(hex(hit) for hit in hits[:8])
            print(f"  {target['name']:18} 值={lua_value:>4}  落点 {len(hits)} 处（太多，无区分力；"
                  f"前 8 个={shown}）  {verdict}")
        else:
            print(f"  {target['name']:18} 值={lua_value:>4}  落点={[hex(h) for h in hits]}  {verdict}")

    if raw.get("note"):
        print(f"\n备注：{raw['note']}")
    if not raw.get("anchors_ok"):
        return 1
    return 0 if not failed else 1


# ----------------------------------------------------------------------------------
# 真机取数
# ----------------------------------------------------------------------------------
def collect(args, spec: dict, elf: pathlib.Path) -> dict:
    missing = DEVICE.sync_syms_from_elf(elf, verbose=True)
    syms = DEVICE.SYMS
    needed = list(ANCHOR_SYMBOLS) + list(LUA_SYMBOLS)
    absent = [key for key in needed if key not in syms]
    if absent:
        print(f"本地 ELF 里没解析到这些符号，读数无法进行：{absent}（其余未解析：{missing}）")
        raise SystemExit(3)

    session = DEVICE.GdbSession()
    attached = False
    try:
        session.command(f"target extended-remote {args.ip}:22225")
        pid = args.pid or DEVICE.parse_processes(session.command("info os processes")) or 0
        if not pid:
            print("没找到正在运行的 Application —— 请先把游戏启动到游戏内。")
            raise SystemExit(3)
        print(f"进程 = {pid}（本次只 attach 一次）")
        session.command(f"attach {pid}")
        attached = True

        base, game_base = args.base, args.game_base
        monitor_text = ""
        if not base or not game_base:
            monitor_text = session.command("monitor get info")
            module, _plugin, found_game = DEVICE.parse_bases(monitor_text)
            base = base or (module or 0)
            game_base = game_base or (found_game or 0)
        if not base or not game_base:
            # `monitor get info` 会抖（2026-09-16 实测连着两次返回空）⇒ 补问 + 内容指纹兜底。
            for attempt in range(2, 4):
                print(f"  monitor get info 没给出模块表（第 {attempt} 次重问）")
                time.sleep(2)
                monitor_text = session.command("monitor get info")
                module, _plugin, found_game = DEVICE.parse_bases(monitor_text)
                base = base or (module or 0)
                game_base = game_base or (found_game or 0)
                if base and game_base:
                    break
        if not base or not game_base:
            scan_base, scan_game = DEVICE.locate_by_scan(session, elf)
            base = base or scan_base or 0
            game_base = game_base or scan_game or 0
            if not base or not game_base:
                print("  提示：`monitor get mappings` 的输出**会被截断**（实测只列到 0x35be…，"
                      "而游戏模块在 0x3942…）⇒ 扫映射表这条兜底不可靠；同进程没重启时直接用"
                      "上一份读数的 `--base/--game-base`（身份锚点会自校验）。")
        if not base or not game_base:
            print("没拿到模块基址 —— 可用 --base/--game-base 指定。")
            raise SystemExit(3)
        print(f"运行时模块基址 = 0x{base:x}；游戏模块基址 = 0x{game_base:x}")

        raw_words: dict[str, list[int]] = {}

        def read(requests: list[tuple[str, int, int]]) -> None:
            requests = [item for item in requests if item[2] > 0]
            if not requests:
                return
            lines: list[str] = []
            for label, address, count in requests:
                lines.append(f"echo TAG {label}\\n")
                lines.append(f"x/{count}gx 0x{address:x}")
            values = DEVICE.parse_words(session.command("\n".join(lines)))
            for label, _address, _count in requests:
                raw_words[label] = values.get(label, [])

        def first(label: str, index: int = 0) -> int:
            words = raw_words.get(label) or []
            return words[index] if len(words) > index else 0

        def reader(address: int) -> int:
            """链求值用的单次读（一次连接只做一件事这里不适用：读数都在一次 attach 里）。"""
            label = f"deref_{address & 0xFFFFFFFFFFFF:x}"
            read([(label, address, 1)])
            return first(label)

        # 身份锚点 + A 通道
        read([("anchors", base + syms["identity"], 2),
              ("anchor_exl_main", base + syms["exl_main"], 2),
              ("anchor_manifest", base + syms["manifest_install"], 2),
              ("lua_error_text", base + syms["g_LastLuaErrorText"], LUA_TEXT_WORDS)])
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

        # 按规格走链、读字段、dump 对象
        chains: dict[str, dict] = {}
        for name, chain_spec in spec["chains"].items():
            address = resolve_chain(reader, base, chain_spec["steps"], game_base)
            print(f"  链 {name}（{chain_spec.get('label', '')}）= "
                  f"{hex(address) if address else '0（走断了）'}")
            entry = {"address": address, "reads": {}, "dump_words": []}
            if address:
                offsets = sorted({int(str(t["offset"]), 0) for t in spec["targets"]
                                  if t["chain"] == name})
                words = (int(chain_spec["dump_bytes"]) + 7) // 8
                read([("chain_dump", address, words)])
                entry["dump_words"] = raw_words.get("chain_dump", [])
                read([(f"field_{offset:x}", address + offset, 1) for offset in offsets])
                entry["reads"] = {offset: raw_words.get(f"field_{offset:x}", []) for offset in offsets}
            chains[name] = entry

        session.command("detach")
        attached = False
    finally:
        if attached:
            try:
                session.command("detach")
            except Exception:
                pass
        session.close()

    return {"note": args.note, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "spec": spec.get("batch", ""), "base": base, "game_base": game_base,
            "anchors_ok": anchors_ok, "lua_error_text": lua_text, "chains": chains}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", default="tools/probe_specs/fields2.json")
    ap.add_argument("--ip", default="192.168.124.11")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0, help="运行时模块基址")
    ap.add_argument("--game-base", type=lambda s: int(s, 0), default=0, help="游戏模块基址")
    ap.add_argument("--elf", default=DEVICE.DEFAULT_ELF)
    ap.add_argument("--out", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--from-raw", default="", help="离线重放一次已落盘的读数（不连设备）")
    ap.add_argument("--dry-run", action="store_true", help="只测 gdb 管道，不连设备")
    args = ap.parse_args(argv)

    spec_path = pathlib.Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = ROOT / spec_path
    spec = load_spec(spec_path)
    spec["spec_path"] = str(spec_path.relative_to(ROOT))

    if args.from_raw:
        raw = json.loads(pathlib.Path(args.from_raw).read_text(encoding="utf-8"))
        return report(raw, spec)

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

    raw = collect(args, spec, elf)
    out_path = pathlib.Path(args.out) if args.out else (
        ROOT / "dist" / f"field-anchors-{time.strftime('%Y%m%d-%H%M%S')}.json")
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"原始读数已落盘：{out_path}（可用 --from-raw 离线重放）")
    return report(raw, spec)


if __name__ == "__main__":
    sys.exit(main())
