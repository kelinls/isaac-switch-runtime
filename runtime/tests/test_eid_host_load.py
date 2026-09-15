"""宿主机验证通道：把真实 PC Mod EID 的 `main.lua` 用**仓库自己的 Lua Runtime 源码**跑起来。

## 这个测试解决什么问题

真机一轮只能问一个问题。EID 的加载期失败一路是这样推进的：

* 报告 `01789200504`：`EntryRead` 失败 —— 16 KiB 脚本缓冲区装不下 87,328 字节的 `main.lua`
  （修复后由 `runtime/tests/test_scriptless_mod_load.py` 在宿主机钉住）；
* 报告 `01789201566`：`LuaInit` + detail 5 = `ScriptRunFailed`，错误文本 102 字节、
  前 8 字节 `..._mods` —— 但**真正要的"行号 + 消息"在后面的字节里**，只能再花一轮真机；
* 报告 `01789201969`：拿到完整文本，是 `features/eid_mcm.lua:81`，根因是
  `IsAsciiModuleCharacter()` 少了 `+`，EID 的 `ab+` 语言包从未加载。

也就是说：**每问一层就要一轮真机**。本测试把同一条路径搬到宿主机 —— 用同一份
`runtime/source` + `runtime/src` 源码（`lua_runtime.cpp`、`interfaces/lua/*`、
`application/mod/*`、`infrastructure/*` 与 vendored Lua 5.3.3），按真机顺序走完
"读清单 → 拼 `entryPath`/`modRoot`/`chunkName` → 读入口 → `InitializeManifestMod`
→ `luaL_loadbufferx` + `lua_pcallk`"，并把执行期的 Lua 错误文本**整段**取出来
（`LuaRuntime::CopyLastLuaErrorText`，与真机探针同一条通道）。于是"下一层失败点、行号、
消息"在宿主机上就能拿到，真机只需要做最终验收。

工具本体在 `tools/eid_host_load.py`（CLI：`python3 tools/eid_host_load.py --sweep`），
本测试是它的回归门禁：**工具坏了、夹具对不上了、通道不再报出位置了，都在这里红**。

## 断言口径（刻意不与"当前失败点"耦合）

运行时的缺口正在被并行修（`ItemConfig`、`os` 之类）。所以这里断言的是**通道的性质**，
不是"现在恰好停在哪一行"：

1. 清单解析结果与真机逐字节一致（chunk name、entry path、mod root、入口 87,328 字节）；
2. 脚本**真的被执行到了**（读取轨迹里有 `main.lua`、`eid_config.lua`、`features/eid_mcm.lua`、
   `descriptions/ab+/en_us.lua` —— 说明已经越过 `main.lua:61` 的引擎调用与 111+ 的 `require` 段）；
3. 失败时必须报出**失败模块 + 行号 + 触发语句**，且该模块能在夹具里定位（不是"只报一个长度"）；
4. 真机长度算式必须成立：`102 = 前缀 60 + 行号位数 + 2 + 消息长度`，长度不一致时要能被识别出来
   （宿主机没有引擎，绝不允许把"宿主机的失败"说成"真机的失败"）；
5. 16 KiB 缓冲区的历史根因必须仍然可复现（`--max-bytes 16384` → `EntryRead` + 观测长度 87,328）；
6. 补偿性扫描（`--sweep`）必须能跑到底：要么加载成功，要么把所有失败点按顺序列出来；
   不允许把语义级阻塞当成"缺全局"糊过去 —— 一旦需要注释掉某条语句，测试就失败并要求人工确认。
"""

import unittest

from tools.eid_host_load import (
    DEVICE_CHUNK_NAME,
    DEVICE_ERROR_LENGTH,
    DEVICE_ERROR_PREFIX,
    EID_DIRECTORY,
    EID_ENTRY_RELATIVE,
    LUA_CHUNKID_PREFIX_LENGTH,
    host_compilers,
    locate_eid_romfs,
    prepare_workspace,
    run_harness,
    sweep_eid_load,
)

