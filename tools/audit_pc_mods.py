"""Write conservative static-dependency reports for a PC Isaac Mods directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.pc_mod_audit import audit_mods


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def markdown_report(mods: list[dict[str, object]], source: Path) -> str:
    """Render an intentionally compact human review of the JSON report."""
    lines = [
        "# PC Mod 静态兼容矩阵",
        "",
        f"审计输入：`{source}`。本报告只列出可静态识别的 Lua 依赖；动态调用、动态 require 和运行期分支均为未知，不能据此视为兼容。",
        "",
        "| Mod | 已启用 | 候选 | 阻断原因 | 回调 | 方法 | 资源目录 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mod in mods:
        reasons = mod.get("candidate_reasons") or []
        callbacks = mod.get("callbacks") or []
        methods = mod.get("methods") or []
        resources = mod.get("resource_directories") or []
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(mod.get("directory", "")),
                    "是" if mod.get("enabled") else "否",
                    "是" if mod.get("candidate") else "否",
                    _cell("<br>".join(reasons) if isinstance(reasons, list) and reasons else "-"),
                    _cell("<br>".join(callbacks) if isinstance(callbacks, list) and callbacks else "-"),
                    _cell("<br>".join(methods) if isinstance(methods, list) and methods else "-"),
                    _cell("<br>".join(resources) if isinstance(resources, list) and resources else "-"),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mods_root", type=Path)
    parser.add_argument("--json", required=True, type=Path, dest="json_path")
    parser.add_argument("--markdown", required=True, type=Path, dest="markdown_path")
    args = parser.parse_args()
    try:
        mods = audit_mods(args.mods_root)
        _write(args.json_path, json.dumps({"schema_version": 1, "mods": mods}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _write(args.markdown_path, markdown_report(mods, args.mods_root))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{parser.prog}: error: {error}\n")


if __name__ == "__main__":
    main()
