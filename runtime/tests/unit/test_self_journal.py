"""The self-journal must be able to reach the SD card without the host plugin.

Every earlier attempt to publish the Runtime's diagnostics went through the file
table the SaltyNX host plugin registers, and `isaac-runtime-events.bin` never
appeared. The state the Runtime reported about that path is what narrowed it down:
the runtime the plugin calls holds the complete table (`fileApiMask` 0xf, handshake
'HAND') and still reports `sessionCreated` 0, `gateStoppedEarly` 0 and zero
callback entries -- a copy that has never executed `exl_main` or a hook callback.
The module's hooks do run (crash report 01789112265 returns through an address
inside `ManagerUpdateHook::Callback`), so the code that runs and the copy the plugin
reaches are not provably the same memory.

**更正（2026-09-14，见 `docs/问题与解决记录.md` 的「用调试桩直接读设备状态」一节）**：
上面这段"两个副本"的推断**已被推翻**。用大气层调试桩直接读运行中的游戏内存后确认：
内存里我们的运行时模块**只有一份**，插件解析到的 `ownBase`/`ownIdentity` 与它逐位一致，
而且那一份的 `g_runtimeSelfAddress` 非 0（正是 `exl_main` 的地址）、启动状态机已推进到第 7 步。
上面那些 0（`sessionCreated`、`gateStoppedEarly`、回调计数）是**读取时机**造成的：插件在
**加载那一刻**就读，那时运行时入口还没跑。所以本文件下面这些断言仍然有意义（它们锁的是
self-journal 自己的实现约束），但"两个副本"不再是它们的理由。

The self-journal therefore removes the plugin from the path: the executing copy
appends its own record through the `fs` service. Three properties keep that honest
and are asserted here:

* the writer is reachable from `exl_main` and from both hook callbacks through a
  direct `bl`, never a `.plt` stub whose resolution the module loader may skip;
* the libc thread pointer is installed before the first libnx `fs` call, because
  devkitA64's `__aarch64_read_tp` dereferences it and a game thread leaves it zero
  (the exact fault this module already paid for at `runtime+0x440c`);
* the plugin reports the Runtime's own account of that path, so a failure that
  writes no record at all is still visible.
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
SOURCE = ROOT / "source"
BRIDGE = SOURCE / "saltynx_runtime_bridge.cpp"
HEADER = SOURCE / "saltynx_self_journal.hpp"
ENTRY = SOURCE / "runtime_entry.cpp"
HOOKS = SOURCE / "hook_manager.cpp"
PLUGIN = ROOT / "src" / "host_plugin" / "saltynx_host_plugin.cpp"
PLUGIN_HEADER = ROOT / "src" / "host_plugin" / "saltynx_host_plugin.hpp"
DECODER = REPO / "tools" / "read_host_plugin_bridge.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SelfJournalWireFormatTests(unittest.TestCase):
    def test_bridge_writes_a_144_byte_record_with_a_trailing_checksum(self):
        text = read(BRIDGE)
        self.assertIn("constexpr std::size_t kRecordSize = 144;", text)
        self.assertIn('constexpr std::uint64_t kMagic = 0x3153524341415349ULL;', text)
        self.assertIn("PutWord(record + 140, Checksum(record, 140));", text)

    def test_state_export_publishes_attempts_results_and_the_thread_pointer(self):
        text = read(BRIDGE)
        self.assertIn("IsaacModRuntime_GetSelfJournalState", text)
        self.assertIn("constexpr std::size_t kStateWordCount = 16;", text)
        self.assertIn("output[12] = g_totalRecords;", text)
        self.assertIn("output[15] = g_tlsState;", text)

    def test_marker_ids_are_shared_with_the_call_sites(self):
        text = read(HEADER)
        self.assertIn("constexpr std::uint32_t kSelfJournalUpdateMarker = 1;", text)
        self.assertIn("constexpr std::uint32_t kSelfJournalRenderMarker = 2;", text)
        self.assertIn("constexpr std::uint32_t kSelfJournalExlMainMarker = 3;", text)
        self.assertIn('__attribute__((visibility("hidden")))', text)


class SelfJournalCallSiteTests(unittest.TestCase):
    def test_entry_and_both_hook_callbacks_append_a_record(self):
        self.assertIn("IsaacModRuntime_WriteSelfJournal(isaac::runtime::kSelfJournalExlMainMarker)",
                      read(ENTRY))
        hooks = read(HOOKS)
        self.assertIn("IsaacModRuntime_WriteSelfJournal(isaac::runtime::kSelfJournalUpdateMarker)",
                      hooks)
        self.assertIn("IsaacModRuntime_WriteSelfJournal(isaac::runtime::kSelfJournalRenderMarker)",
                      hooks)

    def test_call_sites_are_bounded_so_no_frame_keeps_writing(self):
        text = read(BRIDGE)
        self.assertIn("constexpr std::uint32_t kAttemptLimit = 3;", text)
        self.assertIn("if (g_attempts[marker] >= kAttemptLimit) {", text)

    def test_writer_is_hidden_so_the_call_binds_with_a_direct_branch(self):
        text = read(BRIDGE)
        match = re.search(
            r'extern "C" __attribute__\(\(visibility\("hidden"\)\)\) void\s*\n'
            r"IsaacModRuntime_WriteSelfJournal",
            text,
        )
        self.assertIsNotNone(match, "the writer must be declared hidden")


class ThreadPointerTests(unittest.TestCase):
    def test_thread_pointer_is_installed_before_the_first_libnx_call(self):
        text = read(BRIDGE)
        body = text.split("bool EnsureSelfJournalServices()", 1)[1]
        install = body.index("EnsureThreadTlsPointer()")
        self.assertLess(install, body.index("smInitialize()"))
        self.assertIn("mrs %0, tpidrro_el0", text)
        self.assertIn("kThreadTlsPointerOffset = 0x1f8", text)

    def test_the_install_only_fills_an_empty_slot(self):
        text = read(BRIDGE)
        body = text.split("bool EnsureThreadTlsPointer()", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (*slot != 0) {", body)
        self.assertIn("*slot = reinterpret_cast<std::uintptr_t>", body)


class PluginReadbackTests(unittest.TestCase):
    def test_plugin_resolves_the_runtime_state_readers(self):
        text = read(PLUGIN)
        self.assertIn('constexpr char kSelfJournalStateSymbol[] = "IsaacModRuntime_GetSelfJournalState";',
                      text)
        self.assertIn("constexpr std::size_t kSelfJournalStateWordCount = 16;", text)
        self.assertIn("constexpr std::size_t kDiagnosticsStateWordCount = 5;", text)

    def test_plugin_exposes_the_handover_flag(self):
        header = read(PLUGIN_HEADER)
        self.assertIn("HandoverThreadStarted = 1U << 21,", header)

    def test_offline_decoder_still_knows_every_record(self):
        text = read(DECODER)
        for magic in ('b"ISAACJS1"', 'b"ISAACRS1"', 'b"ISAACSG1"', 'b"ISAACWT1"', 'b"ISAACHD1"',
                      'b"ISAACDS1"'):
            self.assertIn(magic, text)
        self.assertIn("READ_INDEX_NAMES", text)


class ModuleCopyTests(unittest.TestCase):
    """The plugin must count copies by content, and hand the table over after the entry."""

    def test_scan_finds_copies_by_content_and_verifies_before_calling(self):
        text = read(PLUGIN)
        self.assertIn("FindModuleCopies(work.copies, kMaxModuleCopies", text)
        self.assertIn("kImageVerifyBytes", text)
        # The verification loop must run before anything is called in the candidate.
        body = text.split("std::size_t FindModuleCopies(", 1)[1].split("\n}", 1)[0]
        self.assertLess(body.index("codeMatches"), body.index("candidateExport(words, 4)"))

    def test_scan_uses_the_modules_own_code_as_the_needle(self):
        # The crt0 string is not at image offset 8 in the loaded image: the first version
        # of this scan searched for it and found nothing in a session whose crash report
        # listed the module at the address the plugin had already measured.
        text = read(PLUGIN)
        body = text.split("std::size_t FindModuleCopies(", 1)[1]
        self.assertIn("constexpr std::size_t kNeedleOffset = 16;", body)
        self.assertIn("const std::uint8_t* needle = ownCode + kNeedleOffset;", body)
        self.assertNotIn("kModuleSignature", text)

    def test_scan_is_bounded_by_region_size(self):
        text = read(PLUGIN)
        self.assertIn("constexpr std::uint64_t kMinScannedRegionSize = 0x10000;", text)
        self.assertIn("constexpr std::uint64_t kMaxScannedRegionSize = 0x800000;", text)

    def test_plugin_waits_for_the_runtime_entry_before_handing_over(self):
        text = read(PLUGIN)
        self.assertIn("kEntryPollIntervalNanoseconds = 250'000'000LL;", text)
        body = text.split("void HostHandoverMain(void*) {", 1)[1]
        self.assertLess(body.index("entryAddress != 0"), body.index("slots[0] = api.open;"))
        self.assertIn("kHandoverRounds", text)
        # And the plugin's own run must not write the table: it is erased by the Runtime's
        # own startup, which happens after this thread returns.
        run = text.split("std::uint32_t SaltyNxHostPlugin::Run(bool report) noexcept {", 1)[1]
        run = run.split("// The hand-over thread.", 1)[0]
        self.assertNotIn("client.RegisterFileApi", run)

    def test_the_hand_over_thread_performs_no_file_io(self):
        # Measured: a thread the game did not create cannot use the game's libc. The first
        # version of this thread called the Runtime's registration entry, which opened a file
        # through the game's fopen, and the session died with Result 0x2A8 and PC = 0
        # (report 01789118886).
        text = read(PLUGIN)
        body = text.split("void HostHandoverMain(void*) {", 1)[1]
        body = body.split("} // namespace", 1)[0]
        for forbidden in ("AppendRecord(", "AppendBridgeRecord(", "AppendSnapshotRecord(",
                          "AppendCreateProbe(", "AppendMappingRecord(", "AppendModuleCopyRecord(",
                          "registerFileApi"):
            self.assertNotIn(forbidden, body)
        # Only memory reads and memory writes remain.
        self.assertIn("slots[0] = api.open;", body)

    def test_the_thread_never_returns_from_its_entry(self):
        # A thread created with raw `svcCreateThread` runs with LR = 0, so returning from its
        # entry jumps to address 0: report 01789119239 died exactly that way, with the
        # arguments of the thread's last call still in X[00]/X[01].
        text = read(PLUGIN)
        body = text.split("void HostHandoverMain(void*) {", 1)[1].split("} // namespace", 1)[0]
        self.assertNotIn("\n    return;", body)
        # Parking, not a syscall: a returning `svcExitThread` would fall through into code
        # that assumes the Runtime's entry was found.
        self.assertNotIn("svcExitThread();", body)
        self.assertIn("for (;;) {", body)
        self.assertIn("svcSleepThread(kEntryWaitDeadlineNanoseconds);", body)

    def test_the_thread_reports_through_the_module_instead_of_a_file(self):
        text = read(PLUGIN)
        self.assertIn("kPluginNoteSymbol", text)
        self.assertIn("g_helper.setPluginNote(note, 8)", text)
        module = read(BRIDGE)
        self.assertIn("IsaacModRuntime_SetPluginNote", module)
        break_body = module.split("IsaacModRuntime_ProbeBreak(std::uint32_t marker) {", 1)[1]
        for register in ("x13", "x14", "x15", "x16", "x17", "x18", "x19", "x20"):
            self.assertIn(f'__asm__("{register}")', break_body)


class ProbeBreakTests(unittest.TestCase):
    """AGENTS.md 探针证据规范: a probe must also be able to raise a visible error."""

    def test_module_raises_a_user_break_with_the_payload_in_registers(self):
        text = read(BRIDGE)
        body = text.split("IsaacModRuntime_ProbeBreak(std::uint32_t marker) {", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn('__asm__ volatile("svc 0x7f"', body)
        for register in ("x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7"):
            self.assertIn(f'__asm__("{register}")', body)
        self.assertIn("g_probeBreakTaken", body)

    def test_break_is_one_shot(self):
        text = read(BRIDGE)
        body = text.split("IsaacModRuntime_ProbeBreak(std::uint32_t marker) {", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("if (g_probeBreakTaken != 0) {", body)

    def test_production_builds_compile_the_break_away(self):
        text = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertIn("inline void IsaacModRuntime_ProbeBreak(std::uint32_t) {}", text)
        self.assertIn("#if defined(EXL_PROBE_BREAK)", text)
        self.assertIn("constexpr std::uint32_t kProbeBreakAfterEntries = 0;", text)

    def test_break_waits_for_the_plugin_samples_to_exist(self):
        header = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertIn("constexpr std::uint32_t kProbeBreakAfterEntries = 100000;", header)
        hooks = read(HOOKS)
        # 口径：内容挂载点探针由"测试 Mod 自己画了多少帧"触发（`STAGE149_FRAME`），通用 break 只是
        # 兜底，阈值放得很远。update 回调次数与渲染帧数都会在加载阶段被烧掉大半，用它计时会让会话在
        # 进入房间后一两秒就被探针自己结束（2026-09-13 实测）。
        self.assertIn("ReadLuaGlobalNumber(\"STAGE149_FRAME\"", hooks)
        self.assertIn("kContentMountProbeAfterDraws", hooks)
        # 兜底口径：主口径（Mod 画够帧数）没出现时也必须有一次报告，否则 Mod 的回调被静默摘除
        # 时现象只剩"既不报错也没有显示"（2026-09-13 第七轮）。
        self.assertIn("kContentMountProbeFallbackAfterUpdates", hooks)
        # update 计数只允许用作**兜底**触发（与兜底常量比较），不能再拿它当通用 break 的口径。
        self.assertIn(
            "g_UpdateCallbackEntries.load(std::memory_order_relaxed) >=\n"
            "                isaac::runtime::kContentMountProbeFallbackAfterUpdates",
            hooks,
        )
        self.assertNotIn("g_UpdateCallbackEntries.load(std::memory_order_relaxed) >=\n"
                         "            isaac::runtime::kProbeBreakAfterEntries", hooks)

    def test_build_flag_switches_it_on(self):
        text = read(ROOT / "Makefile")
        self.assertIn("PROBE_BREAK ?= 0", text)
        self.assertIn("-DEXL_PROBE_BREAK=1", text)


class ThreadWriteProbeTests(unittest.TestCase):
    """Which thread may perform file I/O: the plugin's own, or the game's?"""

    def test_module_probes_a_file_write_from_the_hook_callbacks(self):
        text = read(BRIDGE)
        self.assertIn("IsaacModRuntime_ProbeFileWriteFromHook", text)
        self.assertIn("kThreadWriteProbePath", text)
        # It must create the file, not append to an existing one.
        self.assertIn('open(kThreadWriteProbePath, "wb")', text)
        # Bounded, so a failure cannot become per-frame I/O.
        self.assertIn("kThreadWriteProbeLimit = 3", text)
        self.assertIn("if (g_threadWriteAttempts >= kThreadWriteProbeLimit) {", text)
        hooks = read(HOOKS)
        self.assertIn("IsaacModRuntime_ProbeFileWriteFromHook()", hooks)

    def test_both_file_write_results_reach_the_crash_report(self):
        text = read(BRIDGE)
        for register in ("x21", "x22", "x23", "x24", "x25"):
            self.assertIn(f'__asm__("{register}")', text)
        header = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertIn("x21 the game thread's file-write probe", header)

    def test_plugin_writes_its_own_probe_file_from_its_own_thread(self):
        text = read(PLUGIN)
        self.assertTrue("kThreadWriteProbePath" in text)
        self.assertTrue('open(path, "wb")' in text)
        self.assertTrue("kThreadWriteProbePath, kThreadWriteProbeText" in text)
        self.assertTrue("AppendThreadWriteRecord" in text)
        # It happens in Run(), which SaltyNX calls on its own thread.
        body = text.split("std::uint32_t SaltyNxHostPlugin::Run(bool report) noexcept {", 1)[1]
        self.assertTrue("WriteThreadProbe(api, kThreadWriteProbePath" in body)

    def test_handover_thread_writes_its_own_probe_file(self):
        # The game's thread cannot open a file through this table (x21: openOk 0 after three
        # attempts), so whether a thread the plugin creates can decides how the journal gets
        # written at all.
        text = read(PLUGIN)
        self.assertTrue("kHandoverWriteProbePath" in text)
        body = text.split("void HostHandoverMain(void*) {", 1)[1].split("} // namespace", 1)[0]
        self.assertTrue("kHandoverWriteProbePath" in body)
        self.assertTrue("handoverWrite.openOk" in body)

    def test_decoder_knows_the_thread_write_record(self):
        text = read(DECODER)
        self.assertIn('b"ISAACIO1"', text)


class FsStepProbeTests(unittest.TestCase):
    """The Runtime's own `fs` write path, one step at a time."""

    def test_probe_runs_each_step_and_breaks_with_the_codes(self):
        text = read(BRIDGE)
        self.assertTrue("IsaacModRuntime_ProbeFsSteps" in text)
        self.assertTrue('kFsProbeMagic = 0x3153464341415349ULL' in text)
        for symbol in ("smInitialize()", "fsInitialize()", "fsOpenSdCardFileSystem(&filesystem)",
                       "fsFsOpenFile(&filesystem", "fsFsCreateFile(&filesystem", "fsFileWrite(",
                       "fsFileClose(&file)", "fsFsClose(&filesystem)"):
            self.assertTrue(symbol in text, symbol)
        # One Result per register, in call order.
        for register in ("x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10"):
            self.assertTrue(f'__asm__("{register}")' in text, register)

    def test_probe_runs_on_its_own_thread_so_a_block_only_hangs_that_thread(self):
        # Measured 2026-09-11: running this sequence on the game's thread froze the game at
        # eight seconds into a room, with no crash report, because a blocked thread does not
        # fault. A hang must therefore not cost the evidence.
        text = read(BRIDGE)
        self.assertTrue("svcCreateThread(&thread, reinterpret_cast<void*>(&FsProbeThread)" in text)
        self.assertTrue("g_fsProbeStack" in text)
        body = text.split("void FsProbeThread(void*) {", 1)[1].split("\n}", 1)[0]
        self.assertTrue("EnsureThreadTlsPointer();" in body)
        self.assertTrue("for (;;) {" in body)
        self.assertNotIn("\n    return;", body)

    def test_only_the_general_break_reports_now(self):
        # The fs probe used to end every session before the general break could report the
        # other probes' results, so its call site is gone; the payload it filled is still
        # produced by the module but nothing waits on it.
        hooks = read(HOOKS)
        # Only the declaration remains; the declaration line ends with `;` too, so require the
        # call to be absent from a statement position.
        lines = [line.strip() for line in hooks.splitlines()]
        self.assertNotIn("IsaacModRuntime_ProbeFsSteps();", lines)
        # 只留**渲染回调**那一处报错，且口径是渲染帧数：两套计时（update 次数 / 渲染帧数）
        # 同时存在时，先到的那一套会把会话提前结束 —— 2026-09-13 就是这样在进房间一两秒后
        # 结束的。update 侧现在只保留计数。
        self.assertTrue("IsaacModRuntime_ProbeBreak(isaac::runtime::kProbeBreakRenderCallback);" in hooks)
        self.assertNotIn("kProbeBreakUpdateCallback", hooks)
        header = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertTrue("kProbeBreakAfterEntries = 100000;" in header)


