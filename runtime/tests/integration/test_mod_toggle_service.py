"""模组开关服务的行为测试（宿主，注入假存档端口）。

## 这个用例覆盖什么

开关服务的三件事：读状态、改状态、**按状态过滤一批已解析的模组**。第三件最危险，因为
`ResolvedManifestMod` 里存的是**指向路径缓冲的指针**（`manifest_service.hpp` 的注释写明它是个
廉价值对象）。过滤时要搬动条目，搬完如果不同时把指针重新指到新位置，就会出现
"加载的是另一个模组的路径" 这类极难查的错误 —— 所以这里专门钉住指针。

真机侧的能力（存档分区读写）由 `EngineSaveFileAdapter` 提供，真机已验：
`docs/问题与解决记录.md` 续三十四。这里注入假端口，钉的是**应用层判定**：

1. 状态文件不存在 ⇒ 空状态、`Ok`（第一次运行不能报错）；
2. 读坏（`Corrupted`）⇒ 如实上报，**不静默当成"全都开着"**；
3. 过滤保持原顺序、只移走被关掉的；`count` 与指针都要自洽；
4. 用户在卡上放的 `<modRoot>/disable.it` 也算禁用，而且**查不了就不拦**（宁可多加载）；
5. 存档里的 `off <名字>` 与 `modRoot` 的**末段目录名**匹配（清单路径与自动发现路径共用一把钥匙）。
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
    #include "application/mod/mod_toggle_service.hpp"

    #include <cstdio>
    #include <cstring>
    #include <map>
    #include <string>
    #include <string_view>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    struct FakeSaveFilePort final : ISaveFilePort {
        std::map<std::string, std::string> files;
        std::vector<std::string> markers;      // 存在的"绝对路径"
        Status readStatus{StatusCode::Ok};
        Status existsStatus{StatusCode::Ok};
        int writes{0};

        Status Read(std::string_view name, char* out, std::size_t capacity,
                    std::size_t* size) noexcept override {
            if (!readStatus.ok()) { return readStatus; }
            const auto hit = files.find(std::string(name));
            if (hit == files.end()) { return Status{StatusCode::NotFound}; }
            if (hit->second.size() > capacity) { return Status{StatusCode::CapacityExceeded}; }
            std::memcpy(out, hit->second.data(), hit->second.size());
            *size = hit->second.size();
            return Status::Ok();
        }
        Status Write(std::string_view name, std::string_view data) noexcept override {
            ++writes;
            files[std::string(name)] = std::string(data);
            return Status::Ok();
        }
        Status Remove(std::string_view name) noexcept override {
            files.erase(std::string(name));
            return Status::Ok();
        }
        Status Exists(const char* absolutePath, bool* exists) noexcept override {
            if (!existsStatus.ok()) { return existsStatus; }
            *exists = false;
            for (const std::string& marker : markers) {
                if (marker == absolutePath) { *exists = true; }
            }
            return Status::Ok();
        }
    };

    // 造一批"已解析的模组"，形态与 `ModDiscoveryService` 产出的一致（4 个上限）。
    void Fill(ResolvedManifestModBatch* batch, const std::vector<std::string>& directories,
              bool withEntry) {
        batch->count = 0;
        for (std::size_t index = 0; index < directories.size(); ++index) {
            ModPathBuffers& buffers = batch->paths[index];
            const std::string root = "rom:/isaac_mods/mods/" + directories[index];
            std::snprintf(buffers.modRoot.data(), buffers.modRoot.size(), "%s", root.c_str());
            std::snprintf(buffers.entryPath.data(), buffers.entryPath.size(),
                          "rom:/isaac_mods/mods/%s/main.lua", directories[index].c_str());
            std::snprintf(buffers.chunkName.data(), buffers.chunkName.size(),
                          "@rom:/isaac_mods/mods/%s/main.lua", directories[index].c_str());
            ResolvedManifestMod& mod = batch->mods[index];
            mod.modRoot = buffers.modRoot.data();
            mod.entryPath = withEntry ? buffers.entryPath.data() : "";
            mod.chunkName = withEntry ? buffers.chunkName.data() : "";
            mod.hasEntry = withEntry;
            mod.manifestBytes = 0;
            ++batch->count;
        }
    }

    std::string RootOf(const ResolvedManifestModBatch& batch, std::size_t index) {
        return batch.mods[index].modRoot == nullptr ? "" : batch.mods[index].modRoot;
    }

    // 指针必须指回**它自己那一格**缓冲：搬动之后忘了重指，就会读到别人的路径。
    void CheckPointersSelfConsistent(const ResolvedManifestModBatch& batch, const char* what) {
        for (std::size_t index = 0; index < batch.count; ++index) {
            const ResolvedManifestMod& mod = batch.mods[index];
            Check(mod.modRoot == batch.paths[index].modRoot.data(), what);
            if (mod.hasEntry) {
                Check(mod.entryPath == batch.paths[index].entryPath.data(), what);
                Check(mod.chunkName == batch.paths[index].chunkName.data(), what);
            } else {
                Check(mod.entryPath != nullptr && mod.entryPath[0] == '\0', what);
            }
        }
    }

    } // namespace

    int main() {
        // --- 1) 没有状态文件 ⇒ 空状态且 Ok ----------------------------------
        {
            FakeSaveFilePort port{};
            ModToggleService service{port};
            Check(service.Load().ok(), "文件不存在时 Load 应当成功（第一次运行）");
            Check(service.DisabledCount() == 0, "第一次运行没有禁用项");
            Check(service.IsEnabled("Anything"), "默认全部启用（新装的模组默认开着）");
        }

        // --- 2) 读坏要如实上报，不能当成"全都开着" ---------------------------
        {
            FakeSaveFilePort port{};
            port.readStatus = Status{StatusCode::IoFailure};
            ModToggleService service{port};
            Check(!service.Load().ok(), "端口报错时 Load 必须报错");
        }
        {
            FakeSaveFilePort port{};
            port.files["isaac-switch-mods-state.txt"] = "not our file\n";
            ModToggleService service{port};
            Check(service.Load().code() == StatusCode::Corrupted, "不是我们的文件必须报 Corrupted");
        }

        // --- 3) 关掉 / 打开 / 落盘 ------------------------------------------
        {
            FakeSaveFilePort port{};
            ModToggleService service{port};
            Check(service.Load().ok(), "先读一次");
            Check(service.SetEnabled("ModB", false).ok(), "关掉 ModB");
            Check(!service.IsEnabled("ModB"), "ModB 应当被关掉");
            Check(service.Save().ok(), "落盘应当成功");
            Check(port.writes == 1, "落盘应当写一次");
            Check(port.files["isaac-switch-mods-state.txt"] ==
                      "isaac-switch-mods 1\noff ModB\n",
                  "落盘内容应当是约定的文本格式");

            // 换一个实例读回来：状态要能跨"启动"保留
            ModToggleService reloaded{port};
            Check(reloaded.Load().ok(), "重新载入应当成功");
            Check(!reloaded.IsEnabled("ModB"), "重新载入之后 ModB 仍然被关掉");
            Check(reloaded.IsEnabled("ModA"), "没写进去的仍然是启用");
        }

        // --- 4) 过滤：保顺序、只移走被关掉的、指针自洽 -----------------------
        {
            FakeSaveFilePort port{};
            ModToggleService service{port};
            Check(service.Load().ok(), "先读一次");
            Check(service.SetEnabled("ModC", false).ok(), "关掉 ModC");

            static ResolvedManifestModBatch batch{};
            Fill(&batch, {"ModA", "ModB", "ModC", "ModD"}, true);
            const std::size_t removed = service.ApplyToBatch(&batch);
            Check(removed == 1, "应当只移走一个");
            Check(batch.count == 3, "剩下三个");
            Check(RootOf(batch, 0) == "rom:/isaac_mods/mods/ModA", "顺序要保持");
            Check(RootOf(batch, 1) == "rom:/isaac_mods/mods/ModB", "顺序要保持");
            Check(RootOf(batch, 2) == "rom:/isaac_mods/mods/ModD", "被关掉的 ModC 应当不在");
            CheckPointersSelfConsistent(batch, "搬动之后指针必须指回自己那一格");
        }

        // --- 5) 卡上的手工标记也算禁用；查不了就不拦 -------------------------
        {
            FakeSaveFilePort port{};
            port.markers.push_back("rom:/isaac_mods/mods/ModB/disable.it");
            ModToggleService service{port};
            Check(service.Load().ok(), "先读一次");
            static ResolvedManifestModBatch batch{};
            Fill(&batch, {"ModA", "ModB"}, true);
            Check(service.ApplyToBatch(&batch) == 1, "带 disable.it 的模组应当被移走");
            Check(RootOf(batch, 0) == "rom:/isaac_mods/mods/ModA", "剩下的应当是 ModA");
            CheckPointersSelfConsistent(batch, "只移走一个时指针同样要自洽");
        }
        {
            FakeSaveFilePort port{};
            port.existsStatus = Status{StatusCode::Unsupported};
            ModToggleService service{port};
            Check(service.Load().ok(), "先读一次");
            static ResolvedManifestModBatch batch{};
            Fill(&batch, {"ModA"}, true);
            Check(service.ApplyToBatch(&batch) == 0, "查不了标记就不能拦（宁可多加载）");
            Check(batch.count == 1, "模组应当还在");
        }

        // --- 6) 纯资源型模组（没有入口脚本）也要能被过滤 ---------------------
        {
            FakeSaveFilePort port{};
            ModToggleService service{port};
            Check(service.Load().ok(), "先读一次");
            Check(service.SetEnabled("ModB", false).ok(), "关掉 ModB");
            static ResolvedManifestModBatch batch{};
            Fill(&batch, {"ModA", "ModB"}, false);
            Check(service.ApplyToBatch(&batch) == 1, "纯资源型也要能被关掉");
            Check(batch.count == 1 && RootOf(batch, 0) == "rom:/isaac_mods/mods/ModA",
                  "剩下的应当是 ModA");
            CheckPointersSelfConsistent(batch, "纯资源型的指针口径（entryPath 为空串）");
        }

        if (failures == 0) { std::printf("MOD_TOGGLE_SERVICE_CHECKS_PASSED\n"); }
        return failures == 0 ? 0 : 1;
    }
    """
)


class ModToggleServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("需要宿主 C++ 编译器（本用例不需要 docker）")
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-mod-toggle-service-")
        temporary = Path(cls.temporary.name)
        source = temporary / "mod_toggle_service.cpp"
        source.write_text(DRIVER.lstrip(), encoding="utf-8")
        binary = temporary / "mod_toggle_service"
        build = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-I", str(SRC), "-I", str(ROOT / "runtime" / "source"),
             str(source),
             str(SRC / "application" / "mod" / "mod_toggle_service.cpp"),
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

    def test_mod_toggle_service(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MOD_TOGGLE_SERVICE_CHECKS_PASSED", result.stdout)
        self.assertNotIn("FAILED_CHECK", result.stdout)


if __name__ == "__main__":
    unittest.main()
