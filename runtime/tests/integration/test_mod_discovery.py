"""方案 A（免清单自动发现模组）的宿主行为测试。

## 这个用例覆盖什么

`Runtime` 以前必须靠 `isaac_mods/manifest.json` 才知道要加载哪几个模组 —— 玩家加一个模组就得
在 PC 上重跑一次打包工具。方案 A 让运行时自己列 `isaac_mods/mods/`，把发现的每个**子目录**
当成一个模组（PC 的语义就是这样：`mods/` 下的文件夹就是一个模组），清单降级成可选项。

真正"列目录"的能力由引擎提供（`KAGE::Filesys::IContentManager::GetDirectoryEntries`，
真机已验：见 `docs/问题与解决记录.md` 续三十一），宿主里用假端口注入。这里钉住的是**应用层**
的判定：

1. **只认目录**：同一层里混进来的文件条目必须被忽略；
2. **顺序确定**：按名字升序（引擎给的顺序没有保证，而两个模组的回调派发顺序会影响游戏行为）；
3. **不截断**：目录数超过容量 ⇒ `CapacityExceeded`，绝不安安静静少加载一个模组；
4. **可疑名字整批拒绝**：`.`/`..`/带斜杠的名字说明我们对引擎返回块的解读有问题 ⇒ `Corrupted`
   （宁可让用户看到"没加载"，也不要拿可疑字符串去拼路径调引擎）；
5. **产出的东西能直接喂给加载器**：与清单路径产出同一个 `ResolvedManifestModBatch`。
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
    #include "application/mod/mod_discovery_service.hpp"
    #include "application/mod/mod_load_service.hpp"
    #include "ports/mod_directory_port.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>
    #include <string_view>
    #include <vector>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    struct FakeDirectoryPort final : IModDirectoryPort {
        std::vector<std::string> directories;
        std::vector<std::string> files;
        Status status{StatusCode::Ok};
        std::string lastPath;
        int calls{0};

        Status ListSubdirectories(std::string_view relativePath, ModDirectoryEntry* out,
                                  std::size_t capacity, std::size_t* count) noexcept override {
            ++calls;
            lastPath = std::string(relativePath);
            if (!status.ok()) { return status; }
            std::size_t written = 0;
            for (const std::string& name : directories) {
                if (written >= capacity) { return Status{StatusCode::CapacityExceeded}; }
                std::strncpy(out[written].name.data(), name.c_str(), out[written].name.size() - 1);
                ++written;
            }
            *count = written;
            return Status{StatusCode::Ok};
        }
    };

    struct FakeContent final : IContentPort {
        Status Size(std::string_view, std::size_t*) noexcept override { return Status::Ok(); }
        Status Read(std::string_view path, std::uint8_t* target, std::size_t capacity,
                    std::size_t* readCount) noexcept override {
            const std::string text(path);
            if (text.find("manifest.json") != std::string::npos) {
                return Status{StatusCode::NotFound};
            }
            // 每个模组的入口脚本都"存在"：内容就是它的路径，加载器会把路径记下来。
            if (text.size() + 1 > capacity) { return Status{StatusCode::CapacityExceeded}; }
            std::memcpy(target, text.data(), text.size());
            *readCount = text.size();
            return Status{StatusCode::Ok};
        }
    };

    struct FakeLua final : ILuaEnginePort {
        int loads{0};
        std::vector<std::string> entries;
        std::vector<std::string> roots;
        Status Initialize() noexcept override { return Status::Ok(); }
        bool IsReady() const noexcept override { return true; }
        Status RunChunk(const LuaChunk&) noexcept override { return Status{StatusCode::Unsupported}; }
        Status LoadManifestMod(const ManifestModLoad& load,
                               std::uint32_t* failureDetail) noexcept override {
            ++loads;
            static_cast<void>(failureDetail);
            entries.emplace_back(load.entryBytes, load.entryLength);
            roots.emplace_back(load.modRoot == nullptr ? "" : load.modRoot);
            return Status::Ok();
        }
        Status CallGlobal(const char*, int, int*) noexcept override { return Status::Ok(); }
        void Shutdown() noexcept override {}
    };

    std::string ModRoot(const ResolvedManifestModBatch& batch, std::size_t index) {
        return batch.mods[index].modRoot == nullptr ? "" : batch.mods[index].modRoot;
    }
    std::string EntryPath(const ResolvedManifestModBatch& batch, std::size_t index) {
        return batch.mods[index].entryPath == nullptr ? "" : batch.mods[index].entryPath;
    }
    std::string ChunkName(const ResolvedManifestModBatch& batch, std::size_t index) {
        return batch.mods[index].chunkName == nullptr ? "" : batch.mods[index].chunkName;
    }

    } // namespace

    int main() {
        FakeDirectoryPort port{};
        ModDiscoveryService service{port};
        static ResolvedManifestModBatch batch{};

        // --- 1) 两个目录 + 一个文件：只认目录，而且按名字升序 ---------------
        port.directories = {"Zeta", "alpha"};
        port.files = {"readme.txt"};
        Status status = service.Discover(&batch);
        Check(status.ok(), "discover_two_directories");
        Check(batch.count == 2, "discover_counts_directories_only");
        Check(std::string(port.lastPath) == "isaac_mods/mods", "discover_lists_the_mods_root");
        Check(ModRoot(batch, 0) == "rom:/isaac_mods/mods/Zeta", "sorted_uppercase_first");
        Check(ModRoot(batch, 1) == "rom:/isaac_mods/mods/alpha", "sorted_second");
        Check(EntryPath(batch, 1) ==
                  "rom:/isaac_mods/mods/alpha/main.lua", "entry_path_built");
        Check(ChunkName(batch, 1) ==
                  "@rom:/isaac_mods/mods/alpha/main.lua", "chunk_name_built");
        Check(batch.mods[0].hasEntry, "discovered_mods_declare_an_entry");
        Check(batch.mods[0].manifestBytes == 0, "no_manifest_size_reported");

        // --- 2) 一个目录都没有 ⇒ NotFound（与"清单里一个 enabled 都没有"同口径） ----
        port.directories = {};
        status = service.Discover(&batch);
        Check(status.code() == StatusCode::NotFound, "empty_directory_is_not_found");
        Check(batch.count == 0, "empty_directory_yields_no_batch");

        // --- 3) 超过容量 ⇒ CapacityExceeded，而且**不截断** ----------------------
        port.directories = {"m1", "m2", "m3", "m4", "m5"};
        status = service.Discover(&batch);
        Check(status.code() == StatusCode::CapacityExceeded, "too_many_directories_rejected");
        Check(batch.count == 0, "too_many_directories_yields_no_batch");

        // --- 4) 可疑名字整批拒绝 ------------------------------------------------
        for (const char* bad : {".", "..", "a/b", "a\\b", ""}) {
            port.directories = {bad};
            status = service.Discover(&batch);
            Check(status.code() == StatusCode::Corrupted, "suspicious_name_rejected");
            Check(batch.count == 0, "suspicious_name_yields_no_batch");
        }

        // --- 5) 端口的失败要如实传上去 ------------------------------------------
        port.directories = {"mod"};
        port.status = Status{StatusCode::Unsupported};
        status = service.Discover(&batch);
        Check(status.code() == StatusCode::Unsupported, "port_failure_is_forwarded");

        // --- 6) 发现出来的批次能直接喂给加载器（与清单路径同一个入参类型） ----------
        port.status = Status{StatusCode::Ok};
        port.directories = {"beta", "alpha"};
        status = service.Discover(&batch);
        Check(status.ok() && batch.count == 2, "discover_before_load");
        FakeContent content{};
        FakeLua lua{};
        ModLoadService loader{content, lua};
        static std::uint8_t entry[4096]{};
        ModLoadBatchOutcome outcome{};
        ModLoadFailure failure{};
        const Status loaded = loader.LoadAll(batch, entry, sizeof(entry), &outcome, &failure);
        Check(loaded.ok() && !outcome.anyFailure, "discovered_batch_loads_cleanly");
        Check(lua.loads == 2, "both_discovered_mods_reach_the_engine");
        Check(lua.entries.size() == 2 &&
                  lua.entries[0].find("alpha/main.lua") != std::string::npos &&
                  lua.entries[1].find("beta/main.lua") != std::string::npos,
              "load_order_matches_the_sorted_names");

        if (failures == 0) { std::printf("MOD_DISCOVERY_CHECKS_PASSED\n"); }
        return failures == 0 ? 0 : 1;
    }
    """
)


class ModDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("需要宿主 C++ 编译器（本用例不需要 docker）")
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-mod-discovery-")
        temporary = Path(cls.temporary.name)
        source = temporary / "mod_discovery.cpp"
        source.write_text(DRIVER.lstrip(), encoding="utf-8")
        binary = temporary / "mod_discovery"
        build = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-I", str(SRC), "-I", str(ROOT / "runtime" / "source"),
             str(source),
             str(SRC / "application" / "mod" / "mod_discovery_service.cpp"),
             str(SRC / "application" / "mod" / "mod_load_service.cpp"),
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

    def test_discovery_service(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MOD_DISCOVERY_CHECKS_PASSED", result.stdout)
        self.assertNotIn("FAILED_CHECK", result.stdout)


if __name__ == "__main__":
    unittest.main()
