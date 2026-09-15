"""零占洞后端必须：只经 entry_relay 安装、不调用旧安装器、失败不重试、无阻塞 token。

两类断言：
  * 源码契约（文本）：只经 `TryInstallEntryRelay` 安装、一个旧 IPS 安装器都不引用、
    不出现阻塞 token、不出现 `std::atomic<bool>`；
  * 行为契约（宿主机编译真实适配器 + 打桩的 entry_relay）：纯判定
    `EvaluateEntryRelayBinding` 的每条拒绝理由，以及"判定拒绝时绝不去碰
    `entry_relay`／目标字节"这条安全性质（打桩的 `TryInstallEntryRelay` 记录调用次数，
    让它成为可观测行为）。
    注意：回调表要等 Task 4 接线，所以**成功路径在此之前不可达**，驱动只覆盖
    "未接线 → 拒绝且零调用"这一段；成功路径的判定逻辑由纯函数用例直接覆盖。

"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELAY = ROOT / "runtime" / "src" / "infrastructure" / "relay"
SRC = ROOT / "runtime" / "src"
SOURCE = ROOT / "runtime" / "source"

# 安装路径上禁止出现的阻塞 token（`smInitialize`/`fsInitialize`/`fopen(`），
# 以及本项目对 libnx/exlaunch 头与 bool 原子量的两道门禁。
FORBIDDEN_TOKENS = ("smInitialize", "fsInitialize", "fopen(", "lib/nx", "<switch.h>",
                    "std::atomic<bool>")


def forbidden_tokens_in(text):
    """返回 text 里命中的被禁 token。扫描判据抽成纯函数，本文件的
    `test_forbidden_token_scan_has_teeth` 会用反例证明它不是恒真断言。"""
    return [token for token in FORBIDDEN_TOKENS if token in text]


class EntryRelayAdapterContractTests(unittest.TestCase):
    def test_adapter_only_installs_through_entry_relay(self):
        text = (RELAY / "entry_relay_hook_adapter.cpp").read_text(encoding="utf-8")
        self.assertIn("TryInstallEntryRelay(", text)
        for legacy in ("TryInstallManagerUpdateHook", "TryInstallManagerRenderHook",
                       "TryInstallPreGetCollectibleRelay", "TryInstallManagerPresentRelay",
                       "TryInstallRebuildMountPointsRelay"):
            self.assertNotIn(legacy, text, f"零占洞后端不得引用旧 IPS 安装器 {legacy}")

    def test_adapter_has_no_blocking_tokens_and_no_bool_atomics(self):
        sources = sorted(RELAY.glob("*.cpp"))
        self.assertTrue(sources, "relay 目录下必须有适配器实现，空集合会让本测试恒真")
        for file in sources:
            text = file.read_text(encoding="utf-8")
            for token in forbidden_tokens_in(text):
                self.fail(f"{file} 含被禁 token {token}")

    def test_adapter_maps_failure_without_retry(self):
        text = (RELAY / "entry_relay_hook_adapter.cpp").read_text(encoding="utf-8")
        self.assertIn("EntryRelayFailure::WriteVerifyFailed", text,
                      "WriteVerifyFailed 是不可恢复语义，必须显式处理而不是重试")
        body = text.split("Status EntryRelayHookAdapter::Install")[1]
        self.assertNotIn("for (", body, "安装失败不得重试")
        self.assertNotIn("while (", body, "安装失败不得重试")
        self.assertEqual(body.count("TryInstallEntryRelay("), 1,
                         "每个挂点只允许尝试安装一次（失败即返回，不得循环重试）")

    def test_pure_evaluator_is_host_testable(self):
        header = (RELAY / "entry_relay_binding.hpp").read_text(encoding="utf-8")
        self.assertIn("EvaluateEntryRelayBinding", header)
        self.assertIn("constexpr", header)
        # 地址判定必须是编译期可求值的 constexpr（宿主机测试与编译期自证共用同一份逻辑）
        self.assertIn("constexpr bool IsHookableEntryAddress(", header)
        self.assertIn("static_assert(!IsHookableEntryAddress(", header,
                      "纯判定必须带反例自证，否则无法证明判据不是恒真")
        for token in ("svcQueryMemory", "RwPages", "mrs "):
            self.assertNotIn(token, header, "纯判定头不得碰系统")

    def test_forbidden_token_scan_has_teeth(self):
        """自证：扫描判据必须对反例报错，否则上面那条测试是空断言。"""
        self.assertEqual(forbidden_tokens_in("int main() { return 0; }"), [])
        self.assertEqual(forbidden_tokens_in("\tauto flag = std::atomic<bool>{};\n"),
                         ["std::atomic<bool>"])
        self.assertIn("fopen(", forbidden_tokens_in('FILE* f = fopen("a", "rb");'))
        self.assertIn("smInitialize", forbidden_tokens_in("smInitialize();"))
        # 常量表本身不许被悄悄缩水
        for token in ("smInitialize", "fsInitialize", "fopen(", "std::atomic<bool>"):
            self.assertIn(token, FORBIDDEN_TOKENS)

    def test_ledger_constants_come_from_the_shared_ledger(self):
        """四个入口型挂点必须引用台账里的既有常量名，不得自造第二套真值源。"""
        text = (RELAY / "entry_relay_hook_adapter.cpp").read_text(encoding="utf-8")
        for constant in ("kManagerUpdateFileOffset", "kManagerUpdateExpectedBytes",
                         "kManagerRenderFileOffset", "kManagerRenderExpectedBytes",
                         "kPreGetCollectibleRelayOffset",
                         "kPreGetCollectibleRelayExpectedOriginal",
                         "kRebuildContentMountPointsOffset",
                         "kRebuildContentMountPointsExpectedBytes"):
            self.assertIn(constant, text, f"绑定表必须引用台账常量 {constant}")

    def test_adapter_does_not_extend_the_port(self):
        """`LastFailureCode()` 是本适配器的自有诊断接口，不得动 `IHookPort`。"""
        port = (SRC / "ports" / "hook_port.hpp").read_text(encoding="utf-8")
        self.assertNotIn("LastFailureCode", port, "端口接口不因诊断信息而改动")
        header = (RELAY / "entry_relay_hook_adapter.hpp").read_text(encoding="utf-8")
        self.assertIn("LastFailureCode", header)
        self.assertIn("public IHookPort", header)


ADAPTER_DRIVER = r"""
#include "infrastructure/relay/entry_relay_hook_adapter.hpp"

