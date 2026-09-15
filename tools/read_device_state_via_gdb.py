#!/usr/bin/env python3
"""通过大气层的调试桩，读设备上「正在运行的以撒」里我们自己的状态（只读）。

为什么需要它：设备侧原有的通道都有硬限制——写文件那条受 SaltyNX 会话生命周期约束
（注入流程结束即失效），崩溃报告一次会话只能取一次。调试桩（大气层自带）可以在游戏
运行时**直接读内存**，一次拿到全部读数：启动状态机走到第几步、清单安装为什么失败、
挂点各自装成什么样、运行时的文件表是否已植入、Lua 侧有没有登记成功。

★ 2026-09-14 夜间改版：**默认走"一次挂载读完所有地址"**。
原因：实测这个调试桩**每次开机只可靠支撑很少几次 attach**，用光后重试不会恢复，只能重启主机
（见 `docs/交接文档-20260914.md` 第五节 B 与 `.workbuddy/memory/2026-09-14.md` 的"调试桩使用预算"）。
旧版一次读数要 attach 四次（找进程 → 取基址 → 读身份 → 读变量），常常读到一半就没预算了。
新版把四件事合并进**同一个 gdb 会话、只 attach 一次**：会话是交互式的（gdb 从管道读命令），
先用 `monitor get info` 拿到基址，再用算出来的地址继续发读命令，最后 `detach`。

用法：
    python3 tools/read_device_state_via_gdb.py                      # 一次挂载读完（推荐）
    python3 tools/read_device_state_via_gdb.py --ip 192.168.124.11
    python3 tools/read_device_state_via_gdb.py --pid 141 --base 0x4e8a181000 --game-base 0x...
    python3 tools/read_device_state_via_gdb.py --dry-run            # 只测管道，不连设备

前置条件见 docs/问题与解决记录.md 的「用调试桩直接读设备状态」一节。
"""

from __future__ import annotations

import argparse
import os
import re
import select
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GDB_IMAGE = 'devkitpro/devkita64:latest'
DEFAULT_ELF = 'runtime/.gdbsym-artifacts/runtime.elf'