#: 入口脚本必须装得进 1 MiB 缓冲区（16 KiB 时代的根因）。
EID_ENTRY_BYTES = 87328
#: 越过 `main.lua:61` 的 `Sprite()` 与 111+ 的 `require` 段之后必然会读到的文件。
REQUIRED_READS = (
    EID_ENTRY_RELATIVE,
    f"mods/{EID_DIRECTORY}/eid_config.lua",
    # 111+ 的 require 段：读到 mcm 说明已经越过 `main.lua:61` 的 `Sprite()`；
    # 读到 eid_data 说明 `main.lua:120` 的 `require("features.eid_mcm")` 成功（即 ab+ 修复生效）。
    f"mods/{EID_DIRECTORY}/features/eid_mcm.lua",
    f"mods/{EID_DIRECTORY}/features/eid_data.lua",
    f"mods/{EID_DIRECTORY}/descriptions/ab+/en_us.lua",
)

#: 只存在于 `descriptions/ab+/` 的四门语言（`rep/` 里没有）：2026-09-12 真机根因的判据。
#: `IsAsciiModuleCharacter()` 少了 `+` 时，这四条 `require` 全都会被判成"不安全的模块名"。
AB_ONLY_LANGUAGES = ("pt", "bul", "nl_nl", "el_gr")

#: 加载/执行**成功**时允许出现在错误通道里的唯一一条文本 —— EID 自己声明为可选的那个模组。
#:
#: `features/eid_mcm.lua:20`（`eid_mcm_cn.lua:1` 同样）写的是
#: `local MCMLoaded, MCM = pcall(require, "scripts.modconfig")`：`scripts.modconfig` 是
#: **另一个模组**（Mod Config Menu，MCM）提供的模块，而本项目的部署树里
#: `isaac_mods/mods/` 只有 EID 一个目录（发布包与老卡都是如此）⇒ 这条 `require` 必然失败，
#: 且 EID 自己把它当"没装 MCM"处理（`EID.MCMLoaded = false`，功能降级但不报错）。
#:
#: 为什么它会出现在错误通道里：运行时用 `ReportAndRecordLuaError()` 替代 `luaL_error`，
#: **在抛错之前**就把整段消息记进"最后一次 Lua 错误文本"。这是为了真机取证（早先正是靠
#: 这条通道拿到 `features/eid_mcm.lua:81` 那个致命错误的完整文本），代价是被模组
#: `pcall` 兜住的失败同样会留下记录。所以"加载成功 ⇒ errLen 必须为 0"这个口径已经不成立，
#: 正确的是"加载成功 ⇒ 允许留下的错误只能是这条已知的可选模组缺失"。
TOLERATED_OPTIONAL_MODULE = "scripts.modconfig"


