#!/usr/bin/env python3
"""只读实验：设备上 `Entity_Player` 的"收藏品计数数组"到底是什么形状。

为什么需要它：`EntityPlayer:GetCollectibleNum` 从"恒 0 占位"改成真读数后，真机上套装进度
**仍然恒 `(0/3)`**。两种可能：

  A. 那对指针（`+0x1AB8` begin / `+0x1AC0` end）**确实是"按收藏品 id 索引的计数数组"** ——
     那么 `count = (end-begin)/4` 应当约等于收藏品总数（几百），读 `begin + id*4` 就是拥有数；
  B. 它其实是**别的容器**（例如"已拥有 id 的稀疏列表"）—— 那么 `count` 会很小（十几~几十），
     我们的 `collectibleId >= count` 判断会把绝大多数 id 直接当成 0 ⇒ **恒 `(0/3)`**。

一次只读就能把 A/B 分开：读 `count`，再读数组头几个字，看它们是"小整数计数"还是"收藏品 id"。

用法：
    python3 tools/probe_player_collectible_array.py --pid 140 --base 0x2949981000
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import read_device_state_via_gdb as base_tool  # noqa: E402

SYMS = {
    'identity': 0x27cd8,
    # 运行时缓存下来的游戏对象与玩家指针（都是 8 字节）
    'g_LuaGameOwnerSlot': 0x2335b0,
    'g_EnginePlayerLookupFirst': 0x23406c0,
    'g_EnginePlayerLookupCount': 0x23406b0,
    'g_GetPlayerSuccess': 0x2340720,
    'g_GetPlayerFailure': 0x2340718,
}
# Entity_Player 的字段偏移（与 runtime/source/runtime_constants.hpp 保持一致）
PLAYER = {
    'PlayerType(0x1738)': 0x1738,
    'Luck(0x1920)': 0x1920,
    'Index(0x19F0)': 0x19F0,
    'CollectibleBegin(0x1AB8)': 0x1AB8,
    'CollectibleEnd(0x1AC0)': 0x1AC0,
    'Trinket0(0x1AB0)': 0x1AB0,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip', default='192.168.124.11')
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--elf', default=base_tool.DEFAULT_ELF)
    args = ap.parse_args()

    elf = base_tool.ROOT / args.elf
    pid = args.pid or base_tool.find_pid(args.ip)
    if not pid:
        print('没找到正在运行的 Application。')
        return 3
    base = args.base
    if not base:
        base, _p, _g = base_tool.find_bases(args.ip, pid)
        if not base:
            print('定位不到运行时模块基址，请用 --base 指定。')
            return 3
    print(f'进程 = {pid}；运行时模块基址 = 0x{base:x}')

    expect = base_tool.local_bytes(elf, SYMS['identity'], 16)
    actual = base_tool.read_bytes(args.ip, pid, base + SYMS['identity'], 16)
    if expect is None or actual != expect:
        print('偏移自校验失败：设备上的模块与本地构建不是同一版。')
        return 4
    print('偏移自校验通过\n')

    head = base_tool.read_words(args.ip, pid, [
        ('game', base + SYMS['g_LuaGameOwnerSlot'], 1),
        ('player', base + SYMS['g_EnginePlayerLookupFirst'], 1),
        ('lookup_count', base + SYMS['g_EnginePlayerLookupCount'], 1),
        ('get_player_ok', base + SYMS['g_GetPlayerSuccess'], 1),
        ('get_player_fail', base + SYMS['g_GetPlayerFailure'], 1),
    ])
    game = head.get('game', [0])[0]
    player = head.get('player', [0])[0]
    print('=== 运行时缓存的指针 ===')
    print(f'  Game*                      = 0x{game:x}')
    print(f'  Entity_Player*（首个）     = 0x{player:x}')
    lc = head.get('lookup_count', [0])[0]
    ok = head.get('get_player_ok', [0])[0]
    fail = head.get('get_player_fail', [0])[0]
    print(f'  玩家查找次数/成功/失败     = {lc & 0xffffffff} / {ok & 0xffffffff} / {fail & 0xffffffff}')

    if player == 0:
        print('\n拿不到玩家指针 —— 先在游戏里进入一局，让 EID 调过 Isaac.GetPlayer 再看。')
        return 0

    print()
    print('=== Entity_Player 的字段 ===')
    fields = base_tool.read_words(
        args.ip, pid, [(name, player + off, 1) for name, off in PLAYER.items()])
    for name in PLAYER:
        got = fields.get(name, [])
        print(f'  {name:26} = 0x{got[0]:016x}' if got else f'  {name:26} 读不到')

    def u32(name):
        got = fields.get(name, [0])
        return got[0] & 0xffffffff if got else 0

    begin = fields.get('CollectibleBegin(0x1AB8)', [0])[0]
    end = fields.get('CollectibleEnd(0x1AC0)', [0])[0]
    print()
    print('=== 收藏品计数数组的形状（这是本次实验要回答的）===')
    print(f'  begin = 0x{begin:x}   end = 0x{end:x}')
    if begin == 0 or end <= begin:
        print('  ⇒ 不是一个可用的 begin/end 对 —— 我们的读法必然返回 0。')
        return 0
    span = end - begin
    count = span // 4
    print(f'  跨度 = {span} 字节 ⇒ 元素数 = {count}')
    print('  判据：元素数 ≈ 收藏品总数（几百）⇒ 按 id 索引的计数数组（我们的读法对）；'
          '元素数很小（十几~几十）⇒ 稀疏列表（读法错）。')

    probe = 24
    words = base_tool.read_words(args.ip, pid, [('vec', begin, probe)])
    vec = words.get('vec', [])
    print(f'  begin 处前 {len(vec)} 个字（低 32 位）: '
          + ', '.join(str(v & 0xffffffff) for v in vec))
    nonzero = [(i, v & 0xffffffff) for i, v in enumerate(vec) if (v & 0xffffffff) != 0]
    print(f'  其中非零项: {nonzero if nonzero else "（全 0）"}')
    print()
    print(f'  参考：Luck = {fields.get("Luck(0x1920)", [0])[0] & 0xffffffff}，'
          f'Trinket0 = {fields.get("Trinket0(0x1AB0)", [0])[0] & 0xffffffff}，'
          f'PlayerType = {u32("PlayerType(0x1738)")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
