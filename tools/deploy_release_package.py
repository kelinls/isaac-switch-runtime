#!/usr/bin/env python3
"""把发布包部署到设备 SD 卡上：**先整树下载做备份与哈希比对，只补真正不同的文件**。

为什么这么做（而不是"整包覆盖"）：
* **备份要逐字节**：整树下载下来就是一份可以原样还原的备份，顺带就能算出每个文件的 sha256；
* **只写差异**：卡上多数文件本来就与发布包一致，比对后只上传不同/缺失的那些，
  FTP 连接次数从"包内全部文件"降到"真正不同的文件"，对这台敏感的服务友好得多；
* **可审计**：比对结果、备份清单、上传清单都落盘，谁都能复核。

纪律（照 AGENTS.md「设备取证纪律」）：一次连接只做一件事、动作之间留 1.5 秒、
撞 `451` 等几秒单次重试；**游戏必须没在跑**（先确认 FTP 开着且游戏未启动）。

用法：
    python3 tools/deploy_release_package.py --package dist/isaac-eid-release-20260915 \\
        --backup dist/old-card-backup-20260915 --dry-run     # 只下载+比对，不写卡
    python3 tools/deploy_release_package.py --package ... --backup ... --apply
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

HOST = '192.168.124.11'
PORT = 5000
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.device_access import resolve_device as _resolve_device
HOST, PORT, USER, PASSWORD = _resolve_device()
SETTLE_SECONDS = 1.5
RETRY_WAIT_SECONDS = 5.0
_LS_RE = re.compile(r'^([dl-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$')


def _connect() -> ftplib.FTP:
    ftp = ftplib.FTP()
    ftp.connect(HOST, PORT, timeout=30)
    ftp.login(USER, PASSWORD)
    ftp.set_pasv(True)
    return ftp


def with_connection(action, label: str):
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            ftp = _connect()
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
    """返回 [(名字, 大小, 是不是目录)]。"""
    lines: list[str] = []
    with_connection(lambda ftp: ftp.retrlines(f'LIST {path}', lines.append), f'ls {path}')
    out = []
    for line in lines:
        m = _LS_RE.match(line.strip())
        if m:
            out.append((m.group(3).strip(), int(m.group(2)), m.group(1) == 'd'))
    return out


def walk(remote_root: str) -> list[str]:
    """递归列出远端所有文件的**相对路径**。"""
    files: list[str] = []
    stack = ['']
    while stack:
        rel = stack.pop()
        full = f'{remote_root}/{rel}' if rel else remote_root
        for name, _size, is_dir in list_dir(full):
            child = f'{rel}/{name}' if rel else name
            if is_dir:
                stack.append(child)
            else:
                files.append(child)
    return sorted(files)


def fetch(remote: str) -> bytes:
    buf = io.BytesIO()
    with_connection(lambda ftp: ftp.retrbinary(f'RETR {remote}', buf.write), f'get {remote}')
    return buf.getvalue()


def store(local: Path, remote: str) -> bytes:
    data = fetch(remote)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return data


def upload(local: Path, remote: str) -> None:
    payload = local.read_bytes()

    def action(ftp: ftplib.FTP):
        ftp.storbinary(f'STOR {remote}', io.BytesIO(payload))

    with_connection(action, f'put {remote}')
    print(f'  ↑ {remote}（{len(payload)} 字节）')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', required=True, help='发布包目录（含 atmosphere/…）')
    ap.add_argument('--backup', required=True, help='整树备份与清单写到这个目录')
    ap.add_argument('--game-content', default='/atmosphere/contents/010021C000B6A000',
                    help='设备上这个游戏的 contents 目录')
    ap.add_argument('--extra', action='append', default=['/atmosphere/nro_patches'],
                    help='除 contents 之外还要一起备份/比对的远端目录（可重复）')
    ap.add_argument('--apply', action='store_true', help='真的写卡（默认只下载+比对+报告）')
    args = ap.parse_args()

    package = ROOT / args.package
    backup = ROOT / args.backup
    if not package.is_dir():
        print(f'发布包目录不存在：{args.package}')
        return 2
    backup.mkdir(parents=True, exist_ok=True)

    # 1) 发布包清单：路径 → sha256（相对 `atmosphere/`，方便与远端对齐）
    # 键统一成"相对包根、含 atmosphere/ 的路径"，与设备上的绝对路径去掉开头斜杠后**完全同形**，
    # 免得两边路径拼法不一致（第一版就因此把 199 个文件全判成"卡上有、包里没有"）。
    pack_files: dict[str, str] = {}
    for path in package.rglob('*'):
        if path.is_file():
            rel = path.relative_to(package).as_posix()
            if rel.startswith('atmosphere/'):
                pack_files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f'发布包：{len(pack_files)} 个文件（只统计 atmosphere/ 下的，说明书与校验文件不参与部署）')

    # 2) 整树下载做备份 + 算哈希
    remote_roots = [args.game_content.lstrip('/')] + [e.lstrip('/') for e in args.extra]
    card_files: dict[str, str] = {}
    for remote_root in remote_roots:
        print(f'\n枚举远端 /{remote_root} …')
        try:
            rels = walk('/' + remote_root)
        except SystemExit:
            print(f'  （远端 /{remote_root} 不存在或列不出来，跳过）')
            continue
        print(f'  共 {len(rels)} 个文件，开始下载做备份（这一步最慢，一个文件一次连接）…')
        for index, rel in enumerate(rels, 1):
            data = store(backup / remote_root / rel, f'/{remote_root}/{rel}')
            card_files[f'{remote_root}/{rel}'] = hashlib.sha256(data).hexdigest()
            if index % 25 == 0 or index == len(rels):
                print(f'  [{index}/{len(rels)}] 已备份到 {backup.name}/')

    (backup / 'backup-manifest.json').write_text(json.dumps({
        'source': remote_roots, 'files': card_files}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n备份完成：{len(card_files)} 个文件 → {backup}（清单 backup-manifest.json）')

    # 3) 比对：发布包 vs 卡（都按 "contents/…" / "nro_patches/…" 这种"atmosphere 下"的相对路径）
    to_upload: list[str] = []
    same = 0
    for rel, digest in sorted(pack_files.items()):
        card_digest = card_files.get(rel)
        if card_digest is None or card_digest != digest:
            to_upload.append(rel)
        else:
            same += 1

    print('\n=== 比对结果（发布包 vs 卡）===')
    print(f'  一致 {same}；需要上传/覆盖 {len(to_upload)}')
    for rel in to_upload:
        print(f'  ↑ 要写：{rel}')

    card_only = [k for k in sorted(card_files) if k not in pack_files]
    print(f'\n卡上有、发布包里没有的 {len(card_only)} 个文件（不自动删，列出来人工判断）：')
    for key in card_only:
        print(f'  · {key}')

    report = {'same': same, 'to_upload': to_upload, 'card_only': card_only,
              'card_files': len(card_files), 'pack_files': len(pack_files)}
    (backup / 'deploy-diff.json').write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                             encoding='utf-8')

    if not args.apply:
        print('\n（没有 --apply：以上只做了备份与比对，卡没被改动）')
        return 0

    # 4) 写卡：只上传需要补的文件
    print('\n=== 开始写卡（只补差异）===')
    for rel in to_upload:
        upload(package / rel, f'/{rel}')
    print(f'\n写卡完成：{len(to_upload)} 个文件')
    return 0


if __name__ == '__main__':
    sys.exit(main())
