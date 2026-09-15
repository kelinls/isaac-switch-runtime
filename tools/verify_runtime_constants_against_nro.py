#!/usr/bin/env python3
"""逐条核对 `runtime_constants.hpp` 里的守卫字节与真实游戏镜像。

存在的理由：`hook_manager.cpp` 的每个 `Verify*` 都用 `memcmp` 精确比较 16 字节守卫，**一个字节
抄错就让整个绑定静默失效**——没有日志、没有报错，只是"这个 API 永远装不上"。2026-09-13 就是
这样发现的：`kGameIsGreedModeExpectedBytes` 第 10 字节写成 `0xB8`（真值 `0x68`），使
`Game:IsGreedMode` 在真机上从未可用。所以每次构建/部署前跑一遍这个工具。

判据：
  * `kXOffset` + `kXExpectedBytes` / `kXExpectedEntry`（以及 `kXFileOffset` / `kXCallFileOffset`
    这些写法）配对；
  * 与 `Repentance.nro` 同偏移的字节逐字节比较；
  * **例外**：`tools/build_patches.py` 会写入的偏移（中继入口与代码洞）在原始镜像里本来就是
    未打补丁的样子，它们的期望值描述的是**打补丁后**的镜像，因此归为 `patched-image`，不计入不符。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import build_patches


DEFAULT_NRO = (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)
DEFAULT_CONSTANTS = "runtime/source/runtime_constants.hpp"
OFFSET_PATTERN = re.compile(r"inline constexpr uintptr_t (k\w+?) = (0x[0-9A-Fa-f]+);")
ARRAY_PATTERN = re.compile(
    r"inline constexpr std::array<u8, (\d+)> (k\w+?(?:ExpectedBytes|ExpectedEntry)) = \{(.*?)\};",
    re.S,
)
OFFSET_SUFFIXES = ("FileOffset", "CallFileOffset", "Offset")


def patched_offsets() -> set[int]:
    """Offsets `tools/build_patches.py` writes into the game image."""
    offsets = set()
    for name in dir(build_patches):
        if not name.endswith("RECORDS"):
            continue
        records = getattr(build_patches, name)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, tuple) and record and isinstance(record[0], int):
                offsets.add(record[0])
    return offsets


def parse_constants(text: str):
    offsets = {name: int(value, 16) for name, value in OFFSET_PATTERN.findall(text)}
    arrays = {}
    for size, name, body in ARRAY_PATTERN.findall(text):
        values = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", body))
        arrays[name] = (int(size), values)
    return offsets, arrays


def pair_guards(offsets, arrays):
    """Pair `kXExpected*` arrays with the `kX*Offset` constant that locates them.

    Only **exact base** matches are used (`kFontLoadExpectedBytes` -> `kFontLoadOffset`,
    `kManagerRenderExpectedBytes` -> `kManagerRenderFileOffset`,
    `kGameIsGreedModeExpectedBytes` -> `kGameIsGreedModeOffset`). Relay guards spell their location
    differently (`kManagerRenderRelayExpectedBytes` describes the *patched* code cave at
    `kManagerRenderRelayCodeOffset`, and several arrays share one offset), so they are reported as
    `unresolved` here instead of being force-paired: those guards describe the post-patch image and
    are checked at boot by the Runtime's own `Verify*` functions, with `tools/build_patches.py`
    computing the very same bytes.
    """
    by_base = {}
    for name, offset in offsets.items():
        for suffix in OFFSET_SUFFIXES:
            if name.endswith(suffix):
                by_base.setdefault(name[: -len(suffix)], []).append((name, suffix, offset))
                break
    pairs, unpaired = [], []
    for array_name, (size, values) in sorted(arrays.items()):
        base = array_name[: -len("ExpectedBytes")] if array_name.endswith("ExpectedBytes") else (
            array_name[: -len("ExpectedEntry")]
        )
        candidates = by_base.get(base, [])
        if not candidates:
            unpaired.append(array_name)
            continue
        # Prefer the plain `...Offset` spelling, then `...FileOffset`, then `...CallFileOffset`.
        candidates.sort(key=lambda item: OFFSET_SUFFIXES.index(item[1]))
        offset_name, suffix, offset = candidates[0]
        pairs.append({
            "array": array_name,
            "offset_name": offset_name,
            "offset": offset,
            "declared_size": size,
            "expected": values,
        })
    return pairs, unpaired


def check(constants_text: str, nro: bytes, patched: set[int] | None = None) -> dict:
    patched = patched_offsets() if patched is None else patched
    offsets, arrays = parse_constants(constants_text)
    pairs, unpaired = pair_guards(offsets, arrays)
    results = []
    for pair in pairs:
        expected = pair["expected"]
        entry = {key: value for key, value in pair.items() if key != "expected"}
        entry["expected_hex"] = expected.hex().upper()
        if len(expected) != pair["declared_size"]:
            entry["status"] = "declared-size-mismatch"
        elif pair["offset"] in patched:
            entry["status"] = "patched-image"
        elif pair["offset"] + len(expected) > len(nro):
            entry["status"] = "out-of-range"
        else:
            actual = nro[pair["offset"]:pair["offset"] + len(expected)]
            entry["actual_hex"] = actual.hex().upper()
            entry["status"] = "match" if actual == expected else "mismatch"
        results.append(entry)
    failures = [entry for entry in results if entry["status"] not in ("match", "patched-image")]
    return {
        "checked": len(results),
        "matched": sum(1 for entry in results if entry["status"] == "match"),
        "patched_image": sum(1 for entry in results if entry["status"] == "patched-image"),
        "failures": failures,
        "unpaired_arrays": unpaired,
        "offsets_without_guard": sorted(
            name for name in offsets
            if not any(entry["offset_name"] == name for entry in results)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--constants", type=Path, default=Path(DEFAULT_CONSTANTS))
    parser.add_argument("--nro", type=Path, default=Path(DEFAULT_NRO))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    nro = args.nro.read_bytes()
    report = check(args.constants.read_text(encoding="utf-8"), nro)
    report["nro"] = str(args.nro)
    report["nro_size"] = len(nro)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failures"]:
        print(f"\n{len(report['failures'])} 条守卫与镜像不符", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
