"""诊断设施**已冻结**（用户裁定 2026-09-14）：不再新增一次性诊断阶段。

冻结的是 `EXL_DIAGNOSTIC_STAGE == N` 那类"为每个问题造一个专用构建"的做法，以及
`src/diagnostics/` 那套写文件的记录通道。理由：
  ① 现在有调试桩（能直接读写内存），临时问题的答案不必靠专用构建；
  ② 写文件的通道随宿主插件一起停用（插件已按裁定长期禁用）。

本测试是**守卫**：允许删除阶段（数量减少不算错），但**新增阶段号会让它失败** ——
新问题请改走"调试桩现场取数"或"在常驻状态面里加字段"（见 AGENTS.md 的对应章节）。

清理旧阶段（删分支与对应测试）是另一件事，另开一轮做；这里不拦。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
AGENTS = ROOT / "AGENTS.md"

# 冻结时存在的阶段号（39 个）。**只允许减少，不允许增加。**
FROZEN_STAGES = frozenset({
    4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17,
    45, 48, 88, 89,
    102, 104, 108, 109, 110, 112, 118, 127, 128,
    133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145,
})

STAGE_PATTERN = re.compile(r"EXL_DIAGNOSTIC_STAGE\s*==\s*(\d+)")


def stages_in_tree():
    found = set()
    for base in (RUNTIME / "source", RUNTIME / "src"):
        for path in base.rglob("*"):
            if path.suffix not in (".cpp", ".hpp", ".c", ".s", ".mk"):
                continue
            if any(part.startswith(("build-", "deploy-", ".")) for part in path.parts):
                continue
            for match in STAGE_PATTERN.finditer(path.read_text(encoding="utf-8", errors="ignore")):
                found.add(int(match.group(1)))
    return found


class DiagnosticFreezeTests(unittest.TestCase):
    def test_no_new_diagnostic_stage_is_added(self):
        added = sorted(stages_in_tree() - FROZEN_STAGES)
        self.assertEqual(
            added, [],
            "诊断阶段已冻结：不要新增 EXL_DIAGNOSTIC_STAGE（新问题请用调试桩读数，"
            f"或在常驻状态面里加字段）。发现新增：{added}",
        )

    def test_the_freeze_is_documented_in_agents(self):
        text = AGENTS.read_text(encoding="utf-8")
        self.assertIn("诊断设施的冻结范围", text, "AGENTS.md 必须写明诊断设施已冻结")
        self.assertIn("test_diagnostic_freeze.py", text, "冻结条目要指向本守卫测试")
        self.assertIn("调试桩", text)


if __name__ == "__main__":
    unittest.main()
