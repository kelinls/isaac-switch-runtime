import re
import unittest
from pathlib import Path

from tools import build_patches as patches


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class PreGetCollectibleRelayRuntimeTests(unittest.TestCase):
    def extract_function_body(self, source, signature):
        start = source.find(signature)
        self.assertNotEqual(start, -1, signature)
        opening = source.find("{", start)
        self.assertNotEqual(opening, -1, signature)
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:index + 1]
        self.fail(f"unterminated function: {signature}")

    def test_runtime_constants_match_the_only_version_locked_relay_payload(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        for name, expected in (
            ("kPreGetCollectibleRelayOffset", patches.PRE_GET_COLLECTIBLE_RELAY_TARGET_OFFSET),
            ("kPreGetCollectibleRelayCodeOffset", patches.PRE_GET_COLLECTIBLE_RELAY_CODE_OFFSET),
            ("kPreGetCollectibleRelaySlotOffset", patches.PRE_GET_COLLECTIBLE_RELAY_SLOT_OFFSET),
        ):
            match = re.search(rf"{name} = (0x[0-9A-Fa-f]+);", constants)
            self.assertIsNotNone(match, name)
            self.assertEqual(int(match.group(1), 16), expected)
        relay = re.search(r"kPreGetCollectibleRelayExpectedBytes = \{(.*?)\n\};", constants, re.DOTALL)
        self.assertIsNotNone(relay)
        runtime_bytes = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", relay.group(1)))
        self.assertEqual(runtime_bytes, patches.PRE_GET_COLLECTIBLE_RELAY_CODE)

    def test_hook_manager_verifies_and_publishes_the_runtime_bridge_into_the_relay_slot(self):
        header = (SOURCE / "hook_manager.hpp").read_text(encoding="utf-8")
        source = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        self.assertIn("PreGetCollectibleRelayInstallResult", header)
        self.assertIn("TryInstallPreGetCollectibleRelay", header)
        install = self.extract_function_body(
            source, "PreGetCollectibleRelayInstallResult TryInstallPreGetCollectibleRelay")
        for required in ("VerifyPreGetCollectibleRelay", "LuaRuntime::DispatchPreGetCollectible"):
            self.assertIn(required, install)
        verify = self.extract_function_body(source, "bool VerifyPreGetCollectibleRelay")
        for required in (
            "kPreGetCollectibleRelayExpectedEntry", "kPreGetCollectibleRelayExpectedBytes",
            "kPreGetCollectibleRelaySlotOffset",
        ):
            self.assertIn(required, verify)
        publish = self.extract_function_body(
            source, "PreGetCollectibleRelayInstallResult PublishPreGetCollectibleRelayCallback")
        for required in ("__atomic_load_n", "__atomic_store_n", "exl::util::RwPages", "slotPages.Flush()"):
            self.assertIn(required, publish)

    def test_default_runtime_installs_required_relays_before_arming_the_manifest(self):
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        worker = self.extract_function_body(entry, "void ModuleWorker(void*)")
        self.assertIn("TryInstallPreGetCollectibleRelay(*scan.module)", worker)
        self.assertRegex(
            worker,
            r"TryInstallManagerRenderHook\(\*scan\.module\)[\s\S]*"
            r"TryInstallPreGetCollectibleRelay\(\*scan\.module\)[\s\S]*"
            r"TryInstallDefaultManifestMod\(\*scan\.module\)",
        )

    def test_default_deployment_tree_contains_the_pre_get_collectible_ips(self):
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_PRE_GET_COLLECTIBLE_RELAY_IPS", makefile)
        self.assertIn("--kind pre-get-collectible-relay", makefile)
        self.assertIn("isaac-repentance-pre-get-collectible-relay", makefile)


if __name__ == "__main__":
    unittest.main()