# 运行时的符号偏移。**必须与设备上那份模块同版本**。
# 同步时间：2026-09-14 23:09 构建（也就是设备上那份诊断版 `subsdk9`，410722 字节 /
# sha256 前 16 位 `633cdbedd743bc90`）。脚本会用三个**代码锚点**（身份函数、`exl_main`、
# `TryInstallDefaultManifestMod` 的函数头）与本地 ELF 逐字节比对——三个都对上，说明
# "设备上跑的这份"与"本表对应的那份"是同一个映像，读数才有意义。
#
# ⚠️ 教训（写在代码里免得再犯）：`identity` 那 16 字节的自校验只能证明"是同一版族"，
# **分不出相邻两次构建**；改过任何编译单元都可能让 `bss`/`data` 整体挪位。
# 所以任何一次重建之后，都必须用 `tools/elf_syms.py` 重新核对下面整张表。
SYMS = {
    # ---- 代码锚点（只用于核对版本，不作为"变量"读）----
    'identity': 0x27e48,              # IsaacModRuntime_GetRuntimeIdentity
    'exl_main': 0x8890,               # 模块入口（旧表写 0x8840，那是 20:00 那次的构建）
    'manifest_install': 0x361c,       # TryInstallDefaultManifestMod（本次诊断改的就是它）
    'entry_relay_update': 0x1910,     # EntryRelayManagerUpdateCallback（更新挂点入口中继）
    'entry_relay_render': 0x1904,     # EntryRelayManagerRenderCallback（渲染挂点）
    'entry_relay_collectible': 0x1f7c,  # EntryRelayPreGetCollectibleCallback
    'entry_relay_rebuild': 0x1ff8,    # EntryRelayRebuildMountPointsCallback
    'present_got_intercept': 0x16e4,  # IsaacModRuntime_PresentGotSlotIntercept
    # ---- 运行状态（相对我们的运行时模块）----
    'g_runtimeSelfAddress': 0x23476b8,
    'g_sequence': 0x2347940,          # TestRunObserver 状态机：走过的步数
    'g_stateDetail': 0x2347948,       # 低 32 位 = 状态号，高 32 位 = 该步的 detail
    'g_diagnosticFileApi': 0x23478f8,
    'g_hookCallbacks': 0x2339620,
    'g_HookInstallResults': 0x2345c8,   # 16 字诊断数组（线格式，见 IsaacModRuntime_GetHookDiagnostics）
    'g_HookEnabled': 0x234610,
    'g_RenderPresentRelayState': 0x2345bc,
    'g_PresentGotSlotTarget': 0xb40f0,
    # ---- 2026-09-14 夜间加：清单/挂点失败原因（这次读数的主角）----
    'g_DefaultManifestFailureWord': 0x234598,   # 诊断字 [11]，打包格式见 mod_load_step.hpp
    'g_DefaultManifestFailureDetail': 0x23459c,  # 旧步骤字（1=读清单 2=解析 3=拼路径 4=读入口 5=纯资源）
    'g_DefaultManifestState': 0x2345a0,          # 0 未武装 1 已武装 2 安装中 3 就绪 4 失败
    'g_HookInstallFailureCode': 0x234614,        # 入口中继后端转发的失败码（0 = 无失败）
    'g_HookInstallFailureSlot': 0x234618,        # 第一个失败挂点 index+1（0 = 没有装失败的）
    'g_HookInstallInstalledCount': 0x23461c,     # 装好的挂点数
    'g_PendingGameStarted': 0x234624,            # 待派发的"游戏开局"事件
    # ---- Lua 侧 ----
    'g_ModRegistered': 0x334da4,       # 模组是否登记成功（1 字节；= Lua 真的跑起来了）
    'g_LastLuaErrorLength': 0x334c94,
    'g_LastLuaErrorText': 0x334c98,    # 256 字节文本
    'g_PostUpdateCount': 0x334c80,
    'g_CallbackRegistry': 0xa0578,     # 回调登记表：每项 20 字节，count_ 在 +0x1400
    # ---- 运行时读进来的文件内容（诊断"模组为什么算纯资源型"的关键）----
    'g_DefaultManifest': 0x1b4560,     # 上次读进来的 `manifest.json` 原文（512 KiB 缓冲）
    'g_DefaultEntry': 0xb4560,         # 上次读进来的入口脚本原文（1 MiB 缓冲）
    # ---- 下面三条都相对**游戏模块**----
    'manager_update_entry': 0x3F8DB8,  # `Manager::Update` 入口（该是原样 `ff4301d1...`）
    'manager_render_entry': 0x3F9684,  # `Manager::Render` 入口（该是原样 `ffc302d1...`）
    'present_callsite_offset': 0x3F9B40,  # `Manager::Render` 体内那次 `bl Present`
    'present_got_slot': 0xA9E488,      # Present 的 GOT 槽（应等于 运行时基址 + present_got_intercept）
    'present_target': 0x4F3014,        # `Present` 本体（期望值比对用）
}
#: 上表里"属于**我们运行时模块**"的那些键 → 它们在本地 ELF 里的**精确符号名**。
#: 有这张表，偏移就不必再靠人手抄：`sync_syms_from_elf()` 每次读数前直接问 ELF。
#: 不在表里的键（`manager_update_entry` 那几条）是**游戏模块**的常量，与我们的重建无关，保持原值。
SYM_ELF_NAMES = {
    'identity': 'IsaacModRuntime_GetRuntimeIdentity',
    'exl_main': 'exl_main',
    'manifest_install': '_Z28TryInstallDefaultManifestModRK12TargetModule',
    'entry_relay_update': 'EntryRelayManagerUpdateCallback',
    'entry_relay_render': 'EntryRelayManagerRenderCallback',
    'entry_relay_collectible': 'EntryRelayPreGetCollectibleCallback',
    'entry_relay_rebuild': 'EntryRelayRebuildMountPointsCallback',
    'present_got_intercept': 'IsaacModRuntime_PresentGotSlotIntercept',
    'g_runtimeSelfAddress': 'g_runtimeSelfAddress',
    'g_sequence': '_ZN12_GLOBAL__N_1L10g_sequenceE',
    'g_stateDetail': '_ZN12_GLOBAL__N_1L13g_stateDetailE',
    'g_diagnosticFileApi': '_ZN12_GLOBAL__N_1L19g_diagnosticFileApiE',
    'g_hookCallbacks': '_ZN5isaac7runtime12_GLOBAL__N_1L15g_hookCallbacksE',
    'g_HookInstallResults': '_ZN12_GLOBAL__N_1L20g_HookInstallResultsE',
    'g_HookEnabled': '_ZN12_GLOBAL__N_1L13g_HookEnabledE',
    'g_RenderPresentRelayState': '_ZN12_GLOBAL__N_1L25g_RenderPresentRelayStateE',
    'g_PresentGotSlotTarget': 'g_PresentGotSlotTarget',
    'g_DefaultManifestFailureWord': '_ZN12_GLOBAL__N_1L28g_DefaultManifestFailureWordE',
    'g_DefaultManifestFailureDetail': '_ZN12_GLOBAL__N_1L30g_DefaultManifestFailureDetailE',
    'g_DefaultManifestState': '_ZN12_GLOBAL__N_1L22g_DefaultManifestStateE',
    'g_HookInstallFailureCode': '_ZZN5isaac7runtime22HookInstallFailureCodeEvE5value',
    'g_HookInstallFailureSlot': '_ZZN5isaac7runtime22HookInstallFailureSlotEvE5value',
    'g_PendingGameStarted': '_ZN10LuaRuntime12_GLOBAL__N_1L20g_PendingGameStartedE',
    'g_ModRegistered': '_ZN10LuaRuntime12_GLOBAL__N_1L15g_ModRegisteredE',
    'g_LastLuaErrorLength': '_ZN10LuaRuntime12_GLOBAL__N_1L20g_LastLuaErrorLengthE',
    'g_LastLuaErrorText': '_ZN10LuaRuntime12_GLOBAL__N_1L18g_LastLuaErrorTextE',
    'g_PostUpdateCount': '_ZN10LuaRuntime12_GLOBAL__N_1L17g_PostUpdateCountE',
    'g_CallbackRegistry': '_ZN10LuaRuntime12_GLOBAL__N_1L18g_CallbackRegistryE',
    'g_DefaultManifest': '_ZN12_GLOBAL__N_1L17g_DefaultManifestE',
    'g_DefaultEntry': '_ZN12_GLOBAL__N_1L14g_DefaultEntryE',
    # ---- 错误通道探针（`tools/probe_lua_error_channel.py`）也用这张表 ----
    'g_CallbackError': '_ZN10LuaRuntime12_GLOBAL__N_1L15g_CallbackErrorE',
    'g_RequireFailureTotal': '_ZN10LuaRuntime12_GLOBAL__N_1L21g_RequireFailureTotalE',
    'g_RequireLastFailureName': '_ZN10LuaRuntime12_GLOBAL__N_1L24g_RequireLastFailureNameE',
    'g_RequireLastFailureCode': '_ZN10LuaRuntime12_GLOBAL__N_1L24g_RequireLastFailureCodeE',
    'g_RequireFirstFailureName': '_ZN10LuaRuntime12_GLOBAL__N_1L25g_RequireFirstFailureNameE',
    'g_RequireFirstFailureCode': '_ZN10LuaRuntime12_GLOBAL__N_1L25g_RequireFirstFailureCodeE',
    'g_RequireErrorTail': '_ZN10LuaRuntime12_GLOBAL__N_1L18g_RequireErrorTailE',
    'g_RequireFailureDetail': '_ZN10LuaRuntime12_GLOBAL__N_1L22g_RequireFailureDetailE',
    'g_Dispatchable': '_ZN10LuaRuntime12_GLOBAL__N_1L14g_DispatchableE',
    'g_Unhooked': '_ZN10LuaRuntime12_GLOBAL__N_1L10g_UnhookedE',
}
#: 表里有、但**这次构建的 ELF 里已经找不到**的符号：不能沿用过期的偏移（那是"看起来有值、
#: 其实是别人的变量"），只能在读数时报"读不到"。这里只记账，方便汇报时说明缺了什么。
SYM_MISSING: dict[str, str] = {}
#: 已经不再存在的全局量（2026-09-15：装好的挂点数改为从 `g_HookInstallResults` 现算，没有独立变量）。
SYM_UNRESOLVABLE = ('g_HookInstallInstalledCount',)
_syms_synced = False

HOOK_NAMES = ['ManagerUpdate', 'ManagerRender', 'PreGetCollectible',
              'ManagerPresent', 'RebuildMountPoints', 'GameStart']
# 只用来做比对、本身不是"要读的内存变量"的符号（读数请求要跳过它们）。
SYM_ONLY = ('identity', 'manifest_install',
            'entry_relay_update', 'entry_relay_render',
            'entry_relay_collectible', 'entry_relay_rebuild',
            'present_got_slot', 'present_target', 'present_callsite_offset')
# 已经切到零占洞入口中继的挂点 → 它的回调应当等于"模块基址 + 下面这个符号的偏移"。
RELAY_BY_HOOK = {'ManagerUpdate': 'entry_relay_update',
                 'ManagerRender': 'entry_relay_render',
                 'PreGetCollectible': 'entry_relay_collectible',
                 'RebuildMountPoints': 'entry_relay_rebuild'}