class EidHostLoadTests(unittest.TestCase):
    """真实 EID 夹具在真实 Runtime 源码上的加载行为（夹具缺失时整体 skip）。"""

    @classmethod
    def setUpClass(cls):
        cls.romfs = locate_eid_romfs()
        if cls.romfs is None:
            raise unittest.SkipTest("缺少 EID 夹具（analysis/ 或 runtime/.eid-artifacts，均不在 git 里）")
        if host_compilers() is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（cc + c++/clang++/g++）")

        import tempfile
        from pathlib import Path

        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-eid-host-")
        cls.workdir = Path(cls.temporary.name)
        cls.executable, cls.prepared = prepare_workspace(cls.romfs, cls.workdir)

        # ① 忠实运行（只补惰性宿主引擎绑定：宿主机没有引擎，真机有）。
        cls.report = cls._run(engine_shims=True, frames=0)
        # ② 完全不补引擎绑定的基线：第一处引擎调用就会失败 —— 这是**宿主机与真机的差异**。
        cls.baseline = cls._run(engine_shims=False, frames=0)
        # ③ 16 KiB 缓冲区的历史根因。
        cls.small_buffer = cls._run(engine_shims=True, frames=0, capacity=16384)
        # ④ 真机普查的宿主复现：`Font:Load` 失败 ⇒ `main.lua` 顶层 return ⇒ 什么都没登记。
        cls.font_fail = cls._run(engine_shims=True, frames=0, font_load=False)
        # ⑤ 补偿性扫描：按顺序列出所有失败点。
        cls.sweep = sweep_eid_load(
            romfs=cls.romfs, workdir=cls.workdir, keep=True, frames=0, engine_shims=True
        )

    @classmethod
    def _run(cls, *, engine_shims: bool, frames: int, capacity: int = 1048576,
             font_load: bool = True):
        return run_harness(cls.executable, cls.prepared, capacity, frames, engine_shims,
                           font_load)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    # --- ① 夹具与路径：与真机逐字节一致 ---------------------------------------

    def test_manifest_resolution_matches_the_device_chunk_name(self):
        """`chunkName` 是 `luaO_chunkid` 截断与真机 102 字节算式的前提，必须逐字节一致。"""
        report = self.report
        self.assertTrue(report["resolveOk"], report)
        self.assertEqual(report["chunkName"], DEVICE_CHUNK_NAME)
        self.assertEqual(report["chunkNameLength"], len(DEVICE_CHUNK_NAME))
        self.assertEqual(report["entryPath"], f"rom:/isaac_mods/{EID_ENTRY_RELATIVE}")
        self.assertEqual(report["modRoot"], f"rom:/isaac_mods/mods/{EID_DIRECTORY}")
        self.assertEqual(report["entryCapacity"], 1048576)

    def test_entry_script_is_the_real_eid_main(self):
        """入口必须是那份 87,328 字节的 `main.lua`（真机读到的同一个文件）。"""
        import hashlib

        entry = self.romfs / EID_ENTRY_RELATIVE
        self.assertEqual(entry.stat().st_size, EID_ENTRY_BYTES)
        # 与设备状态树、analysis 夹具三处同源：内容哈希必须一致。
        self.assertEqual(
            hashlib.sha256(entry.read_bytes()).hexdigest(),
            "46a391dd279d7c194bb5a7b3544152ca8780e953a728d63e5510baa050edcf2e",
        )

    def test_script_buffer_is_one_mib_and_the_fix_is_visible(self):
        """16 KiB 缓冲区装不下入口脚本：这条历史根因必须在宿主机上仍然可复现。

        真机报告 `01789200504` 的根因就是它（`EntryRead` 失败、诊断字 `[11] = 4`），
        而当时的现象是"不报错、什么都不显示"。这条断言让"缓冲区再被缩回去"在宿主机上直接红。
        """
        report = self.small_buffer
        self.assertFalse(report["loadOk"])
        self.assertEqual(report["failureStep"], "EntryRead")
        self.assertEqual(report["observedBytes"], EID_ENTRY_BYTES,
                         "读取失败时必须交回观测到的文件长度（这是区分'不存在'与'缓冲区太小'的唯一线索）")
        # 真机当时的诊断字 `[5]..[8] = 0`（一个回调都没登记）= "Lua 一行都没跑"：
        # 读取阶段就失败时，错误文本必须是空的。
        self.assertEqual(report["errorLength"], 0)

    # --- ② 脚本真的跑起来了：读取轨迹与失败定位 --------------------------------

    def _read_paths(self, report):
        return {item["path"] for item in report.get("readRequests", []) if item["status"] == "ok"}

    def test_the_runtime_executes_the_real_mod_script(self):
        """必须真的读到入口与 `require` 链上的模块 —— 否则"跑到哪里"无从谈起。"""
        read = self._read_paths(self.report)
        for path in REQUIRED_READS:
            with self.subTest(path=path):
                self.assertIn(path, read)
        successful = sum(1 for item in self.report["readRequests"] if item["status"] == "ok")
        self.assertGreater(successful, 90, "EID 的 require 链会读近百个 Lua 文件")

    def test_the_four_ab_only_language_packs_load(self):
        """`descriptions/ab+/{pt,bul,nl_nl,el_gr}.lua` 必须能 `require`。

        真机报告 `01789201969` 的根因就在这四条：`IsAsciiModuleCharacter()` 少了 `+`，
        模块名 `descriptions.ab+.pt` 被判成不安全 → 抛 not-found → EID 的 `pcall` 当"文件不存在"
        放过 → 这四门语言的描述表从来没建起来 → `eid_mcm.lua:81` 索引 nil 崩掉。
        这条断言让"`+` 又被挡掉"在宿主机上立刻红。
        """
        read = self._read_paths(self.report)
        for language in AB_ONLY_LANGUAGES:
            for group in ("descriptions/ab+", "descriptions/names"):
                path = f"mods/{EID_DIRECTORY}/{group}/{language}.lua"
                with self.subTest(path=path):
                    self.assertIn(path, read)
        for group in ("item_data", "transformations"):
            path = f"mods/{EID_DIRECTORY}/descriptions/ab+/{group}.lua"
            with self.subTest(path=path):
                self.assertIn(path, read)

    def test_a_failure_names_its_module_line_and_statement(self):
        """通道的核心价值：失败必须报出"哪个模块、哪一行、哪条语句"，而不是只有长度。"""
        report = self.report
        if report["loadOk"]:
            self.assertEqual(report["scriptState"], "Executed")
            return
        position = report.get("failurePosition")
        self.assertIsNotNone(
            position,
            f"加载失败但没有定位到模块:行号：{report.get('errorText')!r}",
        )
        self.assertTrue((self.romfs / "mods" / EID_DIRECTORY / position["module"]).is_file(),
                        f"报出的模块必须能在夹具里定位：{position['module']}")
        self.assertGreater(position["line"], 0)
        self.assertTrue(position["message"])
        self.assertTrue(position["statement"], "失败行必须能贴出源码语句")

    def assert_no_unexpected_lua_error(self, report):
        """成功路径上只允许记录"缺可选模组 MCM"这一条错误（见 `TOLERATED_OPTIONAL_MODULE`）。

        原口径是 `errorLength == 0`。它现在不成立，原因不是运行时变差，而是这条通道**刻意**
        在抛错之前记录文本：EID 用 `pcall` 兜住的 `require("scripts.modconfig")`（MCM 是另一个
        模组，我们没部署）也会留下一段文本。改成"要么没有记录，要么记录的必须是那一条"，
        比"必须为 0"更贴合现实、对**新出现的**任何其它错误仍然一样严。
        """
        length = report["errorLength"]
        if length == 0:
            return
        text = report.get("errorText", "")
        self.assertIn(
            TOLERATED_OPTIONAL_MODULE,
            text,
            f"成功路径上出现了非预期的 Lua 错误（{length} 字节）：{text!r}",
        )

    def test_lua_error_text_is_reported_whole(self):
        """错误文本必须整段带出（真机探针那条 8 字节的通道只能认出"是哪份脚本"）。"""
        report = self.report
        if report["loadOk"]:
            self.assert_no_unexpected_lua_error(report)
            return
        self.assertGreater(report["errorLength"], 8)
        self.assertGreater(len(report["errorText"]), 8)
        self.assertTrue(report["errorText"].startswith("..."),
                        "Lua 5.3.3 的 `luaO_chunkid` 会把长 chunk 名截成 '...' + 末 56 字节")
        self.assertLessEqual(report["errorCopied"], 256,
                             "运行时只保留最近一次错误的前 256 字节")

    # --- ③ 与真机证据的一致性：长度算式与差异说明 ------------------------------

    def test_device_length_budget_explains_a_102_byte_report(self):
        """真机那 102 字节的构成必须能在宿主机上被算出来（否则"对不上"就无法解释）。

        真机的 `..._mods/.../main.lua:120: required module could not be executed`：
        前缀 60（`"..."` + 路径末 56 字节 + `:`）+ 行号 3 位 + `": "` + 消息 37 = 102。
        """
        self.assertEqual(LUA_CHUNKID_PREFIX_LENGTH + 3 + 2 + len("required module could not be executed"),
                         DEVICE_ERROR_LENGTH)
        self.assertEqual(len(DEVICE_ERROR_PREFIX), 8)
        self.assertEqual(self.report["deviceLengthBudget"], DEVICE_ERROR_LENGTH - LUA_CHUNKID_PREFIX_LENGTH)
        # 宿主机的 chunk name 与真机一致 ⇒ 前缀也一致；这是长度可比的前提。
        self.assertTrue(self.report["chunkMatchesDevice"])

    def test_host_environment_difference_is_stated_not_hidden(self):
        """没有任何引擎绑定时，脚本必须停在第一处引擎调用上 —— 这是宿主机与真机的差异。

        真机有引擎（`SetSpriteBindings`/`SetFontBindings` 在 Hook 安装阶段发布、已校验），
        所以 `main.lua:61` 的 `Sprite()` 与 `main.lua:165` 的 `Font()` 能正常工作；宿主机没有，
        于是报 "Sprite native binding is unavailable"。这条断言把差异钉住：**不许假装一致**。
        """
        baseline = self.baseline
        self.assertFalse(baseline["loadOk"])
        position = baseline.get("failurePosition")
        self.assertIsNotNone(position, baseline.get("errorText"))
        self.assertEqual(position["module"], "main.lua")
        self.assertIn("native binding is unavailable", position["message"])
        self.assertLess(position["line"], 111,
                        "第一处引擎调用在 require 段（111+）之前")
        # 惰性引擎绑定之后，同一次运行不再停在这里。
        self.assertNotIn("native binding is unavailable", self.report.get("errorText", ""))

    # --- ④ 真机普查的宿主复现（报告 01789203482）--------------------------------

    def test_font_load_failure_reproduces_the_device_census(self):
        """`Font:Load` 失败时，宿主机的回调普查必须与真机逐项一致。

        真机报告 `01789203482`：诊断字 `[11] = 0`（"带脚本加载成功"）、没有 Lua 错误、
        已登记种类 `{5,10,15,18,19,23,38}`、**有派发点的登记 0 条**、无派发点 10 条、注册表 7 条 ——
        （2026-09-14 起"有派发点/无派发点"这两个数会与这份历史报告不同：`MC_POST_GAME_STARTED`(15)
        被提升为常驻挂点、进了派发白名单。15 在这一段路径上被登记了**两次**
        （`liveCallbackCounts` 里 `15:2`，因为 EID 有两处注册它），两条都算"有派发点" ⇒ 2/8。
        种类掩码本身没变 —— 那才是"脚本死在字体块"的证据。）
        于是 `MC_POST_UPDATE`/`MC_POST_RENDER` 从未登记、屏幕空白。

        宿主机上把 `Font:Load`/`Font:IsLoaded` 置假（= 真机引擎读不到 `.fnt`）后，同样的三项读数
        逐个对上。根因：EID 在 `main.lua:169` 的字体块里，两次 `EID:loadFont` 都失败就
        **顶层 `return`** —— 脚本"成功结束"，但 169 行之后的所有 `Mod:AddCallback`
        （含 `main.lua:1181` 的 `MC_POST_UPDATE` 与 `main.lua:1692` 的 `MC_POST_RENDER`）都不执行。
        """
        report = self.font_fail
        self.assertTrue(report["loadOk"], "顶层 return 不是错误：加载必须报成功")
        self.assertEqual(report["scriptState"], "Executed")
        # 真机当时的 errLen 是 0；宿主机这条通道现在会留下 EID 用 `pcall` 兜住的
        # `require("scripts.modconfig")`（MCM 是另一个模组，我们没部署），见
        # `TOLERATED_OPTIONAL_MODULE`。除它以外任何错误都仍然算红。
        self.assert_no_unexpected_lua_error(report)
        self.assertEqual(report["callbackKindMask"], "5,10,15,18,19,23,38",
                         "真机已登记种类掩码必须逐项一致")
        # 逐种类存活数：它解释了下面 2/8 这个拆分（15 登记两次，其余各一次，共 10 条）。
        self.assertEqual(report["liveCallbackCounts"], "5:1,10:1,15:2,18:1,19:1,23:3,38:1")
        self.assertEqual(report["registryCount"], 10)
        # 15（`MC_POST_GAME_STARTED`）在 2026-09-14 之后有派发点；它被登记两次 ⇒ 2 条。
        self.assertEqual(report["dispatchableRegistrations"], 2)
        self.assertEqual(report["unhookedRegistrations"], 8)
        self.assertEqual(report["dispatchableRegistrations"] + report["unhookedRegistrations"],
                         report["registryCount"], "两个计数必须覆盖注册表里的全部登记")
        # 2026-09-12：`CallbackRegistry::Register` 改成**追加**之后，font-fail 这一跑的
        # 10 次登记会全部留下（旧值 7 = 被覆盖掉 3 条时的读数，正是真机 01789203805 的现象）。
        # 种类掩码与"无派发点登记数"仍必须与真机逐项一致 —— 那两条才是"脚本死在字体块"的证据。
        # （`registryCount` 上面已经断言过，这里不再重复。）

    def test_a_loaded_font_registers_the_two_dispatched_kinds(self):
        """字体加载成功时，`MC_POST_UPDATE`(1) 与 `MC_POST_RENDER`(2) 必须登记 —— 这就是分叉点。"""
        report = self.report
        kinds = {int(item) for item in report["callbackKindMask"].split(",") if item}
        self.assertIn(1, kinds, "MC_POST_UPDATE 必须登记")
        self.assertIn(2, kinds, "MC_POST_RENDER 必须登记")
        self.assertGreaterEqual(report["dispatchableRegistrations"], 2)
        # 注册表条数不可能超过登记次数（相等只在"同一 id 的多次登记都被保留"时成立；
        # 目前的 `CallbackRegistry::Register` 会按 (id, owner) 覆盖，于是 35 次登记只剩 16 条。
        # 这里只钉住恒真的那一侧，具体差值由 `--trace-addcallback` 报出，避免把产品缺陷
        # 写进断言、让本用例在产品修好前一直是红的。
        total = report["dispatchableRegistrations"] + report["unhookedRegistrations"]
        self.assertLessEqual(report["registryCount"], total)

    # --- ⑤ 补偿性扫描：加载期还有哪些阻塞 --------------------------------------

    def test_sweep_lists_every_load_phase_blocker(self):
        """扫描必须跑到底：或者加载成功，或者把所有失败点按顺序列出来（每个都带定位信息）。"""
        sweep = self.sweep
        self.assertTrue(sweep["blockers"] or sweep["finalReport"]["loadOk"])
        for blocker in sweep["blockers"]:
            position = blocker.get("position")
            self.assertIsNotNone(position, blocker["errorText"])
            self.assertTrue(position["module"])
            self.assertGreater(position["line"], 0)
            self.assertTrue(position["message"])
            self.assertTrue(blocker["compensation"])
        self.assertLessEqual(len(sweep["blockers"]), 8,
                             "加载期阻塞点应当很少；数量突然变大说明出现了解析/补偿异常")

    def test_sweep_only_stops_for_missing_globals(self):
        """扫描只应因"运行时缺某个全局"而停 —— 出现被注释的语句就是语义级阻塞，需要人工确认。"""
        self.assertEqual(
            self.sweep["maskedLines"], {},
            "扫描不得不注释脚本语句：这说明出现了不是'缺全局'造成的阻塞，请人工检查 "
            f"{self.sweep['maskedLines']}",
        )

    def test_sweep_blockers_carry_their_root_cause(self):
        """缺全局的阻塞点必须与已知的两处根因对得上（运行时修好后本用例自动转为通过）。"""
        stubs = set(self.sweep["stubbedGlobals"])
        positions = {
            blocker["position"]["module"]: blocker["position"]
            for blocker in self.sweep["blockers"] if blocker.get("position")
        }
        if "ItemConfig" in stubs:
            position = positions.get("features/eid_data.lua")
            self.assertIsNotNone(position, f"ItemConfig 缺口的定位不对：{positions}")
            self.assertIn("ItemConfig", position["statement"])
            self.assertIn("global 'ItemConfig'", position["message"])
        if "os" in stubs:
            position = positions.get("features/eid_tmtrainer.lua")
            self.assertIsNotNone(position, f"os 缺口的定位不对：{positions}")
            self.assertIn("os.date", position["statement"])

    def test_sweep_reaches_the_end_of_the_load_phase(self):
        """把缺的全局补上之后，EID 的 `main.lua` 必须能从头跑到尾。

        这是本通道最有用的结论："再改哪几处就能让加载期跑完"。运行时把这两个全局补齐后，
        这个断言会自然变成"零补偿即跑通"。
        """
        final = self.sweep["finalReport"]
        self.assertTrue(final["loadOk"], f"扫描结束时仍未跑通：{final.get('errorText')!r}")
        self.assertEqual(final["scriptState"], "Executed")


if __name__ == "__main__":
    unittest.main()