#include <cstdint>
#include <cstdio>

using isaac::runtime::BindingVerdict;
using isaac::runtime::EntryRelayBinding;
using isaac::runtime::EntryRelayFallbackEntry;
using isaac::runtime::EntryRelayHookAdapter;
using isaac::runtime::EvaluateEntryRelayBinding;
using isaac::runtime::HookId;
using isaac::runtime::HookTarget;
using isaac::runtime::Original;
using isaac::runtime::StatusCode;

// ---- entry_relay 的打桩实现：只用于观察适配器怎么调用它 ----
namespace isaac::runtime::relay {

bool g_stubResult = false;
std::uint32_t g_stubFailure = 0;
std::size_t g_stubCalls = 0;
std::uintptr_t g_stubTarget = 0;
std::uintptr_t g_stubCallback = 0;
const void* g_stubExpected = nullptr;

bool TryInstallEntryRelay(std::uintptr_t target, const void* expectedEntry16,
                          std::uintptr_t callback, std::uintptr_t* outOriginalEntry) {
    ++g_stubCalls;
    g_stubTarget = target;
    g_stubExpected = expectedEntry16;
    g_stubCallback = callback;
    if (!g_stubResult) {
        return false;
    }
    *outOriginalEntry = 0x7100A00020u;  // 槽 + kSlotFallbackOffset
    return true;
}

std::size_t InstalledCount() { return g_stubResult ? 1u : 0u; }
std::uint32_t LastFailure() { return g_stubFailure; }

}  // namespace isaac::runtime::relay

