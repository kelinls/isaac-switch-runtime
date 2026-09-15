#!/usr/bin/env python3
"""按**实测的每 API 成本**算清"还能加多少条"，并指出哪个段先撞墙。

## 为什么需要它

批量补齐 API 之前必须知道预算还剩多少 —— 否则可能做到一半撞墙，而那时改动已经
散落在几十个文件里。本项目有两个硬上限（见 `tools/runtime_layout_budget.py`）：
代码段 `0x80000`（512 KiB）与只读段结束 `0x100000`（1024 KiB）。

**要紧的是代码段，不是只读段**：某次交接记录里写过"只读段超预算"，那是把布局测试
**故意构造**的越限样例当成了真实产物（同一次输出里紧邻的一行 `ok=yes` 才是真实产物）。
本工具因此把两个段的余量与实际产物一起打出来，避免再靠记忆判断。

## 每 API 的成本从哪来（实测，不是估）

用构建产物的符号表直接量（`nm --print-size`，2026-09-15，批次 8 那一版）：

| 组成 | 字节 | 说明 |
| --- | --- | --- |
| 入口守卫校验函数 | 224 | `VerifyLevelGetAbsoluteStage` = 0xe0（与既有的 `VerifyLevelIsAscent` 同尺寸） |
| handler | 192–236 | `LevelGetAbsoluteStage` 0xec、`LevelGetCurrentRoomIndex` 0xc0 |
| 取值器 + 发布器 | 32 | thunk getter 0x10 + setter 0x10 |
| **代码合计** | **≈ 490** | 一个"引擎方法类"API |
| 目录行 + 守卫常量 + 绑定行 | ≈ 96 | `LuaApiDescriptor` 48 + 16 字节守卫 + 绑定行 8 |
| 错误文本字面量 | ≈ 150 | 每个 handler 有 2–3 条 `luaL_error` 消息（这才是只读段的大头） |
| **只读合计** | **≈ 272** | 与实测一致的交叉验证见下 |

交叉验证：批次 8 加了 4 个 API，只读段从 `0x9f74c` 涨到 `0x9fb8c` = **1088 B / 4 = 272 B**，
与本表推算相同，所以"每条 API 约 272 B 只读"是有实测支撑的，不是拍脑袋。

字段直读类 API（没有守卫校验函数）更便宜：代码约 240–280 B，只读同样约 272 B ——
`FIELD_READ_API_CODE_BYTES` 给的就是这个口径。

## 用法

    python3 tools/runtime_budget_forecast.py --elf runtime/runtime.elf
    python3 tools/runtime_budget_forecast.py --elf <产物> --apis 96 --kind field
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from runtime_layout_budget import (  # noqa: E402
    DEFAULT_CODE_LIMIT,
    DEFAULT_RO_END_LIMIT,
    ElfError,
    build_report,
    parse_layout,
)

#: 一个"引擎方法类"API（带 16 字节入口守卫 + 安装期校验）的成本。
ENGINE_METHOD_API_CODE_BYTES = 490
#: 一个"字段直读类"API（无守卫校验函数，只有 handler + 读取）的成本。
FIELD_READ_API_CODE_BYTES = 260
#: 与实现方式无关的只读成本（目录行 + 守卫常量/绑定行 + 错误文本字面量）。
RODATA_BYTES_PER_API = 272

#: **保留线**：任何一批 API 做完之后，代码段余量不得低于这个数。
#:
#: 为什么要有它、以及为什么是 32 KiB：这是"还能不能临时塞进一小批"的应急余量 ——
#: 按字段直读类约 260 B/条算，32 KiB 够再塞约 120 条，也就是"一次临时决定的修补"仍然放得下。
#: 低于这条线不是"立刻不能用"，而是**必须先把成本压下来**（见 `POLICY_ADVICE`），
#: 否则后面每一批都在赌没有意外。基线的实测余量是 68.9 KiB，所以这条闸门现在就有效
#: （96 条的批量做完会落到 23 KiB，正好触发它 —— 这是有意为之，不是误报）。
DEFAULT_FLOOR_KIB = 32

#: 触发保留线时给出的可执行清单（按性价比排序）。
POLICY_ADVICE = (
    "预算不足时的处置顺序（越靠前性价比越高）：",
    "  1) 共享入口守卫校验：现在每条引擎方法都抄一份 ~224 B 的校验函数，抽成一个带参数的共用函数后"
    "每条只剩一次调用（粗估省 ~180 B/条，容量上限可涨五成左右）；",
    "  2) 能用字段直读就别调引擎方法：字段直读类约 260 B/条，引擎方法类约 490 B/条；",
    "  3) 共享错误文本：每个 handler 有 2–3 条 luaL_error 消息（约 150 B/条，只读段的大头）"
    "可以合并成同一模板复用；",
    "  4) 以上都不够、且确认不是失控增长时，才改 `runtime/misc/link.ld` 的两条 ASSERT 与"
    "本文件的同名常量（有测试核对必须同时改）。",
)


def evaluate_policy(code_end: int, ro_end: int, *, apis: int = 0, kind: str = "engine",
                    floor_kib: int = DEFAULT_FLOOR_KIB,
                    code_limit: int = DEFAULT_CODE_LIMIT,
                    ro_end_limit: int = DEFAULT_RO_END_LIMIT) -> dict[str, object]:
    """把预算做成**闸门**：算出"这一批做完还剩多少"，并与保留线比较。

    返回 `{ok, reason, ...}`；`ok=False` 时调用方（构建/门禁）应当失败，
    并把 `POLICY_ADVICE` 打给使用者 —— 闸门的价值在于"要么放得下，要么明确告诉你该省哪一块"。
    """
    result = forecast(code_end, ro_end, apis=apis, kind=kind,
                      code_limit=code_limit, ro_end_limit=ro_end_limit)
    per_api_code = result["code_per_api"]
    remaining_code = result["code_reserve"] - apis * per_api_code
    remaining_rodata = result["rodata_reserve"] - apis * RODATA_BYTES_PER_API
    floor = floor_kib * 1024
    problems: list[str] = []
    if not result["requested_fits"]:
        problems.append(
            f"这一批 {apis} 条放不下：上限 {result['capacity']} 条（先撞 {result['binding_segment']} 段）"
        )
    if remaining_code < floor:
        problems.append(
            f"做完这一批代码段只剩 {remaining_code / 1024:.1f} KiB，低于保留线 {floor_kib} KiB"
        )
    if remaining_rodata < floor:
        problems.append(
            f"做完这一批只读段只剩 {remaining_rodata / 1024:.1f} KiB，低于保留线 {floor_kib} KiB"
        )
    return {
        **result,
        "floor_kib": floor_kib,
        "remaining_code": remaining_code,
        "remaining_rodata": remaining_rodata,
        "ok": not problems,
        "problems": problems,
    }


def forecast(code_end: int, ro_end: int, *, apis: int, kind: str,
             code_limit: int = DEFAULT_CODE_LIMIT,
             ro_end_limit: int = DEFAULT_RO_END_LIMIT) -> dict[str, object]:
    """算出"按给定成本再加 `apis` 条会不会超"，以及"还能加多少条"。"""
    per_api_code = ENGINE_METHOD_API_CODE_BYTES if kind == "engine" else FIELD_READ_API_CODE_BYTES
    code_reserve = code_limit - code_end
    ro_reserve = ro_end_limit - ro_end
    capacity_by_code = code_reserve // per_api_code
    capacity_by_rodata = ro_reserve // RODATA_BYTES_PER_API
    return {
        'kind': kind,
        'code_per_api': per_api_code,
        'rodata_per_api': RODATA_BYTES_PER_API,
        'code_reserve': code_reserve,
        'rodata_reserve': ro_reserve,
        'capacity_by_code': capacity_by_code,
        'capacity_by_rodata': capacity_by_rodata,
        # 真正的上限取两者较小者 —— 这就是"哪个段先撞墙"。
        'capacity': min(capacity_by_code, capacity_by_rodata),
        'binding_segment': 'code' if capacity_by_code <= capacity_by_rodata else 'rodata',
        'requested_apis': apis,
        'requested_code': apis * per_api_code,
        'requested_rodata': apis * RODATA_BYTES_PER_API,
        'requested_fits': apis <= min(capacity_by_code, capacity_by_rodata),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--elf', required=True, help='构建产物（例如 runtime/runtime.elf）')
    ap.add_argument('--apis', type=int, default=0, help='打算再加多少条（用于判断"这一批放不放得下"）')
    ap.add_argument('--kind', choices=('engine', 'field'), default='engine',
                    help='engine=带入口守卫的引擎方法；field=字段直读')
    ap.add_argument('--code-limit', type=lambda value: int(value, 0), default=DEFAULT_CODE_LIMIT)
    ap.add_argument('--ro-end-limit', type=lambda value: int(value, 0), default=DEFAULT_RO_END_LIMIT)
    ap.add_argument('--floor-kib', type=int, default=DEFAULT_FLOOR_KIB,
                    help=f'做完这一批之后必须剩下的余量（默认 {DEFAULT_FLOOR_KIB} KiB）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    path = pathlib.Path(args.elf)
    if not path.is_file():
        print(f'找不到产物：{args.elf}', file=sys.stderr)
        return 2
    try:
        layout = parse_layout(path.read_bytes())
    except (OSError, ElfError) as error:
        # 与 `runtime_layout_budget.py` 同口径：桩工具链产出的非 ELF 不算失败，按"跳过"处理，
        # 这样构建里那一步不需要为测试用桩工具链开特例。
        print(f'预算闸门：跳过 {args.elf}（不是真实 ELF 产物）：{error}', file=sys.stderr)
        return 3

    report = build_report(layout, code_limit=args.code_limit, ro_end_limit=args.ro_end_limit)
    result = evaluate_policy(layout.code_end, layout.read_only_end, apis=args.apis,
                             kind=args.kind, floor_kib=args.floor_kib,
                             code_limit=args.code_limit, ro_end_limit=args.ro_end_limit)

    if args.json:
        print(json.dumps({'layout_ok': report['ok'], **result}, ensure_ascii=False, indent=1))
        return 0 if result['ok'] and report['ok'] else 1

    print(f"产物：{args.elf}    布局判定：{'ok' if report['ok'] else '越限'}")
    print(f"  代码段 {layout.code_end:#x} / {args.code_limit:#x}"
          f"  余 {result['code_reserve'] / 1024:.1f} KiB")
    print(f"  只读段 {layout.read_only_end:#x} / {args.ro_end_limit:#x}"
          f"  余 {result['rodata_reserve'] / 1024:.1f} KiB")
    print(f"\n按每条约 {result['code_per_api']} B 代码 + {RODATA_BYTES_PER_API} B 只读"
          f"（{args.kind} 类）算：")
    print(f"  还能加约 {result['capacity_by_code']} 条（受代码段限制）")
    print(f"  还能加约 {result['capacity_by_rodata']} 条（受只读段限制）")
    print(f"  ⇒ 上限 {result['capacity']} 条，先撞的是 **{result['binding_segment']}** 段")
    if args.apis:
        verdict = '放得下' if result['requested_fits'] else '放不下'
        print(f"\n这一批 {args.apis} 条：{verdict}"
              f"（需要代码 {result['requested_code'] / 1024:.1f} KiB、"
              f"只读 {result['requested_rodata'] / 1024:.1f} KiB）")
        print(f"  做完之后：代码段剩 {result['remaining_code'] / 1024:.1f} KiB、"
              f"只读段剩 {result['remaining_rodata'] / 1024:.1f} KiB"
              f"（保留线 {args.floor_kib} KiB）")
    if not result['ok'] or not report['ok']:
        print("\n预算闸门：不通过")
        for problem in result['problems']:
            print(f"  - {problem}")
        for line in POLICY_ADVICE:
            print(line)
        return 1
    print("\n预算闸门：通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
