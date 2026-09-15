import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class FakeFtp:
    def __init__(self, files, directories=()):
        self.files = dict(files)
        self.directories = set(directories)
        self.uploads = []
        self.commands = []

    def size(self, path):
        return len(self.files[path])

    def retrbinary(self, command, callback):
        callback(self.files[command.split(" ", 1)[1]])

    def storbinary(self, command, source):
        path = command.split(" ", 1)[1]
        self.commands.append(f"STOR {path}")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent and parent not in self.directories:
            # Same behaviour as the device server: no implicit parent creation.
            raise OSError(f"553 No such file or directory: {path}")
        self.files[path] = source.read()
        self.uploads.append(path)

    def mkd(self, path):
        self.commands.append(f"MKD {path}")
        if path in self.directories:
            raise OSError(f"550 {path}: directory already exists")
        self.directories.add(path)
        return f'257 "{path}" created'


class VerifyTestPackageFtpTests(unittest.TestCase):
    def test_manifest_writer_records_mode_sizes_hashes_and_module_id(self):
        with tempfile.TemporaryDirectory(prefix="manifest-v2-") as temporary:
            root = Path(temporary)
            runtime = root / "atmosphere/contents/010021C000B6A000/exefs/subsdk9"
            runtime.parent.mkdir(parents=True)
            data = bytearray(0x70)
            data[0x40:0x60] = bytes(range(0x20))
            runtime.write_bytes(data)
            result = __import__("subprocess").run(
                ["python3", str(ROOT / "tools/write_test_package_manifest.py"),
                 "--root", str(root), "--build-id", "7", "--stage", "145",
                 "--mode", "stage145-persistence-event"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((root / "test-build-manifest.json").read_text())
            self.assertEqual(manifest["protocol"], 2)
            self.assertEqual(manifest["mode"], "stage145-persistence-event")
            self.assertEqual(manifest["runtime_module_id"], data[0x40:0x60].hex().upper())
            self.assertEqual(manifest["files"][0]["size"], len(data))
            self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(data).hexdigest())

    def test_ftp_uploads_only_mismatches_and_reads_back_every_declared_file(self):
        from tools.verify_test_package_ftp import verify_package

        with tempfile.TemporaryDirectory(prefix="ftp-v2-") as temporary:
            root = Path(temporary)
            local = root / "atmosphere/file.bin"
            local.parent.mkdir(parents=True)
            local.write_bytes(b"new")
            manifest = {"files": [{"path": "atmosphere/file.bin", "size": 3,
                                   "sha256": hashlib.sha256(b"new").hexdigest()}]}
            ftp = FakeFtp({"atmosphere/file.bin": b"old"})
            result = verify_package(ftp, root, manifest, root / "readback")
            self.assertEqual(result["uploaded"], ["atmosphere/file.bin"])
            self.assertEqual(result["verified"], ["atmosphere/file.bin"])
            self.assertEqual((root / "readback/atmosphere/file.bin").read_bytes(), b"new")
            self.assertFalse(any("delete" in call.lower() or "rename" in call.lower()
                                 for call in getattr(ftp, "commands", [])))

    def test_ftp_creates_missing_parent_directories_before_uploading(self):
        """A brand new Mod directory must be created first.

        The device server answers ``553 No such file or directory`` for the first file of a
        directory that does not exist yet (observed 2026-09-13 while deploying `Stage149DrawTest`),
        and ``MKD`` on an existing directory is an error, so parents are created and failures
        ignored.
        """
        from tools.verify_test_package_ftp import verify_package

        with tempfile.TemporaryDirectory(prefix="ftp-mkd-") as temporary:
            root = Path(temporary)
            relative = "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/mods/NewMod/main.lua"
            local = root / relative
            local.parent.mkdir(parents=True)
            local.write_bytes(b"return 1\n")
            manifest = {"files": [{"path": relative, "size": 9,
                                   "sha256": hashlib.sha256(b"return 1\n").hexdigest()}]}
            # The device already holds the mods root from earlier rounds, but not `NewMod`.
            existing = "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/mods"
            ftp = FakeFtp({}, directories=[existing])
            result = verify_package(ftp, root, manifest, root / "readback")

            self.assertEqual(result["uploaded"], [relative])
            self.assertEqual(result["verified"], [relative])
            self.assertEqual((root / "readback" / relative).read_bytes(), b"return 1\n")
            self.assertIn(f"MKD {existing}/NewMod", ftp.commands)
            self.assertLess(ftp.commands.index(f"MKD {existing}/NewMod"),
                            ftp.commands.index(f"STOR {relative}"))
            # Existing directories are re-created blindly and their errors swallowed.
            self.assertIn("MKD atmosphere", ftp.commands)

    def test_ftp_source_has_no_destructive_commands(self):
        source = (ROOT / "tools/verify_test_package_ftp.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("delete", source)
        self.assertNotIn("rename", source)


if __name__ == "__main__":
    unittest.main()
