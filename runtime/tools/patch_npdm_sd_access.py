#!/usr/bin/env python3
"""给 NPDM 的 FS 权限加上「SD 卡」那一位（`FsAccessFlag` bit 21 = SdCard）。

为什么需要它：方案 A 把模组文件搬到 `sdmc:/isaac_mods/`，但真机上运行时**读不到清单**
（诊断字 `[11] = 0x00000401` = 读清单失败、清单缓冲区为空）。官方文档里
`FsAccessFlag` 的第 21 位就是 "SdCard"（<https://switchbrew.org/wiki/NPDM>），
而这份 NPDM 的 `FsAccessFlag` 是 `0x4000000000000000`——**没置这一位**，
于是进程根本没有 SD 卡权限，`sdmc:` 打不开。

NPDM 里 FS 权限有**两份**：ACI0 一份、ACID 一份（内容一致），两份都要改。

用法：
    python3 runtime/tools/patch_npdm_sd_access.py <输入.npdm> <输出.npdm> [--dump]
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import struct

BITS = {
    0: 'ApplicationInfo', 1: 'BootModeControl', 2: 'Calibration', 11: 'ContentManager',
    12: 'ImageManager', 13: 'CreateSaveData', 14: 'SystemSaveDataManagement',
    15: 'BisFileSystem', 16: 'SystemUpdate', 17: 'SaveDataMeta', 18: 'DeviceSaveData',
    19: 'SettingsControl', 20: 'SystemData', 21: 'SdCard', 22: 'Host', 23: 'FillBis',
    24: 'CorruptSaveData', 25: 'SaveDataForDebug', 26: 'FormatSdCard', 27: 'GetRightsId',
    28: 'RegisterExternalKey', 29: 'RegisterUpdatePartition', 30: 'SaveDataTransfer',
    31: 'DeviceDetection', 32: 'AccessFailureResolution', 33: 'SaveDataTransferVersion2',
    34: 'RegisterProgramIndexMapInfo', 35: 'CreateOwnSaveData', 36: 'MoveCacheStorage',
    37: 'DeviceTreeBlob', 38: 'NotifyErrorContextServiceReady', 39: 'CalibrationSystemData',
    40: 'CalibrationLog', 41: 'StorageSecure',
}
SD_BIT = 21


def fs_blocks(data: bytes) -> list[tuple[str, int]]:
    """返回 [(哪一份, FsAccessFlag 字段的文件偏移)]。"""
    aci = struct.unpack_from('<I', data, 0x70)[0]
    acid = struct.unpack_from('<I', data, 0x78)[0]
    out = []
    # ⚠️ ACID 前面有 0x200 字节的签名 + 公钥，所以它的字段表从 base+0x200 开始算
    # （ACI0 没有这层，字段表就在 base+0x20）——第一版就是这里算错，读出天文数字。
    for name, base, header in (('ACI0', aci, 0x0), ('ACID', acid, 0x200)):
        block_off, block_size = struct.unpack_from('<II', data, base + header + 0x20)
        if block_size < 0x10:
            continue
        # 两份的 FsAccessFlag 都在自己那份块的 +0x4（ACI0 是 u64；ACID 同）
        out.append((name, base + block_off + 0x4))
    return out


def describe(flag: int) -> str:
    names = [BITS.get(i, f'bit{i}') for i in range(64) if flag >> i & 1]
    return ', '.join(names) if names else '（没有置任何位）'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('output', nargs='?')
    ap.add_argument('--dump', action='store_true')
    args = ap.parse_args()

    src = pathlib.Path(args.source)
    data = bytearray(src.read_bytes())
    blocks = fs_blocks(data)
    if not blocks:
        print('没找到 FS 权限块，NPDM 结构不对？')
        return 2

    for name, off in blocks:
        flag = struct.unpack_from('<Q', data, off)[0]
        print(f'{name}: FsAccessFlag = 0x{flag:016x}  → {describe(flag)}')
    if args.dump:
        return 0

    for name, off in blocks:
        flag = struct.unpack_from('<Q', data, off)[0]
        struct.pack_into('<Q', data, off, flag | (1 << SD_BIT))
    print(f'已给两份都加上 bit {SD_BIT}（{BITS[SD_BIT]}）')

    if not args.output:
        print('（没给输出路径：只做了预览）')
        return 0
    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(data))
    for name, off in fs_blocks(data):
        flag = struct.unpack_from('<Q', data, off)[0]
        print(f'{name}: 现在是 0x{flag:016x} → {describe(flag)}')
    print(f'{src.name}（{len(src.read_bytes())} 字节 / {hashlib.sha256(src.read_bytes()).hexdigest()[:16]}）')
    print(f'→ {out}（{out.stat().st_size} 字节 / {hashlib.sha256(out.read_bytes()).hexdigest()[:16]}）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
