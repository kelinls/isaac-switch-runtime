import unittest
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"
PATCHES = ROOT / "tools" / "build_patches.py"


class Stage108LifecycleReentryDiagnosticTests(unittest.TestCase):
    def test_stage108_installs_only_new_and_saved_game_postcall_relays(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        patches = PATCHES.read_text(encoding="utf-8")

        # 2026-09-14：开局中继已从诊断阶段 108/110 **提升为常驻挂点**（`HookId::GameStart`，
        # 后端 `RelaySlot`），因为模组的 `MC_POST_GAME_STARTED` 依赖它（EID 的套装计数就卡在这）。
        # 所以 Makefile 里它不再挂在 `filter 108 110` 条件下，而是无条件进产物。
        # 下面几条 stage108 诊断自身的断言保持不变 —— 那段代码按裁定冻结、未被改动。
        self.assertIn("all: $(DEPLOY_LIFECYCLE_RELAY_IPS)", makefile)
        self.assertNotIn(
            "ifneq ($(filter 108 110,$(DIAGNOSTIC_STAGE)),)\nall: $(DEPLOY_LIFECYCLE_RELAY_IPS)",
            makefile,
        )
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 108", selector)
        self.assertIn("TryInstallStage108LifecycleDiagnostic", entry)
        self.assertIn("ReportStage108Failure", entry)
        self.assertIn("TryInstallStage108LifecycleDiagnostic", hook)
        self.assertIn("ObserveStage108Lifecycle", hook)
        self.assertIn("lifecycle-relay", patches)
        self.assertIn("GAME_START_SAVED_RELAY_CODE_OFFSET", patches)
        self.assertIn("GAME_START_NEW_RELAY_CODE_OFFSET", patches)

    def test_stage108_is_read_only_and_reports_after_two_lifecycle_events(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 108")
        stage108 = hook[start:hook.index("#endif", start)]

        self.assertIn("kStage108EventLimit = 2", stage108)
        self.assertIn("ReportStage108Success", stage108)
        self.assertNotIn("RestartGame", stage108)
        self.assertNotIn("LuaRuntime", stage108)
        self.assertNotIn("GetTreasureRoomVisitCount", stage108)
        self.assertNotIn("AddTreasureRoomsVisited", stage108)
        self.assertNotIn("new ", stage108)
        self.assertNotIn("delete ", stage108)

    def test_stage108_relays_restore_original_callsite_context_before_resuming(self):
        patches = PATCHES.read_text(encoding="utf-8")

        relay_start = patches.index("def build_game_start_lifecycle_relay(")
        relay = patches[relay_start:patches.index("GAME_START_SAVED_RELAY_CODE =", relay_start)]

        self.assertIn('"E00740A9"', relay)
        self.assertIn('"FE0B40F9"', relay)
        self.assertIn("encode_branch(code_offset + 0x34, resume_offset)", relay)

    def test_runtime_relay_guards_match_the_generator_bytes(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        patches_module = import_module("tools.build_patches")

        for name in ("Saved", "New"):
            start = constants.index(f"kGameStart{name}RelayExpectedBytes")
            end = constants.index("};", start)
            guarded = bytes(
                int(token, 16)
                for token in __import__("re").findall(r"0x([0-9A-F]{2})", constants[start:end])
            )
            self.assertEqual(guarded, getattr(patches_module, f"GAME_START_{name.upper()}_RELAY_CODE"))


if __name__ == "__main__":
    unittest.main()