class NativeFileWriteProbeTests(unittest.TestCase):
    """The Runtime's last route: the game's own libc, resolved by name."""

    def test_module_resolves_and_calls_the_games_own_file_functions(self):
        text = read(BRIDGE)
        self.assertTrue("IsaacModRuntime_ProbeNativeFileWrite" in text)
        self.assertTrue("IsaacModRuntime_SetSymbolLookup" in text)
        self.assertTrue('find("fopen")' in text)
        self.assertTrue('find("fwrite")' in text)
        self.assertTrue('find("fclose")' in text)
        self.assertTrue("kNativeWritePath" in text)
        self.assertTrue('open(kNativeWritePath, "wb")' in text)
        self.assertTrue("kNativeWriteAttemptLimit = 3" in text)
        hooks = read(HOOKS)
        self.assertTrue("IsaacModRuntime_ProbeNativeFileWrite();" in hooks)

    def test_results_reach_the_crash_report(self):
        text = read(BRIDGE)
        for register in ("x26", "x27", "x28"):
            self.assertTrue(f'__asm__("{register}")' in text, register)
        header = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertTrue("x26 the game's own file functions resolved by name" in header)

    def test_plugin_hands_over_the_lookup_address_after_the_entry(self):
        text = read(PLUGIN)
        self.assertTrue("kSetSymbolLookupSymbol" in text)
        self.assertTrue("resolver_.LookupAddress()" in text)
        body = text.split("void HostHandoverMain(void*) {", 1)[1].split("} // namespace", 1)[0]
        self.assertTrue("setSymbolLookup" in body)
        resolver_header = read(ROOT / "src" / "host_plugin" / "saltynx_symbol_resolver.hpp")
        self.assertTrue("LookupAddress() const noexcept" in resolver_header)


