"""`tools/verify_cave_allocation.py` 的行为测试。

覆盖三件事：

1. 当前真源（`build_patches.py` / stage127+128 生成器 / `runtime_constants.hpp`）通过；
2. **回归复现**：把已删除的 image-path-relay 常量放回去，门禁必须报出与 stage48 的洞/槽重叠
   —— 这正是 2026-09-12 修掉的那个缺陷；
3. 两侧只改一侧、未登记重叠、登记过期、源文件不可解析等分支的判定。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.verify_cave_allocation import (
    EXCLUSIVE_GROUPS,
    ExclusiveGroup,
    Relay,
    RelayView,
    collect_views,
    evaluate,
    merge_relays,
    runtime,
)

ROOT = Path(__file__).resolve().parents[2]
PATCH_SOURCE = ROOT / "tools" / "build_patches.py"
RUNTIME_CONSTANTS = ROOT / "runtime" / "source" / "runtime_constants.hpp"


def current_relays() -> dict[str, Relay]:
    return merge_relays(
        collect_views(PATCH_SOURCE, (ROOT / "tools" / "stage127_save_load_observation_relay.py",
                                     ROOT / "tools" / "stage128_save_data_manager_observation_relay.py"),
                      RUNTIME_CONSTANTS)
    )


class CaveAllocationGateTests(unittest.TestCase):
    def test_current_sources_have_no_unregistered_overlap(self):
        problems, stale = evaluate(current_relays())
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertEqual(stale, [], "\n".join(stale))

    def test_stage48_and_lifecycle_families_are_present(self):
        """门禁必须真的解析到两侧的家族，而不是"什么都没解析到所以通过"。"""
        relays = current_relays()
        for name in (
            "Stage48MusicPlayRelay",
            "ManagerUpdateRelay",
            "GameRestartRelay",
            "SaveLoadRelay",
            "SaveDataManagerRelay",
        ):
            with self.subTest(relay=name):
                self.assertIn(name, relays)
        stage48 = relays["Stage48MusicPlayRelay"]
        self.assertIn(0x68CD40, stage48.cave_starts())
        self.assertEqual(stage48.slot(), 0x68CD78)

    def test_original_image_relay_conflict_is_detected(self):
        """回归：把 image-path-relay 的常量放回去，必须报出与 stage48 的重叠。"""
        header = RUNTIME_CONSTANTS.read_text(encoding="utf-8")
        anchor = "// `IContentManager::GetMountedFilePath(char const*)`"
        self.assertIn(anchor, header)
        buggy_header = header.replace(
            anchor,
            "inline constexpr uintptr_t kImagePathRelayCodeOffset = 0x68CD40;\n"
            "inline constexpr uintptr_t kImagePathRelaySlotOffset = 0x68CD78;\n" + anchor,
            1,
        )
        with tempfile.TemporaryDirectory(prefix="cave-relay-regression-") as temporary:
            path = Path(temporary) / "runtime_constants.hpp"
            path.write_text(buggy_header, encoding="utf-8")
            relays = merge_relays(
                collect_views(
                    PATCH_SOURCE,
                    (
                        ROOT / "tools" / "stage127_save_load_observation_relay.py",
                        ROOT / "tools" / "stage128_save_data_manager_observation_relay.py",
                    ),
                    path,
                )
            )
        problems, _ = evaluate(relays)
        self.assertTrue(problems, "门禁没有报出 image-path-relay 与 stage48 的重叠")
        joined = "\n".join(problems)
        self.assertIn("Stage48MusicPlayRelay", joined)
        self.assertIn("0x68cd40", joined)

    def test_one_sided_address_change_is_reported(self):
        """只改运行时一侧的槽地址，必须报"两侧不一致"。"""
        header = RUNTIME_CONSTANTS.read_text(encoding="utf-8")
        tampered = header.replace(
            "kStage48MusicPlayRelaySlotOffset = 0x68CD78;",
            "kStage48MusicPlayRelaySlotOffset = 0x68CD70;",
            1,
        )
        self.assertNotEqual(tampered, header)
        with tempfile.TemporaryDirectory(prefix="cave-one-sided-") as temporary:
            path = Path(temporary) / "runtime_constants.hpp"
            path.write_text(tampered, encoding="utf-8")
            relays = merge_relays(
                collect_views(
                    PATCH_SOURCE,
                    (
                        ROOT / "tools" / "stage127_save_load_observation_relay.py",
                        ROOT / "tools" / "stage128_save_data_manager_observation_relay.py",
                    ),
                    path,
                )
            )
        problems, _ = evaluate(relays)
        self.assertTrue(
            any("两侧槽地址不一致" in problem for problem in problems),
            "\n".join(problems),
        )

    def test_exclusive_group_is_required_and_must_stay_live(self):
        """登记表不是装饰：去掉登记就报冲突，登记过期也要报。"""
        relays = current_relays()
        without_groups = evaluate(relays, groups=())
        self.assertTrue(
            any("未登记的跨中继重叠" in problem for problem in without_groups[0]),
            "\n".join(without_groups[0]),
        )
        # 已登记的那一组目前一定活着；换成一组不存在的名字就应报"登记已过期"。
        self.assertEqual(evaluate(relays)[1], [])
        stale_groups = (
            ExclusiveGroup(reason="测试用：不存在的中继对", relays=frozenset({"A", "B", "C"})),
        ) + EXCLUSIVE_GROUPS
        _, stale = evaluate(relays, groups=stale_groups)
        self.assertTrue(any("登记已过期" in problem for problem in stale), "\n".join(stale))

    def test_unparsable_source_exits_with_three(self):
        with tempfile.TemporaryDirectory(prefix="cave-broken-source-") as temporary:
            broken = Path(temporary) / "runtime_constants.hpp"
            broken.write_text("没有中继槽常量", encoding="utf-8")
            self.assertEqual(runtime(["--runtime-constants", str(broken)]), 3)

    def test_cli_reports_success_on_current_sources(self):
        result = subprocess.run(
            [sys.executable, "tools/verify_cave_allocation.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("未发现未登记的跨中继重叠", result.stdout)


if __name__ == "__main__":
    unittest.main()
