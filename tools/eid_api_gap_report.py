#!/usr/bin/env python3
"""把「某个 PC 模组实际用到的游戏 API」与「我们运行时的实现状态」做交叉核对，产出缺口表。

为什么需要它：这套运行时是 PC Lua API 在 Switch 上的**逐条重实现**，而模组（如 EID）对缺口
几乎没有容忍度——缺一个方法就是"调 nil"，在带条件回调的地方会让**整条描述消失**（潘多拉魔盒的
`Level:IsAltStage` 就是这样），而且不报错。逐个撞太慢，必须先把缺口**静态枚举**出来。

三维验收基准里的前两维在这里落地：
  · ① 存在：模组调用了 `Owner:Name`，我们运行时的 API Catalog / 实现文件里有没有它；
  · ② 语义：PC 对照清单里这条的状态是 `ready`（有真实符号）还是 `Experimental` / `—`（未实现或没底）。

（第三维"调用时机"静态查不了，只能真机对照，另行登记。）

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
        entries[f'{owner}:{name}'] = {'id': f'0x{identifier:08X}'}
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
    return {f'{owner}:{name}' for owner, name in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)",\s*"([A-Za-z_][A-Za-z0-9_]*)"', body)}


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
    """模组里 `Owner:Name(` 形式的调用（只保留"看起来像游戏对象"的 owner）。"""
    usage: collections.Counter = collections.Counter()
    for path in mod.rglob('*.lua'):
        text = path.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(', text):
            usage[f'{m.group(1)}:{m.group(2)}'] += 1
    return usage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mod', default=DEFAULT_MOD)
    ap.add_argument('--json', default='dist/eid-api-gap-report.json')
    ap.add_argument('--top', type=int, default=40)
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
            identifier = catalog_entries.get(api, {}).get('id', '')
            deviation = deviations.get(identifier) if identifier else None
            rows.append({
                'api': api,
                'called_as': key,
                'calls': count,
                'pc_status': inventory.get(api, 'unknown'),
                'in_runtime': api in runtime_apis,
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

    out = ROOT / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'mod': args.mod, 'used': rows, 'missing_in_runtime': missing,
                               'missing_low_confidence': missing_lowconf,
                               'weak_status': weak, 'unknown_status': unknown,
                               'semantic_deviations': deviating},
                              ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n报告已写：{out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
