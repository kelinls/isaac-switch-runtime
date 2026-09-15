#!/usr/bin/env python3
"""把 PC 侧（IsaacDocs）的 API 契约与我们的实现**并排列出来**，供逐条收口语义。

## 这个工具解决什么

验收基准的第二维是"语义"：返回值要对。但在批量补齐之前，"语义对不对"这件事
既没有清单也没有判据 —— 只有 83 条"有实现但 PC 状态偏弱"的模糊说法。本工具把
**PC 侧的契约**（种类 / 返回类型 / 参数表）从 IsaacDocs 快照里解析出来，和我们这一侧
能机械判定的事实放在一起，逐条给出判定：

| 判定 | 含义 | 依据 |
| --- | --- | --- |
| `missing` | 运行时里根本没有这条 API | Catalog 里查不到 |
| `kind-mismatch` | PC 是**变量**（`obj.X`），我们却登记成了**方法**（`obj:X()`），或反过来 | IsaacDocs 的 `aria-label` 与我们的绑定表 |
| `index-served` | PC 是变量，Catalog 里没有，但我们的 `__index` 处理里出现了这个名字 | 源码里的字符串字面量 |
| `variable-unknown` | PC 是变量，而我们在 catalog、绑定表、`__index` 里都找不到 | 三处都没有 |
| `deviation` | 有实现，但**已经承认**与 PC 语义有偏离（偏离表里记着） | `api_deviation.cpp` |
| `review` | 有实现、形态对得上、也没登记偏离 —— **但仍未经人工逐条核对**（本工具不假装它是对的） | 其余情况 |
| `unknown-pc` | IsaacDocs 里没解析到这条 API 的契约 | 快照里没有 |

**`review` 不等于"语义正确"**：它只表示"机械判据没发现问题"。真正的语义收口要么靠
逐条人工核对 + 真机读数，要么靠以后把"返回值类型"也写进 Catalog 再机械比对。

## 数据来源

* PC 契约：`analysis/isaacdocs-snapshot/docs/*.md`（IsaacDocs 镜像，237 个文件）。
  每节形状固定：
    * 函数/构造/运算符：`#### <返回类型> <名字> ( <参数…> ) {: .copyable … }`
    * 变量：`#### <类型> <名字>  {: .copyable aria-label='Variables' }`（**没有括号**）
  节标题 `### <名·字·分·隔> {: aria-label='<种类>' }` 给出种类。
* 我们这一侧：Catalog（`api_catalog.cpp`）、绑定表 id（各家族 TU 的 `kXHandlers`）、
  偏离表（`api_deviation.cpp`）、`__index` 里的字符串字面量（`interfaces/lua/*.cpp`）。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / 'analysis' / 'isaacdocs-snapshot' / 'docs'
CATALOG = ROOT / 'runtime' / 'src' / 'interfaces' / 'lua' / 'api_catalog.cpp'
DEVIATIONS = ROOT / 'runtime' / 'src' / 'interfaces' / 'lua' / 'api_deviation.cpp'
LUA_DIR = ROOT / 'runtime' / 'src' / 'interfaces' / 'lua'
GAP_REPORT = ROOT / 'dist' / 'eid-api-gap-report.json'

# 注意：下面按 `^### ` 分节，所以每个 section 是**锚点行的剩余部分**（不再带 `### `）。
# 锚点有两种形状：`名字 () {: aria-label='Functions' }` 与 `名字 {: aria-label='Variables' }`。
_ANCHOR = re.compile(r"^(.+?)\s*\{\:\s*aria-label='(\w+)'")
_FUNCTION = re.compile(r"^####\s+(.+?)\s*\{\:\s*\.copyable", re.M)

#: Domain → id 高字节（与 `api_descriptor.hpp` 的 `MakeId` 一致）。
DOMAIN_BYTES = {
    'Global': 0, 'Mod': 1, 'Game': 2, 'Level': 3, 'Room': 4, 'ItemPool': 5,
    'Music': 6, 'Rng': 7, 'Persistence': 8, 'Input': 9, 'Diagnostic': 10,
    'Font': 11, 'Vector': 12, 'Sprite': 13, 'Isaac': 14, 'Seed': 15,
}


def strip_links(text: str) -> str:
    """`[RoomDescriptor](RoomDescriptor.md)` → `RoomDescriptor`（保留可读的类型名）。"""
    return re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text).strip()


def parse_signature(kind: str, line: str) -> tuple[str, list[str]]:
    """把一条签名拆成 `(返回类型, 参数表)`；变量没有参数表，返回空列表。"""
    line = strip_links(line)
    if kind == 'Variables':
        # `int AwardSeed` —— 类型在前、名字在后，取类型即可。
        parts = line.split()
        return (' '.join(parts[:-1]) if len(parts) > 1 else line), []
    match = re.match(r'^(.*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$', line)
    if not match:
        return line, []
    returns = match.group(1).strip()
    params = [item.strip() for item in match.group(3).split(',') if item.strip()]
    return returns, params


def load_pc_contracts() -> dict[str, dict[str, object]]:
    """`Owner:Name` → `{kind, returns, params}`（来自 IsaacDocs 快照）。"""
    contracts: dict[str, dict[str, object]] = {}
    if not DOCS.is_dir():
        return contracts
    for path in sorted(DOCS.glob('*.md')):
        owner = path.stem
        text = path.read_text(encoding='utf-8', errors='replace')
        sections = re.split(r'^### ', text, flags=re.M)[1:]
        for section in sections:
            anchor = _ANCHOR.match(section)
            if anchor is None:
                continue
            # 锚点里函数带一对空括号（`GetAbsoluteStage ()`），变量没有 —— 统一去掉。
            name = re.sub(r'\s*\(\s*\)\s*$', '', anchor.group(1)).replace('·', '')
            kind = anchor.group(2)
            signature = _FUNCTION.search(section)
            if signature is None:
                continue
            returns, params = parse_signature(kind, signature.group(1))
            contracts[f'{owner}:{name}'] = {
                'kind': kind, 'returns': returns, 'params': params,
            }
    return contracts


def load_catalog_ids() -> dict[str, str]:
    """`Owner:Name` → 完整 id（只认 `kDefaultApis[]` 之内）。"""
    text = CATALOG.read_text(encoding='utf-8', errors='replace')
    start = text.find('kDefaultApis[]')
    end = text.find('const ApiCatalog g_defaultCatalog', start)
    body = text[start:end if end > start else len(text)]
    pattern = re.compile(
        r'MakeId\(ApiDomain::(\w+),\s*(\d+),\s*(0x[0-9A-Fa-f]+)\).*?'
        r'"([A-Za-z_][A-Za-z0-9_]*)",\s*"([A-Za-z_][A-Za-z0-9_]*)"',
        re.DOTALL,
    )
    found: dict[str, str] = {}
    for match in pattern.finditer(body):
        domain, group, sequence, owner, name = match.groups()
        if domain not in DOMAIN_BYTES:
            continue
        identifier = (DOMAIN_BYTES[domain] << 24) | (int(group) << 16) | int(sequence, 16)
        found[f'{owner}:{name}'] = f'0x{identifier:08X}'
    return found


def load_bound_ids() -> set[str]:
    """绑定表里出现过的 id（各家族 TU 的 `kXHandlers`）—— 用来区分"方法"与"值/字段"。"""
    ids: set[str] = set()
    for path in sorted(LUA_DIR.glob('*.cpp')):
        text = path.read_text(encoding='utf-8', errors='replace')
        for table in re.findall(r'k\w*Handlers\[\]\s*=\s*\{(.*?)\};', text, re.DOTALL):
            ids.update(match.group(0).upper() for match in re.finditer(r'0x[0-9A-Fa-f]{8}', table))
    return ids


def load_deviation_ids() -> dict[str, dict[str, str]]:
    text = DEVIATIONS.read_text(encoding='utf-8', errors='replace')
    pattern = re.compile(
        r'\{\s*(0x[0-9A-Fa-f]{8})\s*,\s*ApiDeviationKind::(\w+)\s*,\s*((?:\s*"(?:[^"\\]|\\.)*"\s*)+)\}'
    )
    found: dict[str, dict[str, str]] = {}
    for match in pattern.finditer(text):
        identifier, kind, raw = match.groups()
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
        found[f'0x{int(identifier, 16):08X}'] = {'kind': kind, 'text': ''.join(pieces)}
    return found


def load_index_names() -> set[str]:
    """`__index`/`__newindex` 处理里以字符串字面量出现的名字（字段/变量走的路径）。"""
    names: set[str] = set()
    for path in sorted(LUA_DIR.glob('*.cpp')):
        text = path.read_text(encoding='utf-8', errors='replace')
        if 'Index' not in text:
            continue
        names.update(re.findall(r'"([A-Za-z_][A-Za-z0-9_]{2,})"', text))
    return names


def classify(api: str, pc: dict[str, object] | None, catalog_id: str | None,
             bound_ids: set[str], deviations: dict[str, dict[str, str]],
             index_names: set[str], confident: bool) -> tuple[str, str]:
    """返回 `(判定, 一句话依据)`。

    `confident` 沿用缺口报告的口径：`player:GetPlayerType()` 这种调用里的 owner 是局部变量，
    **凭方法名对不上真实类型**（同名方法在 PC 目录里属于多个 owner），所以低置信条目
    不给 `missing` 这种结论，只标出来供人工看。
    """
    name = api.split(':', 1)[1]
    if not confident:
        return 'low-confidence', '调用点的 owner 与 PC 目录里的 owner 对不上（凭名字无法定类型）'
    if catalog_id is None:
        if pc is not None and pc['kind'] == 'Variables' and name in index_names:
            return 'index-served', f'PC 是变量；Catalog 无条目，但 `__index` 源码里出现了 `{name}`'
        if pc is not None and pc['kind'] == 'Variables':
            return 'variable-unknown', 'PC 是变量；Catalog / 绑定表 / `__index` 三处都没找到'
        return 'missing', 'Catalog 里没有这条 API'
    if catalog_id in deviations:
        return 'deviation', f"已登记偏离：{deviations[catalog_id]['kind']}"
    if pc is None:
        return 'unknown-pc', 'IsaacDocs 快照里没解析到这条 API 的契约'
    is_variable = pc['kind'] == 'Variables'
    is_bound = catalog_id.upper() in bound_ids
    if is_variable and is_bound:
        return 'kind-mismatch', 'PC 是变量（`obj.X`），我们却把它登记成了方法（`obj:X()`）'
    if not is_variable and not is_bound:
        return 'kind-mismatch', 'PC 是方法（`obj:X()`），我们的绑定表里却没有它'
    return 'review', '形态对得上、也没登记偏离 —— 但**尚未人工核对语义**'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--gap-report', default=str(GAP_REPORT.relative_to(ROOT)))
    ap.add_argument('--json', default='dist/eid-api-contract-diff.json')
    ap.add_argument('--top', type=int, default=25)
    args = ap.parse_args(argv)

    gap_path = ROOT / args.gap_report
    if not gap_path.is_file():
        print(f'缺少缺口报告：{args.gap_report}（先跑 tools/eid_api_gap_report.py）', file=sys.stderr)
        return 2

    gap = json.loads(gap_path.read_text(encoding='utf-8'))
    pc = load_pc_contracts()
    catalog = load_catalog_ids()
    bound = load_bound_ids()
    deviations = load_deviation_ids()
    index_names = load_index_names()

    # EID 实际用到的 API（去重，保留最大调用次数）。
    calls: dict[str, int] = collections.Counter()
    confident: dict[str, bool] = {}
    for row in gap.get('used', []):
        api = row['api']
        calls[api] = max(calls[api], int(row.get('calls', 0)))
        confident[api] = confident.get(api, False) or bool(row.get('confident'))

    rows = []
    for api, count in calls.items():
        verdict, why = classify(api, pc.get(api), catalog.get(api), bound, deviations,
                                index_names, confident.get(api, False))
        contract = pc.get(api) or {}
        rows.append({
            'api': api,
            'calls': count,
            'verdict': verdict,
            'why': why,
            'pc_kind': contract.get('kind', ''),
            'pc_returns': contract.get('returns', ''),
            'pc_params': contract.get('params', []),
            'catalog_id': catalog.get(api, ''),
        })

    summary = collections.Counter(row['verdict'] for row in rows)
    print(f'IsaacDocs 契约解析：{len(pc)} 条；EID 用到的 API：{len(rows)} 条')
    for verdict in ('missing', 'kind-mismatch', 'variable-unknown', 'deviation',
                    'index-served', 'review', 'unknown-pc', 'low-confidence'):
        if summary[verdict]:
            print(f'  {verdict:<18} {summary[verdict]:>4} 条')

    for title, keys in (
        ('① 缺失 / 形态不符（必须先修）', ('missing', 'kind-mismatch', 'variable-unknown')),
        ('② 已登记的语义偏离', ('deviation',)),
    ):
        picked = [row for row in rows if row['verdict'] in keys]
        if not picked:
            continue
        print(f'\n== {title} ==')
        for row in sorted(picked, key=lambda item: -item['calls'])[:args.top]:
            signature = f"{row['pc_returns']} {row['api'].split(':')[1]}({', '.join(row['pc_params'])})" \
                if row['pc_kind'] == 'Functions' else f"{row['pc_returns']} {row['api'].split(':')[1]}"
            print(f"   {row['api']:34} 调用 {row['calls']:>3} 次  PC: {signature}")
            print(f"      → {row['why']}")

    out = ROOT / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        'source': 'analysis/isaacdocs-snapshot/docs',
        'pc_contracts_parsed': len(pc),
        'summary': dict(summary),
        'rows': rows,
    }, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n报告已写：{out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
