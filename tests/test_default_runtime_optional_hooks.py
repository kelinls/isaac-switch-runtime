from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENTRY = ROOT / "runtime" / "source" / "runtime_entry.cpp"


class DefaultRuntimeOptionalHooksTest(unittest.TestCase):
    def test_default_manifest_install_does_not_exit_when_optional_hooks_are_missing(self):
        source = RUNTIME_ENTRY.read_text()
        start = source.index("#if !defined(EXL_DIAGNOSTIC_STAGE)\n            // Render and collectible interception")
        end = source.index("            const DefaultManifestInstallResult install", start)
        block = source[start:end]
        self.assertIn("TryInstallManagerRenderHook", block)
        self.assertIn("TryInstallPreGetCollectibleRelay", block)
        self.assertNotIn("svcExitThread();", block)


if __name__ == "__main__":
    unittest.main()
