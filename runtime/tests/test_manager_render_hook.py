import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
SRC = ROOT / "runtime" / "src"


class ManagerRenderHookTests(unittest.TestCase):
    def test_render_relay_constants_and_install_api_are_declared(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")

        for token in (
            "kManagerRenderFileOffset",
            "kManagerRenderRelayCodeOffset",
            "kManagerRenderRelaySlotOffset",
            "kManagerRenderRelayExpectedBytes",
        ):
            with self.subTest(token=token):
                self.assertIn(token, constants)
        self.assertIn("TryInstallManagerRenderHook", header)

    def test_render_hook_calls_original_before_dispatching_render_callback(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        # 两条后端（exlaunch 蹦床 / 入口中继）共用 `RenderHookBody`，行为单一来源：
        # 先跑原函数，再在 `Present` 前中继缺失时回退派发。
        body_start = source.index("void RenderHookBody(IsaacRepentance::Manager* self")
        body_end = source.index('extern "C" void EntryRelayManagerRenderCallback', body_start)
        body = source[body_start:body_end]

        # 入口中继只做记账 + 诊断 + 探针，不派发 MC_POST_RENDER（那条路径在 Present 调用点）。
        self.assertLess(body.index("call_original(self)"), body.index("DispatchModPostRender(self)"))
        self.assertIn("IsRenderPresentRelayInstalled()", body)
        self.assertNotIn("LuaRuntime::DispatchPostRender", body)

        # 旧后端（exlaunch 蹦床）仍把原函数经 `Orig` 派发给 `RenderHookBody`。
        start = source.index("HOOK_DEFINE_TRAMPOLINE(ManagerRenderHook)")
        trampoline = source[start:source.index("};", start)]
        self.assertIn("RenderHookBody(self,", trampoline)
        self.assertIn("Orig(", trampoline)

    def test_present_relay_moves_the_dispatch_point_before_the_frame_is_presented(self):
        """`MC_POST_RENDER` 必须在 `Present` 之前派发。

        入口中继是在 `Manager::Render` 整体返回之后派发的，那时本帧已经上屏、帧图像队列已经
        清空：2026-09-13 的真机照片证明回调里画的东西会进下一帧队列、并被实体（以撒）与 HUD
        盖住。补丁把派发点插到 `Manager::Render` 体内最后一次 `Present` 调用之前。
        """
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        patches = (ROOT / "tools" / "build_patches.py").read_text(encoding="utf-8")
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")

        # 1) 补丁：调用点是 `0x3F9B40` 上的 `bl Present`，桩补回原调用并跳回 `调用点+4`。
        self.assertIn("RENDER_PRESENT_CALL_OFFSET = 0x003F9B40", patches)
        self.assertIn('RENDER_PRESENT_CALL_ORIGINAL = bytes.fromhex("C0DB0994")', patches)
        self.assertIn("RENDER_PRESENT_PLT_OFFSET = 0x00670A40", patches)
        self.assertIn("RENDER_PRESENT_RELAY_CODE_OFFSET = 0x0068CF00", patches)
        self.assertIn("RENDER_PRESENT_RELAY_SLOT_OFFSET = RENDER_PRESENT_RELAY_CODE_OFFSET + 0x30", patches)
        self.assertIn('"render-present-relay"', patches)

        # 2) 守卫常量与补丁里的偏移一致（两边写死同一组数字，必须对得上）。
        for token in (
            "kManagerPresentCallFileOffset = 0x3F9B40",
            "kManagerPresentRelayCodeOffset = 0x68CF00",
            "kManagerPresentRelaySlotOffset = 0x68CF30",
            "kManagerPresentRelayExpectedBytes",
        ):
            with self.subTest(token=token):
                self.assertIn(token, constants)

        # 3) 运行时：安装函数把派发入口发布进槽，并记录结果；入口中继按该结果决定是否回退。
        install = source[
            source.index("bool VerifyManagerPresentRelay("):
            source.index("RenderHookInstallResult TryInstallManagerRenderHook")
        ]
        self.assertIn(
            "reinterpret_cast<uintptr_t>(&IsaacModRuntime_DispatchPostRenderBeforePresent)", install
        )
        self.assertIn("RecordRenderPresentRelayInstall", install)
        self.assertIn("VerifyManagerPresentRelay(module, &slot)", install)
        self.assertIn("kManagerPresentCallExpectedEntry", install)
        self.assertIn("kManagerPresentRelayExpectedBytes", install)

        # 4) 派发实现仍然走同一套 Lua 派发器与就绪闸门。
        dispatch_start = source.index("void DispatchModPostRender(void* self) {")
        dispatch = source[dispatch_start:source.index("bool IsRenderPresentRelayInstalled() {", dispatch_start)]
        self.assertIn("LuaRuntime::DispatchPostRender(reinterpret_cast<uintptr_t>(self))", dispatch)
        self.assertIn("PrimeDefaultCallbacks(reinterpret_cast<uintptr_t>(self))", dispatch)
        self.assertIn("LuaRuntime::IsReady()", dispatch)

    def test_present_relay_dispatch_uses_the_frame_manager_not_the_present_argument(self):
        """`Present` 调用点的 `x0` 是图形管理器，不能当作 `Manager*` 使用。

        真机第四轮（报告 `01789140862`）就是栽在这里：中继装上了、也进入了（掩码位 52/55 = 1），
        但 `PrimeDefaultCallbacks` 拿到 `KAGE::Graphics::g_Manager` 后音乐就绪判定为假，于是
        `MC_POST_RENDER` 的回调一次都没被调用 —— Mod 的标记停在 1、屏幕上什么都没有。
        `Manager*` 由两个入口中继记进 `g_RenderFrameManager`，派发入口只用缓存值。
        """
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        entry_start = source.index("IsaacModRuntime_DispatchPostRenderBeforePresent(void* graphicsManager)")
        entry = source[entry_start:source.index("\n}\n", entry_start)]
        self.assertIn("g_RenderFrameManager.load(std::memory_order_acquire)", entry)
        # 参数只作兜底，不能直接进派发路径。
        self.assertNotIn("DispatchModPostRender(graphicsManager)", entry)
        self.assertLess(
            entry.index("g_RenderFrameManager.load(std::memory_order_acquire)"),
            entry.index("DispatchModPostRender"),
        )

        # `ManagerRender` 的记账存值已抽到共享 `RenderHookBody`（两条后端共用）；
        # `ManagerUpdate` 仍是内联。两者都必须在渲染/更新线程里记下 Manager*。
        render_body = source[
            source.index("void RenderHookBody(IsaacRepentance::Manager* self"):
            source.index('extern "C" void EntryRelayManagerRenderCallback')
        ]
        for hook in ("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)",
                     "HOOK_DEFINE_TRAMPOLINE(ManagerRenderHook)"):
            start = source.index(hook)
            body = source[start:source.index("};", start)]
            with self.subTest(hook=hook):
                target = body if hook == "HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)" else render_body
                self.assertIn(
                    "g_RenderFrameManager.store(reinterpret_cast<uintptr_t>(self), "
                    "std::memory_order_release)",
                    target,
                )

        # 缓存必须是常编（不能留在只有分层构建才编译的块里，否则非分层构建直接编译不过）。
        store_line = source.index("std::atomic<std::uintptr_t> g_RenderFrameManager{0};")
        gated = source.index("#if defined(EXL_LAYERED_RUNTIME)")
        self.assertLess(store_line, gated)

    def test_present_relay_is_installed_from_the_layered_mod_install_path(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[
            source.index("DefaultManifestInstallResult TryInstallDefaultManifestMod"):
            source.index("uintptr_t target_address(uintptr_t base, uintptr_t offset)")
        ]
        # 两个可选中继（Present 前派发点、内容挂载点重建）一律经 `IHookPort` 安装：
        # hook_manager 只把目标模块交给 `HookInstallService`，具体安装器由基础设施适配器
        # 按 `HookId` 调用，装没装上记在 `HookInstallReport` 里（设备侧可回读）。
        self.assertIn("InstallProductionHooks(info, &hookReport)", install)
        self.assertNotIn("TryInstallRebuildMountPointsRelay(module)", install)
        self.assertNotIn("TryInstallManagerPresentRelay(module)", install)

        adapter = (SRC / "infrastructure" / "exlaunch" / "exlaunch_hook_adapter.cpp").read_text(
            encoding="utf-8"
        )
        # 两条可选能力都不得让安装失败：端口装不上时 `HookInstallService` 只记 Skipped。
        self.assertIn("HookId::ManagerPresent", adapter)
        self.assertIn("TryInstallManagerPresentRelay(module)", adapter)
        self.assertIn("HookId::RebuildMountPoints", adapter)
        self.assertIn("TryInstallRebuildMountPointsRelay(module)", adapter)

    def test_runtime_installs_render_hook_before_lua_initialization(self):
        source = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        worker = source[source.index("void ModuleWorker"):source.index("extern \"C\" void exl_main")]

        self.assertIn("TryInstallManagerRenderHook(*scan.module)", worker)
        self.assertLess(worker.index("TryInstallManagerRenderHook(*scan.module)"), worker.index("LuaRuntime::Initialize()"))

    def test_post_render_dispatch_uses_only_a_callback_local_manager(self):
        header = (SOURCE / "lua_runtime.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")

        self.assertIn("void DispatchPostRender(uintptr_t manager);", header)
        # The manager pointer must be published for exactly one callback and
        # cleared afterwards. Layered and probe builds share this invoker.
        publish = "g_CurrentCallbackManager.store(manager, std::memory_order_release)"
        clear = "g_CurrentCallbackManager.store(0, std::memory_order_release)"

        def assert_callback_local_manager(block: str, label: str) -> None:
            self.assertIn(publish, block, label)
            self.assertIn(clear, block, label)
            self.assertLess(block.index(publish), block.index("lua_pcallk"), label)
            self.assertLess(block.rindex("lua_pcallk"), block.index(clear), label)

        self.assertIn("struct LuaCallbackInvoker", source)
        invoker = source[
            source.index("struct LuaCallbackInvoker"):source.index("void DispatchPostUpdate() {")
        ]
        assert_callback_local_manager(invoker, "共享回调调用器")

        # And the render dispatcher must actually route through that path.
        render = source[
            source.index("void DispatchPostRender(uintptr_t manager)"):
            source.index("std::uint64_t DispatchPreGetCollectible")
        ]
        self.assertIn("LuaCallbackInvoker invoker{", render)


if __name__ == "__main__":
    unittest.main()
