"""构建形状不变量：顶层的安装函数用到的辅助定义不能被条件编译挡掉。

2026-09-13 的教训：`RecordUpdateHookInstall`/`RecordRenderHookInstall`/
`RecordPreGetCollectibleInstall`/`RecordRenderPresentRelayInstall` 原本关在
`#if !defined(EXL_DIAGNOSTIC_STAGE)`（外层）与 `#if defined(EXL_LAYERED_RUNTIME)`（内层）里，
而 `TryInstallManagerRenderHook`/`TryInstallManagerPresentRelay` 在文件顶层（任何配置都会编译）
引用它们 —— 于是 `make probe DIAGNOSTIC_STAGE=<n>`（= 诊断宏 + 分层运行时）必然报
"… was not declared in this scope"。这类错误只在**真编一次诊断构建**时才暴露，本机默认构建看不到，
所以这里用"记录函数不得有任何外层 `#if`"把不变量钉住（真编验证见 docs/问题与解决记录.md）。
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "runtime" / "source" / "hook_manager.cpp"

# 顶层（无 #if 守卫）安装函数引用的定义：它们必须同样常编。
UNCONDITIONAL_DEFINITIONS = (
    "HookInstallResult RecordUpdateHookInstall(",
    "RenderHookInstallResult RecordRenderHookInstall(",
    "PreGetCollectibleRelayInstallResult RecordPreGetCollectibleInstall(",
    "RenderPresentRelayInstallResult RecordRenderPresentRelayInstall(",
    "std::atomic<std::uint32_t> g_HookInstallResults[",
    "std::atomic<std::uint32_t> g_UpdateCallbackEntries{",
    "std::atomic<std::uint32_t> g_RenderCallbackEntries{",
)

# 只有分层构建才存在的部分（导出给宿主插件/自记日志），必须继续被守卫着。
LAYERED_ONLY_DEFINITIONS = (
    "IsaacModRuntime_GetHookDiagnostics(std::uint32_t* output",
    "IsaacModRuntime_FillHookJournalWords(std::uint32_t* output",
)


def enclosing_conditionals(source: str, needle: str) -> list[str] | None:
    """Return the `#if` lines enclosing the first non-comment line containing `needle`."""
    stack: list[str] = []
    for line in source.splitlines():
        text = line.strip()
        if text.startswith("#if"):
            stack.append(text)
            continue
        if text.startswith("#endif"):
            if stack:
                stack.pop()
            continue
        if not text.startswith("//") and needle in line:
            return list(stack)
    return None


class DiagnosticBuildInvariantsTests(unittest.TestCase):
    def setUp(self):
        self.source = HOOK.read_text(encoding="utf-8")

    def test_install_recorders_are_compiled_in_every_configuration(self):
        for needle in UNCONDITIONAL_DEFINITIONS:
            with self.subTest(definition=needle):
                guards = enclosing_conditionals(self.source, needle)
                self.assertIsNotNone(guards, f"未找到定义：{needle}")
                self.assertEqual(
                    guards, [],
                    f"{needle} 被条件编译守卫住（{guards}），诊断构建会因未定义符号编不过",
                )

    def test_host_plugin_diagnostics_exports_stay_layered_only(self):
        for needle in LAYERED_ONLY_DEFINITIONS:
            with self.subTest(definition=needle):
                guards = enclosing_conditionals(self.source, needle)
                self.assertIsNotNone(guards, f"未找到定义：{needle}")
                self.assertIn(
                    "#if !defined(EXL_DIAGNOSTIC_STAGE)", guards,
                    f"{needle} 应当只存在于生产（非诊断）分层构建里",
                )


if __name__ == "__main__":
    unittest.main()