# 插件里 g_helper 的位置：0xd220 就是它在插件 ELF 里的虚拟地址——插件的段是连续的，
# 所以**物理地址 = 插件代码段基址 + 虚拟地址**（实测：0x090ec1e000 + 0xd220 = 0x090ec2b220）。
PLUGIN_HELPER = 0xd220
HANDOVER_FIELDS = ['api[0] open', 'api[1] read', 'api[2] write', 'api[3] close',
                   'identity', 'diagnosticsState', 'selfJournalState',
                   'setPluginNote', 'setSymbolLookup', 'snapshot',
                   'lookupAddress', 'ownIdentity', 'ownBase', 'fileApiSlot']

# 版本核对用的锚点：本地 ELF 里从该偏移起 16 字节，设备上必须逐字节相同。
ANCHOR_NAMES = ('identity', 'exl_main', 'manifest_install')

TEST_RUN_STATES = ['Unseen', 'ExlMainEntered', 'ModuleWorkerEntered', 'TitleStateRead',
                   'ModuleScanEntered', 'ModuleScanReturned', 'ManifestInstallEntered',
                   'ManifestInstallReturned', 'LuaInitializeEntered', 'LuaInitializeReturned',
                   'FinalReportEntered']
MANIFEST_STATES = ['Unarmed(未武装)', 'Armed(已武装)', 'Running(安装中)', 'Ready(就绪)', 'Failed(失败)']
MANIFEST_STEPS = {0: '成功/无失败', 1: '读清单失败', 2: '解析清单失败', 3: '拼路径失败',
                  4: '读入口脚本失败', 5: '纯资源型（无脚本，不是失败）'}
# `LuaRuntime::LuaInitResult`（`0x10 + 这个值` 就是报告里看到的 detail）。
LUA_INIT_RESULTS = {0: '成功', 1: 'StateCreateFailed（建 lua_State 失败）',
                    2: 'RuntimePreparationMemoryFailed（准备运行时内存失败）',
                    3: 'RuntimePreparationFailed（准备运行时失败）',
                    4: 'ScriptLoadFailed（脚本装载失败）',
                    5: 'ScriptRunFailed（脚本**执行中报错**，具体错误见下面的 Lua 错误文本）',
                    6: 'MissingPostUpdateCallback（没登记 MC_POST_UPDATE）'}


def decode_failure_detail(value: int) -> str:
    """把 `g_DefaultManifestFailureDetail` / 状态机 detail 这个字翻成人话。

    它不是单一枚举：低段是"清单加载步骤"（1..4 失败、5 纯资源型），
    `0x10 + n` 是 Lua 初始化失败，`0x20 + n` 是请求本身不可用（见 `mod_load_step.hpp`）。
    """
    if value == 0:
        return MANIFEST_STEPS[0]
    if value == 5:
        return MANIFEST_STEPS[5]
    if 1 <= value <= 4:
        return MANIFEST_STEPS[value]
    if 0x10 <= value < 0x20:
        n = value - 0x10
        return f'Lua 初始化失败：{LUA_INIT_RESULTS.get(n, f"未知({n})")}'
    if 0x20 <= value < 0x30:
        return f'请求本身不可用（StatusCode {value - 0x20}）'
    return f'未知({value})'
# `HookInstallResult` 家族（旧安装器路径）的失败码：0 = 成功，其余见 hook_manager.hpp。
LEGACY_HOOK_CODES = {0: 'Success', 1: 'InstructionMismatch(指令不符)', 2: 'RelayPatchMismatch(中继补丁不符)',
                     3: 'RelaySlotNotEmpty(中继槽非空)', 4: 'RelayPublishFailed(中继发布失败)',
                     5: 'GameBindingsMismatch', 6: 'GameBindingsPublishFailed', 7: 'MusicBindingsMismatch'}
PRESENT_RELAY_CODES = {0: 'Success', 1: 'RelayPatchMismatch', 2: 'RelaySlotNotEmpty',
                       3: 'RelayPublishFailed', 4: 'GotSlotTargetMismatch(桩/本体/调用点守卫不符)',
                       5: 'GotSlotUnavailable(槽没映射或值为 0)', 6: 'GotSlotWriteFailed(写完读回不一致)'}
HOOK_OUTCOMES = ['Pending(没试过)', 'Installed(装好)', 'Skipped(跳过)', 'Failed(装失败)']


# ---------------------------------------------------------------------------
# 一次挂载读完：交互式 gdb 会话
# ---------------------------------------------------------------------------

class GdbSession:
    """一个长活的 gdb 进程：命令一行一条地喂进去，靠哨兵串确认一条命令的输出已经收完。

    为什么不用 `-batch -x 脚本`：那种方式**一次会话只能发固定的一串命令**，而我们要先用
    `monitor get info` 的输出算出基址、再据此拼出读命令 —— 这要求"能来回对话"。
    又因为 gdb 这份构建**没有 Python 支持**（`Python scripting is not supported`），
    解析只能在宿主这边做，所以用了管道 + 哨兵。
    """

    def __init__(self, timeout: float = 120.0) -> None:
        self.timeout = timeout
        self._counter = 0
        cmd = ['docker', 'run', '--rm', '-i', GDB_IMAGE, 'bash', '-lc',
               '. /opt/devkitpro/devkita64.sh && exec aarch64-none-elf-gdb -q -nx']
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
        self.log: list[str] = []
        self.command('set pagination off')
        self.command('set confirm off')

    def _drain(self, token: bytes) -> str:
        buf = b''
        deadline = time.time() + self.timeout
        while token not in buf:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f'等不到哨兵 {token!r}；已收到：\n{buf.decode(errors="replace")}')
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 4096)
            if not chunk:
                raise EOFError(f'gdb 提前退出；已收到：\n{buf.decode(errors="replace")}')
            buf += chunk
        # 把提示符之类的残余读干净（非阻塞）。
        while select.select([self.proc.stdout], [], [], 0.15)[0]:
            extra = os.read(self.proc.stdout.fileno(), 4096)
            if not extra:
                break
            buf += extra
        text = buf.decode(errors='replace')
        text = text.split(token.decode(), 1)[0]
        text = text.replace('(gdb) ', '')  # gdb 的提示符混在输出里，去掉免得干扰解析
        self.log.append(text)
        return text

    def command(self, text: str) -> str:
        self._counter += 1
        token = f'@@DSH_DONE_{self._counter}@@'.encode()
        payload = text.rstrip('\n') + '\n' + f'echo {token.decode()}\\n\n'
        assert self.proc.stdin is not None
        self.proc.stdin.write(payload.encode())
        self.proc.stdin.flush()
        return self._drain(token)

    def close(self) -> None:
        try:
            self.command('quit')
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def parse_processes(text: str) -> int | None:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith('Applicat'):
            return int(parts[0])
    return None


