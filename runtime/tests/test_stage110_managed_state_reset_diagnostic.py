import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage110ManagedStateResetDiagnosticTests(unittest.TestCase):
    def test_stage110_installs_the_existing_non_overlapping_room_and_lifecycle_relays(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")

        # 2026-09-14：lifecycle 中继已**提升为常驻挂点**（`HookId::GameStart`），
        # 不再挂在 `filter 108 110,$(DIAGNOSTIC_STAGE)` 门下，所以这里改钉"它无条件进产物"。
        # change-room 那条仍然只在 102/104/110/112 下构建（下面单独断言）。
        self.assertIn("all: $(DEPLOY_LIFECYCLE_RELAY_IPS)", makefile)
        self.assertIn("102 104 110 112,$(DIAGNOSTIC_STAGE)", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 110", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 110", entry)
        self.assertIn("TryInstallStage110ChangeRoomDiagnostic", entry)
        self.assertIn("TryInstallStage110LifecycleDiagnostic", entry)
        self.assertIn("ReportStage110Failure", entry)
        self.assertIn("TryInstallStage110ChangeRoomDiagnostic", hook)
        self.assertIn("TryInstallStage110LifecycleDiagnostic", hook)
        self.assertIn("ObserveStage110ChangeRoom", hook)
        self.assertIn("ObserveStage110Lifecycle", hook)
        self.assertIn("Stage110ChangeRoomInstallResult", header)
        self.assertIn("Stage110LifecycleInstallResult", header)
        self.assertIn("all: $(DEPLOY_CHANGE_ROOM_RELAY_IPS)", makefile)
        self.assertIn("all: $(DEPLOY_LIFECYCLE_RELAY_IPS)", makefile)
        self.assertIn("InitMemLayout", entry)
        self.assertIn("ReportStage110Failure(1)", entry)

    def test_stage110_reports_only_the_saved_room_restart_new_room_reset_sequence(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        start = hook.index("#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 110")
        stage110 = hook[start:hook.index("#endif", start)]

        self.assertIn("kStage110SavedLifecycle", stage110)
        self.assertIn("kStage110PreResetRoom", stage110)
        self.assertIn("kStage110NewLifecycle", stage110)
        self.assertIn("kStage110PostResetRoom", stage110)
        self.assertIn("kStage110CounterReset", stage110)
        self.assertIn("kStage110PostResetCountOne", stage110)
        self.assertIn("ReportStage110Success", stage110)
        self.assertIn("g_Stage110RoomEvents.exchange(0", stage110)
        self.assertNotIn("LuaRuntime", stage110)
        self.assertNotIn("GetTreasureRoomVisitCount", stage110)
        self.assertNotIn("AddTreasureRoomsVisited", stage110)
        self.assertNotIn("RestartGame", stage110)
        self.assertNotIn("new ", stage110)
        self.assertNotIn("delete ", stage110)

    def test_stage110_keeps_both_relay_guards_and_slots_separate(self):
        """两组中继的洞与槽必须彼此分开 —— 判定的是**不相交**，不是那几个固定的十六进制值。

        旧断言把四个常量写死成 `0x68CE60/0x68CE90/0x68CEA0/0x68CF90`。其中生命周期那一组
        后来被**搬走了**：`GameStart` 于 2026-09-14 从诊断阶段 108/110 提升为常驻挂点，为了把
        `0x68CEA0 / 0x68CEF0 / 0x68CF90` 整段让给 stage108/109/127/128 那组互斥诊断，
        它的洞/槽改到了 `0x68CC20 / 0x68CC70 / 0x68CCC0`（见 `tools/verify_cave_allocation.py`
        的 `EXCLUSIVE_GROUPS` 理由）。于是"写死旧地址"的断言误红，而它真正要守的性质
        ——**change-room 中继与 lifecycle 中继不共用同一个洞或槽**——并没有变。

        这里改成按实际值算区间再判不相交：地址将来再挪也不会误红，而两组一旦真的撞在一起
        （那会让两段中继代码互相覆盖）立刻红。
        """
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")

        def value(name: str) -> int:
            match = re.search(rf"inline constexpr uintptr_t {name} = (0x[0-9A-Fa-f]+);", constants)
            self.assertIsNotNone(match, f"{name} 未在 runtime_constants.hpp 中定义")
            return int(match.group(1), 16)

        change_room = {
            "code": value("kGameChangeRoomRelayCodeOffset"),
            "slot": value("kGameChangeRoomRelaySlotOffset"),
        }
        lifecycle = {
            "saved_code": value("kGameStartSavedRelayCodeOffset"),
            "new_code": value("kGameStartNewRelayCodeOffset"),
            "slot": value("kGameStartRelaySlotOffset"),
        }
        # 洞的口径与 `tools/verify_cave_allocation.py` 一致：8 字节槽、洞到下一个分配起点为止；
        # 这里只比较"起点 + 8 字节槽"这一层，足以抓住"两组抢同一个字节"的实质。
        occupied = {
            "change_room_code": range(change_room["code"], change_room["code"] + 4),
            "change_room_slot": range(change_room["slot"], change_room["slot"] + 8),
            "lifecycle_saved_code": range(lifecycle["saved_code"], lifecycle["saved_code"] + 4),
            "lifecycle_new_code": range(lifecycle["new_code"], lifecycle["new_code"] + 4),
            "lifecycle_slot": range(lifecycle["slot"], lifecycle["slot"] + 8),
        }
        pairs = list(occupied.items())
        for index, (name, span) in enumerate(pairs):
            for other_name, other_span in pairs[index + 1:]:
                self.assertFalse(
                    set(span) & set(other_span),
                    f"{name} 与 {other_name} 抢同一段字节：{hex(span.start)} / "
                    f"{hex(other_span.start)} —— 两段中继代码会互相覆盖",
                )


if __name__ == "__main__":
    unittest.main()
