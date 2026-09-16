import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
RUNTIME_MOD = ROOT / "runtime" / "pc-mods" / "MuteOnPause"
MUTE_ON_PAUSE_SHA256 = {
    "main.lua": "2fce2d7846ea485d06a0c23288f82d62f21d388ebe98dcdb74ca543bba752237",
    "metadata.xml": "a09b124f7652936007ef5f892165052c4bd702caf59c3aad10c2bd947f94cf23",
    "src/metadata.lua": "35f3c0f7ba2e6db93c9c6f7149b4bf55a23e3d2db0a5df54911ab0ae6bfc957d",
    "src/mod.lua": "0c905b211770babc888f401934509fc157f916fd1d2dd3fc2054784271fae59b",
}


class DefaultManifestLoaderTests(unittest.TestCase):
    def test_default_loader_uses_manifest_on_manager_update_without_diagnostic_exit(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")

        self.assertIn("TryInstallDefaultManifestMod", hook)
        self.assertIn("InitializeDefaultManifestMod", hook)
        # The Lua entry call lives in the service path that the initializer now
        # always delegates to (the non-layered branch was deleted).
        self.assertIn("LoadDefaultManifestModThroughService", hook)
        self.assertIn("isaac::runtime::ModLoadService service", hook)
        self.assertIn("TryInstallDefaultManifestMod(*scan.module)", entry)
        default_block = hook[
            hook.index("bool InitializeDefaultManifestMod(u32* failureDetail)"):
            hook.index("bool PrimeDefaultCallbacks")
        ]
        self.assertNotIn("svcBreak", default_block)
        self.assertNotIn("svcExitProcess", default_block)

    def test_default_preprocessing_defines_the_manifest_installer(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        preprocessed = subprocess.run(
            ["c++", "-std=c++17", "-E", "-P", "-x", "c++", "-"],
            input=re.sub(r"^#include.*$", "", hook, flags=re.MULTILINE),
            text=True,
            capture_output=True,
        )
        self.assertEqual(preprocessed.returncode, 0, preprocessed.stdout + preprocessed.stderr)
        self.assertIn(
            "DefaultManifestInstallResult TryInstallDefaultManifestMod(const TargetModule& module)",
            preprocessed.stdout,
        )
        self.assertGreater(
            hook.index("DefaultManifestInstallResult TryInstallDefaultManifestMod"),
            hook.index("} // namespace"),
        )

    def test_default_package_contains_unmodified_mute_on_pause_sources(self):
        for relative, expected_sha256 in MUTE_ON_PAUSE_SHA256.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    hashlib.sha256((RUNTIME_MOD / relative).read_bytes()).hexdigest(),
                    expected_sha256,
                )

    def test_default_make_build_generates_manifest_and_both_manager_relays(self):
        with tempfile.TemporaryDirectory(prefix="isaac-default-manifest-build-") as temporary:
            output = Path(temporary) / "romfs-output"
            build = subprocess.run(
                ["python3", "-m", "tools.inspect_pc_mod", "--mods-root", "runtime/pc-mods", "--romfs-output", str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertTrue((output / "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/manifest.json").is_file())
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")
        self.assertIn("default-pc-mods", makefile)
        self.assertIn("DEPLOY_RENDER_RELAY_IPS", makefile)

    def test_a_missing_manifest_falls_back_to_auto_discovery(self):
        """清单是**可选项**：没有/读不出/解析失败时，运行时自己列 `isaac_mods/mods`（方案 A）。

        这条按源码钉住三件事（自动发现本身的行为由
        `runtime/tests/integration/test_mod_discovery.py` 覆盖）：
        1. 加载路径里确实挂了自动发现这条兜底；
        2. 兜底**只**在"清单不可用"（读取或解析失败）时生效 —— 路径拼装失败说明清单本身有问题，
           那种情况下静默改用扫描会把用户写的清单悄悄忽略掉；
        3. 用自动发现装载时诊断字是 6（`Discovered`），与"清单点名加载"区分得开。
        """
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        self.assertIn("mod_discovery_service.hpp", hook)
        self.assertIn("g_ModDiscoveryService.Discover(&batch)", hook)
        self.assertIn("ModLoadStep::ManifestRead", hook)
        self.assertIn("ModLoadStep::ManifestParse", hook)
        self.assertIn("DefaultManifestFailureDetail::Discovered", hook)
        discovery = (ROOT / "runtime" / "src" / "application" / "mod"
                     / "mod_discovery_service.cpp").read_text(encoding="utf-8")
        # 发现出来的路径必须与清单路径用**同一套前缀**，否则同一个模组走两条路会落到不同地方。
        self.assertIn('"isaac_mods/mods"', discovery)
        self.assertIn('"rom:/isaac_mods/mods/"', discovery)
        # 顺序必须确定（引擎给的顺序没有保证，而派发顺序影响游戏行为）。
        self.assertIn("SortNames(", discovery)


if __name__ == "__main__":
    unittest.main()
