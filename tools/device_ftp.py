#!/usr/bin/env python3
"""设备 FTP 读写小工具（一次连接只做一件事）。

为什么要单独写它：这台主机上的 FTP 服务**对连续快速连接敏感**（会返回 `451` 或直接 reset），
而项目纪律明确要求「**一个连接只做一件事、随用随关、动作之间留 1~3 秒**」。
把这条纪律写进工具里，比每次手写 `ftplib` 更不容易违反（也避免并发）。

纪律（照 `AGENTS.md`「设备取证纪律」）：
* **不要常驻**：要用时临时前台开，**用完关掉再进游戏**（常驻的 FTP 会与 `fs.mitm` 抢 SD，
  导致游戏在装载阶段崩）。
* **写卡必须在游戏未运行时做**（先用调试桩确认 `Application` 进程不在，或人工确认已退出）。
* 撞上 `451`/reset 时**等几秒再单次重试**，不要猛重试。

用法：
    python3 tools/device_ftp.py ls  /atmosphere/contents/010021C000B6A000/exefs
    python3 tools/device_ftp.py cat /atmosphere/config/system_settings.ini
    python3 tools/device_ftp.py get /path/on/device  /path/local
    python3 tools/device_ftp.py put /path/local  /path/on/device
    python3 tools/device_ftp.py sha /path/on/device            # 读回并算 sha256（写后核验用）

选项：`--host`（默认 192.168.124.11）、`--port`（默认 5000）、`--user/--password`（账号密码见 tools/device_access.py 的说明）
"""

from __future__ import annotations

import argparse
import ftplib
import hashlib
import io
import sys
import time
from pathlib import Path

DEFAULT_HOST = '192.168.124.11'
DEFAULT_PORT = 5000
import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))
from tools.device_access import resolve_device as _resolve_device
_H, _P, _U, _W = _resolve_device(required=False)
DEFAULT_USER = _U
DEFAULT_PASSWORD = _W
SETTLE_SECONDS = 1.5
RETRY_WAIT_SECONDS = 5.0


def connect(args) -> ftplib.FTP:
    ftp = ftplib.FTP()
    ftp.connect(args.host, args.port, timeout=30)
    ftp.login(args.user, args.password)
    ftp.set_pasv(True)
    return ftp


def with_retry(args, action, label: str):
    """一次连接只做一件事；失败等几秒单次重试（`451` 是服务端限流，不代表卡坏了）。"""
    last = None
    for attempt in (1, 2):
        try:
            ftp = connect(args)
            try:
                return action(ftp)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    try:
                        ftp.close()
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001 - 打印原文更有用
            last = exc
            print(f'[{label}] 第 {attempt} 次失败：{type(exc).__name__}: {exc}', file=sys.stderr)
            if attempt == 1:
                time.sleep(RETRY_WAIT_SECONDS)
        finally:
            time.sleep(SETTLE_SECONDS)
    raise SystemExit(f'[{label}] 两次都没成功：{last}')


def fetch_bytes(args, remote: str) -> bytes:
    def action(ftp: ftplib.FTP) -> bytes:
        buf = io.BytesIO()
        ftp.retrbinary(f'RETR {remote}', buf.write)
        return buf.getvalue()

    return with_retry(args, action, f'get {remote}')


def cmd_ls(args) -> int:
    lines: list[str] = []

    def action(ftp: ftplib.FTP):
        ftp.retrlines(f'LIST {args.path}', lines.append)

    with_retry(args, action, f'ls {args.path}')
    for line in lines:
        print(line)
    return 0


def cmd_cat(args) -> int:
    data = fetch_bytes(args, args.path)
    sys.stdout.write(data.decode('utf-8', 'replace'))
    print()
    return 0


def cmd_get(args) -> int:
    data = fetch_bytes(args, args.path)
    Path(args.local).write_bytes(data)
    print(f'{args.path} -> {args.local}: {len(data)} 字节  sha256={hashlib.sha256(data).hexdigest()[:16]}')
    return 0


