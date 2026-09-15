#!/usr/bin/env python3
"""解析 Switch NSO0 头，输出三段（Rx/R/Rw）的映射区间与只读区边界。

用途：`docs/开发规范.md` 第 10 条要求"新增 Runtime 代码或 trampoline 时，Docker 产物还必须与
最近真机可启动包比较 `.text` 段大小、JIT 页和 LOAD 段边界"。部署到设备的 `subsdk9` 是
NSO0（LZ4 压缩），不是 ELF，所以要用这个脚本读它的头。

NSO0 头布局（小端）：
    0x00  magic "NSO0"
    0x04  version
    0x08  reserved
    0x0C  flags
    0x10  .text  file_offset
    0x14  .text  memory_offset
    0x18  .text  decompressed_size
    0x1C  .text  compressed_size(0 = 未压缩)
    0x20  .rodata 同上四项
    0x30  .data   同上四项
    0x3C  bss_size
    0x40  module_id[32]

自证：三段 compressed_size（为 0 时取 decompressed_size）之和 + 0x100 应等于文件大小。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


def read_nso(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 0x100 or data[:4] != b"NSO0":
        raise ValueError(f"{path} 不是 NSO0（前 4 字节 {data[:4]!r}）")

    segments = {}
    for name, base in (("text", 0x10), ("rodata", 0x20), ("data", 0x30)):
        file_off, mem_off, dec_size, comp_size = struct.unpack_from("<IIII", data, base)
        segments[name] = {
            "file_offset": file_off,
            "memory_offset": mem_off,
            "decompressed_size": dec_size,
            "compressed_size": comp_size,
        }

    bss_size = struct.unpack_from("<I", data, 0x3C)[0]
    module_id = data[0x40:0x60].hex()

    on_disk = sum(
        (s["compressed_size"] or s["decompressed_size"]) for s in segments.values()
    )
    return {
        "path": str(path),
        "file_size": len(data),
        "segments": segments,
        "bss_size": bss_size,
        "module_id": module_id,
        "on_disk_payload": on_disk,
        "header_self_check_ok": on_disk + 0x100 == len(data),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path, help="一个或多个 NSO0 文件（subsdk9）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = []
    for path in args.paths:
        try:
            results.append(read_nso(path))
        except Exception as exc:  # noqa: BLE001 - 报告后继续处理其余输入
            print(f"跳过 {path}: {exc}", file=sys.stderr)

    if not results:
        return 1

    if args.json:
        import json

        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    for r in results:
        print(f"=== {r['path']}")
        print(f"    文件大小 {r['file_size']} 字节    bss 0x{r['bss_size']:x}")
        print(f"    头部自证（三段压缩后之和 + 0x100 == 文件大小）: {r['header_self_check_ok']}")
        for name in ("text", "rodata", "data"):
            s = r["segments"][name]
            begin = s["memory_offset"]
            end = begin + s["decompressed_size"]
            print(
                f"    {name:<7} 映射 [0x{begin:06x},0x{end:06x})  "
                f"大小 {s['decompressed_size']:>9}  "
                f"文件偏移 0x{s['file_offset']:06x}  压缩后 {s['compressed_size']:>9}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
