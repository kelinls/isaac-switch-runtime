"""M2b 的 GOT 槽后端（`GotSlotHookAdapter`）：只服务 `ManagerPresent`，且只在安装器成功时算装上。

两类断言：
  * 源码契约（文本）：路由表把 `ManagerPresent` 指到 `GotSlot`；
    安装器里"三段守卫 + 按调用方过滤"这些**关键性质**必须在源码里可见，不能被删掉；
    适配器不引用任何旧 IPS 安装器、不含阻塞 token。
  * 行为契约（宿主机编译真实适配器 + 打桩的安装器）：只有 `ManagerPresent` 走这条路、
    安装器失败时报告失败、重复安装报 InvalidState、越界 `HookId` 报 InvalidArgument。
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "runtime" / "src"
SOURCE = ROOT / "runtime" / "source"
GOTSLOT = SRC / "infrastructure" / "gotslot"
HOOK_MANAGER = SOURCE / "hook_manager.cpp"
ROUTING = SRC / "infrastructure" / "hook_routing_adapter.hpp"

FORBIDDEN_TOKENS = ("smInitialize", "fsInitialize", "fopen(", "lib/nx", "<switch.h>",
                    "std::atomic<bool>")


class GotSlotAdapterContractTests(unittest.TestCase):
    def test_routing_sends_present_to_the_got_slot_backend(self):
        # 后端选择来自 domain 的登记表；路由表由它派生。
        catalog = (SRC / "domain" / "runtime" / "hook_catalog.hpp").read_text(encoding="utf-8")
        rows = re.findall(r"\{HookId::(\w+),\s*\"[^\"]*\",\s*HookBackend::(\w+)", catalog)
        backends = {hook_id: backend for hook_id, backend in rows}
        self.assertEqual(backends.get("ManagerPresent"), "GotSlot",
                         "ManagerPresent 是 M2b 的 GOT 槽后端")
        text = ROUTING.read_text(encoding="utf-8")
        self.assertIn("GotSlotHookAdapter gotSlot_", text)
        self.assertIn("CatalogBackends()", text)

    def test_adapter_only_installs_through_the_got_slot_installer(self):
        text = (GOTSLOT / "got_slot_hook_adapter.cpp").read_text(encoding="utf-8")
        self.assertIn("TryInstallManagerPresentGotSlot(", text)
        for legacy in ("TryInstallManagerPresentRelay", "TryInstallManagerUpdateHook",
                       "TryInstallEntryRelay"):
            self.assertNotIn(legacy, text, f"GOT 槽后端不得引用 {legacy}")
        for token in FORBIDDEN_TOKENS:
            self.assertNotIn(token, text, f"GOT 槽后端不得含被禁 token {token}")

    def test_installer_keeps_the_three_guards_and_the_caller_filter(self):
        """三段守卫与"按调用方过滤"是这一路的**安全性来源**，不许在改动里丢掉。"""
        text = HOOK_MANAGER.read_text(encoding="utf-8")
        installer_start = text.index("RenderPresentRelayInstallResult TryInstallManagerPresentGotSlot")
        interceptor_start = text.index("IsaacModRuntime_PresentGotSlotIntercept")
        installer_end = text.index("RenderHookInstallResult TryInstallManagerRenderHook",
                                   installer_start)
        # 拦截函数 + 页属性判定 + 安装器是一整块，三个断言一起看。
        installer = text[interceptor_start:installer_end]
        for token in ("kManagerPresentCallExpectedBytes", "kManagerPresentStubExpectedBytes",
                      "kManagerPresentTargetExpectedBytes", "kManagerPresentGotSlotOffset",
                      "svcQueryMemory", "Perm_W", "RwPages"):
            with self.subTest(token=token):
                self.assertIn(token, installer)
        interceptor = installer
        # 过滤条件：返回地址必须等于那**一处**调用点的下一条指令。
        self.assertIn("__builtin_return_address(0)", interceptor)
        self.assertIn("g_PresentGotSlotDispatchReturn", interceptor)
        self.assertIn("IsaacModRuntime_DispatchPostRenderBeforePresent", interceptor)

    def test_caller_filter_matches_the_documented_single_call_site(self):
        """过滤用的返回地址必须是"调用点 + 4"，且只在安装成功时才被写入（否则一次都不派发）。"""
        text = HOOK_MANAGER.read_text(encoding="utf-8")
        installer = text[text.index("RenderPresentRelayInstallResult TryInstallManagerPresentGotSlot"):]
        self.assertIn("g_PresentGotSlotDispatchReturn.store(callsite + 4", installer)
        self.assertIn("g_PresentGotSlotTarget.store(target", installer)


DRIVER = r"""
#include "infrastructure/gotslot/got_slot_hook_adapter.hpp"
#include "hook_manager.hpp"

