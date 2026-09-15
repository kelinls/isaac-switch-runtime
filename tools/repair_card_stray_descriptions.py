#!/usr/bin/env python3
"""把卡上"错位"的模组文件搬回正确位置（**只改名，不改内容**）。

背景（2026-09-14 夜）：新卡的覆盖层里，84 个语言/描述文件被"搬回"到了**模组根目录**下
（`mods/<模组>/ab+/…`），而 EID 是用 `pcall(require, "descriptions.<版本>.<语言>")` 去找它们的
（`features/eid_language_manager.lua` 97~114 行）——**缺文件时 pcall 会把错误吞掉**，
于是 `EID.descriptions[<语言>]` 保持 nil，EID 走到 `features/eid_mcm.lua:81` 就索引 nil 报错，
整个模组加载失败（调试桩读数：Lua 初始化失败 detail = 0x10+5 = ScriptRunFailed）。

本工具把 `mods/<模组>/<集合>/<文件>` 搬到 `mods/<模组>/descriptions/<集合>/<文件>`，
来源清单默认取 `tools/verify_card_overlay.py` 生成的比对结果（`--plan`）。

纪律（照 AGENTS.md「设备取证纪律」）：
* **游戏必须没在跑**（FTP 与游戏同时存在是已知的崩溃诱因）；
* 一次连接只做一次改名、动作之间留 1.5 秒、撞 `451` 等几秒单次重试；
* 目录多余了就等搬空后用 `rmdir` 清掉（本工具不带 `--prune` 时不动目录）。

用法：
    python3 tools/repair_card_stray_descriptions.py --dry-run      # 只看计划
    python3 tools/repair_card_stray_descriptions.py                # 真搬
    python3 tools/repair_card_stray_descriptions.py --prune        # 搬完把空掉的错位目录删掉
"""

from __future__ import annotations

import argparse
import ftplib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = 'dist/overlay-diff-20260914.json'
DEFAULT_REMOTE = '/atmosphere/contents/010021C000B6A000/romfs/isaac_mods'

HOST = '192.168.124.11'
PORT = 5000
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.device_access import resolve_device as _resolve_device
HOST, PORT, USER, PASSWORD = _resolve_device()
SETTLE_SECONDS = 1.5
RETRY_WAIT_SECONDS = 5.0


def with_connection(action, label: str):
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            ftp = ftplib.FTP()
            ftp.connect(HOST, PORT, timeout=30)
            ftp.login(USER, PASSWORD)
            ftp.set_pasv(True)
            try:
                return action(ftp)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f'  [{label}] 第 {attempt} 次失败：{type(exc).__name__}: {exc}', file=sys.stderr)
            if attempt == 1:
                time.sleep(RETRY_WAIT_SECONDS)
        finally:
            time.sleep(SETTLE_SECONDS)
    raise SystemExit(f'  [{label}] 两次都没成功：{last}')


def list_dir(path: str) -> list[str]:
    lines: list[str] = []
    with_connection(lambda ftp: ftp.retrlines(f'LIST {path}', lines.append), f'ls {path}')
    names = []
    for line in lines:
        m = re.match(r'^[dl-][rwx-]{9}\s+\d+\s+\S+\s+\S+\s+\d+\s+\S+\s+\S+\s+\S+\s+(.+)$', line.strip())
        if m:
            names.append(m.group(1).strip())
    return names


def build_plan(plan_path: Path) -> list[tuple[str, str]]:
    """从比对结果里算出"错位文件 → 正确位置"的搬家清单。

    错位规则：正确位置是 `…/descriptions/<集合>/<文件名>`，错位副本就在
    `…/<集合>/<文件名>`（少了一层 `descriptions`）。
    """
    data = json.loads(plan_path.read_text(encoding='utf-8'))
    pairs: list[tuple[str, str]] = []
    for missing in data['missing']:
        parts = missing.split('/')
        if 'descriptions' not in parts:
            continue
        index = parts.index('descriptions')
        if index + 2 >= len(parts):
            continue
        stray = '/'.join(parts[:index] + parts[index + 1:])  # 去掉 descriptions 这一层
        pairs.append((stray, missing))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', default=DEFAULT_PLAN)
    ap.add_argument('--remote', default=DEFAULT_REMOTE)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--prune', action='store_true', help='搬空之后删掉错位目录')
    args = ap.parse_args()

    plan_file = ROOT / args.plan
    if not plan_file.exists():
        print(f'找不到比对结果：{args.plan}（先跑 tools/verify_card_overlay.py）')
        return 2
    pairs = build_plan(plan_file)
    if not pairs:
        print('清单里没有需要搬的文件（可能已经搬过了）。')
        return 0

    print(f'计划搬运 {len(pairs)} 个文件（只改名）。样例：')
    for stray, target in pairs[:3]:
        print(f'  {stray}\n    → {target}')
    if args.dry_run:
        print('（--dry-run：没有动卡）')
        return 0

    done = failed = 0
    for index, (stray, target) in enumerate(pairs, 1):
        src = f'{args.remote}/{stray}'
        dst = f'{args.remote}/{target}'
        try:
            with_connection(lambda ftp, s=src, d=dst: ftp.rename(s, d), f'mv {stray}')
            done += 1
            print(f'[{index}/{len(pairs)}] ✓ {stray} → {target}')
        except SystemExit as exc:
            failed += 1
            print(f'[{index}/{len(pairs)}] ✗ {exc}')
        except ftplib.all_errors as exc:  # 目标已存在之类
            failed += 1
            print(f'[{index}/{len(pairs)}] ✗ {stray}：{exc}')

    print()
    print(f'搬运结束：成功 {done}、失败 {failed}')
    strays = sorted({s.split('/')[-2] for s, _t in pairs})
    for set_name in strays:
        left = list_dir(f'{args.remote}/mods/external item descriptions_836319872/{set_name}')
        print(f'  错位目录 {set_name}/ 还剩 {len(left)} 项')
        if args.prune and not left:
            with_connection(lambda ftp, p=f'{args.remote}/mods/external item descriptions_836319872/{set_name}':
                            ftp.rmd(p), f'rmdir {set_name}')
            print(f'  已删除空目录 {set_name}/')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
