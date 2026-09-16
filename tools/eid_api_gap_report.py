#!/usr/bin/env python3
"""把「某个 PC 模组实际用到的游戏 API」与「我们运行时的实现状态」做交叉核对，产出缺口表。

为什么需要它：这套运行时是 PC Lua API 在 Switch 上的**逐条重实现**，而模组（如 EID）对缺口
几乎没有容忍度——缺一个方法就是"调 nil"，在带条件回调的地方会让**整条描述消失**（潘多拉魔盒的
`Level:IsAltStage` 就是这样），而且不报错。逐个撞太慢，必须先把缺口**静态枚举**出来。

三维验收基准里的前两维在这里落地：
  · ① 存在：模组调用了 `Owner:Name`，我们运行时的 API Catalog / 实现文件里有没有它；
  · ② 语义：PC 对照清单里这条的状态是 `ready`（有真实符号）还是 `Experimental` / `—`（未实现或没底）。

（第三维"调用时机"静态查不了，只能真机对照，另行登记。）

方法面**两条路合起来才算全**：`Owner:Name(`（第 ①②③④ 段）与**链式调用** `a:b():c(`
（第 ⑥ 段，2026-09-16 补 —— 原先只认前者，`Room:GetFrameCount` 就是这么漏掉的）。
字段面在第 ⑤ 段。口径、判据与假阳性来源都写在各段代码注释里，改之前先读。

用法：
    python3 tools/eid_api_gap_report.py                       # 默认扫发布包里的 EID
    python3 tools/eid_api_gap_report.py --mod <模组目录> --json <报告.json>
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MOD = ('dist/isaac-eid-release-20260915/atmosphere/contents/010021C000B6A000/'
               'romfs/isaac_mods/mods/external item descriptions_836319872')
INVENTORY = 'docs/PC-Lua-API-对照清单.md'
#: 机器生成的 PC Lua API 清单（`tools/build_lua_api_inventory.py` 跑出来的）。
#: 与上面那份**人维护**的对照清单互为补充：清单可能漏，inventory 覆盖全（见链式那一段的判据 ②）。
API_INVENTORY = 'analysis/lua-api-inventory/inventory.json'
API_DIRS = ('runtime/src/interfaces/lua', 'runtime/source')

# PC 对照清单里的"状态"列取值（宽松匹配，清单里有多张表、列数不一）
READY_TOKENS = ('ready', 'hostverified', 'exact_class')
WEAK_TOKENS = ('experimental', 'partial', 'stub')


def load_inventory() -> dict[str, str]:
    """从 PC 对照清单里取 {Owner:Name: 状态}。清单有多张表，按行宽松解析。"""
    text = (ROOT / INVENTORY).read_text(encoding='utf-8', errors='replace')
    status: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        names = re.findall(r'`([A-Za-z_][A-Za-z0-9_]*)[:.]([A-Za-z_][A-Za-z0-9_]*)`', line)
        if not names:
            continue
        low = line.lower()
        if any(t in low for t in READY_TOKENS):
            state = 'ready'
        elif any(t in low for t in WEAK_TOKENS):
            state = 'weak'
        elif '| — |' in line or '|—|' in line:
            state = 'missing'
        else:
            state = 'unknown'
        for owner, name in names:
            key = f'{owner}:{name}'
            # 同一条目可能出现在多张表里，取"最弱"的那个状态（保守）
            order = {'ready': 0, 'unknown': 1, 'weak': 2, 'missing': 3}
            if key not in status or order[state] > order[status[key]]:
                status[key] = state
    return status


def canonical_owner(owner: str) -> str:
    """owner 标签的归一口径：去下划线 + 小写。

    为什么需要（2026-09-16 实测）：PC 清单里这个族叫 `ItemConfigItem`，而我们的目录用
    `ItemConfig_Item` —— 两边逐字比对就会把**已经实现**的 `IsTrinket` 判成"缺失"，
    于是账本看起来"还缺 9 条"（其实只缺 4 条）。账本可信是这套验收的地基，所以按归一后比对。
    """
    return owner.replace("_", "").lower()


def load_catalog_entries() -> dict[str, dict[str, str]]:
    """Catalog 里 `Owner:Name` → `{id, maturity}`（`kDefaultApis[]` 之内）。

    需要 `id` 才能与"语义偏离表"（`api_deviation.cpp` 按 id 登记）对上。
    """
    text = (ROOT / 'runtime/src/interfaces/lua/api_catalog.cpp').read_text(encoding='utf-8', errors='replace')
    start = text.find('kDefaultApis[]')
    end = text.find('const ApiCatalog g_defaultCatalog', start)
    body = text[start:end if end > start else len(text)]
    domain_values = {
        'Global': 0, 'Mod': 1, 'Game': 2, 'Level': 3, 'Room': 4, 'ItemPool': 5,
        'Music': 6, 'Rng': 7, 'Persistence': 8, 'Input': 9, 'Diagnostic': 10,
        'Font': 11, 'Vector': 12, 'Sprite': 13, 'Isaac': 14, 'Seed': 15,
    }
    entries: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r'MakeId\(ApiDomain::(\w+),\s*(\d+),\s*(0x[0-9A-Fa-f]+)\).*?'
        r'"([A-Za-z_][A-Za-z0-9_]*)",\s*"([A-Za-z_][A-Za-z0-9_]*)"',
        re.DOTALL,
    )
    for match in pattern.finditer(body):
        domain, group, sequence, owner, name = match.groups()
        if domain not in domain_values:
            continue
        identifier = (domain_values[domain] << 24) | (int(group) << 16) | int(sequence, 16)
        entries[f'{canonical_owner(owner)}:{name}'] = {'id': f'0x{identifier:08X}'}
    return entries


def load_deviations() -> dict[str, dict[str, str]]:
    """`api_deviation.cpp` 的 id → `{kind, text}`。

    这是"② 语义不满足"的**标记来源**：有了它，报告才能区分"实现正确"与
    "实现但有已知偏离"，而不是靠人去读实现处的注释。
    """
    path = ROOT / 'runtime/src/interfaces/lua/api_deviation.cpp'
    if not path.is_file():
        return {}
    text = path.read_text(encoding='utf-8', errors='replace')
    deviations: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r'\{\s*(0x[0-9A-Fa-f]{8})\s*,\s*ApiDeviationKind::(\w+)\s*,\s*((?:\s*"(?:[^"\\]|\\.)*"\s*)+)\}'
    )
    for match in pattern.finditer(text):
        identifier, kind, raw = match.groups()
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
        deviations[f'0x{int(identifier, 16):08X}'] = {'kind': kind, 'text': ''.join(pieces)}
    return deviations


def load_runtime_apis() -> set[str]:
    """**权威口径**：API Catalog 的 `kDefaultApis[]` 里登记过的 `Owner:Name`。

    为什么只认这张表：它是"本运行时暴露哪些 Lua API"的唯一来源（契约测试也在管它）。
    早先版本是"扫源码里所有 `X:Y` 字样"，结果**连注释里提到过的名字**都被当成已实现
    ——`Level:IsAltStage` 就这么被漏报过（清单里明明写着"未实现"）。
    """
    text = (ROOT / 'runtime/src/interfaces/lua/api_catalog.cpp').read_text(encoding='utf-8', errors='replace')
    start = text.find('kDefaultApis[]')
    end = text.find('const ApiCatalog g_defaultCatalog', start)
    body = text[start:end if end > start else len(text)]
    # owner 归一（见 `canonical_owner` 的说明）：PC 清单与我们目录对这个族的拼法不同。
    return {f'{canonical_owner(owner)}:{name}'
            for owner, name in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)",\s*"([A-Za-z_][A-Za-z0-9_]*)"', body)}


def load_mod_definitions(mod: pathlib.Path) -> set[str]:
    """模组**自己定义**的方法（要排除掉，否则会把模组内部函数当成游戏 API）。"""
    defined: set[str] = set()
    for path in mod.rglob('*.lua'):
        text = path.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'function\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:.]\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(', text):
            defined.add(f'{m.group(1)}:{m.group(2)}')
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*[:.]\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*function', text):
            defined.add(f'{m.group(1)}:{m.group(2)}')
    return defined


def load_mod_usage(mod: pathlib.Path) -> collections.Counter:
    """模组里 `Owner:Name(` 形式的调用（只保留"看起来像游戏对象"的 owner）。

    注意这一路**只认"owner 是一个标识符"**的写法。`game:GetRoom():GetFrameCount()` 这种
    "在另一个调用的结果上再调方法"的链式写法由下面 `load_mod_chained_usage()` /
    `iter_chained_calls()` 那一套负责，两边合起来才是完整的方法面。
    """
    usage: collections.Counter = collections.Counter()
    for path in mod.rglob('*.lua'):
        text = path.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(', text):
            usage[f'{m.group(1)}:{m.group(2)}'] += 1
    return usage


# --------------------------------------------------------------------------------------------
# 链式调用枚举（方法面的一部分，2026-09-16 补）
#
# 为什么单开一路：`load_mod_usage()` 认的是 `Owner:Name(` —— **要求 `:` 左边是一个标识符**。
# 模组里 `game:GetRoom():GetFrameCount()` 这种"**在另一个调用的结果上再调方法**"的写法，
# `:` 左边是 `)` 而不是标识符 ⇒ 正则扫不到 ⇒ `Room:GetFrameCount` 这条真实缺口
# **从来没进过任何清单**，直到 2026-09-16 真机上它每帧抛错才被发现
# （EID `features/eid_bagofcrafting.lua:967`，玩家一持有背包合成，EID 的渲染回调就每帧异常、
# 整段道具描述被打断；诊断见 `docs/问题与解决记录.md` 2026-09-16 续十八/续十九）。
#
# **判据（四道，缺一不可）：**
#
#   ① **形态**：`:` / `.` 这个访问器的**接收者以"表达式收尾字符"结束** —— 即 `)`、`]`、`}`。
#      也就是"在上一个表达式（多半是上一次调用）的结果上再取成员"。
#      接收者是标识符的（`player:HasCollectible(...)`）**不在这里**，它们本来就被上面那一路覆盖
#      ⇒ 这一路不是为了"多收一点"，而是为了覆盖一个**原来完全没覆盖的形态**。
#      没有把 `"`、`'` 算进收尾字符：Lua 里 `"x":upper()` 是**语法错误**（字符串是 `exp` 不是
#      `prefixexp`），收进来只会得到"字符串字面量后面恰好跟了个冒号"的噪音。
#   ② **真的被调用**：名字后面（跳过空白）必须紧跟 `(`。`a:b():c.d(...)` 里的 `c` 只是取成员、
#      `d` 才带参数列表 ⇒ 只记 `d`（链里"取成员"那一段不是调用，收了就是噪音）。
#   ③ **名字必须在 PC 侧存在**：见 `load_pc_method_catalog()`（人维护的对照清单 ∪ 机器生成的
#      inventory）。这条把 `t:gsub()`、`s:find()`、`x:modifierFunction()` 这类**模组自己的方法**
#      滤掉 —— 实测 EID 上原始命中 126 处 / 45 个名字，过滤后 94 处 / 35 个名字，
#      被滤掉的正是这些非游戏方法名。
#   ④ **模组自己定义过的方法名排除**（`load_mod_definitions()`）——与上面那一路同一口径。
#
# **口径：owner 未知。** 链式写法里接收者是"上一次调用的返回值"，静态**认不出类型**
# （`game:GetRoom()` 返回什么只有运行时知道）⇒ 这一路**只报方法名 + 候选 owner**，
# 绝不写死成 `Room:GetFrameCount`。报告里的"候选 owner"= PC 侧所有同名方法的 owner，
# 由人分诊（`GetFrameCount` 的候选是 Game/Isaac/Room，其中 Room 那条正是真机上炸掉的）。
#
# **已知假阳性来源（分诊时先想这几条，免得下一轮重新分诊一遍）：**
#   * `arr[i]:Method()` —— 接收者是**模组自己的表**（实测 EID 的 `EID.coopAllPlayers[i]:GetPlayerType()`、
#     `icon[1]:Play(...)`）。`]` 收尾同样落进这一路，名字恰好与游戏 API 同名时会被收进来。
#     判据是"数组里装的是什么"：装的是游戏对象就是真调用，装的是模组自制对象就是假阳性。
#   * **注释与字符串里的同名字样不会被剥掉**（本文件历来不做词法剥离；字段面那一版的
#     `.V1.9.7.7` 版本号假阳性同源）。本次在 EID 上实测这一路**没有**这类命中（剥注释前后都是
#     94 处），但**换一个模组要先看这一条**。
#   * `..` 连接符与 `::` 标签：`f(a)..b` 里的 `..` 会被当成"成员访问"，已用 `(?![:.])` 前瞻排除；
#     排除后没有名字可读 ⇒ 直接中止，不会产生条目（用例里有这条负对照）。
# --------------------------------------------------------------------------------------------

#: 链式调用的起点：访问器（`:` / `.`）的接收者以"表达式收尾字符"结束。
#: `(?![:.])` 是为了排除 `..` 连接符与 `::`（Lua 5.3 标签）——它们不是成员访问。
CHAIN_ROOT_RE = re.compile(r'([)\]}])\s*([:.])(?![:.])')
#: 链里一个成员的名字（访问器之后）。
CHAIN_MEMBER_RE = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)')


def _skip_balanced(text: str, start: int) -> int:
    """`start` 指向开括号，返回配对闭合括号的后一位；不做词法分析（够用，见上面的假阳性说明）。"""
    pairs = {'(': ')', '[': ']', '{': '}'}
    stack = [pairs[text[start]]]
    index = start + 1
    while index < len(text) and stack:
        char = text[index]
        if char in pairs:
            stack.append(pairs[char])
        elif char == stack[-1]:
            stack.pop()
        index += 1
    return index


def _skip_blank(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index] in ' \t\r\n':
        index += 1
    return index


def iter_chained_calls(text: str):
    """从 Lua 源码里吐出**链式调用**用到的方法名（接收者不是标识符的那些）。

    走法：找到"收尾字符 + 访问器"这个起点，然后沿链往后逐个成员走 ——
    成员后面跟 `(` 就算一次调用（并跳过参数表），跟 `[` 就跳过下标继续，
    后面还接 `:` / `.` 就继续走下一个成员，否则这条链到头。
    """
    for root in CHAIN_ROOT_RE.finditer(text):
        position = root.end()
        while True:
            member = CHAIN_MEMBER_RE.match(text, position)
            if member is None:
                break
            name = member.group(1)
            cursor = _skip_blank(text, member.end())
            if cursor < len(text) and text[cursor] == '(':
                yield name                      # 判据 ②：带参数列表 ⇒ 真的被调用
                cursor = _skip_balanced(text, cursor)
            elif cursor < len(text) and text[cursor] == '[':
                cursor = _skip_balanced(text, cursor)
            cursor = _skip_blank(text, cursor)
            if cursor < len(text) and text[cursor] in ':.':
                position = cursor + 1           # 链还没完（`a:b():c.d(` 的 `.d` 就是在这一步走到的）
                continue
            break


def load_pc_method_catalog() -> dict[str, list[str]]:
    """PC 侧 `方法名 → 候选 owner 列表` —— 链式那一路的过滤器（判据 ③）与候选来源。

    两个来源取并集，**缺一不可**：
      * `docs/PC-Lua-API-对照清单.md`（人维护的对照表；同一方法名常有多个 owner）；
      * `analysis/lua-api-inventory/inventory.json`（`tools/build_lua_api_inventory.py` 从 PC
        Lua 文档生成；`kinds` 含 `Functions` 的条目）。
    为什么两份都要：清单是人写的、可能漏；inventory 是机器生成的、覆盖全但只有"主 owner"。
    只认其中一份，就会重新制造"某条真实缺口扫不到"的老问题 —— 这正是本次要修的东西。
    """
    catalog: dict[str, list[str]] = collections.defaultdict(list)

    def remember(owner: str, name: str) -> None:
        if owner and name and owner not in catalog[name]:
            catalog[name].append(owner)

    text = (ROOT / INVENTORY).read_text(encoding='utf-8', errors='replace')
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        # 对照清单第 2 列是种类。`Variables` 行是**字段**，不是方法 —— 链式判据 ③ 收它进来
        # 只会让过滤变松（`obj:Quality()` 根本不是 PC 上的写法），所以这里显式排掉。
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) >= 2 and cells[1] == 'Variables':
            continue
        for owner, name in re.findall(r'`([A-Za-z_][A-Za-z0-9_]*)[:.]([A-Za-z_][A-Za-z0-9_]*)`', line):
            remember(owner, name)

    path = ROOT / API_INVENTORY
    if path.is_file():
        table = json.loads(path.read_text(encoding='utf-8'))
        for entry in table.get('apis', []):
            if 'Functions' in (entry.get('kinds') or []):
                remember(str(entry.get('owner', '')), str(entry.get('name', '')))

    return dict(catalog)


def load_mod_chained_usage(mod: pathlib.Path) -> collections.Counter:
    """模组里**链式调用**用到的方法名（判据 ①②③；owner 未知，见上面那一段）。"""
    known = load_pc_method_catalog()
    usage: collections.Counter = collections.Counter()
    for path in mod.rglob('*.lua'):
        text = path.read_text(encoding='utf-8', errors='replace')
        for name in iter_chained_calls(text):
            if name in known:
                usage[name] += 1
    return usage


def audit_chained_calls(mod: pathlib.Path) -> dict:
    """链式调用这一路的结论：认出来的方法名、候选 owner、候选里我们没实现的那些。

    **为什么这一路不进 `--strict`**：`--strict` 现在只对**字段面**（第 ⑤ 段）判"未登记 ⇒ 失败"，
    而字段缺口的处置台账 `tools/api_surface_ledger.json` 是**按字段名**建的（`Variables` 节）。
    链式调用是**方法**，它没有对应的台账行；直接把它接进 `--strict` 会让门禁**长期红着**
    （CONTRIBUTING 明确说"否则门禁会红着，等于没有"）。所以这一轮先把它们**枚举出来、报出来**，
    要不要给方法缺口也建一本台账、怎么与 `ApiCatalog` 对齐，属于口径变更，另行决定。
    """
    usage = load_mod_chained_usage(mod)
    defined_names = {key.split(':', 1)[1] for key in load_mod_definitions(mod)}
    runtime_apis = load_runtime_apis()
    catalog = load_pc_method_catalog()

    rows = []
    for name, count in sorted(usage.items(), key=lambda item: (-item[1], item[0])):
        if name in defined_names:
            continue                    # 判据 ④：模组自己定义过的方法，不是游戏 API
        candidates = catalog.get(name, [])
        missing = [owner for owner in candidates
                   if f'{canonical_owner(owner)}:{name}' not in runtime_apis]
        rows.append({
            'api': f'?:{name}',         # `?` = owner 未知（刻意不猜，见上面的口径说明）
            'name': name,
            'calls': count,
            'candidate_owners': candidates,
            # 候选 owner 里"我们运行时没有"的 —— 这一路真正的信号：可能是缺口，要人分诊。
            'missing_owners': missing,
            # **高置信判据**：所有候选 owner 都缺 ⇒ 不管接收者是谁都跑不通 ⇒ 几乎可以断定是缺口。
            # 反例（为什么需要这个字段）：`icon[1]:Update(...)` 的接收者是 Sprite，而 `Sprite:Update`
            # 我们实现了 —— 但候选 owner 里有 Room/Game/Level/HUD/GridEntity 都缺，
            # 不分开的话 `Update` 会一直挂在"缺口"里，看的人会以为它没实现。
            'every_candidate_missing': bool(candidates) and len(missing) == len(candidates),
        })

    with_gap = [row for row in rows if row['missing_owners']]
    return {
        'pc_method_names': len(catalog),
        'names': len(rows),
        'with_candidate_gap': with_gap,
        'every_candidate_missing': [row for row in with_gap if row['every_candidate_missing']],
        'rows': rows,
    }


# --------------------------------------------------------------------------------------------
# 接口面枚举（"第 0 维"，2026-09-16 新增）
#
# 为什么单开一段：三维验收（存在 / 语义 / 时机）问的是"**这一条**接口对不对"，它默认
# **接口清单本身是全的**。而原来的清单只覆盖方法（`obj:Method(`）：字段（PC 文档里的
# `Variables`，例如 `ItemConfig_Item.Quality`）在模组源码里的写法是 `obj.Field`，
# 既匹配不上方法提取的那个正则，也不在 `ApiCatalog`（目录只登记方法）⇒ **从来没被问过**。
# 表现：EID 读 `.Quality` 拿到 nil，被 `and desc.Quality` 短路，静默少一块品质图标。
# 过程记录见 `docs/错误复盘.md` 2026-09-16。这一段把"字段面"补成可枚举、可登记、可门禁的路。
# --------------------------------------------------------------------------------------------

LEDGER_PATH = 'tools/api_surface_ledger.json'
LUA_IMPL_DIR = 'runtime/src/interfaces/lua'
LAYOUT_TABLE_DIR = 'tools/layout_tables'

#: 模组侧的字段读取形态：`obj.Field` 与 `obj["Field"]`。
#: 与 `load_mod_usage()` 同一套思路 —— 不做 owner 解析（模组里的对象多是局部变量），
#: 只按名字收，再由调用方拿"PC 契约里的变量名"过滤。
FIELD_DOT_RE = re.compile(r'\.\s*([A-Za-z_][A-Za-z0-9_]*)')
FIELD_BRACKET_RE = re.compile(r'\[\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\]')

#: 运行时侧的"确实在对外提供字段"的两种写法（实测穷举出来的，别删其中任一）：
#:   ① `{"Name", offsetof(...)}` —— `kColorFields` / `kEntityFields` / `ItemConfig::Item` 字段表；
#:   ② `strcmp(<表达式>, "Name")` —— `PushItemField` / `RoomDescriptorIndex` / `Vector` 分派。
#: 只认其中一种会误报（第一版只认 ②，于是 `Color.A`、`Vector.X` 这种被当成缺口）。
CARRIER_TABLE_RE = re.compile(r'\{\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*,')
CARRIER_STRCMP_RE = re.compile(r'strcmp\s*\([^,]+,\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\)')

#: 运行时侧的**第三种**载体写法（2026-09-16 补）：命名常量 + 用它做名字分派。
#:
#:     constexpr char kSpriteColorField[] = "Color";
#:     if (std::strcmp(field, kSpriteColorField) == 0) { ... }
#:
#: **为什么必须认它**：`Sprite.Color` / `Sprite.FlipX` 在 2026-09-16 的"引擎写通道"批次里真的
#: 实现了（`sprite_api.cpp` 的 `SpriteNewIndex` 把这三个字段写进 `ANM2`），但上面两种写法只认
#: **字面量**，于是工具看不见 ⇒ 台账里那两条"已完成"的条目删不掉（一删就变成"未登记缺口"，
#: 门禁反过来变红）。**漏报**是这条路最危险的失败方式（见文件开头），所以补上这一种。
#:
#: 判据紧贴实现、不放松：常量名必须以 `Field` 结尾，值必须是纯标识符字符串
#: （`"Color"` 算，`"some path/with spaces"` 不算），且**必须真的被同文件里的 `strcmp` 用到**
#: —— 只声明不使用的常量不算载体。
CARRIER_NAMED_CONSTANT_RE = re.compile(r'constexpr\s+char\s+(k\w*Field)\s*\[\s*\]\s*=\s*"(\w+)"')
CARRIER_NAMED_CONSTANT_USE_RE = re.compile(r'strcmp\s*\([^,]+,\s*(k\w*Field)\s*\)')


def load_variable_owners() -> dict[str, list[str]]:
    """PC 对照清单里 `Variables` 行的 `字段名 → 可能归属的 owner 列表`。

    与 `load_inventory()`（服务于方法）分开：这一份只收变量/字段，并保留 owner 线索。
    保留 owner 只是**候选提示** —— 工具分不出模组里 `desc.Quality` 的 `desc` 到底是哪一家，
    所以判定按名字做，owner 写进报告供人工分诊。
    """
    text = (ROOT / INVENTORY).read_text(encoding='utf-8', errors='replace')
    owners: dict[str, list[str]] = collections.defaultdict(list)
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 2 or cells[1] != 'Variables':
            continue
        for owner, name in re.findall(r'`([A-Za-z_]\w*)[:.]([A-Za-z_]\w*)`', cells[0]):
            if owner not in owners[name]:
                owners[name].append(owner)
    return owners


def load_mod_field_usage(mod: pathlib.Path) -> collections.Counter:
    """模组里 `obj.Field` / `obj["Field"]` 形态的字段读取（按名字计数）。"""
    usage: collections.Counter = collections.Counter()
    for path in mod.rglob('*.lua'):
        text = path.read_text(encoding='utf-8', errors='replace')
        usage.update(FIELD_DOT_RE.findall(text))
        usage.update(FIELD_BRACKET_RE.findall(text))
    return usage


def load_implemented_fields() -> set[str]:
    """本运行时会对外提供哪些字段名（载体口径）。

    三类代码形态 + 一张数据表，缺一不可：
      1. `{"Name", offsetof(...)}` 静态字段表；
      2. `strcmp(<表达式>, "Name")` 名字分派；
      3. `constexpr char kXxxField[] = "Name"` + `strcmp(<表达式>, kXxxField)` 命名常量分派
         （见 `CARRIER_NAMED_CONSTANT_RE` 的注释：`Sprite` 那三个可写字段就是这么写的）；
      4. `tools/layout_tables/*.json` 里已复核过的结构体字段（带偏移证据）。
    口径故意只认这四种 —— 宽松到"扫文件里所有标识符字面量"会把普通字符串也算进来，
    造成"看着实现了其实没有"的假阴性（漏报缺口比误报更危险）。
    """
    names: set[str] = set()
    for path in sorted((ROOT / LUA_IMPL_DIR).glob('*.cpp')):
        text = path.read_text(encoding='utf-8', errors='replace')
        names.update(CARRIER_TABLE_RE.findall(text))
        names.update(CARRIER_STRCMP_RE.findall(text))
        # 形态 ③：常量先"声明 → 值"，再要求同文件里真的有 `strcmp(…, 常量)` 用它。
        constant_values = dict(CARRIER_NAMED_CONSTANT_RE.findall(text))
        for constant in CARRIER_NAMED_CONSTANT_USE_RE.findall(text):
            value = constant_values.get(constant)
            if value:
                names.add(value)
    for path in sorted((ROOT / LAYOUT_TABLE_DIR).glob('*.json')):
        table = json.loads(path.read_text(encoding='utf-8'))
        for row in table.get('rows', []):
            name = str(row.get('name', ''))
            if name:
                names.add(name.split('.')[-1])          # `Data.Type` 记成 `Type`
            if row.get('nested_in'):
                names.add(str(row['nested_in']))
    return names


def load_surface_ledger(path: pathlib.Path | None = None) -> dict[str, dict]:
    """人工分诊台账：字段名 → 处置条目（`tools/api_surface_ledger.json`）。

    为什么处置必须落盘成文件、而不是停在报告里：**"补 / 不补"是人的判断，不是工具的结论**。
    工具只能说"模组读了它、我们没实现"；要不要补、什么时候补、为什么不补，得有人负责。
    门禁（`runtime/tests/test_api_surface_ledger.py`）要求两者**一一对应**：
    没有台账条目的缺口 ⇒ 红；台账里已经实现或模组已不再用的条目 ⇒ 也红（过期条目要清掉）。
    """
    path = path if path is not None else (ROOT / LEDGER_PATH)
    if not path.is_file():
        return {}
    table = json.loads(path.read_text(encoding='utf-8'))
    return {str(row['name']): row for row in table.get('rows', [])}


def audit_field_surface(mod: pathlib.Path,
                        ledger: dict[str, dict] | None = None) -> dict:
    """算出字段面的四个集合：已实现 / 已登记 / **未登记缺口** / 过期台账条目。"""
    usage = load_mod_field_usage(mod)
    owners_of = load_variable_owners()
    implemented = load_implemented_fields()
    ledger = load_surface_ledger() if ledger is None else ledger

    used: dict[str, dict] = {}
    for name, count in usage.items():
        if name not in owners_of:
            continue                    # 名字不在 PC 契约的变量节里 ⇒ 不是游戏字段
        used[name] = {
            'name': name,
            'calls': count,
            'owners': owners_of[name],
            'in_runtime': name in implemented,
        }

    implemented_rows = [row for row in used.values() if row['in_runtime']]
    registered, unregistered = [], []
    for row in used.values():
        if row['in_runtime']:
            continue
        entry = ledger.get(row['name'])
        if entry is None:
            unregistered.append(row)
        else:
            registered.append({**row, 'disposition': entry.get('disposition', ''),
                               'why': entry.get('why', '')})

    stale = []
    for name, entry in ledger.items():
        row = used.get(name)
        if row is None:
            stale.append({'name': name, 'why_stale': '模组当前没读到这个字段（或它不在 PC 契约的变量节里）'})
        elif row['in_runtime']:
            stale.append({'name': name, 'why_stale': '运行时已经实现了这个字段，台账条目该删掉'})

    return {
        'variable_names_in_pc_contract': len(owners_of),
        'used': sorted(used.values(), key=lambda item: (-item['calls'], item['name'])),
        'implemented': implemented_rows,
        'registered': registered,
        'unregistered': sorted(unregistered, key=lambda item: (-item['calls'], item['name'])),
        'stale_ledger_rows': stale,
        'ledger_size': len(ledger),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mod', default=DEFAULT_MOD)
    ap.add_argument('--json', default='dist/eid-api-gap-report.json')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--ledger', default=LEDGER_PATH, help='接口面台账（人工分诊结论）')
    ap.add_argument('--strict', action='store_true',
                    help='存在"未登记缺口"或过期台账条目时以退出码 1 结束（门禁用法）')
    args = ap.parse_args()

    mod = ROOT / args.mod
    if not mod.is_dir():
        print(f'模组目录不存在：{args.mod}')
        return 2

    inventory = load_inventory()
    runtime_apis = load_runtime_apis()
    catalog_entries = load_catalog_entries()
    deviations = load_deviations()
    defined = load_mod_definitions(mod)
    usage = load_mod_usage(mod)

    # 判据：**按方法名**匹配 PC 目录（模组里 `player:GetPlayerType()` 的 owner 是局部变量，
    # 拿 owner 去卡会把绝大多数调用滤掉 —— 第一版就是这么只剩 3 条的）。
    # 模组自己定义过的方法名要排除；同名方法可能属于多个 owner，全部列出来。
    owners_of: dict[str, list[str]] = collections.defaultdict(list)
    for key in inventory:
        owner, name = key.split(':', 1)
        owners_of[name].append(owner)
    defined_names = {k.split(':', 1)[1] for k in defined}

    rows = []
    for key, count in usage.items():
        owner, name = key.split(':', 1)
        if key in defined or name in defined_names:
            continue                    # 模组自己的方法
        if name not in owners_of:
            continue                    # 方法名不在 PC 目录里 ⇒ 不是游戏 API
        for candidate_owner in owners_of[name]:
            api = f'{candidate_owner}:{name}'
            identifier = catalog_entries.get(f'{canonical_owner(candidate_owner)}:{name}', {}).get('id', '')
            deviation = deviations.get(identifier) if identifier else None
            rows.append({
                'api': api,
                'called_as': key,
                'calls': count,
                'pc_status': inventory.get(api, 'unknown'),
                'in_runtime': f'{canonical_owner(candidate_owner)}:{name}' in runtime_apis,
                # ② 语义维的标记：有实现、但**已知与 PC 语义不同**（差在哪见 `text`）。
                'deviation_kind': deviation['kind'] if deviation else '',
                'deviation': deviation['text'] if deviation else '',
                # `Get`/`Set` 这类通用方法名会在目录里属于很多 owner，凭方法名对不上真实类型，
                # 只能算"低置信参考"，不进主清单。
                'confident': len(owners_of[name]) == 1,
            })

    missing = [r for r in rows if not r['in_runtime'] and r['confident']]
    missing_lowconf = [r for r in rows if not r['in_runtime'] and not r['confident']]
    weak = [r for r in rows if r['in_runtime'] and r['pc_status'] in ('weak', 'missing')]
    unknown = [r for r in rows if r['in_runtime'] and r['pc_status'] == 'unknown']
    # ④：**EID 确实用到、且我们已知语义有偏离**的条目（每条都带"差在哪"）。
    # 与 ② 的区别：② 是"PC 清单里标 weak/unknown"（外部证据弱），
    # ④ 是"我们自己明确知道返回值与 PC 不同"（内部已承认的偏离）——后者才是必须标记的不满足。
    deviating = [r for r in rows if r['deviation'] and r['confident']]

    print(f'EID 用到的游戏 API：{len(rows)} 条（去掉了模组自建方法 {len(defined)} 条、非游戏 owner）')
    print(f'  ① 运行时里找不到（高置信）：{len(missing)} 条  ← 调用会直接报错，落在条件回调里会让整条描述消失')
    print(f'  ①b 同名方法多 owner、无法凭名字定类型（低置信参考）：{len(missing_lowconf)} 条')
    print(f'  ② 有实现但 PC 状态偏弱（Experimental/—）：{len(weak)} 条')
    print(f'  ③ 状态未知（清单里没查到）：{len(unknown)} 条')
    print(f'  ④ 有实现、但**已知与 PC 语义偏离**（② 维的不满足标记）：{len(deviating)} 条')
    print()
    print('== ① 运行时里找不到的（按调用次数）==')
    for r in sorted(missing, key=lambda x: -x['calls'])[:args.top]:
        print(f"   {r['api']:34} 调用 {r['calls']:4} 次   PC 状态={r['pc_status']}")
    if weak:
        print('\n== ② 有实现但没底的 ==')
        for r in sorted(weak, key=lambda x: -x['calls'])[:args.top]:
            print(f"   {r['api']:34} 调用 {r['calls']:4} 次   PC 状态={r['pc_status']}")
    if deviating:
        print('\n== ④ 已知语义偏离（差在哪、后果是什么）==')
        for r in sorted(deviating, key=lambda x: -x['calls'])[:args.top]:
            print(f"   {r['api']:34} 调用 {r['calls']:4} 次  [{r['deviation_kind']}]")
            print(f"      {r['deviation']}")

    surface = audit_field_surface(mod, load_surface_ledger(ROOT / args.ledger))
    print()
    print(f"== ⑤ 接口面（字段 / PC 契约的 Variables 节）==")
    print(f"   PC 契约里的字段名 {surface['variable_names_in_pc_contract']} 个；"
          f"模组读到 {len(surface['used'])} 个；台账登记 {surface['ledger_size']} 条")
    print(f"   ✅ 已实现：{len(surface['implemented'])} 条")
    print(f"   📝 已登记：{len(surface['registered'])} 条（人工处置过，见台账）")
    print(f"   ❌ 未登记缺口：{len(surface['unregistered'])} 条"
          f"    ← 模组读了、我们没实现、也还没人处置的，必须进台账")
    for r in surface['unregistered'][:args.top]:
        print(f"      {r['name']:24} 读 {r['calls']:4} 次   候选归属 {'/'.join(r['owners'])}")
    if surface['stale_ledger_rows']:
        print(f"   ⚠️ 过期台账条目：{len(surface['stale_ledger_rows'])} 条（已实现或模组不再用，应删除）")
        for r in surface['stale_ledger_rows'][:args.top]:
            print(f"      {r['name']:24} {r['why_stale']}")

    # ⑥：**链式调用**（`a:b():c(` 这类，接收者是上一次调用的返回值）——原来整个形态都扫不到。
    # 只报"方法名 + 候选 owner + 候选里的缺口"，不猜 owner（判据与假阳性来源见上面那一段注释）。
    chained = audit_chained_calls(mod)
    high = chained['every_candidate_missing']
    partial = [r for r in chained['with_candidate_gap'] if not r['every_candidate_missing']]
    print()
    print('== ⑥ 链式调用（`a:b():c(` / `(expr):c(`，接收者是上次调用的返回值 ⇒ owner 未知）==')
    print(f"   模组里认到 {chained['names']} 个方法名；其中 {len(high)} 个**所有**候选 owner 都没有"
          f"（高置信缺口），另有 {len(partial)} 个只有部分候选缺（多半不是缺口）")
    for r in high[:args.top]:
        owners = '/'.join(r['candidate_owners']) or '（PC 清单里没有 owner 线索）'
        print(f"      {r['name']:30} 链式调用 {r['calls']:3} 次   候选 owner：{owners}")
        print(f"         ✗ 全缺：{'、'.join(o + ':' + r['name'] for o in r['missing_owners'])}")
    if partial:
        print('   以下方法名只有**部分**候选 owner 缺 —— 接收者很可能正好落在已实现的那家，'
              '多半不是缺口（要看链的接收者到底是什么类型才能定）：')
        for r in partial[:args.top]:
            print(f"      {r['name']:30} 链式调用 {r['calls']:3} 次   候选 owner："
                  f"{'/'.join(r['candidate_owners'])}   缺：{'、'.join(r['missing_owners'])}")

    out = ROOT / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'mod': args.mod, 'used': rows, 'missing_in_runtime': missing,
                               'missing_low_confidence': missing_lowconf,
                               'weak_status': weak, 'unknown_status': unknown,
                               'semantic_deviations': deviating,
                               'field_surface': surface,
                               'chained_calls': chained},
                              ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n报告已写：{out}')

    if args.strict and (surface['unregistered'] or surface['stale_ledger_rows']):
        print(f"\n门禁未通过：未登记缺口 {len(surface['unregistered'])} 条、"
              f"过期台账条目 {len(surface['stale_ledger_rows'])} 条（口径见本文件顶部的"
              f"\"接口面枚举\"一段）", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