#include <cstdint>
#include <cstdio>

using isaac::runtime::GotSlotHookAdapter;
using isaac::runtime::HookId;
using isaac::runtime::HookTarget;
using ::RenderPresentRelayInstallResult;
using isaac::runtime::StatusCode;

// 打桩安装器：驱动用 g_stubResult 控制它成功还是失败。
static RenderPresentRelayInstallResult g_stubResult = RenderPresentRelayInstallResult::Success;
static int g_stubCalls = 0;
static uintptr_t g_stubBase = 0;

RenderPresentRelayInstallResult TryInstallManagerPresentGotSlot(const TargetModule& module) {
    ++g_stubCalls;
    g_stubBase = module.base;
    return g_stubResult;
}

static int g_failures = 0;
static void Check(bool condition, const char* label) {
    if (!condition) {
        std::printf("FAIL %s\n", label);
        ++g_failures;
    }
}

int main() {
    HookTarget target{};
    target.base = 0x7100000000u;
    target.codeSize = 0x700000u;
    target.imageSize = 0x800000u;

    GotSlotHookAdapter adapter;
    Check(!adapter.IsInstalled(HookId::ManagerPresent), "初始未安装");

    // 安装器成功 → 装上，且它拿到的是端口给的模块基址。
    g_stubResult = RenderPresentRelayInstallResult::Success;
    g_stubCalls = 0;
    Check(adapter.Install(HookId::ManagerPresent, target).ok(), "安装成功必须报成功");
    Check(g_stubCalls == 1, "安装器只应被调用一次");
    Check(g_stubBase == target.base, "安装器必须拿到端口给的基址");
    Check(adapter.IsInstalled(HookId::ManagerPresent), "成功后必须报告已安装");
    Check(adapter.Install(HookId::ManagerPresent, target).code() == StatusCode::InvalidState,
          "重复安装必须报 InvalidState");

    // 安装器失败 → 报失败，且不得报告已安装。
    GotSlotHookAdapter failing;
    g_stubResult = RenderPresentRelayInstallResult::GotSlotWriteFailed;
    Check(failing.Install(HookId::ManagerPresent, target).code() == StatusCode::Rejected,
          "安装器失败必须被拒绝");
    Check(!failing.IsInstalled(HookId::ManagerPresent), "失败后不得报告已安装");

    // 其它挂点不走这个后端（响亮失败，不静默成功）。
    GotSlotHookAdapter other;
    g_stubCalls = 0;
    Check(other.Install(HookId::ManagerUpdate, target).code() == StatusCode::Rejected,
          "别的挂点必须被拒绝");
    Check(g_stubCalls == 0, "别的挂点不得触碰安装器");

    // 参数错误。
    Check(other.Install(HookId::Count, target).code() == StatusCode::InvalidArgument,
          "越界 HookId 是参数错误");
    HookTarget empty{};
    Check(other.Install(HookId::ManagerPresent, empty).code() == StatusCode::InvalidArgument,
          "空 target 是参数错误");

    if (g_failures == 0) {
        std::printf("GOTSLOT_DRIVER_OK\n");
    }
    return g_failures == 0 ? 0 : 1;
}
"""


class GotSlotAdapterBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("c++") is None:
            raise unittest.SkipTest("宿主机没有 c++")
        cls._temporary = tempfile.TemporaryDirectory(prefix="isaac-gotslot-adapter-")
        directory = Path(cls._temporary.name)
        driver = directory / "got_slot_driver.cpp"
        driver.write_text(DRIVER.lstrip(), encoding="utf-8")
        binary = directory / "got_slot_driver"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
             "-I", str(SRC), "-I", str(SOURCE),
             str(driver), str(GOTSLOT / "got_slot_hook_adapter.cpp"),
             "-o", str(binary)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(f"适配器在宿主机上编译失败：\n{build.stdout}{build.stderr}")
        cls._run = subprocess.run([str(binary)], text=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_temporary", None) is not None:
            cls._temporary.cleanup()

    def test_adapter_behavior_on_the_host(self):
        self.assertEqual(self._run.returncode, 0, self._run.stdout + self._run.stderr)
        self.assertEqual(self._run.stdout.strip(), "GOTSLOT_DRIVER_OK")


if __name__ == "__main__":
    unittest.main()
