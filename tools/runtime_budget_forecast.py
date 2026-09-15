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

from runtime_layout_budget import DEFAULT_CODE_LIMIT, DEFAULT_RO_END_LIMIT, build_report, parse_layout  # noqa: E402

#: 一个"引擎方法类"API（带 16 字节入口守卫 + 安装期校验）的成本。
ENGINE_METHOD_API_CODE_BYTES = 490
#: 一个"字段直读类"API（无守卫校验函数，只有 handler + 读取）的成本。
FIELD_READ_API_CODE_BYTES = 260
#: 与实现方式无关的只读成本（目录行 + 守卫常量/绑定行 + 错误文本字面量）。
RODATA_BYTES_PER_API = 272


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
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    path = pathlib.Path(args.elf)
    if not path.is_file():
        print(f'找不到产物：{args.elf}', file=sys.stderr)
        return 2
    try:
        layout = parse_layout(path.read_bytes())
    except ValueError as error:
        print(f'无法解析 {args.elf}：{error}', file=sys.stderr)
        return 2

    report = build_report(layout, code_limit=args.code_limit, ro_end_limit=args.ro_end_limit)
    result = forecast(layout.code_end, layout.read_only_end, apis=args.apis, kind=args.kind,
                      code_limit=args.code_limit, ro_end_limit=args.ro_end_limit)

    if args.json:
        print(json.dumps({'ok': report['ok'], **result}, ensure_ascii=False, indent=1))
        return 0

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
    return 0


if __name__ == '__main__':
    sys.exit(main())
