"""模组开关状态（纯值对象）的编解码测试 —— 不需要设备、不需要引擎。

## 为什么单独测这个

开关状态是**唯一**跨启动保留我们判定的地方：它写错以后的表现是"某个模组这次没加载"，
而用户与我们都很难分辨"是状态文件错了"还是"模组本身有问题"。所以格式与纪律要钉死：

1. **只记例外**（被关掉的目录名）⇒ 新装的模组默认开着，换名字不会留下过期条目；
2. **抬头必须匹配**：第一行不是 `isaac-switch-mods 1` 就整份拒绝（`Corrupted`）——
   拿一份不是我们写的文件当状态会安静地改变加载结果；
3. **可疑名字整份拒绝**（空、`.`、`..`、带斜杠）：与自动发现同一口径，避免拿可疑字符串去比目录；
4. **不认识的整行忽略**：以后加字段不会让老版本解析失败；
5. **表满要报错**（`CapacityExceeded`），不静默丢弃；
6. 序列化放不下就返回 0（调用方按容量不足处理），**不写半截**。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"

DRIVER = textwrap.dedent(
    r"""
    #include "domain/mod/mod_toggle_state.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    std::string Serialize(const ModToggleState& state) {
        char buffer[kModToggleStateMaximumBytes] = {};
        const std::size_t size = state.Serialize(buffer, sizeof(buffer));
        return std::string(buffer, size);
    }

    } // namespace

    int main() {
        // --- 1) 空文本 = 空状态；抬头不符整份拒绝 ---------------------------
        {
            ModToggleState state{};
            Check(state.Parse("").ok(), "空文本应当解析成空状态");
            Check(state.disabledCount == 0 && state.Empty(), "空文本 ⇒ 没有禁用项");
        }
        {
            ModToggleState state{};
            Check(state.Parse("some other file\n").code() == StatusCode::Corrupted,
                  "抬头不符必须整份拒绝");
            Check(state.Parse("isaac-switch-mods 2\noff ModA\n").code() == StatusCode::Corrupted,
                  "版本号不同也按抬头不符处理（宁可拒绝，也不要按未知语义加载）");
        }

        // --- 2) 正常解析：只认 `off <名字>`，不认识的整行忽略 -----------------
        {
            ModToggleState state{};
            const Status parsed = state.Parse(
                "isaac-switch-mods 1\n"
                "# 注释行\n"
                "future-field = 1\n"
                "\n"
                "off ModB\n"
                "off ModA\n"
                "off ModA\n");     // 重复按一条算
            Check(parsed.ok(), "合法文本应当解析成功");
            Check(state.disabledCount == 2, "两条去重后应当是 2");
            Check(state.IsDisabled("ModA") && state.IsDisabled("ModB"), "两个都应当被标成禁用");
            Check(!state.IsDisabled("ModC"), "没写进去的应当是启用");
        }

        // --- 3) 可疑名字整份拒绝 ---------------------------------------------
        for (const char* bad : {"isaac-switch-mods 1\noff \n",
                                "isaac-switch-mods 1\noff .\n",
                                "isaac-switch-mods 1\noff ..\n",
                                "isaac-switch-mods 1\noff a/b\n"}) {
            ModToggleState state{};
            Check(state.Parse(bad).code() == StatusCode::Corrupted, bad);
        }

        // --- 4) 改开关：关掉 ⇒ 追加；打开 ⇒ 从表里删掉 ------------------------
        {
            ModToggleState state{};
            Check(state.SetEnabled("ModA", false).ok(), "关掉一个模组应当成功");
            Check(state.SetEnabled("ModB", false).ok(), "再关一个应当成功");
            Check(state.disabledCount == 2, "应当记下两条");
            Check(state.SetEnabled("ModA", true).ok(), "打开应当成功");
            Check(state.disabledCount == 1 && !state.IsDisabled("ModA") &&
                  state.IsDisabled("ModB"), "打开 ModA 之后只剩 ModB 被关");
            Check(state.SetEnabled("ModA", true).ok(), "重复打开是幂等的");
            Check(state.SetEnabled("", false).code() == StatusCode::InvalidArgument,
                  "空名字必须拒绝");
        }

        // --- 5) 表满必须报错，不静默丢弃 -------------------------------------
        {
            ModToggleState state{};
            for (std::size_t index = 0; index < kMaximumDisabledMods; ++index) {
                const std::string name = "Mod" + std::to_string(index);
                Check(state.SetEnabled(name, false).ok(), "填满之前都应当成功");
            }
            Check(state.SetEnabled("OneTooMany", false).code() == StatusCode::CapacityExceeded,
                  "超出容量必须报 CapacityExceeded");
        }

        // --- 6) 往返：序列化 → 解析 → 一样 -----------------------------------
        {
            ModToggleState state{};
            Check(state.SetEnabled("Zeta", false).ok(), "关掉 Zeta");
            Check(state.SetEnabled("alpha", false).ok(), "关掉 alpha");
            const std::string text = Serialize(state);
            Check(text.rfind("isaac-switch-mods 1\n", 0) == 0, "抬头必须是第一行");
            ModToggleState round{};
            Check(round.Parse(text).ok(), "自己写出来的文本必须能被自己解析");
            Check(round.disabledCount == state.disabledCount, "往返之后条数一致");
            Check(round.IsDisabled("Zeta") && round.IsDisabled("alpha"), "往返之后内容一致");
        }

        // --- 7) 放不下就返回 0（不写半截） -----------------------------------
        {
            ModToggleState state{};
            Check(state.SetEnabled("ModWithALongName", false).ok(), "先塞一条");
            char small[8] = {};
            Check(state.Serialize(small, sizeof(small)) == 0, "缓冲太小必须返回 0");
        }

        if (failures == 0) { std::printf("MOD_TOGGLE_STATE_CHECKS_PASSED\n"); }
        return failures == 0 ? 0 : 1;
    }
    """
)


class ModToggleStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("需要宿主 C++ 编译器（本用例不需要 docker）")
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-mod-toggle-state-")
        temporary = Path(cls.temporary.name)
        source = temporary / "mod_toggle_state.cpp"
        source.write_text(DRIVER.lstrip(), encoding="utf-8")
        binary = temporary / "mod_toggle_state"
        build = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-I", str(SRC), "-I", str(ROOT / "runtime" / "source"),
             str(source),
             str(SRC / "domain" / "mod" / "mod_toggle_state.cpp"),
             "-o", str(binary)],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)
        cls.binary = binary

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_mod_toggle_state(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MOD_TOGGLE_STATE_CHECKS_PASSED", result.stdout)
        self.assertNotIn("FAILED_CHECK", result.stdout)


if __name__ == "__main__":
    unittest.main()