def cmd_sha(args) -> int:
    data = fetch_bytes(args, args.path)
    print(f'{len(data)}\t{hashlib.sha256(data).hexdigest()}\t{args.path}')
    return 0


def cmd_put(args) -> int:
    payload = Path(args.local).read_bytes()

    def action(ftp: ftplib.FTP):
        ftp.storbinary(f'STOR {args.path}', io.BytesIO(payload))

    with_retry(args, action, f'put {args.path}')
    print(f'{args.local} -> {args.path}: 发出 {len(payload)} 字节  sha256={hashlib.sha256(payload).hexdigest()[:16]}')
    print('（务必再用 `sha` 子命令读回核对）')
    return 0


def cmd_mkdir(args) -> int:
    """建目录（`nro_patches` 下每个中继一个目录，写入前必须先建）。"""
    def action(ftp: ftplib.FTP):
        try:
            ftp.mkd(args.path)
        except ftplib.error_perm as exc:
            # 550 = 已存在：对"确保存在"这种用法不算失败，但要如实说明。
            if not str(exc).startswith('550'):
                raise
            print(f'{args.path} 已存在（550），未做改动')

    with_retry(args, action, f'mkdir {args.path}')
    print(f'mkdir {args.path} 完成')
    return 0


def cmd_rm(args) -> int:
    """删一个文件（只用于删自己刚放上去的探针/临时件；目录用 `rmd` 另说）。"""
    def action(ftp: ftplib.FTP):
        ftp.delete(args.path)

    with_retry(args, action, f'rm {args.path}')
    print(f'rm {args.path} 完成')
    return 0


def cmd_mv(args) -> int:
    """改名/移动（`RNFR` + `RNTO`，一次连接只做这一件事）。

    为什么用它而不是"下载再上传"：搬位置不需要改内容，改名几毫秒就完成，
    既省流量也不会有"传一半损坏"的风险（2026-09-14 把 84 个错位的描述文件搬回正确位置就靠它）。
    """
    def action(ftp: ftplib.FTP):
        ftp.rename(args.src, args.dst)

    with_retry(args, action, f'mv {args.src} -> {args.dst}')
    print(f'mv 完成：{args.src} -> {args.dst}')
    return 0


def cmd_rmdir(args) -> int:
    """删一个空目录（搬空之后清理多余目录用）。"""
    def action(ftp: ftplib.FTP):
        ftp.rmd(args.path)

    with_retry(args, action, f'rmdir {args.path}')
    print(f'rmdir {args.path} 完成')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--user', default=DEFAULT_USER)
    parser.add_argument('--password', default=DEFAULT_PASSWORD)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('ls', help='列目录')
    p.add_argument('path')
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser('cat', help='打印文本文件')
    p.add_argument('path')
    p.set_defaults(func=cmd_cat)

    p = sub.add_parser('get', help='下载到本地')
    p.add_argument('path')
    p.add_argument('local')
    p.set_defaults(func=cmd_get)

    p = sub.add_parser('put', help='上传覆盖')
    p.add_argument('local')
    p.add_argument('path')
    p.set_defaults(func=cmd_put)

    p = sub.add_parser('sha', help='读回并算大小+sha256')
    p.add_argument('path')
    p.set_defaults(func=cmd_sha)

    p = sub.add_parser('mkdir', help='建目录（已存在则如实说明）')
    p.add_argument('path')
    p.set_defaults(func=cmd_mkdir)

    p = sub.add_parser('rm', help='删一个文件（主要用来清掉自己放的探针）')
    p.add_argument('path')
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser('mv', help='改名/移动（RNFR+RNTO，一次连接）')
    p.add_argument('src')
    p.add_argument('dst')
    p.set_defaults(func=cmd_mv)

    p = sub.add_parser('rmdir', help='删一个空目录')
    p.add_argument('path')
    p.set_defaults(func=cmd_rmdir)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