// `RegisterEntryRelayCallbacks`（entry_relay_hook_adapter.cpp）把四路回调接成
// `EntryRelayManagerUpdateCallback` / `EntryRelayManagerRenderCallback` /
// `EntryRelayPreGetCollectibleCallback` / `EntryRelayRebuildMountPointsCallback`，这些符号在真实构建里
// 由 hook_manager.cpp 定义；本宿主机驱动只编译适配器本身，故在此提供桩定义，仅用于满足链接
// （驱动**不调用**注册函数，所以四个回调槽仍为空 —— 这正是下面"未接线必须被拒绝"用例的前提）。
extern "C" void EntryRelayManagerRenderCallback(void* self) {
    (void)self;
}

extern "C" void EntryRelayManagerUpdateCallback(void* self) {
    (void)self;
}

extern "C" std::uint32_t EntryRelayPreGetCollectibleCallback(void* itemPool, std::uint32_t itemPoolType,
                                                             std::uint32_t seed,
                                                             std::uint32_t noDecrease,
                                                             std::uint32_t defaultItem) {
    (void)itemPool;
    (void)itemPoolType;
    (void)seed;
    (void)noDecrease;
    (void)defaultItem;
    return 0;
}

extern "C" void EntryRelayRebuildMountPointsCallback() {}

static int g_failures = 0;

static void Check(bool condition, const char* label) {
    if (!condition) {
        std::printf("FAIL %s\n", label);
        ++g_failures;
    }
}