def parse_bases(text: str) -> tuple[int | None, int | None, int | None]:
    """返回（运行时模块基址, 插件基址, 游戏模块基址）。

    游戏模块那一项是 `Repentance.nrs.elf`：**GOT 槽与 `Present` 偏移都相对它**，
    不是相对我们的运行时模块 —— 两者混用会读出一堆看似"不符"的垃圾值（踩过）。
    """
    module = plugin = game = None
    for line in text.splitlines():
        m = re.match(r'\s*0x([0-9a-f]+) - 0x([0-9a-f]+)\s+(\S+)', line)
        if not m:
            continue
        lo, hi, name = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
        if name == 'runtime.elf':
            if hi - lo > 0x20000:
                module = lo
            else:
                plugin = lo
        elif name.startswith('Repentance.'):
            game = lo
    return module, plugin, game


def parse_words(text: str) -> dict[str, list[int]]:
    """从 `TAG xxx` + `x/Ngx` 的输出里取值（按标签分组）。"""
    values: dict[str, list[int]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith('TAG '):
            current = line[4:].strip()
            values[current] = []
        elif current and re.match(r'^0x[0-9a-f]+\s*(<[^>]*>)?:', line):
            _, rest = line.split(':', 1)
            for tok in rest.split():
                try:
                    values[current].append(int(tok, 16))
                except ValueError:
                    pass
    return values


def parse_mappings(text: str) -> list[tuple[int, int, str, str]]:
    """解析 `monitor get mappings`：返回 [(起, 止, 权限, 种类)]。

    格式（实测）：
      `0x2f80800000 - 0x2f80fcefff rw- Normal           ---- [0, 0]`
      `0x18d2c0e000 - 0x18d2c0efff rw- SharedCode       ---- [0, 0]`
    只有带权限字母（不是 `---`）的段才是真的映射过的。
    """
    out: list[tuple[int, int, str, str]] = []
    for line in text.splitlines():
        m = re.match(r'\s*0x([0-9a-f]+)\s*-\s*0x([0-9a-f]+)\s+([rwx-]{3})\s+(\S+)', line)
        if not m:
            continue
        perms = m.group(3)
        if perms == '---':
            continue
        out.append((int(m.group(1), 16), int(m.group(2), 16), perms, m.group(4)))
    return out


def locate_by_scan(session: 'GdbSession', elf: Path) -> tuple[int | None, int | None]:
    """`monitor get info` 不给模块清单时的兜底：扫内存映射，按**内容指纹**认出两个模块。

    为什么能这么认：身份函数那 16 字节是**我们自己的代码**，只有我们的运行时模块里才有；
    而 `Manager::Update` 入口那 8 字节要么是原版序言、要么是我们自己的入口中继桩，二者都认识。
    两处都只用"模块起始 + 已知偏移"，不依赖调试桩的模块表。
    """
    maps = parse_mappings(session.command('monitor get mappings'))
    if not maps:
        return None, None
    print(f'  （monitor get info 没给模块表，改用 mappings 扫描：{len(maps)} 个已映射段）')
    identity_expect = local_bytes(elf, SYMS['identity'], 16)
    candidates = [start for start, end, _p, _k in maps
                  if end - start > SYMS['identity'] + 0x100 and _k not in ('Stack', 'SharedCode')]
    cmds = []
    for i, start in enumerate(candidates):
        cmds.append(f'echo TAG cand_{i}\\n')
        cmds.append(f'x/2gx 0x{start + SYMS["identity"]:x}')
        cmds.append(f'echo TAG game_{i}\\n')
        cmds.append(f'x/2gx 0x{start + SYMS["manager_update_entry"]:x}')
    values = parse_words(session.command('\n'.join(cmds)))
    module = game = None
    for i, start in enumerate(candidates):
        got = b''.join(v.to_bytes(8, 'little') for v in (values.get(f'cand_{i}') or [])[:2])
        if got and got == identity_expect and module is None:
            module = start
        entry = values.get(f'game_{i}') or []
        if entry and game is None:
            word = entry[0] & 0xFFFFFFFF
            # 我们的入口中继桩（`ldr x16,#8; br x16`）或原版 `Manager::Update` 序言。
            if word in (0x58000050, 0xFF4301D1):
                game = start
    return module, game


def sync_syms_from_elf(elf: Path, *, verbose: bool = False) -> dict[str, str]:
    """用**本地这份 ELF**的符号表刷新"模块内偏移"，返回没解析到的符号。

    为什么要有这一步：偏移表是手抄的，而**改任何一个编译单元都会让 bss/data 整体挪位**
    （项目已经因此误读过两次）。手抄表一定会过期，唯一不会过期的来源就是 ELF 自己。
    解析不到的符号**直接从表里删掉**——沿用过期的偏移比读不到更糟：它会把别人的变量
    当成我们要的那个，读数"看起来有值"却是错的。
    """
    global _syms_synced
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import elf_syms  # 同目录的极简 ELF 解析器（只依赖标准库）

    if not elf.exists():
        _syms_synced = False
        print(f'  ⚠ 找不到本地 ELF（{elf}），沿用表内偏移 —— 读数可能与当前构建不符')
        return {'(整个 ELF)': '文件不存在'}
    symbols = elf_syms.load_symbols(elf.read_bytes())
    if not symbols:
        _syms_synced = False
        print(f'  ⚠ {elf} 里没有 .symtab（被 strip 过？），沿用表内偏移')
        return {'(整个 ELF)': '没有符号表'}

    missing: dict[str, str] = {}
    for key, name in SYM_ELF_NAMES.items():
        hit = symbols.get(name)
        if hit is None:
            missing[key] = name
            SYMS.pop(key, None)
        else:
            SYMS[key] = hit[0]
    for key in SYM_UNRESOLVABLE:            # 这次构建里已经不存在的全局量
        SYMS.pop(key, None)
        missing[key] = '（本构建已无此全局量）'
    SYM_MISSING.clear()
    SYM_MISSING.update(missing)
    _syms_synced = True
    if verbose:
        print(f'  偏移已按 {elf.name} 的符号表刷新（{len(SYM_ELF_NAMES)} 项）')
        if missing:
            print(f'  ⚠ 未解析到（读数会显示"读不到"）：{", ".join(sorted(missing))}')
    return missing


def locator_session():
    """读数前的公共准备：刷新偏移表。所有对外入口都该走它，避免漏刷新。"""
    sync_syms_from_elf(ROOT / DEFAULT_ELF)


def local_bytes(elf: Path, vaddr: int, count: int) -> bytes | None:
    data = elf.read_bytes()
    e_phoff, = struct.unpack_from('<Q', data, 0x20)
    e_phentsize, e_phnum = struct.unpack_from('<HH', data, 0x36)
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        p_type, = struct.unpack_from('<I', data, o)
        if p_type != 1:
            continue
        p_offset, p_vaddr, _p, p_filesz = struct.unpack_from('<QQQQ', data, o + 8)
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            start = p_offset + (vaddr - p_vaddr)
            return data[start:start + count]
    return None


def build_requests(base: int, game_base: int, plugin_base: int) -> list[tuple[str, int, int]]:
    """一次挂载里要读完的全部地址（挑最少的字节数换最有用的信息）。"""
    req = [(name, base + offset, 1) for name, offset in SYMS.items() if name not in SYM_ONLY]
    req += [
        ('anchors', base + SYMS['identity'], 2),          # 版本核对：身份函数头 16 字节
        ('anchor_exl_main', base + SYMS['exl_main'], 2),  # 版本核对：入口函数头
        ('anchor_manifest', base + SYMS['manifest_install'], 2),  # 版本核对：诊断改动的那个函数
        ('manifest_state', base + SYMS['g_DefaultManifestState'], 1),
        ('hook_results', base + SYMS['g_HookInstallResults'], 5),
        ('lua_error_text', base + SYMS['g_LastLuaErrorText'], 32),
        ('manifest_head', base + SYMS['g_DefaultManifest'], 24),   # 清单原文前 192 字节
        ('entry_head', base + SYMS['g_DefaultEntry'], 8),          # 入口脚本前 64 字节
        ('callback_registry', base + SYMS['g_CallbackRegistry'], 6),
        ('callback_count', base + SYMS['g_CallbackRegistry'] + 0x1400, 1),
        ('filetable', base + SYMS['g_diagnosticFileApi'], 4),
        ('hook_callbacks', base + SYMS['g_hookCallbacks'], 6),
        ('update_entry', game_base + SYMS['manager_update_entry'], 2),
        ('render_entry', game_base + SYMS['manager_render_entry'], 2),
        ('present_slot', game_base + SYMS['present_got_slot'], 1),
        ('present_callsite', game_base + SYMS['present_callsite_offset'], 1),
        ('present_body', game_base + SYMS['present_target'], 1),
    ]
    if plugin_base:
        req.append(('helper', plugin_base + PLUGIN_HELPER, 14))
    return req


def save_raw(text: str, base: int, game_base: int, plugin_base: int) -> Path:
    """原始读数先落盘（含基址）：解析代码再出 bug 也不至于白烧一次调试桩挂载，
    而且可以**离线重放**（`--from-raw`）——换解释、改解码都不用再连设备。"""
    raw_path = ROOT / 'dist' / f'gdb-read-{time.strftime("%Y%m%d-%H%M%S")}.txt'
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    header = f'# base=0x{base:x} game_base=0x{game_base:x} plugin_base=0x{plugin_base:x}\n'
    raw_path.write_text(header + text, encoding='utf-8')
    print(f'原始读数已存：{raw_path.relative_to(ROOT)}（可用 --from-raw 离线重放）')
    return raw_path


def load_raw(path: Path) -> tuple[str, int, int, int]:
    text = path.read_text(encoding='utf-8')
    first_line, _, rest = text.partition('\n')
    m = re.match(r'# base=0x([0-9a-f]+) game_base=0x([0-9a-f]+) plugin_base=0x([0-9a-f]+)', first_line)
    if not m:
        return text, 0, 0, 0
    return rest, int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)


