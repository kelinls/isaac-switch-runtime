"""端口的覆盖完整性：HookId 必须覆盖全部 5 个挂点，报告必须按 HookId 索引，
且 hook_manager 不得绕过端口直接安装。"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "runtime" / "src"
SOURCE = ROOT / "runtime" / "source"

PORT = SRC / "ports" / "hook_port.hpp"
# 挂点清单（HookId）与"每个点用哪种后端、是不是必需"现在登记在 domain 的登记表里
# （唯一真值源）；端口只引用它，所以覆盖性检查要读那份表。
CATALOG = SRC / "domain" / "runtime" / "hook_catalog.hpp"
SERVICE_H = SRC / "application" / "runtime" / "hook_install_service.hpp"
HOOK_MANAGER = SOURCE / "hook_manager.cpp"

REQUIRED = ("ManagerUpdate", "ManagerRender", "PreGetCollectible",
            "ManagerPresent", "RebuildMountPoints")

# 这两个安装器只允许被基础设施适配器调用，不允许再被 hook_manager 直接调用。
#
# 注意：两个安装器的**定义**仍留在 `hook_manager.cpp` 里（符号必须常编存在，见
# `test_runtime_elf_symbols.py`），由适配器按 `HookId` 调用，所以这里排除定义行、只查
# 调用点。定义行形如 `bool TryInstallRebuildMountPointsRelay(const TargetModule& module) {`。
BYPASS_CALLS = ("TryInstallRebuildMountPointsRelay(", "TryInstallManagerPresentRelay(")
DEFINITION_PREFIXES = ("bool TryInstallRebuildMountPointsRelay(",
                       "RenderPresentRelayInstallResult TryInstallManagerPresentRelay(")


class HookPortCoverageTests(unittest.TestCase):
    def test_hook_ids_cover_every_interception_point(self):
        text = CATALOG.read_text(encoding="utf-8")
        body = re.search(r"enum class HookId[^{]*\{(.*?)\};", text, re.S).group(1)
        names = re.findall(r"(\w+)\s*(?:=\s*\d+)?\s*,", body)
        for name in REQUIRED:
            self.assertIn(name, names, f"HookId 缺少 {name}")
        self.assertIn("Count", names, "HookId 必须以 Count 结尾")

    def test_report_is_indexed_by_hook_id(self):
        text = SERVICE_H.read_text(encoding="utf-8")
        self.assertIn("std::array<HookOutcome, kHookIdCount>", text,
                      "报告必须按 HookId 索引，否则每加一个挂点都要改结构体形状")
        for accessor in ("OutcomeOf", "Set", "productionReady", "InstalledCount",
                         "FirstFailureSlot"):
            self.assertIn(accessor, text, f"报告缺少 {accessor}")
        # 回读口径注释必须写明 `FirstFailureSlot` 只看 `Failed`：旧的"第一个非
        # Installed/Skipped 的挂点"写法会把 `!module.valid` 早退路径留下的五个 `Pending`
        # 读成"ManagerUpdate 装失败"。行为契约由宿主驱动
        # `test_hook_callback_pipeline.py` 钉住，这里只防止注释被改回旧说法。
        readback = text[text.index("回读口径"):]
        self.assertIn("第一个 `Failed`", readback,
                      "回读口径必须写明 FirstFailureSlot 只看 Failed")
        self.assertNotIn("第一个非 Installed/Skipped", text,
                         "回读口径不得再写回与实现矛盾的旧说法")

    def test_hook_manager_does_not_bypass_the_port(self):
        text = HOOK_MANAGER.read_text(encoding="utf-8")
        for call in BYPASS_CALLS:
            sites = [line.strip() for line in text.splitlines()
                     if call in line and not line.strip().startswith(DEFINITION_PREFIXES)]
            self.assertEqual(sites, [],
                             f"{call} 必须移到端口适配器内，不允许 hook_manager 直接安装")

    def test_no_blocking_ipc_in_the_install_path(self):
        for path in (SRC / "application" / "runtime", SRC / "infrastructure"):
            for file in path.rglob("*.cpp"):
                text = file.read_text(encoding="utf-8")
                for token in ("smInitialize", "fsInitialize", "fopen("):
                    self.assertNotIn(token, text, f"{file} 在安装路径上出现阻塞 token {token}")


if __name__ == "__main__":
    unittest.main()