int main() {
    const std::uint8_t expected[16] = {0xFD, 0x7B, 0xBF, 0xA9, 0xFD, 0x03, 0x00, 0x91,
                                       0x08, 0x98, 0x8D, 0x52, 0x68, 0x00, 0xA0, 0x72};
    std::uintptr_t fallback = 0;
    HookTarget target{};
    target.base = 0x7100000000u;
    target.codeSize = 0x700000u;
    target.imageSize = 0x800000u;

    EntryRelayHookAdapter adapter;
    Check(!adapter.IsInstalled(HookId::ManagerUpdate), "初始未安装");
    Check(EntryRelayFallbackEntry(HookId::ManagerUpdate) == 0, "未安装时没有回退入口");
    Check(EntryRelayFallbackEntry(HookId::Count) == 0, "越界 HookId 取回退入口必须为 0");
    Check(static_cast<void (*)()>(Original<void (*)()>(HookId::ManagerUpdate)) == nullptr,
          "Original<Fn>() 必须能把回退入口转成函数指针（此处为 0）");

    // 回调未接线：必须响亮失败，并且**一次都不许调用 entry_relay**（目标字节保持原样）。
    isaac::runtime::relay::g_stubCalls = 0;
    const auto unwired = adapter.Install(HookId::ManagerUpdate, target);
    Check(unwired.code() == StatusCode::Rejected, "回调未接线必须被拒绝");
    Check(isaac::runtime::relay::g_stubCalls == 0,
          "回调未接线时不得触碰 entry_relay / 目标字节");
    Check(adapter.LastFailureCode() == 1, "拒绝时失败码 = 1（InvalidAddress 口径）");
    Check(!adapter.IsInstalled(HookId::ManagerUpdate), "拒绝后不得报告已安装");

    // ManagerPresent 的靶点是 `bl` 调用点，本后端不实现（走 GOT 槽改写）。
    isaac::runtime::relay::g_stubCalls = 0;
    const auto present = adapter.Install(HookId::ManagerPresent, target);
    Check(present.code() == StatusCode::Rejected, "ManagerPresent 不属于本后端");
    Check(isaac::runtime::relay::g_stubCalls == 0, "ManagerPresent 不得调用 entry_relay");
    Check(adapter.LastFailureCode() == 1, "ManagerPresent 拒绝时失败码 = 1");

    // 非法 id 是参数错误；空 target 走纯判定 → Rejected（失败码 1），同样不碰 entry_relay。
    Check(adapter.Install(HookId::Count, target).code() == StatusCode::InvalidArgument,
          "HookId::Count 是参数错误");
    HookTarget empty{};
    isaac::runtime::relay::g_stubCalls = 0;
    Check(adapter.Install(HookId::ManagerUpdate, empty).code() == StatusCode::Rejected,
          "空 target 必须被拒绝");
    Check(isaac::runtime::relay::g_stubCalls == 0, "空 target 不得触碰 entry_relay");
    Check(adapter.LastFailureCode() == 1, "空 target 拒绝时失败码 = 1");

    // ---- 纯判定：每条拒绝理由都要能被反例触发 ----
    const EntryRelayBinding good{"Manager::Update", 0x3F8DB8u, expected,
                                 reinterpret_cast<void*>(0x7100055120u), &fallback};
    std::uintptr_t address = 0;
    Check(EvaluateEntryRelayBinding(good, target, &address) == BindingVerdict::Ok,
          "合法绑定必须通过");
    Check(address == target.base + 0x3F8DB8u, "判定的产物是 base + offset");

    EntryRelayBinding broken = good;
    broken.callback = nullptr;
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "回调未接线必须拒绝");
    broken = good;
    broken.expectedEntry16 = nullptr;
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "缺版本 guard 必须拒绝");
    broken = good;
    broken.fallbackEntry = nullptr;
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "缺回退入口存储必须拒绝");
    broken = good;
    broken.offset = 0;
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "offset 0 必须拒绝");
    Check(EvaluateEntryRelayBinding(good, target, nullptr) == BindingVerdict::Rejected,
          "缺输出参数必须拒绝");
    broken = good;
    broken.offset = 0x3F8DBAu;  // 未 4 字节对齐的入口
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "入口必须 4 字节对齐");
    broken = good;
    broken.offset = 0xFF8u;  // 16 字节跨页（与 entry_relay 的门禁一致）
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "入口 16 字节不得跨 4 KiB 页");
    broken = good;
    broken.offset = 0x700000u;  // 正好落在代码窗口之外
    Check(EvaluateEntryRelayBinding(broken, target, &address) == BindingVerdict::Rejected,
          "入口必须落在代码窗口内");
    HookTarget nullBase{};
    nullBase.codeSize = 0x700000u;
    Check(EvaluateEntryRelayBinding(good, nullBase, &address) == BindingVerdict::Rejected,
          "base 为 0 必须拒绝");
    HookTarget noCode{};
    noCode.base = 0x7100000000u;
    Check(EvaluateEntryRelayBinding(good, noCode, &address) == BindingVerdict::Rejected,
          "codeSize 为 0 必须拒绝");
    HookTarget wrapping{};
    wrapping.base = 0xFFFFFFFFFFFFFF00u;
    wrapping.codeSize = 0x700000u;
    Check(EvaluateEntryRelayBinding(good, wrapping, &address) == BindingVerdict::Rejected,
          "地址回绕必须拒绝");

    if (g_failures == 0) {
        std::printf("ADAPTER_DRIVER_OK\n");
    }
    return g_failures == 0 ? 0 : 1;
}
"""


class EntryRelayAdapterBehaviorTests(unittest.TestCase):
    """在宿主机上编译**真实的适配器实现**，用打桩的 entry_relay 观察它的行为。"""

    @classmethod
    def setUpClass(cls):
        if shutil.which("c++") is None:
            raise unittest.SkipTest("宿主机没有 c++，无法编译纯判定与适配器行为测试")
        cls._temporary = tempfile.TemporaryDirectory(prefix="isaac-entry-relay-adapter-")
        directory = Path(cls._temporary.name)
        driver = directory / "adapter_driver.cpp"
        driver.write_text(ADAPTER_DRIVER.lstrip(), encoding="utf-8")
        binary = directory / "adapter_driver"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
             "-I", str(SRC), "-I", str(SOURCE),
             str(driver), str(RELAY / "entry_relay_hook_adapter.cpp"),
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
        self.assertIn("ADAPTER_DRIVER_OK", self._run.stdout)
        self.assertEqual(self._run.stdout.strip(), "ADAPTER_DRIVER_OK",
                         "驱动不得留下任何 FAIL 行")


if __name__ == "__main__":
    unittest.main()