def run_one_shot(ip: str, elf: Path, pid: int = 0, base: int = 0, game_base: int = 0,
                 plugin_base: int = 0) -> int:
    session = GdbSession()
    try:
        session.command(f'target extended-remote {ip}:22225')
        if not pid:
            pid = parse_processes(session.command('info os processes')) or 0
        if not pid:
            print('没找到正在运行的 Application，请先启动游戏。')
            return 3
        print(f'进程 = {pid}（本会话只 attach 这一次）')
        attach_out = session.command(f'attach {pid}')
        if not base or not game_base:
            found_module, found_plugin, found_game = parse_bases(session.command('monitor get info'))
            base = base or (found_module or 0)
            plugin_base = plugin_base or (found_plugin or 0)
            game_base = game_base or (found_game or 0)
        if not base or not game_base:
            # 这次实测过：`monitor get info` 在"刚开机第一次挂载"也可能返回空。
            # 兜底：用内存映射 + 内容指纹自己找（只多读几十个 16 字节，仍然是同一次挂载）。
            scanned_module, scanned_game = locate_by_scan(session, elf)
            base = base or (scanned_module or 0)
            game_base = game_base or (scanned_game or 0)
        print(f'运行时模块基址 = 0x{base:x}'
              + (f'；插件基址 = 0x{plugin_base:x}' if plugin_base else '')
              + (f'；游戏模块基址 = 0x{game_base:x}' if game_base else '；游戏模块基址（缺）'))
        if not base or not game_base:
            print('基址没取全（`monitor get info` 与 mappings 扫描都没认出来）。')
            print('经验：调试桩在多次 attach 后会不稳定，重启设备后第一次连接最完整；')
            print('也可以直接用 --base/--game-base 指定。')
            return 3

        requests = build_requests(base, game_base, plugin_base)
        lines = []
        for label, addr, count in requests:
            lines.append(f'echo TAG {label}\\n')
            lines.append(f'x/{count}gx 0x{addr:x}')
        out = session.command('\n'.join(lines))
        session.command('detach')
    finally:
        session.close()

    save_raw(out, base, game_base, plugin_base)
    values = parse_words(out)

    # ---- 版本核对：三个代码锚点必须与本地 ELF 逐字节相同 ----
    checks = {'identity': 'anchors', 'exl_main': 'anchor_exl_main', 'manifest_install': 'anchor_manifest'}
    mismatched = []
    for name in ANCHOR_NAMES:
        offset = SYMS[name]
        expect = local_bytes(elf, offset, 16)
        got = b''.join(v.to_bytes(8, 'little') for v in values.get(checks[name], [])[:2])
        same = expect is not None and got == expect
        if not same:
            mismatched.append(name)
        extra = ''
        if name == 'exl_main' and not same:
            # 入口偏移会随构建变；把设备上真实的偏移反推出来（`g_runtimeSelfAddress` 存的就是它）。
            self_addr = first(values, 'g_runtimeSelfAddress')
            if self_addr and base and 0 < self_addr - base < 0x200000:
                extra = f'（设备上入口在 +0x{self_addr - base:x}）'
        print(f'  锚点 {name:18} @+0x{offset:<7x} '
              + ('✓ 与本地构建逐字节相同' if same else
                 f'✗ 不同：设备 {got.hex()} / 本地 {expect.hex() if expect else "(读不到)"}{extra}'))

    if mismatched:
        print()
        print(f'⚠️ 有锚点与本地那份构建不同：{", ".join(mismatched)} ⇒ 设备上跑的是**另一份构建**'
              '（很可能是发布包里那份正常版）。')
        # 换构建通常只动 `.text` 的排布；只要全局变量的**布局**没变，下面这些读数照样能用。
        # 用三个"必须落在合理范围"的量来判布局是否一致：
        seq = first(values, 'g_sequence') & 0xFFFFFFFF
        state = first(values, 'g_stateDetail') & 0xFFFFFFFF
        mstate = first(values, 'g_DefaultManifestState') & 0xFFFFFFFF
        self_addr = first(values, 'g_runtimeSelfAddress')
        sane = (seq < 10000 and state < 16 and mstate < 8
                and (self_addr == 0 or (base and 0 < self_addr - base < 0x200000)))
        print(f'  布局自检：sequence={seq}、状态机={state}、清单状态={mstate}、'
              f'入口自地址差={hex(self_addr - base) if self_addr and base else "n/a"} ⇒ '
              + ('看着自洽，继续读数（偏移表可用）' if sane else '不自洽 ⇒ 偏移表不可用，停止解释'))
        if not sane:
            print('（要读这份构建，必须重建一份同源的 ELF 并同步整张偏移表。）')
            return 4
        print('  注意：下面的解释按当前偏移表给出；如与设备行为矛盾，先怀疑构建不同。')
    else:
        print('偏移自校验通过（三个代码锚点逐字节一致）')

    report(values, base, game_base, plugin_base)
    return 0


