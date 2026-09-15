#!/usr/bin/env python3
"""在设备 SD 卡上**整棵树搬家**（建目录 + 逐文件 `RNFR`/`RNTO`，不传数据）。

为什么用它而不是"下载再上传"：搬位置不改内容，改名几毫秒就完成，既省流量也没有"传一半坏掉"的风险。
2026-09-15 把模组文件从 romfs 覆盖层搬出到卡根 `/isaac_mods/`（方案 A：消除覆盖层重建引发的概率崩）就是用它。

纪律（照 AGENTS.md「设备取证纪律」）：一次连接只做一件事、动作之间留 1.5 秒、撞 `451` 等几秒单次重试；
**游戏必须没在跑**。脚本会先"只列不动"打印计划，加 `--apply` 才真的搬。

用法：
    python3 tools/move_card_tree.py --from /a/b --to /c/d                 # 只打印计划
    python3 tools/move_card_tree.py --from /a/b --to /c/d --apply          # 真搬
    python3 tools/move_card_tree.py --from /a/b --to /c/d --apply --prune  # 搬完把空掉的源目录删掉
"""

from __future__ import annotations

import argparse
import ftplib
import re
import sys
import time

HOST = '192.168.124.11'
PORT = 5000
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.device_access import resolve_device as _resolve_device
HOST, PORT, USER, PASSWORD = _resolve_device()
SETTLE_SECONDS = 1.5
RETRY_WAIT_SECONDS = 5.0
_LS_RE = re.compile(r'^([dl-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$')


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


def list_dir(path: str) -> list[tuple[str, int, bool]]:
    lines: list[str] = []
    with_connection(lambda ftp: ftp.retrlines(f'LIST {path}', lines.append), f'ls {path}')
    out = []
    for line in lines:
        m = _LS_RE.match(line.strip())
        if m:
            out.append((m.group(3).strip(), int(m.group(2)), m.group(1) == 'd'))
    return out


def walk_dirs(root: str) -> tuple[list[str], list[tuple[str, int]]]:
    """返回（目录相对路径按浅→深排序, [(文件相对路径, 大小)…]）。"""
    dirs: list[str] = []
    files: list[tuple[str, int]] = []
    stack = ['']
    while stack:
        rel = stack.pop()
        full = f'{root}/{rel}' if rel else root
        for name, size, is_dir in list_dir(full):
            child = f'{rel}/{name}' if rel else name
            if is_dir:
                dirs.append(child)
                stack.append(child)
            else:
                files.append((child, size))
    dirs.sort(key=lambda d: d.count('/'))
    return dirs, sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='src', required=True)
    ap.add_argument('--to', dest='dst', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--prune', action='store_true', help='搬完把空掉的源目录删掉（自深到浅）')
    args = ap.parse_args()
    src = args.src.rstrip('/')
    dst = args.dst.rstrip('/')

    print(f'源：{src}\n目标：{dst}\n枚举源目录树 …')
    dirs, files = walk_dirs(src)
    print(f'  目录 {len(dirs)} 个、文件 {len(files)} 个')
    if not files:
        print('源里没有文件，什么都不做。')
        return 0
    if not args.apply:
        print('\n（没有 --apply：只列了计划，卡没被改动）举例：')
        for rel, size in files[:3]:
            print(f'  {src}/{rel}  →  {dst}/{rel}   （{size} 字节）')
        return 0

    # 1) 建目录（浅→深；已存在=550 忽略）
    for rel in [None] + dirs:
        target = dst if rel is None else f'{dst}/{rel}'
        def mk(ftp: ftplib.FTP, path=target):
            try:
                ftp.mkd(path)
            except ftplib.error_perm as exc:
                if not str(exc).startswith('550'):
                    raise
        with_connection(mk, f'mkdir {target}')
    print(f'目录就绪：{len(dirs) + 1} 个')

    # 2) 逐文件改名
    done = failed = 0
    for index, (rel, _size) in enumerate(files, 1):
        with_connection(lambda ftp, s=f'{src}/{rel}', d=f'{dst}/{rel}': ftp.rename(s, d), f'mv {rel}')
        done += 1
        if index % 25 == 0 or index == len(files):
            print(f'  [{index}/{len(files)}] 已搬 {done}、失败 {failed}')

    # 3) 清空掉的源目录（深→浅）
    if args.prune:
        removed = 0
        for rel in sorted(dirs, key=lambda d: -d.count('/')):
            remaining = list_dir(f'{src}/{rel}')
            if remaining:
                print(f'  {src}/{rel} 还剩 {len(remaining)} 项，未删')
                continue
            with_connection(lambda ftp, p=f'{src}/{rel}': ftp.rmd(p), f'rmdir {rel}')
            removed += 1
        left = list_dir(src)
        if not left:
            with_connection(lambda ftp: ftp.rmd(src), f'rmdir {src}')
            print(f'  源根目录已空并删除：{src}')
        else:
            print(f'  源根目录还剩 {len(left)} 项（保留）：{src}')

    print(f'\n搬家完成：成功 {done}、失败 {failed}' + (f'、删除空目录 {removed} 个' if args.prune else ''))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
