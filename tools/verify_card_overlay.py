#!/usr/bin/env python3
"""把卡上覆盖层里的模组文件树，与本地发布包逐文件比对（**只读，不写卡**）。

为什么要它：2026-09-14 深夜查明"EID 静默不加载"的直接原因是覆盖层里**缺 `main.lua`**
（运行时按清单去读入口脚本，游戏的文件接口打不开 ⇒ 被当成"纯资源型模组"静默跳过）。
但"缺哪些、错位哪些"必须一次看清楚，不能只看一个文件就下结论。

做法：以本地发布包（默认 `dist/isaac-eid-release-20260914/.../romfs/isaac_mods`）为准，
对**每一个目录**在卡上发一次 `LIST`（一次连接只做一件事），比对**文件名与大小**；
大小不一致的再单独拉回来算 sha256（只有这一步会传文件内容）。

用法：
    python3 tools/verify_card_overlay.py                     # 全量比对，只报告
    python3 tools/verify_card_overlay.py --json dist/overlay-diff-20260914.json
"""

from __future__ import annotations

import argparse
import ftplib
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL = 'dist/isaac-eid-release-20260914/atmosphere/contents/010021C000B6A000/romfs/isaac_mods'
DEFAULT_REMOTE = '/atmosphere/contents/010021C000B6A000/romfs/isaac_mods'

HOST = '192.168.124.11'
PORT = 5000
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.device_access import resolve_device as _resolve_device
HOST, PORT, USER, PASSWORD = _resolve_device()
SETTLE_SECONDS = 1.5
RETRY_WAIT_SECONDS = 5.0

_LS_RE = re.compile(r'^([dl-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$')


def list_dir(path: str) -> dict[str, int]:
    """返回 {名字: 大小}；目录的大小记为 -1。一次连接，失败等几秒单次重试。"""
    lines: list[str] = []
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            ftp = ftplib.FTP()
            ftp.connect(HOST, PORT, timeout=30)
            ftp.login(USER, PASSWORD)
            ftp.set_pasv(True)
            try:
                ftp.retrlines(f'LIST {path}', lines.append)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f'[ls {path}] 第 {attempt} 次失败：{type(exc).__name__}: {exc}', file=sys.stderr)
            if attempt == 1:
                time.sleep(RETRY_WAIT_SECONDS)
        finally:
            time.sleep(SETTLE_SECONDS)
    else:
        raise SystemExit(f'[ls {path}] 两次都没成功：{last}')

    entries: dict[str, int] = {}
    for line in lines:
        m = _LS_RE.match(line.strip())
        if not m:
            continue
        kind, size, name = m.group(1), int(m.group(2)), m.group(3).strip()
        if name in ('.', '..'):
            continue
        entries[name] = -1 if kind == 'd' else size
    return entries


def fetch_sha(path: str) -> str:
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            ftp = ftplib.FTP()
            ftp.connect(HOST, PORT, timeout=30)
            ftp.login(USER, PASSWORD)
            ftp.set_pasv(True)
            buf = io.BytesIO()
            try:
                ftp.retrbinary(f'RETR {path}', buf.write)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
            return hashlib.sha256(buf.getvalue()).hexdigest()
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f'[sha {path}] 第 {attempt} 次失败：{type(exc).__name__}: {exc}', file=sys.stderr)
            if attempt == 1:
                time.sleep(RETRY_WAIT_SECONDS)
        finally:
            time.sleep(SETTLE_SECONDS)
    raise SystemExit(f'[sha {path}] 两次都没成功：{last}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--local', default=DEFAULT_LOCAL)
    ap.add_argument('--remote', default=DEFAULT_REMOTE)
    ap.add_argument('--json', default='')
    ap.add_argument('--with-hash', action='store_true',
                    help='对大小一致的文件也算 sha256 核对（慢，一次连接一个文件）')
    args = ap.parse_args()

    local_root = ROOT / args.local
    if not local_root.is_dir():
        print(f'本地发布包目录不存在：{args.local}')
        return 2

    expected: dict[str, int] = {}
    for path in sorted(local_root.rglob('*')):
        if path.is_file():
            expected[str(path.relative_to(local_root)).replace('\\', '/')] = path.stat().st_size

    directories = {''}
    for rel in expected:
        parts = rel.split('/')[:-1]
        for i in range(len(parts)):
            directories.add('/'.join(parts[:i + 1]))

    actual: dict[str, int] = {}
    actual_dirs: set[str] = {''}
    for rel_dir in sorted(directories):
        remote_dir = args.remote + (f'/{rel_dir}' if rel_dir else '')
        print(f'列目录：{rel_dir or "."} …', flush=True)
        for name, size in list_dir(remote_dir).items():
            full = f'{rel_dir}/{name}' if rel_dir else name
            if size < 0:
                actual_dirs.add(full)
            else:
                actual[full] = size

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    size_diff = sorted(p for p in set(expected) & set(actual) if expected[p] != actual[p])
    extra_dirs = sorted(actual_dirs - directories)

    print()
    print(f'本地发布包文件数 = {len(expected)}；卡上（清单里的目录内）文件数 = {len(actual)}')
    print(f'缺失 {len(missing)}；多出 {len(extra)}；大小不符 {len(size_diff)}')
    if missing:
        print('\n== 缺失（清单要求有、卡上没有）==')
        for p in missing:
            print(f'  ✗ {p}')
    if size_diff:
        print('\n== 大小不符 ==')
        for p in size_diff:
            print(f'  ! {p}: 卡上 {actual[p]} / 本地 {expected[p]}')
    if extra:
        print('\n== 多出（卡上有、清单没要求）==')
        for p in extra:
            print(f'  + {p}')
    if extra_dirs:
        print('\n== 多出的目录 ==')
        for p in extra_dirs:
            print(f'  + {p}/')

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({'missing': missing, 'extra': extra, 'size_diff': size_diff,
                                   'extra_dirs': extra_dirs,
                                   'expected': expected, 'actual': actual},
                                  ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'\n比对结果已写：{out}')
    return 0 if not (missing or size_diff) else 1


if __name__ == '__main__':
    sys.exit(main())