# ---------------------------------------------------------------------------
# 读数解释
# ---------------------------------------------------------------------------

def first(values: dict[str, list[int]], key: str, index: int = 0, default: int = 0) -> int:
    seq = values.get(key) or []
    return seq[index] if index < len(seq) else default


def words_to_bytes(words: list[int]) -> bytes:
    return b''.join(v.to_bytes(8, 'little') for v in words)


def hook_name(slot: int) -> str:
    """`FirstFailureSlot` 是"序号+1"；越界就说明这个读数本身不可信，不要拿它去索引数组。"""
    return HOOK_NAMES[slot - 1] if 1 <= slot <= len(HOOK_NAMES) else f'越界({slot})'


def report(values: dict[str, list[int]], base: int, game_base: int, plugin_base: int) -> None:
    print()
    print('=== 启动状态机（TestRunObserver：游戏主循环那条线程每次启动只走一遍）===')
    seq = first(values, 'g_sequence')
    detail_word = first(values, 'g_stateDetail')
    state = detail_word & 0xFFFFFFFF
    detail = detail_word >> 32
    state_name = TEST_RUN_STATES[state] if state < len(TEST_RUN_STATES) else f'未知({state})'
    print(f'  sequence = {seq}（走过 {seq} 步）')
    print(f'  最后一步 = {state} = {state_name}，detail = {detail}')
    if state_name == 'ManifestInstallReturned':
        print(f'    ⇒ 清单安装这一步**走过了**，detail = {detail} = {decode_failure_detail(detail)}')
        if detail == 1:
            print('       （1 = FileBindingMismatch：挂点/文件校验没过 ⇒ 看下面的挂点报告）')
    elif state_name in ('ManifestInstallEntered', 'LuaInitializeEntered'):
        print('    ⇒ 卡在这一步**没返回** ⇒ 不是"校验失败"，是这一步本身没走完。')
    elif state == 5:
        print('    ⇒ 只走到"模块扫描返回"，**清单安装那一步根本没进入**。')

    print()
    print('=== 清单安装为什么失败（2026-09-14 夜间新增的诊断）===')
    mstate = first(values, 'g_DefaultManifestState') & 0xFFFFFFFF
    print(f'  g_DefaultManifestState = {mstate} = '
          + (MANIFEST_STATES[mstate] if mstate < len(MANIFEST_STATES) else '未知'))
    step = first(values, 'g_DefaultManifestFailureDetail') & 0xFFFFFFFF
    print(f'  清单/加载结果字 = {step} = {decode_failure_detail(step)}')
    word = first(values, 'g_DefaultManifestFailureWord') & 0xFFFFFFFF
    if word:
        print(f'  诊断字 [11] = 0x{word:08x}：步骤 {word & 0xFF}、原因 {(word >> 8) & 0xFF}、'
              f'读到的文件大小 {(word >> 16) & 0xFFFFFFFF} 字节')
    else:
        print('  诊断字 [11] = 0（没走过加载）')
    # 32 位变量的高 32 位是**相邻的另一个变量**（gdb 一次读 8 字节），必须屏蔽掉再判读。
    results = values.get('hook_results') or []
    results = [v & 0xFFFFFFFF for v in results]
    if step == 5 or word & 0xFF == 5:
        print('    ⇒ 步骤 5 = "纯资源型"：清单挂载成功，但**没有脚本被执行**。两种可能：')
        print('       ① 清单里这个模组**没有 `entry`**；② 清单写了 `entry`，但那个文件在包里**不存在**。')
        print('       下面"清单原文 / 入口脚本"两段就是用来区分这两条的。')

    # 三个"发布口径"的字：装失败也会被发布（`PublishHookInstallReport` 在判失败之前执行），
    # 所以它们能告诉我们"是哪个挂点、什么原因"，即使模组最终没装上。
    installed = first(values, 'g_HookInstallInstalledCount') & 0xFFFFFFFF
    slot = first(values, 'g_HookInstallFailureSlot') & 0xFFFFFFFF
    code = first(values, 'g_HookInstallFailureCode') & 0xFFFFFFFF
    print()
    print('=== 挂点安装报告（PublishHookInstallReport 发布，装失败也会留下）===')
    print(f'  装好的挂点数 = {installed}')
    print(f'  第一个失败挂点 = {slot}（0 = 没有装失败的；否则是挂点序号+1）'
          + (f' ⇒ {hook_name(slot)}' if slot else ''))
    print(f'  入口中继失败码 = {code}（0 = 无失败）')
    if installed == 0 and slot == 0 and code == 0:
        print('    ⇒ 三项全 0 有两种含义：**安装根本没被走到**，或者报告从未被填充'
              '（`InstallProductionHooks` 在模块无效时会清零报告）。'
              '要区分请看上面的启动状态机走到第几步。')
    elif slot:
        print(f'    ⇒ 挂点 `{hook_name(slot)}` 没装上 ⇒ 这就是模组装不上的直接原因。')

    legacy = ['ManagerUpdate 入口中继', 'ManagerRender 入口中继', 'PreGetCollectible 中继',
              'update 回调进入次数', 'render 回调进入次数']
    print()
    print('=== 旧安装器诊断数组 g_HookInstallResults[0..4]（线格式，供交叉核对）===')
    for i, name in enumerate(legacy):
        v = results[i] if i < len(results) else None
        if v is None:
            print(f'  [{i}] {name:22} 读不到')
        elif i < 3:
            print(f'  [{i}] {name:22} = {v} = {LEGACY_HOOK_CODES.get(v, "未知")}')
        else:
            print(f'  [{i}] {name:22} = {v}')
    present_state = first(values, 'g_RenderPresentRelayState') & 0xFFFFFFFF
    print(f'  Present 前派发点（GOT 槽方案）状态 = {present_state} = '
          f'{PRESENT_RELAY_CODES.get(present_state, "未知")}')

    print()
    print('=== Lua 侧：模组到底跑到哪一步 ===')
    registered = first(values, 'g_ModRegistered') & 0xFF
    print(f'  g_ModRegistered = {registered}'
          + ('  ⇒ 模组登记成功，Lua 真的跑起来了 ✓' if registered else
             '  ⇒ 模组没登记（Lua 从未跑到登记那一步）'))
    err_len = first(values, 'g_LastLuaErrorLength') & 0xFFFFFFFF
    if err_len:
        raw = b''.join(v.to_bytes(8, 'little') for v in (values.get('lua_error_text') or []))
        text = raw[:min(err_len, len(raw))].split(b'\0', 1)[0].decode('utf-8', 'replace')
        print(f'  最后的 Lua 错误（{err_len} 字节）：{text}')
    else:
        print('  最后的 Lua 错误：无（长度 0）')
    print(f'  g_PostUpdateCount = {first(values, "g_PostUpdateCount") & 0xFFFFFFFF}')
    count = first(values, 'callback_count')
    print(f'  回调登记数 = {count}')
    reg = values.get('callback_registry') or []
    for i in range(min(3, len(reg) // 5)):
        entry = reg[i * 5:i * 5 + 5]
        print(f'    第 {i} 条：id = {entry[0]}、owner = {entry[1]}、'
              f'affinity = {entry[2]}、luaReference = {entry[3]}、modReference = {entry[4]}')
    self_addr = first(values, 'g_runtimeSelfAddress')
    mark = '（= exl_main，入口跑过 ✓）' if self_addr == base + SYMS['exl_main'] else ''
    print(f'  g_runtimeSelfAddress = 0x{self_addr:012x} {mark}')
    table = values.get('filetable') or []
    if table:
        print(f'  运行时文件表：已植入 {sum(1 for v in table if v)}/4 项'
              f'（{", ".join(hex(v) for v in table)}）')

    print()
    print('=== 运行时**读进来的**清单原文与入口脚本（判"纯资源型"到底属于哪一种）===')
    manifest_raw = words_to_bytes(values.get('manifest_head') or [])
    if manifest_raw.strip(b'\0'):
        text = manifest_raw.split(b'\0', 1)[0].decode('utf-8', 'replace')
        print(f'  清单原文（前 {len(text)} 字节）：')
        for line in text.splitlines()[:12]:
            print(f'    | {line}')
        print('    ⇒ 看 `mods[]` 里的 `directory` / `entry`：')
        print('       · 没有 `entry` 字段 ⇒ 运行时按"纯资源型模组"处理（不会跑 Lua）')
        print('       · 有 `entry` 但那个文件不在包里 ⇒ 同上（且不报错）')
        enabled = re.search(r'"enabled"\s*:\s*(true|false)', text)
        if enabled:
            if enabled.group(1) == 'false':
                print('    ★ 注意：这份清单里模组被标成 **"enabled": false** ⇒'
                      ' 运行时按"没有启用的模组"处理（解析阶段就失败）⇒ **模组不加载、也不报错**。')
            else:
                print('    清单里模组是 "enabled": true ⇒ 不是"被禁用"这一条。')
    else:
        print('  清单缓冲区是空的 ⇒ 这次启动没读进清单（那"纯资源型"就不该出现，需复查）')
    entry_raw = words_to_bytes(values.get('entry_head') or [])
    head = entry_raw.split(b'\0', 1)[0]
    print(f'  入口脚本缓冲区前 {len(head)} 字节：'
          + (f'{head[:80].decode("utf-8", "replace")!r}' if head else '（空 ⇒ 没有脚本被读进来）'))

    print()
    print('=== 五个挂点：各自的回调指针（g_hookCallbacks）===')
    callbacks = values.get('hook_callbacks') or []
    for i, name in enumerate(HOOK_NAMES):
        ptr = callbacks[i] if i < len(callbacks) else None
        relay_key = RELAY_BY_HOOK.get(name)
        if ptr is None:
            print(f'  [{i}] {name:20} 读不到')
        elif relay_key and ptr == base + SYMS[relay_key]:
            print(f'  [{i}] {name:20} 0x{ptr:012x} ← 零占洞入口中继（模块+0x{SYMS[relay_key]:x}）★')
        elif ptr == 0:
            note = '（该路走 GOT 槽，判据见下）' if name == 'ManagerPresent' else '（该路未走入口中继）'
            print(f'  [{i}] {name:20} 0x{ptr:012x} ← {note}')
        else:
            print(f'  [{i}] {name:20} 0x{ptr:012x}')

    print()
    print('=== 游戏模块的两个入口字节（旧式 IPS 有没有改写它们）===')
    for key, name, expect in (('update_entry', 'Manager::Update', 'ff4301d1fd7b01a9'),
                              ('render_entry', 'Manager::Render', 'ffc302d1e83b00fd')):
        vals = values.get(key) or []
        got = b''.join(v.to_bytes(8, 'little') for v in vals[:2]).hex()
        if not vals:
            print(f'  {name:16} 读不到')
        elif got.startswith(expect):
            print(f'  {name:16} = {got} ← 原样（旧式 IPS 没生效）★')
        else:
            word = vals[0] & 0xFFFFFFFF
            note = '  ← 是 `bl`/`b`（被改写成跳转了）' if (word >> 26) in (0x25, 0x05) else ''
            print(f'  {name:16} = {got} ← 与原版不同{note}')

    print()
    print('=== Present 的 GOT 槽（下面几条的偏移都相对**游戏模块**）===')
    expect_intercept = base + SYMS['present_got_intercept']
    got_slot = values.get('present_slot') or []
    if got_slot:
        mark = '★（= 我们的拦截函数）' if got_slot[0] == expect_intercept else '✗ 与拦截函数不符'
        print(f'  GOT 槽内值     = 0x{got_slot[0]:012x} {mark}')
    print(f'  拦截函数应为   = 0x{expect_intercept:012x}（运行时模块+0x{SYMS["present_got_intercept"]:x}）')
    target = first(values, 'g_PresentGotSlotTarget')
    if target:
        mark = '★（= Present 本体）' if target == game_base + SYMS['present_target'] else '✗ 与 Present 本体不符'
        print(f'  转发目标       = 0x{target:012x} {mark}')
    body = values.get('present_body') or []
    if body:
        word = body[0] & 0xFFFFFFFF
        mark = '★（序言未被改动）' if word == 0xA9BE7BFD else '✗ 序言不符'
        print(f'  Present 序言   = 0x{word:08x} {mark}')
    callsite = values.get('present_callsite') or []
    if callsite:
        word = callsite[0] & 0xFFFFFFFF
        if (word >> 26) == 0x25:
            imm = word & 0x03FFFFFF
            if imm & (1 << 25):
                imm -= 1 << 26
            dest = SYMS['present_callsite_offset'] + (imm << 2)
            mark = '★（目标正是 Present 桩）' if dest == 0x670A40 else f'✗ 目标 0x{dest:x}'
            print(f'  调用点首字     = 0x{word:08x}（bl）{mark}')
        else:
            print(f'  调用点首字     = 0x{word:08x} ✗ 不是 bl（那份 IPS 可能还在）')

    helper = values.get('helper')
    if helper:
        print()
        print('=== 插件自己解析到的地址（与上面交叉核对）===')
        for i, name in enumerate(HANDOVER_FIELDS):
            v = helper[i]
            note = ''
            if name == 'ownBase' and v == base:
                note = '← 与调试桩看到的模块基址一致 ✓'
            elif name == 'ownIdentity' and v == base + SYMS['identity']:
                note = '← 与真实身份函数地址一致 ✓'
            print(f'  {name:20} 0x{v:012x} {note}')


# ---------------------------------------------------------------------------
# 备用：两段式（各一次 attach），老流程，留作对照
# ---------------------------------------------------------------------------

def run_gdb_batch(commands: list[str], timeout: int = 120) -> str:
    with tempfile.NamedTemporaryFile('w', suffix='.cmd', delete=False) as fh:
        fh.write('set pagination off\nset confirm off\n')
        fh.write('\n'.join(commands) + '\nquit\n')
        path = fh.name
    cmd = ['docker', 'run', '--rm', '-v', f'{path}:/gdb.cmd:ro', GDB_IMAGE, 'bash', '-lc',
           f'. /opt/devkitpro/devkita64.sh && timeout {timeout} aarch64-none-elf-gdb -batch -x /gdb.cmd']
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    finally:
        Path(path).unlink(missing_ok=True)


def header(ip: str) -> list[str]:
    return [f'target extended-remote {ip}:22225']


def run_two_phase(ip: str, elf: Path, pid: int = 0, base: int = 0, game_base: int = 0,
                  plugin_base: int = 0) -> int:
    """老流程：找进程一次、取基址一次、读身份一次、读变量一次 = 四次 attach。费预算，慎用。"""
    if not pid:
        out = run_gdb_batch(header(ip) + ['info os processes'], timeout=60)
        pid = parse_processes(out) or 0
    if not pid:
        print('没找到正在运行的 Application，请先启动游戏。')
        return 3
    if not base or not game_base:
        out = run_gdb_batch(header(ip) + [f'attach {pid}', 'monitor get info', 'detach'])
        module, plugin, game = parse_bases(out)
        base = base or (module or 0)
        plugin_base = plugin_base or (plugin or 0)
        game_base = game_base or (game or 0)
    if not base or not game_base:
        print('定位不到模块基址（调试桩的 monitor 输出缺失）。可加 --base/--game-base 指定。')
        return 3
    print(f'进程 = {pid}；运行时模块基址 = 0x{base:x}；游戏模块基址 = 0x{game_base:x}')

    requests = build_requests(base, game_base, plugin_base)
    cmds = header(ip) + [f'attach {pid}']
    for label, addr, count in requests:
        cmds.append(f'printf "TAG {label}\\n"')
        cmds.append(f'x/{count}gx 0x{addr:x}')
    cmds.append('detach')
    out = run_gdb_batch(cmds)
    values = parse_words(out)

    for name in ANCHOR_NAMES:
        expect = local_bytes(elf, SYMS[name], 16)
        got = b''.join(v.to_bytes(8, 'little') for v in values.get(
            {'identity': 'anchors', 'exl_main': 'anchor_exl_main',
             'manifest_install': 'anchor_manifest'}[name], [])[:2])
        if expect is None or got != expect:
            print(f'偏移自校验失败（锚点 {name}）：设备 {got.hex()} / 本地 '
                  f'{expect.hex() if expect else "(读不到)"}')
            return 4
    print('偏移自校验通过（三个代码锚点逐字节一致）')
    report(values, base, game_base, plugin_base)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip', default='192.168.124.11')
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0, help='运行时模块基址（十六进制）')
    ap.add_argument('--game-base', type=lambda s: int(s, 0), default=0,
                    help='游戏模块（Repentance.nrs.elf）基址；GOT 槽与 Present 偏移相对它')
    ap.add_argument('--plugin-base', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--elf', default=DEFAULT_ELF)
    ap.add_argument('--two-phase', action='store_true',
                    help='走老流程（四次 attach，费调试桩预算），只在一次挂载失败时用')
    ap.add_argument('--dry-run', action='store_true', help='只测 gdb 管道，不连设备')
    ap.add_argument('--from-raw', default='',
                    help='离线重放某次读数（不连设备）：换解码/改解释时用，别再烧调试桩预算')
    args = ap.parse_args()

    elf = ROOT / args.elf
    if not elf.exists():
        print(f'找不到本地重建的模块 ELF：{args.elf}')
        print('偏移自校验需要它。先在本地用同一套旗标重建：')
        print('  docker run --rm -v "$PWD":/work -w /work devkitpro/devkita64:latest bash -lc \\')
        print('    ". /opt/devkitpro/devkita64.sh && make -C runtime -j6 LAYERED_RUNTIME=1 \\')
        print('     PROBE_BREAK=0 OUT=.gdbsym-artifacts/deploy BUILD=.gdbsym-build \\')
        print('     EXL_ARTIFACT_DIR=.gdbsym-artifacts all"')
        return 2

    # 偏移表以**本地这份构建**为准：手抄表会随任何一次重建过期（项目已因此误读过两次）。
    sync_syms_from_elf(elf, verbose=True)

    if args.dry_run:
        session = GdbSession(timeout=60)
        try:
            print(session.command('echo @@管道_ok@@').strip())
            print(session.command('show version').splitlines()[0])
        finally:
            session.close()
        return 0

    if args.from_raw:
        text, raw_base, raw_game, raw_plugin = load_raw(ROOT / args.from_raw)
        values = parse_words(text)
        base = args.base or raw_base
        game_base = args.game_base or raw_game
        plugin_base = args.plugin_base or raw_plugin
        print(f'离线重放 {args.from_raw}：运行时基址 = 0x{base:x}；游戏模块基址 = 0x{game_base:x}')
        report(values, base, game_base, plugin_base)
        return 0

    if args.two_phase:
        return run_two_phase(args.ip, elf, args.pid, args.base, args.game_base, args.plugin_base)
    return run_one_shot(args.ip, elf, args.pid, args.base, args.game_base, args.plugin_base)


if __name__ == '__main__':
    sys.exit(main())