class SaltyIpcProbeTests(unittest.TestCase):
    """Can the Runtime talk to SaltyNX's sysmodule at all?"""

    def test_probe_calls_the_sysmodule_entries_that_report_their_result(self):
        text = read(BRIDGE)
        self.assertTrue("IsaacModRuntime_ProbeSaltyIpc" in text)
        self.assertTrue('find("SaltySD_printf")' in text)
        self.assertTrue('find("SaltySD_GetBID")' in text)
        self.assertTrue("g_saltyPrintfResult = saltyPrintf(" in text)
        self.assertTrue("g_saltyGetBid = saltyGetBid();" in text)
        self.assertTrue("kSaltyIpcAttemptLimit = 3" in text)
        hooks = read(HOOKS)
        self.assertTrue("IsaacModRuntime_ProbeSaltyIpc();" in hooks)

    def test_results_are_reported_in_their_own_registers(self):
        text = read(BRIDGE)
        for register in ("x23", "x24", "x25"):
            self.assertTrue(f'__asm__("{register}")' in text, register)
        header = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertTrue("x24 what `SaltySD_printf` returned" in header)
        self.assertTrue("x25 the title id `SaltySD_GetBID` answered with" in header)


class SaltyReinitProbeTests(unittest.TestCase):
    """SaltyNX closes its session when its payload finishes; can the Runtime open its own?"""

    def test_probe_reopens_the_session_and_writes_through_it(self):
        text = read(BRIDGE)
        self.assertTrue("IsaacModRuntime_ProbeSaltyReinit" in text)
        self.assertTrue('find("SaltySD_Init")' in text)
        self.assertTrue("g_reinitResult = reinterpret_cast<SaltyInitFn>(initAddress)();" in text)
        self.assertTrue('open(kReinitProbePath, "wb")' in text)
        self.assertTrue("kReinitProbePath" in text)
        self.assertTrue("if (g_reinitAttempts != 0 || g_symbolLookup == 0) {" in text)
        hooks = read(HOOKS)
        # Probe-build only, like every other probe call site. The *call* is the indented
        # statement; the declaration above it is not what this checks.
        calls = [line.strip() for line in hooks.splitlines()]
        for probe in ("IsaacModRuntime_ProbeSaltyReinit();", "IsaacModRuntime_ProbeSaltyIpc();",
                      "IsaacModRuntime_ProbeNativeFileWrite();"):
            self.assertIn(probe, calls)
            # 渲染钩子体已抽成共享 `RenderHookBody`（Task 4），这些调用从 8 空格缩进变成 4 空格。
            # 这里不绑定缩进宽度，只要求"它是一条独立语句"，守卫与 #endif 邻近性断言不变。
            matches = list(re.finditer(r"^[ \t]+" + re.escape(probe) + r"$", hooks, re.M))
            self.assertTrue(matches, probe)
            index = matches[-1].start()
            guard = hooks.rfind("#if defined(EXL_PROBE_BREAK)", 0, index)
            self.assertTrue(guard != -1, probe)
            self.assertLess(hooks.index("#endif", index), index + 400, probe)

    def test_outcome_is_reported_in_registers(self):
        text = read(BRIDGE)
        header = read(SOURCE / "saltynx_probe_break.hpp")
        self.assertTrue("the session re-opening probe" in header)
        self.assertTrue("x9  what `SaltySD_printf` returned after the session was reopened" in header)
        self.assertTrue("g_reinitOpenOk" in text)


class BuildStampTests(unittest.TestCase):
    """A stale object must never stamp an old Build ID into a fresh package."""

    def test_build_rule_checks_the_flag_stamp_before_compiling(self):
        text = read(ROOT / "misc" / "mk" / "common.mk")
        self.assertIn("check-build-id.sh", text)

    def test_stamp_script_drops_objects_when_the_flags_change(self):
        script = read(ROOT / "misc" / "scripts" / "check-build-id.sh")
        self.assertIn('rm -f "${BUILD_DIR}"/*.o', script)
        self.assertIn(".build_flags", script)


if __name__ == "__main__":
    unittest.main()
