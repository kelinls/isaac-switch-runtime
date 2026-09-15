#!/usr/bin/env python3
"""门禁：所有 nro_patches 中继补丁**两两不许改到同一片字节**。

为什么需要它：2026-09-14 踩过一次 —— 新加的"游戏开局"中继把代码洞选在 `0x68CEA0`，
而后来加入的 render-present 中继占 `0x68CF00`，两条在 `0x68CF00–0x68CF40` 重叠。
**两个 IPS 改同一片游戏模块字节 ⇒ 后写的那条覆盖另一条的中继代码 ⇒ 游戏加载失败。**
当时的手工审计（stage107/108，2026-08-30）写着"不与现有中继重叠"，但那之后又加了新中继，
**结论过期**。所以这条检查必须每次构建都跑，且只看"当前这一套补丁"。

用法：
    python3 tools/check_relay_cave_overlap.py                       # 默认查部署目录
    python3 tools/check_relay_cave_overlap.py --dir <目录>          # 查指定目录（递归找 *.ips）
退出码：0 = 无重叠；1 = 有重叠（并打印冲突的两个区间与所属补丁）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

DEFAULT_DIR = 'runtime/.gdbsym-artifacts/deploy/atmosphere/nro_patches'


def parse_ips(path: pathlib.Path) -> list[tuple[int, int]]:
    """返回 [(偏移, 长度), ...]（IPS 的 RLE 记录按 run 长度算实际覆盖）。"""
    data = path.read_bytes()
    if data[:5] != b'PATCH':
        raise ValueError(f'{path} 不是 IPS 文件')
    i = 5
    records: list[tuple[int, int]] = []
    while i < len(data):
        if data[i:i + 3] == b'EOF':
            break
        offset = int.from_bytes(data[i:i + 3], 'big')
        i += 3
        size = int.from_bytes(data[i:i + 2], 'big')
        i += 2
        if size == 0:
            run = int.from_bytes(data[i:i + 2], 'big')
            i += 2
            records.append((offset, run))
            i += 1
        else:
            records.append((offset, size))
            i += size
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', default=DEFAULT_DIR, help='递归查找 *.ips 的根目录')
    args = parser.parse_args()

    root = pathlib.Path(args.dir)
    files = sorted(root.rglob('*.ips'))
    if not files:
        print(f'没找到任何 *.ips：{root}')
        return 0

    ranges = {path.parent.name: parse_ips(path) for path in files}
    print(f'检查 {len(ranges)} 条中继补丁：')
    for name, records in sorted(ranges.items()):
        low = min(offset for offset, _ in records)
        high = max(offset + size for offset, size in records)
        print(f'  {name:52} 记录 {len(records):2d}  范围 0x{low:06x}~0x{high:06x}')

    conflicts = 0
    names = sorted(ranges)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for offset_a, size_a in ranges[names[i]]:
                for offset_b, size_b in ranges[names[j]]:
                    if offset_a < offset_b + size_b and offset_b < offset_a + size_a:
                        print(f'  ✗ 冲突：{names[i]} [0x{offset_a:x}+{size_a}] '
                              f'× {names[j]} [0x{offset_b:x}+{size_b}]')
                        conflicts += 1
    if conflicts:
        print(f'\n有 {conflicts} 处字节重叠：两个中继会互相覆盖代码，必须换代码洞。')
        return 1
    print('\n无重叠 ✓')
    return 0


if __name__ == '__main__':
    sys.exit(main())
