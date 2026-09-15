"""适配器桥接验证：真实 JSON 后端 → `ManifestParser` 的 domain 结果。

`runtime/src` 里只有 `ManifestSelectorAdapter` 引用旧的 `::ModManifest`，所以这个
桥接必须单独验证：如果它把旧结果映射错（例如没截断、没补 NUL、把“无启用 Mod”当成
成功），上层的假后端测试完全发现不了，而真机上只会表现为路径拼错后读不到入口脚本。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
LEGACY = ROOT / "runtime" / "source"

DRIVER = textwrap.dedent(
    r"""
    #include "application/mod/manifest_parser.hpp"
    #include "infrastructure/mod/manifest_selector_adapter.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    ManifestParser Parser() { return ManifestParser{ManifestSelectorAdapter::SelectFunction()}; }

    void ExpectReject(const char* json, ManifestParseFailure expected, const char* what) {
        const ManifestParseOutcome outcome = Parser().Parse(json, std::strlen(json));
        if (outcome.failure != expected) {
            std::printf("FAILED_CHECK %s (got %u)\n", what,
                        static_cast<unsigned>(outcome.failure));
            ++failures;
        }
    }

    } // namespace

    int main() {
        // 固定 schema：解析器要求根对象有 `schema_version` 与 `mods`，每个条目要有
        // `directory`/`enabled`；`entry` 可选（纯资源 Mod 没有 `main.lua`）。这里用最短的
        // 合法清单，避免测试依赖无关字段。
        const char* kEnabled =
            "{\"schema_version\":1,\"mods\":[{\"directory\":\"MuteOnPause\",\"enabled\":true,"
            "\"entry\":\"mods/MuteOnPause/main.lua\"}]}";
        ManifestParseOutcome outcome = Parser().Parse(kEnabled, std::strlen(kEnabled));
        Check(outcome.failure == ManifestParseFailure::None, "enabled_mod_parsed");
        Check(std::strcmp(outcome.manifest.directory.data(), "MuteOnPause") == 0,
              "directory_mapped");
        Check(std::strcmp(outcome.manifest.entry.data(), "mods/MuteOnPause/main.lua") == 0,
              "entry_mapped");
        Check(outcome.manifest.HasEntry(), "scripted_mod_reports_an_entry");

        // 第一个 enabled 的条目胜出，禁用的条目被跳过。
        const char* kSecondEnabled =
            "{\"schema_version\":1,\"mods\":[{\"directory\":\"Disabled\",\"enabled\":false,"
            "\"entry\":\"mods/Disabled/main.lua\"},"
            "{\"directory\":\"MuteOnPause\",\"enabled\":true,"
            "\"entry\":\"mods/MuteOnPause/main.lua\"}]}";
        outcome = Parser().Parse(kSecondEnabled, std::strlen(kSecondEnabled));
        Check(outcome.failure == ManifestParseFailure::None &&
                  std::strcmp(outcome.manifest.directory.data(), "MuteOnPause") == 0,
              "first_enabled_mod_wins");

        // 纯资源 Mod：清单没有 `entry`（打包工具对没有 `main.lua` 的 Mod 就是这么写的）。
        // 这是**合法**清单，解析成功且 entry 为空 —— 加载器据此只挂内容、不初始化 Lua。
        const char* kScriptless =
            "{\"schema_version\":1,\"mods\":[{\"directory\":\"qualityonsprites\","
            "\"content_only\":true,\"enabled\":true,\"files\":[]}]}";
        outcome = Parser().Parse(kScriptless, std::strlen(kScriptless));
        Check(outcome.failure == ManifestParseFailure::None, "scriptless_mod_parsed");
        Check(std::strcmp(outcome.manifest.directory.data(), "qualityonsprites") == 0,
              "scriptless_directory_mapped");
        Check(!outcome.manifest.HasEntry() && outcome.manifest.entry[0] == '\0',
              "scriptless_entry_is_empty");

        // 后端不给失败原因，适配器只能把它归一成“格式非法”；关键是不得当成成功。
        ExpectReject("{\"schema_version\":1,\"mods\":[]}", ManifestParseFailure::InvalidJson,
                     "no_enabled_mod_rejected");
        // `entry: null` 不是“无脚本”的写法：打包工具省略该键，解析器只在值是字符串时接受它。
        ExpectReject("{\"schema_version\":1,\"mods\":[{\"directory\":\"MuteOnPause\","
                     "\"enabled\":true,\"entry\":null}]}",
                     ManifestParseFailure::InvalidJson, "null_entry_rejected");
        ExpectReject("{\"schema_version\":1,\"mods\":[{\"directory\":\"../escape\","
                     "\"enabled\":true,\"entry\":\"mods/../escape/main.lua\"}]}",
                     ManifestParseFailure::InvalidJson, "unsafe_directory_rejected");
        ExpectReject("{\"schema_version\":1,\"mods\":[{\"directory\":\"../escape\","
                     "\"enabled\":true}]}",
                     ManifestParseFailure::InvalidJson, "unsafe_directory_without_entry_rejected");
        ExpectReject("{\"schema_version\":1,\"mods\":[{\"directory\":\"MuteOnPause\","
                     "\"enabled\":true,\"entry\":\"mods/Other/main.lua\"}]}",
                     ManifestParseFailure::InvalidJson, "mismatched_entry_prefix_rejected");
        ExpectReject("{\"schema_version\":1,\"mods\":[{\"directory\":\"MuteOnPause\","
                     "\"enabled\":true,\"entry\":\"\"}]}",
                     ManifestParseFailure::InvalidJson, "empty_string_entry_rejected");
        ExpectReject("{\"mods\":[]}", ManifestParseFailure::InvalidJson,
                     "missing_schema_version_rejected");
        ExpectReject("{", ManifestParseFailure::InvalidJson, "truncated_json_rejected");

        // 适配器只接受“解析成功且路径可用”的后端结果，重复解析必须稳定。
        outcome = Parser().Parse(kEnabled, std::strlen(kEnabled));
        Check(outcome.failure == ManifestParseFailure::None &&
                  std::strcmp(outcome.manifest.entry.data(), "mods/MuteOnPause/main.lua") == 0,
              "adapter_is_repeatable");

        if (failures != 0) { std::printf("MANIFEST_BRIDGE_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("MANIFEST_BRIDGE_CHECKS_PASSED\n");
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


class ManifestBridgeTests(unittest.TestCase):
    def test_adapter_maps_the_real_json_backend(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-manifest-bridge-") as temporary:
            directory = Path(temporary)
            source = directory / "manifest_bridge.cpp"
            binary = directory / "manifest_bridge"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(SRC), "-I", str(LEGACY),
                    str(source),
                    str(SRC / "application" / "mod" / "manifest_parser.cpp"),
                    str(SRC / "infrastructure" / "mod" / "manifest_selector_adapter.cpp"),
                    str(LEGACY / "mod_manifest.cpp"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("MANIFEST_BRIDGE_CHECKS_PASSED", run_result.stdout)
            self.assertNotIn("FAILED_CHECK", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
