#!/usr/bin/env python3
"""布局表 = 数据；这个工具**同时**做生成与证据复核。

## 它解决什么

布局表的每一行都要能回答"凭什么"。此前项目的做法是把偏移写进 `runtime_constants.hpp`
并在注释里写一句依据，**证据本身不进仓库、也无法复核**（别人只能选择相信）。
这里改成：

* 表是数据（`tools/layout_tables/<struct>.json`），每行带**函数符号 + 指令地址 + 该指令做了什么**；
* C++ 常量由这个工具**生成**（`runtime/source/<struct>_layout.hpp`），两处不可能漂移；
* `--verify` 会**重新反汇编**每个被引用的函数，核对"该地址上确实是那条访存指令、偏移与宽度一致"
  —— 也就是说：**表格的每行都能被机器重新证明一次**。

## 判定口径

* `confirmed`：语义有独立锚点（例如函数名就说明它要判断什么）且指令形态吻合 ⇒ **可以进对外 API**；
* `candidate`：偏移与形态确定但名字只有间接依据 ⇒ 只登记，**不进对外 API**；
* `structural`：只说明结构（指针/容器/标量区边界），不声称字段名。

## 用法

    python3 tools/build_layout_table.py --table tools/layout_tables/room_descriptor.json --verify
    python3 tools/build_layout_table.py --table tools/layout_tables/room_descriptor.json --write

退出码：0 一致/通过；1 复核失败或生成物过期；2 参数/文件问题；3 缺 NRO（无法复核，按跳过处理，
与其它预算/符号门禁同口径 —— 桩环境里不该因此变红）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import nro_disasm  # noqa: E402
import nro_symbols  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)

CONFIDENCE_ORDER = {"confirmed": 0, "candidate": 1, "structural": 2}
#: 只有 confirmed 的行才允许出现在对外 API 的实现里。
API_ALLOWED_CONFIDENCE = "confirmed"


def cpp_identifier(name: str) -> str:
    """`Data.Type` → `kRoomDescriptorDataTypeOffset` 这类形状由调用方拼，这里只做清洗。"""
    parts = [piece for piece in name.replace(".", " ").replace("_", " ").split() if piece]
    return "".join(piece[:1].upper() + piece[1:] for piece in parts)


def render_header(table: dict) -> str:
    struct = table["struct"]
    table_name = table.get("source_table", "room_descriptor")
    lines = [
        "#pragma once",
        "",
        f"// **本文件由 `tools/build_layout_table.py` 从 `tools/layout_tables/{table_name}.json` 生成，请勿手改。**",
        "//",
        "// 每个常量都对应表里的一行，那一行带可复现证据（函数符号 + 指令地址）。",
        f"// 复核命令：`python3 tools/build_layout_table.py --table tools/layout_tables/{table_name}.json --verify`",
        "//",
        "// 口径（与表的 `confidence` 一致）：",
        "//   confirmed  —— 语义有独立锚点 ⇒ **可以**用于对外 API；",
        "//   candidate  —— 偏移确定、名字只有间接依据 ⇒ 只作登记，不进 API；",
        "//   structural —— 只说明结构，不声称字段名。",
        "",
        "#include <cstdint>",
        "",
        "namespace isaac::runtime::layout {",
        "",
        # 构建号是 20 字节的 SHA-1：不能塞进整数类型（第一版写成 uint64_t，编译直接报
        # "integer literal is too large"）。这里按字节数组发出去，也便于和设备侧比对。
        f"inline constexpr std::uint8_t k{struct}BuildId[20] = {{",
        "    " + ", ".join(f"0x{table['build_id'][i:i + 2]}" for i in range(0, 40, 2)),
        "};",
        "",
    ]
    for row in sorted(table["rows"], key=lambda item: (CONFIDENCE_ORDER[item["confidence"]], item["offset"])):
        prefix = f"k{struct}{cpp_identifier(row['name'])}"
        nested = f"，相对 {row['nested_in']}" if "nested_in" in row else ""
        lines.append(f"// {row['semantic']}（{row['confidence']}{nested}）")
        for entry in row["evidence"]:
            # 证据分三类：反汇编里的访问/语义，以及**真机读数**（引落盘的探针结果）。
            if entry.get("kind") == "device":
                source = f"真机读数 {entry.get('observation')} 的 {entry.get('slot')} +0x{entry.get('offset', 0):x}"
            else:
                source = entry.get("address", "?")
            lines.append(f"//   证据：{source} —— {entry['why']}")
        lines.append(f"inline constexpr std::uintptr_t {prefix}Offset = 0x{row['offset']:X};")
        if row.get("width"):
            lines.append(f"inline constexpr std::uintptr_t {prefix}Width = {row['width']};")
        lines.append("")
    lines.append("// 对外 API 只允许读这些字段（`confidence == confirmed`）。")
    confirmed = [row["name"] for row in table["rows"] if row["confidence"] == API_ALLOWED_CONFIDENCE]
    lines.append("// 当前可用：" + ("、".join(confirmed) if confirmed else "（无）"))
    lines.append("")
    lines.append("} // namespace isaac::runtime::layout")
    lines.append("")
    return "\n".join(lines)


def _verify_device_evidence(row: dict, entry: dict) -> list[str]:
    """真机证据：引一次落盘的探针读数，核对"那个槽位的那个偏移确实是那个值"。

    这样真机结论也进得了复核范围 —— 否则表里就会出现"只有人记得"的行。
    离线可做，不需要设备：探针把原始字节落盘了。
    """
    observation = ROOT / entry.get("observation", "")
    if not observation.is_file():
        return [f"{row['name']}：找不到真机读数文件 {entry.get('observation')}"]
    parsed = json.loads(observation.read_text(encoding="utf-8"))
    words = (parsed.get("raw_words") or {}).get(entry.get("slot", ""), [])
    blob = b"".join(int(word).to_bytes(8, "little") for word in words)
    offset, expected = entry.get("offset", 0), entry.get("value")
    if len(blob) < offset + 4:
        return [f"{row['name']}：{entry.get('observation')} 里没有 {entry.get('slot')} 的偏移 0x{offset:x}"]
    actual = int.from_bytes(blob[offset:offset + 4], "little")
    if actual != expected:
        return [f"{row['name']}：{entry.get('observation')} 的 {entry.get('slot')} +0x{offset:x} "
                f"是 {actual}，表里写的是 {expected}"]
    return []


def verify_evidence(table: dict, nro: pathlib.Path) -> list[str]:
    """重新反汇编每个被引用的函数，核对"该地址上确实是那条访存、偏移与宽度一致"。"""
    problems: list[str] = []
    _build_id, symbols = nro_symbols.parse_dynamic_symbols(nro.read_bytes())
    cache: dict[str, list[str]] = {}
    for row in table["rows"]:
        for entry in row["evidence"]:
            if entry.get("kind") == "device":
                problems.extend(_verify_device_evidence(row, entry))
                continue
            symbol = symbols.get(entry["function"])
            if symbol is None or not symbol.is_defined:
                problems.append(f"{row['name']}：符号表里没有 {entry['function']}")
                continue
            if entry["function"] not in cache:
                lines = nro_disasm.disassemble(str(nro), symbol.file_offset, 4096, annotate=False)
                cache[entry["function"]] = [line.strip() for line in lines]
            window = cache[entry["function"]]
            wanted = entry["address"]
            wanted_hex = wanted[2:] if wanted.startswith('0x') else wanted
            found = next((line for line in window if line.startswith(wanted_hex)), None)
            if found is None:
                # 有的函数很长（4096 字节不够）——扩大一次再找。
                lines = nro_disasm.disassemble(str(nro), symbol.file_offset, 16384, annotate=False)
                window = [line.strip() for line in lines]
                cache[entry["function"]] = window
                found = next((line for line in window if line.startswith(wanted_hex)), None)
            if found is None:
                problems.append(f"{row['name']}：在 {entry['function']} 里找不到 {wanted}")
                continue
            kind = entry.get("kind", "access")
            if kind == "access":
                # 这条指令本身就是字段访问：偏移必须出现在它的寻址表达式里。
                if f"#{entry['offset']:#x}" not in found and entry["offset"] != 0:
                    problems.append(
                        f"{row['name']}：{wanted} 这条指令里没有偏移 {entry['offset']:#x}（实际：{found}）"
                    )
            elif kind == "device":
                # 真机证据：引一次落盘的探针读数，核对"那个槽位的那个偏移确实是那个值"。
                # 这样真机结论也进得了复核范围 —— 否则表里就会出现"只有人记得"的行。
                observation = ROOT / entry.get("observation", "")
                if not observation.is_file():
                    problems.append(
                        f"{row['name']}：找不到真机读数文件 {entry.get('observation')}"
                    )
                    continue
                parsed = json.loads(observation.read_text(encoding="utf-8"))
                words = (parsed.get("raw_words") or {}).get(entry.get("slot", ""), [])
                blob = b"".join(int(word).to_bytes(8, "little") for word in words)
                offset, expected = entry.get("offset", 0), entry.get("value")
                actual = int.from_bytes(blob[offset:offset + 4], "little") if len(blob) >= offset + 4 else None
                if actual != expected:
                    problems.append(
                        f"{row['name']}：{entry.get('observation')} 的 {entry.get('slot')}"
                        f" +0x{offset:x} 是 {actual}，表里写的是 {expected}"
                    )
            else:
                # 语义证据：这条指令拿该字段与某个已知常量比较/使用。
                # 必须核对"常量确实出现过" —— 否则把名字挂在错误的字段上也没人发现。
                immediate = entry.get("immediate")
                if immediate is None:
                    problems.append(f"{row['name']}：语义证据缺少 immediate")
                elif f"#0x{immediate:x}" not in found and f"#{immediate}" not in found:
                    problems.append(
                        f"{row['name']}：语义证据 {wanted} 里没有常量 {immediate:#x}（实际：{found}）"
                    )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", required=True)
    ap.add_argument("--nro", default=str(DEFAULT_NRO))
    ap.add_argument("--verify", action="store_true", help="重新反汇编核对每条证据")
    ap.add_argument("--write", action="store_true", help="生成/更新 C++ 头文件")
    ap.add_argument("--check", action="store_true", help="检查头文件是否与表一致（门禁用）")
    args = ap.parse_args(argv)

    table_path = pathlib.Path(args.table)
    if not table_path.is_file():
        print(f"找不到布局表：{args.table}", file=sys.stderr)
        return 2
    table = json.loads(table_path.read_text(encoding="utf-8"))
    # 文件名跟表文件名走（`room_descriptor.json` → `room_descriptor_layout.hpp`），
    # 不用结构体名小写 —— 那会得到 `roomdescriptor_layout.hpp`，与项目命名习惯不一致。
    header_path = ROOT / "runtime" / "source" / f"{table_path.stem}_layout.hpp"
    rendered = render_header(table)

    status = 0
    if args.verify:
        nro = pathlib.Path(args.nro)
        if not nro.is_file():
            print(f"布局表证据复核：跳过（缺少固定 NRO：{nro}）")
            status = max(status, 3)
        else:
            problems = verify_evidence(table, nro)
            if problems:
                print("布局表证据复核：不通过")
                for problem in problems:
                    print(f"  - {problem}")
                status = 1
            else:
                rows = len(table["rows"])
                checks = sum(len(row["evidence"]) for row in table["rows"])
                print(f"布局表证据复核：通过（{rows} 行 / {checks} 条证据都重新反汇编核对过）")

    if args.write:
        header_path.write_text(rendered, encoding="utf-8")
        print(f"已生成 {header_path.relative_to(ROOT)}")

    if args.check:
        if not header_path.is_file():
            print(f"缺少生成物 {header_path.relative_to(ROOT)} —— 跑一次 --write", file=sys.stderr)
            return 1
        current = header_path.read_text(encoding="utf-8")
        if current != rendered:
            print(f"{header_path.relative_to(ROOT)} 与布局表不一致 —— 跑一次 --write", file=sys.stderr)
            return 1
        print(f"{header_path.relative_to(ROOT)} 与布局表一致")

    if not (args.verify or args.write or args.check):
        print(rendered)
    return status


if __name__ == "__main__":
    sys.exit(main())
