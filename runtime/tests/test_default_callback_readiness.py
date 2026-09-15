import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class DefaultCallbackReadinessTests(unittest.TestCase):
    def test_default_persistence_gate_waits_for_registration_but_has_a_bound(self):
        header = SOURCE / "default_persistence_gate.hpp"
        self.assertTrue(header.is_file(), "default persistence readiness gate is not implemented")
        with tempfile.TemporaryDirectory(prefix="isaac-default-persistence-gate-") as temporary:
            harness = Path(temporary) / "gate.cpp"
            binary = Path(temporary) / "gate"
            harness.write_text(
                r'''
#include "default_persistence_gate.hpp"

int main() {
    DefaultPersistenceGate gate;
    for (unsigned int frame = 0; frame < DefaultPersistenceGate::kMaximumDeferredUpdates; ++frame) {
        if (gate.ShouldDispatch(false, false)) return 1;
    }
    if (!gate.ShouldDispatch(false, false)) return 2;

    DefaultPersistenceGate registered;
    if (registered.ShouldDispatch(false, false)) return 3;
    if (!registered.ShouldDispatch(true, false)) return 4;

    DefaultPersistenceGate required;
    for (unsigned int frame = 0; frame <= DefaultPersistenceGate::kMaximumDeferredUpdates; ++frame) {
        if (required.ShouldDispatch(false, true)) return 5;
    }
    if (!required.ShouldDispatch(true, true)) return 6;
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            build = subprocess.run(
                ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                 str(harness), "-o", str(binary)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_default_update_waits_for_file_api_only_during_bounded_startup_window(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        update = hook[hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)"):
                      hook.index("HOOK_DEFINE_TRAMPOLINE(MusicReplayMusicPlayHook)")]

        self.assertIn('#include "default_persistence_gate.hpp"', hook)
        self.assertIn('#include "mod_persistence.hpp"', hook)
        self.assertIn("DefaultPersistenceGate g_DefaultPersistenceGate{};", hook)
        self.assertIn("constexpr bool kDefaultPersistenceRequired", hook)
        self.assertIn("const bool fileApiReady = ModPersistence::IsFileApiReady();", update)
        gate = "g_DefaultPersistenceGate.ShouldDispatch(fileApiReady, kDefaultPersistenceRequired)"
        self.assertIn(gate, update)
        self.assertLess(update.index(gate), update.index("LuaRuntime::DispatchPostUpdate();"))

    def test_gate_opens_only_after_an_unpaused_render_frame_with_ready_music(self):
        header = SOURCE / "default_callback_gate.hpp"
        self.assertTrue(header.is_file(), "default callback gate is not implemented")
        with tempfile.TemporaryDirectory(prefix="isaac-default-callback-gate-") as temporary:
            harness = Path(temporary) / "gate.cpp"
            binary = Path(temporary) / "gate"
            harness.write_text(
                r'''
#include "default_callback_gate.hpp"

int main() {
    DefaultCallbackGate gate;
    if (gate.IsOpen()) return 1;
    if (gate.Observe(GameIsPausedObservation::OwnerNull, false)) return 2;
    if (gate.Observe(GameIsPausedObservation::PausedTrue, false)) return 3;
    if (gate.Observe(GameIsPausedObservation::PausedFalse, false)) return 4;
    if (gate.IsOpen()) return 5;
    if (!gate.Observe(GameIsPausedObservation::PausedFalse, true)) return 6;
    if (!gate.IsOpen()) return 7;
    if (!gate.Observe(GameIsPausedObservation::PausedTrue, false)) return 8;
    return 0;
}
'''.lstrip(),
                encoding="utf-8",
            )
            build = subprocess.run(
                ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                 str(harness), "-o", str(binary)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_default_update_dispatch_does_not_wait_for_music_gate(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        update = hook[hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)"):
                      hook.index("HOOK_DEFINE_TRAMPOLINE(MusicReplayMusicPlayHook)")]

        self.assertIn("if (LuaRuntime::IsReady())", update)
        self.assertNotIn("DefaultCallbacksReady()", update)
        self.assertNotIn("PrimeDefaultCallbacks()", update)

        # `MC_POST_RENDER` 的派发点已经搬到 `Present` 调用点上的中继（见
        # `test_manager_render_hook.py`），所以就绪闸门现在在 `DispatchModPostRender` 里；
        # 入口中继只保留"Present 前中继不可用"时的回退分支。
        render_start = hook.index("void DispatchModPostRender(void* self) {")
        render = hook[render_start:hook.index("bool IsRenderPresentRelayInstalled() {", render_start)]
        self.assertIn("LuaRuntime::IsReady() && PrimeDefaultCallbacks(reinterpret_cast<uintptr_t>(self))", render)
        self.assertIn("#if !defined(EXL_DIAGNOSTIC_STAGE)", render)
        self.assertIn("#else\n    if (LuaRuntime::IsReady())", render)
        self.assertLess(render.index("PrimeDefaultCallbacks"), render.index("LuaRuntime::DispatchPostRender"))

        # 渲染钩子体已抽成两条后端共用的 `RenderHookBody`（Task 4），断言对象随之移动；
        # 语义不变：先跑原函数，再由 `IsRenderPresentRelayInstalled()` 决定是否回退派发。
        relay_start = hook.index("void RenderHookBody(IsaacRepentance::Manager* self")
        relay = hook[relay_start:hook.index('extern "C" void EntryRelayManagerRenderCallback', relay_start)]
        self.assertLess(relay.index("call_original(self)"), relay.index("DispatchModPostRender(self)"))
        self.assertIn("IsRenderPresentRelayInstalled()", relay)

        # 旧后端（exlaunch 蹦床）仍把原函数经 `Orig` 交给共享钩子体。
        trampoline_start = hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerRenderHook)")
        trampoline = hook[trampoline_start:hook.index("};", trampoline_start)]
        self.assertIn("RenderHookBody(self,", trampoline)
        self.assertIn("Orig(", trampoline)

    def test_default_update_hook_treats_music_bindings_as_optional(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        install = hook[hook.index("HookInstallResult TryInstallManagerUpdateHook"):
                       hook.index("RenderHookInstallResult TryInstallManagerRenderHook")]
        default = install[install.index("#if !defined(EXL_DIAGNOSTIC_STAGE)"):install.index("#else\n#if EXL_DIAGNOSTIC_STAGE")]
        self.assertNotIn("return HookInstallResult::MusicBindingsMismatch;", default)
        self.assertNotIn("return HookInstallResult::GameBindingsMismatch;", default)

    def test_default_readiness_uses_lua_runtime_music_guard_instead_of_replicating_it(self):
        header = (SOURCE / "lua_runtime.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "lua_runtime.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")

        self.assertIn("bool IsMusicReadyForDefaultCallback(uintptr_t manager);", header)
        self.assertIn("bool IsMusicReadyForDefaultCallback(uintptr_t manager)", source)
        self.assertIn("ResolveMusicForManager", source)
        self.assertIn("LuaRuntime::IsMusicReadyForDefaultCallback(manager)", hook)


if __name__ == "__main__":
    unittest.main()
