"""分层构建下 Lua 回调管线的接线测试。

分层构建把回调存储从“每个阶段一个全局单槽”换成共享的 CallbackRegistry，并由
CallbackDispatcher 按注册顺序派发。这里检查接线本身（源码层面）以及注册表新增的
按 (id, owner) 查找/移除行为（宿主行为层面）。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
LUA_RUNTIME = ROOT / "runtime" / "source" / "lua_runtime.cpp"

DRIVER = textwrap.dedent(
    r"""
    #include "application/callback/callback_registry.hpp"
    #include "domain/callback/callback_descriptor.hpp"

    #include <cstdio>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;

    void Check(bool condition, const char* what) {
        if (!condition) {
            std::printf("FAILED_CHECK %s\n", what);
            ++failures;
        }
    }

    CallbackDescriptor Make(CallbackId id, ModHandle owner, int functionRef, int modRef) {
        CallbackDescriptor descriptor{};
        descriptor.id = id;
        descriptor.owner = owner;
        descriptor.affinity = ThreadAffinity::Any;
        descriptor.luaReference = functionRef;
        descriptor.modReference = modRef;
        return descriptor;
    }

    } // namespace

    int main() {
        CallbackRegistry registry{};
        const ModHandle owner{1, 1};
        const ModHandle other{2, 1};

        Check(registry.Register(Make(kCallbackPostRender, owner, 11, 21)).ok(), "register_owner");
        Check(registry.Register(Make(kCallbackPostRender, other, 12, 22)).ok(), "register_other");

        const CallbackDescriptor* found = registry.Find(kCallbackPostRender, other);
        Check(found != nullptr && found->luaReference == 12 && found->modReference == 22,
              "find_by_owner");
        Check(registry.Find(kCallbackPostRender, ModHandle{9, 1}) == nullptr, "find_missing_owner");

        CallbackDescriptor removed{};
        Check(registry.Remove(kCallbackPostRender, owner, &removed) == 1, "remove_reports_change");
        Check(removed.luaReference == 11 && removed.modReference == 21, "remove_returns_entry");
        Check(registry.CountOf(kCallbackPostRender) == 1, "remove_only_selected_owner");
        Check(registry.Remove(kCallbackPostRender, owner, nullptr) == 0, "remove_is_idempotent");
        Check(registry.Find(kCallbackPostRender, other) != nullptr, "other_owner_untouched");

        CallbackDescriptor none{};
        Check(registry.Remove(kCallbackPostRender, other, &none) == 1, "remove_second_owner");
        Check(registry.Count() == 0, "registry_empty_again");

        if (failures != 0) {
            std::printf("LUA_CALLBACK_CHECKS_FAILED %d\n", failures);
            return 1;
        }
        std::printf("LUA_CALLBACK_CHECKS_PASSED\n");
        return 0;
    }
    """
)


def host_compiler() -> str | None:
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return found
    return None


class LuaCallbackPipelineTests(unittest.TestCase):
    def test_layered_runtime_uses_the_shared_registry(self):
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        self.assertIn("isaac::runtime::CallbackRegistry g_CallbackRegistry;", source)
        self.assertIn("isaac::runtime::CallbackDispatcher dispatcher{g_CallbackRegistry", source)
        self.assertIn("g_CallbackRegistry.CountOf(isaac::runtime::kCallbackPostUpdate)", source)
        self.assertIn("g_CallbackRegistry.CountOf(isaac::runtime::kCallbackPostRender)", source)

    def test_registration_appends_instead_of_replacing(self):
        """同一 Mod 对同一回调 id 的多次登记必须**追加**，不能被后一次替换掉。

        原断言要求 `lua_runtime.cpp` 里出现 `g_CallbackRegistry.Remove(` —— 那是"先删旧登记
        再登记"的替换语义留下的。替换语义是**错的**（真机报告 `01789203482`：注册表只数出 7 条，
        即"种类的个数"而不是"登记的个数"）：PC 引擎按登记顺序调用同一 id 的**全部**回调，
        而 EID 一次登记 5 个 `MC_POST_NEW_ROOM`、10 个 `MC_PRE_USE_ITEM`，替换会把它们吃掉。

        现在登记只在 `interfaces/lua/mod_api.cpp` 里做，且是纯追加；`lua_runtime.cpp` 不再需要
        删除路径。所以这条改为断言"追加语义"本身，并守住"核心 TU 里不得又冒出删除调用"。
        """
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        self.assertNotIn("g_CallbackRegistry.Remove(", source)
        mod_api = (SRC / "interfaces" / "lua" / "mod_api.cpp").read_text(encoding="utf-8")
        self.assertIn("registry.Register(descriptor)", mod_api)
        # 追加语义：登记前不得调用 `Remove`（那正是被推翻的替换语义）。
        self.assertNotIn("registry.Remove(", mod_api)

    def test_single_slot_limit_only_remains_for_non_layered_builds(self):
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        # 探针与生产共用注册表后，旧单槽实现已删除，不能再留下构建分叉。
        self.assertNotIn("#if !defined(EXL_LAYERED_RUNTIME)", source)
        self.assertNotIn("only one callback is supported for each Runtime phase", source)
        self.assertNotIn("g_PostRenderCallback", source)

    def test_registry_owner_lookup_and_removal(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-lua-callbacks-") as temporary:
            directory = Path(temporary)
            source = directory / "callbacks.cpp"
            binary = directory / "callbacks"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(SRC),
                    str(source),
                    str(SRC / "application" / "callback" / "callback_registry.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("LUA_CALLBACK_CHECKS_PASSED", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
