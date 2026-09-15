#pragma once

#include <cstdint>

// Deliberate error, used as an evidence channel.
//
// The project rule (AGENTS.md「探针证据规范」) is that a probe answering "did execution
// reach this point" must not rely on file writes alone. This module's file writes have
// produced no artifact for thirteen hardware rounds, while a crash report always
// appears and carries three things a written record never had: the module base
// address, the exact offset that stopped, and the register file. So the probe loads
// its payload into `x1`..`x7` and then executes `svc 0x7f` with `BreakReason_User`.
//
// The payload (`ISAACPB1` in `x1`):
//   x2  marker << 32 | self-journal records written so far
//   x3  address of `IsaacModRuntime_WriteSelfJournal` in this copy -- subtract its ELF
//       offset (0xe7c8 as of 2026-09-11) to get the load base of the copy that ran
//   x4  the address `exl_main` published for this copy (0 if it never ran here)
//   x5  file-table mask | sessionCreated << 8 | gateStoppedEarly << 9
//       | registrationCalls << 16 | self-journal service state << 24
//   x6  hook install results (update, render, pre-get-collectible) in bytes 0..2
//       | update-callback entries in byte 3
//   x7  registration handshake ('HAND' once the table reached this copy)
//   x8  the session re-opening probe: lookup mask (byte 0), attempts (byte 1), and the
//       Result of `SaltySD_Init` in bits 16..47
//   x9  what `SaltySD_printf` returned after the session was reopened
//   x10 the file that reopening allowed: open succeeded (byte 0), bytes written
//       (bytes 1-2), close succeeded (byte 3)
//   x11 tls state (byte 0) | last self-journal marker (byte 1)
//       | render-callback entries (bytes 2-3) | records per marker (bytes 4-6)
//   x12 the first registered file pointer, low word in bytes 0-3 and high word in 4-7
//   x13..x20 the host plugin's note (`IsaacModRuntime_SetPluginNote`):
//       x13 polls, x14 copies of the module seen,
//       x15/x16 the copy's published `exl_main`,
//       x17 the hand-over thread's own file write: openOk (byte 0), closeOk (byte 1),
//           attempts (byte 2),
//       x18 bytes it wrote,
//       x19 the table mask the Runtime reported after the hand-over,
//       x20 its sessionCreated (byte 0) and gateStoppedEarly (byte 1)
//   x21 the game thread's file-write probe: table-missing count (byte 0), attempts after
//       the table was complete (byte 1), open succeeded (byte 2), close succeeded (byte 3)
//   x22 bytes written by that probe
//   x23 which of SaltyNX's own IPC entries resolved (bits 0-1: SaltySD_printf,
//       SaltySD_GetBID) and how many attempts were made (byte 1)
//   x24 what `SaltySD_printf` returned
//   x25 the title id `SaltySD_GetBID` answered with
//   x26 the game's own file functions resolved by name: which of fopen/fwrite/fclose
//       resolved (bits 0..2), open succeeded (bit 8), close succeeded (bit 16),
//       attempts (bits 24..31)
//   x27 bytes that route wrote
//   x28 the resolved `fopen` address
//
// The break is one shot per session, and only a probe build compiles it in: a
// production build turns every call site into nothing.
namespace isaac::runtime {
constexpr std::uint32_t kProbeBreakUpdateCallback = 1;
constexpr std::uint32_t kProbeBreakRenderCallback = 2;
constexpr std::uint32_t kProbeBreakExlMain = 3;
constexpr std::uint64_t kProbeBreakMagic = 0x3142504341415349ULL;  // "ISAACPB1"
// Entries of the update (or render) callback to allow before the break. The wait is
// deliberate: the host plugin's own samples and the self-journal's write attempts all
// happen in the first seconds, and a session that dies at the first frame would leave
// none of them: the plugin's second sample lands ten seconds after it loads, so the
// break waits about a minute and a quarter of frames. The game's own loading screen runs
// the update hook too (measured: the first crash landed there), so the wait has to clear the
// loading and let a run start before the session ends.
// 兜底阈值：内容挂载点探针现在由"Mod 自己画了多少帧"触发（见 `content_mount_point_probe.hpp`），
// 这个通用 break 只是"连那个信号都没出现"时的保险，所以放得很远 —— 它在加载阶段按渲染帧数计
// 也会被烧掉大半，设成 1500 曾在进入房间后一两秒就结束会话（2026-09-13 实测）。
constexpr std::uint32_t kProbeBreakAfterEntries = 100000;
// Entries of the update callback to allow before the step-by-step `fs` probe runs. It is not
// wired to a call site any more: its answer is in hand (see the 2026-09-11 notes), and while
// it was armed it ended every session before the general break could report the other probes'
// results.
constexpr std::uint32_t kFsProbeAfterEntries = 900;
}  // namespace isaac::runtime

#if defined(EXL_PROBE_BREAK)
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_ProbeBreak(std::uint32_t marker);
// Runs smInitialize, fsInitialize, the SD open and the file open/create/write/close one step
// at a time, then breaks with one Result code per register (`ISAACFS1` in x1).
extern "C" __attribute__((visibility("hidden"))) void IsaacModRuntime_ProbeFsSteps();
// 帧时间归因探针（`ISAACEV1`）：由 `hook_manager.cpp` 在渲染钩子里调用，负载字段见
// `saltynx_runtime_bridge.cpp` 的实现与 `tools/decode_engine_probe_payload.py`。
extern "C" __attribute__((visibility("hidden"))) void
IsaacModRuntime_EngineProbeBreak();
#else
inline void IsaacModRuntime_ProbeBreak(std::uint32_t) {}
// Zero makes the call sites' threshold test unconditional, so a production build keeps
// the same code shape with the call itself compiled away.
constexpr std::uint32_t kProbeBreakAfterEntries = 0;
#endif
