import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class LuaGameIsPausedStage37Tests(unittest.TestCase):
    def test_stage14_controller_waits_until_ready_claims_once_and_classifies_all_results(self):
        with tempfile.TemporaryDirectory(prefix="isaac-stage14-classifier-") as temporary:
            temporary = Path(temporary)
            harness = temporary / "stage14_classifier.cpp"
            harness.write_text(
                r'''
#include "stage14_diagnostic.hpp"

using namespace Stage14Diagnostic;

int main() {
    Controller controller;
    if (controller.State() != Lifecycle::Installing) return 1;
    if (controller.TryClaimCallback() != Claim::WaitForReady) return 2;
    controller.MarkReady();
    if (controller.State() != Lifecycle::Ready) return 3;
    if (controller.TryClaimCallback() != Claim::Run) return 4;
    if (controller.State() != Lifecycle::Finished) return 5;
    if (controller.TryClaimCallback() != Claim::IgnoreFinished) return 6;
    if (ClassifyCallback(1, false) != CallbackResult::SuccessFalse) return 7;
    if (ClassifyCallback(2, false) != CallbackResult::SuccessTrue) return 8;
    if (ClassifyCallback(0, true) != CallbackResult::CallbackError) return 9;
    if (ClassifyCallback(3, true) != CallbackResult::BooleanTypeMismatch) return 10;
    if (ClassifyCallback(0, false) != CallbackResult::CallbackNotReached) return 11;
    return 0;
}
'''.lstrip()
            )
            binary = temporary / "stage14_classifier"
            build = subprocess.run(
                ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
                 "-I", str(SOURCE), str(harness), "-o", str(binary)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stage14_publishes_ready_only_after_successful_lua_initialization(self):
        runtime = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        worker = runtime[runtime.index("void ModuleWorker"):runtime.index("extern \"C\" void exl_main")]

        initialize = worker.index("LuaRuntime::Initialize()")
        reject_failure = worker.index("luaResult != LuaRuntime::LuaInitResult::Success", initialize)
        publish_ready = worker.index("MarkStage14DiagnosticReady()", reject_failure)
        self.assertLess(initialize, reject_failure)
        self.assertLess(reject_failure, publish_ready)
        self.assertIn("Stage14Diagnostic::Controller g_Stage14Controller", hook)
        self.assertIn("g_Stage14Controller.TryClaimCallback()", hook)

    def test_stage14_embedded_script_caches_game_and_checks_ispaused_boolean(self):
        script = (SOURCE / "program/embedded_lua_test_script.hpp").read_text(encoding="utf-8")

        self.assertIn("local game = Game()", script)
        self.assertIn("local paused = game:IsPaused()", script)
        self.assertIn('type(paused) ~= "boolean"', script)
        self.assertIn("RuntimeTest.MarkPostUpdate()", script)
        self.assertLess(script.index("local game = Game()"), script.index("mod:AddCallback"))
        self.assertLess(script.index("mod:AddCallback"), script.index("game:IsPaused()"))

    def test_stage14_has_independent_callback_success_and_failure_protocol(self):
        runtime = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        self.assertIn("kStage14SuccessMagic", hook)
        self.assertIn("kStage14FailureMagic", runtime)
        self.assertIn("kStage14FailureMagic", hook)
        self.assertIn("(14ULL << 32) | (paused ? 2ULL : 1ULL)", hook)
        self.assertIn("Stage14Diagnostic::ClassifyCallback", hook)
        for status in ("InstallFailed", "LuaInitializationFailed", "CallbackError", "CallbackNotReached", "BooleanTypeMismatch"):
            with self.subTest(status=status):
                self.assertIn(f"Stage14Failure::{status}", runtime + hook)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-game-stage37-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_game_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"

#include <cstring>

namespace {
bool g_Observed = false;
GameIsPausedObservation g_Observation = GameIsPausedObservation::PausedTrue;
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t ownerSlot, uintptr_t isPausedThunk) {
    g_Observed = ownerSlot == 0x1111 && isPausedThunk == 0x2222;
    return g_Observation;
}

GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}

GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) { return GameIsAscentObservation::MethodUnavailable; }

GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}

GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}

GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    const char* scenario = argv[1];
    if (std::strcmp(scenario, "embedded_false") == 0 ||
        std::strcmp(scenario, "embedded_true") == 0) {
        g_Observation = std::strcmp(scenario, "embedded_true") == 0
                            ? GameIsPausedObservation::PausedTrue
                            : GameIsPausedObservation::PausedFalse;
        LuaRuntime::SetGameBindings(0x1111, 0x2222);
        if (LuaRuntime::Initialize() != LuaRuntime::LuaInitResult::Success) return 5;
        LuaRuntime::DispatchPostUpdate();
        const auto expected = g_Observation == GameIsPausedObservation::PausedTrue ? 2u : 1u;
        return g_Observed && LuaRuntime::PostUpdateCount() == expected &&
               !LuaRuntime::TakeCallbackError() ? 0 : 6;
    }
    if (std::strcmp(scenario, "native_unavailable") == 0) {
        g_Observation = GameIsPausedObservation::ThunkUnavailable;
        LuaRuntime::SetGameBindings(0x1111, 0x2222);
        if (LuaRuntime::Initialize() != LuaRuntime::LuaInitResult::Success) return 7;
        LuaRuntime::DispatchPostUpdate();
        return g_Observed && LuaRuntime::TakeCallbackError() ? 0 : 8;
    }
    const char* script = nullptr;
    if (std::strcmp(scenario, "create") == 0) {
        script = "local game=Game(); if type(game)~='userdata' then error('not userdata') end "
                 "local mod=RegisterMod('Probe',1); mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function() end)";
    } else if (std::strcmp(scenario, "top_level") == 0) {
        script = "local game=Game(); game:IsPaused()";
    } else {
        script = "local game=Game(); local mod=RegisterMod('Probe',1); "
                 "mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function() "
                 "local paused=game:IsPaused(); if type(paused)~='boolean' or not paused then error('not boolean') end "
                 "RuntimeTest.MarkPostUpdate() end)";
    }

    if (std::strcmp(scenario, "bound") == 0) {
        LuaRuntime::SetGameBindings(0x1111, 0x2222);
    }
    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@stage37.lua");
    if (std::strcmp(scenario, "top_level") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 1;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 2;
    if (std::strcmp(scenario, "create") == 0) return 0;

    LuaRuntime::DispatchPostUpdate();
    if (std::strcmp(scenario, "bound") == 0) {
        return g_Observed && LuaRuntime::PostUpdateCount() == 1 && !LuaRuntime::TakeCallbackError() ? 0 : 3;
    }
    return LuaRuntime::TakeCallbackError() ? 0 : 4;
}
'''.lstrip()
        )

        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            lua_object = temporary / (lua_source.stem + ".o")
            compile_lua = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root),
                 "-c", str(lua_source), "-o", str(lua_object)],
                text=True,
                capture_output=True,
            )
            if compile_lua.returncode != 0:
                raise AssertionError(compile_lua.stdout + compile_lua.stderr)
            lua_objects.append(lua_object)

        cls.harness = temporary / "lua_game_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_DIAGNOSTIC_STAGE=14",
             "-DEXL_LOAD_KIND=Module", "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *(str(path) for path in lua_objects), "-lm", "-o", str(cls.harness)],
            text=True,
            capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_game_handle_can_be_created_at_script_top_level(self):
        self.run_harness("create")

    def test_game_ispaused_returns_a_boolean_only_while_post_update_dispatches(self):
        self.run_harness("bound")

    def test_game_ispaused_uses_a_managed_runtime_callback_scope(self):
        source = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        game_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "game_api.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("std::atomic<u32> g_InManagedCallbackDispatch{false};", source)
        self.assertNotIn("g_InPostUpdateDispatch", source)
        self.assertIn("Game:IsPaused is only available during a Runtime callback", game_api)

        invoker = source[
            source.index("struct LuaCallbackInvoker"):
            source.index("void DispatchPostUpdate()")
        ]
        self.assertEqual(invoker.count("g_InManagedCallbackDispatch.store(true, std::memory_order_release)"), 1)
        self.assertEqual(invoker.count("g_InManagedCallbackDispatch.store(false, std::memory_order_release)"), 1)
        self.assertLess(
            invoker.index("g_InManagedCallbackDispatch.store(true, std::memory_order_release)"),
            invoker.index("lua_pcallk"),
        )
        self.assertLess(
            invoker.rindex("lua_pcallk"),
            invoker.rindex("g_InManagedCallbackDispatch.store(false, std::memory_order_release)"),
        )

    def test_game_ispaused_at_top_level_is_a_lua_error(self):
        self.run_harness("top_level")

    def test_game_ispaused_without_published_bindings_is_a_lua_error(self):
        self.run_harness("unbound")

    def test_game_ispaused_with_an_unavailable_runtime_thunk_is_a_lua_error(self):
        self.run_harness("native_unavailable")

    def test_stage14_embedded_script_encodes_the_observed_boolean(self):
        self.run_harness("embedded_false")
        self.run_harness("embedded_true")

    def test_default_and_stage14_publish_only_verified_native_bindings(self):
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[
            source.index("HookInstallResult TryInstallManagerUpdateHook"):
            source.index("bool InstallManagerUpdateHook")
        ]

        self.assertIn("bool VerifyGameOwnerSlot", header)
        self.assertIn("bool VerifyGameIsPausedThunk", header)
        self.assertIn("!defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 14", install)
        for token in (
            "VerifyGameOwnerSlot(module, &ownerSlot)",
            "VerifyGameIsPausedThunk(module, &isPausedThunk)",
            "g_LuaGameOwnerSlot.store(ownerSlot, std::memory_order_release)",
            "g_LuaGameIsPausedThunk.store(isPausedThunk, std::memory_order_release)",
            "g_LuaGameOwnerSlot.load(std::memory_order_acquire)",
            "g_LuaGameIsPausedThunk.load(std::memory_order_acquire)",
            "LuaRuntime::SetGameBindings(publishedOwnerSlot, publishedIsPausedThunk)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, install)
        self.assertLess(install.index("VerifyGameOwnerSlot(module, &ownerSlot)"),
                        install.index("g_LuaGameOwnerSlot.store"))
        self.assertLess(install.index("VerifyGameIsPausedThunk(module, &isPausedThunk)"),
                        install.index("g_LuaGameIsPausedThunk.store"))
        self.assertLess(install.index("g_LuaGameOwnerSlot.store"),
                        install.index("g_LuaGameOwnerSlot.load"))
        self.assertLess(install.index("g_LuaGameIsPausedThunk.store"),
                        install.index("g_LuaGameIsPausedThunk.load"))
        self.assertLess(install.index("g_LuaGameIsPausedThunk.load"),
                        install.index("LuaRuntime::SetGameBindings"))
        self.assertLess(install.index("__atomic_store_n"),
                        install.index("LuaRuntime::SetGameBindings"))
        self.assert_binding_publication_order(install)

    def test_binding_publication_order_rejects_a_missing_relay_confirmation(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[
            source.index("HookInstallResult TryInstallManagerUpdateHook"):
            source.index("bool InstallManagerUpdateHook")
        ]
        missing_relay_confirmation = install.replace(
            "    if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {\n"
            "        return RecordUpdateHookInstall(HookInstallResult::RelayPublishFailed);\n"
            "    }\n\n",
            "",
        )

        with self.assertRaises(AssertionError):
            self.assert_binding_publication_order(missing_relay_confirmation)

    def assert_binding_publication_order(self, install):
        binding_publish = install.index("LuaRuntime::SetGameBindings")
        owner_confirmation = (
            "if (publishedOwnerSlot != ownerSlot || publishedIsPausedThunk != isPausedThunk) {\n"
            "        return HookInstallResult::GameBindingsPublishFailed;"
        )
        relay_confirmation = (
            "if (__atomic_load_n(reinterpret_cast<const uintptr_t*>(slot), __ATOMIC_ACQUIRE) != callback) {\n"
            "        return RecordUpdateHookInstall(HookInstallResult::RelayPublishFailed);"
        )

        self.assertIn(owner_confirmation, install)
        self.assertIn(relay_confirmation, install)
        self.assertLess(install.index("g_LuaGameOwnerSlot.load(std::memory_order_acquire)"),
                        install.index(owner_confirmation))
        self.assertLess(install.index("g_LuaGameIsPausedThunk.load(std::memory_order_acquire)"),
                        install.index(owner_confirmation))
        self.assertLess(install.index(owner_confirmation), binding_publish)
        self.assertLess(install.index(relay_confirmation), binding_publish)

    def test_default_and_stage14_initialize_and_dispatch_only_after_hook_success(self):
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        runtime = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        worker = runtime[runtime.index("void ModuleWorker"):runtime.index("extern \"C\" void exl_main")]
        callback = hook[hook.index("static void Callback"):hook.index("};", hook.index("static void Callback"))]

        self.assertIn("EXL_DIAGNOSTIC_STAGE != 14", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 14", runtime)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 14", worker)
        self.assertIn("LuaRuntime::Initialize()", worker)
        self.assertLess(worker.index("HookInstallResult::Success"), worker.index("LuaRuntime::Initialize()"))
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 14", callback)
        self.assertLess(callback.index("Orig(self);"), callback.index("LuaRuntime::DispatchPostUpdate()"))

    def test_stage6_keeps_its_existing_ispaused_publication_protocol(self):
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = source[
            source.index("HookInstallResult TryInstallManagerUpdateHook"):
            source.index("bool InstallManagerUpdateHook")
        ]
        stage6 = install[
            install.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6"):
            install.index("#endif", install.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 6"))
        ]

        for token in (
            "g_Stage6GameOwnerSlot.store(ownerSlot, std::memory_order_release)",
            "g_Stage6GameOwnerSlot.load(std::memory_order_acquire)",
            "g_Stage6GameIsPausedThunk.store(isPausedThunk, std::memory_order_release)",
            "g_Stage6GameIsPausedThunk.load(std::memory_order_acquire)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, stage6)
        self.assertNotIn("LuaRuntime::SetGameBindings", stage6)

    def test_stage37_documents_lua_game_boundary_and_stage14_hardware_protocol(self):
        documents = (
            ROOT / "README.md",
            ROOT.parent / "docs" / "问题与解决记录.md",
            ROOT.parent / "docs" / "会话交接-2026-08-26.md",
        )
        required = (
            "Stage 37",
            "Game()",
            "可缓存",
            "无指针 userdata",
            "MC_POST_UPDATE",
            "owner/thunk",
            "逐次解析",
            "Lua error",
            "默认/Stage14",
            "地址发布前置",
            "ISAAC_GP",
            "(14,1)",
            "(14,2)",
            "ISAAC_GF",
            "Installing",
            "静默",
            "初始化竞态",
            "同次完整 Atmosphere 报告",
            "恢复默认三文件",
            "smoke test",
            "原始 NRO/NPDM",
            "真实 Docker clean build",
            "Stage14 三文件",
            "Stage37 真机验证通过",
            "Stage37 默认包 smoke test 通过",
            "2DFFF55CADFCE9C01492E7738B3F7A8D6EA5299F",
            "不等于真机",
            "历史 Stage 14 分析",
            "统一原生访问作用域",
            "Runtime callback",
            "标量只读 API",
            "对象返回 API",
            "GetFrameCount",
            "GetNumPlayers",
            "GetLevel",
            "GetRoom",
            "当前只实现 MC_POST_UPDATE",
        )
        for document_path in documents:
            document = document_path.read_text(encoding="utf-8")
            for token in required:
                with self.subTest(document=document_path, token=token):
                    self.assertIn(token, document)

    def run_harness(self, scenario):
        result = subprocess.run([str(self.harness), scenario], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
