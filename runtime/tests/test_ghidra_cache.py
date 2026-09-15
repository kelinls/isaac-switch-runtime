"""Ghidra 导出缓存包装器的门禁（**不需要设备，也不需要真的 Ghidra**）。

为什么要它：门禁里有 30+ 个模块各自跑一次 Ghidra 无头分析（合计 164 秒、且因为共用同一个
工程只能串行），缓存把其中绝大多数变成 0.05 秒。但缓存一旦"该失效而没失效"，门禁就会拿
**旧结论**当新证据 —— 这正是本项目最怕的假绿。所以要钉住：

1. **命中真的不跑**（用一个假的 analyzeHeadless 计数）；
2. **输出路径不进键**（每个模块都用临时目录，写进去就永远不命中）；
3. **脚本变了 / 工程持久状态变了 ⇒ 键必须变**；
4. **会话缓冲（`*.gbf`）不许进键**（Ghidra 每次开工程都新建一个 147 MB 的、名字还换，
   实测按 mtime/size 做键会次次不命中）；
5. 找不到真身时退出码 77、且**不许假装成功**。
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "tools" / "ghidra_cached.py"
#: 模块名不在本文件里写成字面量：`tools/run_tests.py` 按"文件里有没有这个字符串常量"判断
#: 模块会不会用 Ghidra，写死了本模块就会被塞进串行批次（多花时间，且没有意义）。
WRAPPER_NAME = "ghidra" + "_cached.py"

#: 假的 analyzeHeadless：把每次调用记进计数文件，并写出脚本要求的输出文件。
FAKE_HEADLESS = """#!/usr/bin/env python3
import pathlib, sys
counter = pathlib.Path(sys.argv[0]).with_suffix('.count')
counter.write_text(str(int(counter.read_text()) + 1) if counter.is_file() else '1')
output = pathlib.Path(sys.argv[-1])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text('{"schema_version": "fake", "invocations": ' + counter.read_text() + '}')
print('FAKE-HEADLESS-RAN')
"""


def load_wrapper():
    spec = importlib.util.spec_from_file_location("ghidra_cached_gate", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GhidraCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = load_wrapper()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="isaac-ghidra-gate-")
        self.root = Path(self._temporary.name)
        self.cache = self.root / "cache"
        # 伪造的工程：`<name>.rep` 下有一个"持久状态"文件与一个会话缓冲
        self.project_dir = self.root / "ghidra"
        rep = self.project_dir / "isaac-switch.rep"
        (rep / "idata" / "00").mkdir(parents=True)
        (rep / "project.prp").write_text("persistent-state-1", encoding="utf-8")
        (rep / "idata" / "00" / "00000000.prp").write_text("program-props-1", encoding="utf-8")
        # 会话缓冲：每次运行都会新建、名字与内容都不同 ⇒ 不许进键
        self.session_buffer = rep / "idata" / "00" / "db.0001.gbf"
        self.session_buffer.write_bytes(b"session-buffer-1")
        # 伪造的导出脚本
        self.script_dir = self.root / "scripts"
        self.script_dir.mkdir()
        self.script = self.script_dir / "ExportFake.java"
        self.script.write_text("// fake exporter v1\n", encoding="utf-8")
        # 假的 analyzeHeadless
        self.headless = self.root / ("analyze" + "Headless")
        self.headless.write_text(FAKE_HEADLESS, encoding="utf-8")
        self.headless.chmod(0o755)

    def tearDown(self):
        self._temporary.cleanup()

    def environment(self):
        environment = dict(os.environ)
        environment["ISAAC_GHIDRA_HEADLESS"] = str(self.headless)
        environment["ISAAC_GHIDRA_CACHE"] = str(self.cache)
        environment.pop("ISAAC_GHIDRA_NO_CACHE", None)
        return environment

    def invocations(self) -> int:
        counter = self.headless.with_suffix(".count")
        return int(counter.read_text()) if counter.is_file() else 0

    def run_wrapper(self, output: Path, script: Path | None = None,
                    environment: dict | None = None):
        script = script or self.script
        argv = [
            sys.executable, str(WRAPPER), str(self.project_dir), "isaac-switch",
            "-process", "Repentance.nro", "-noanalysis",
            "-scriptPath", str(script.parent), "-postScript", script.name, str(output),
        ]
        return subprocess.run(argv, text=True, capture_output=True,
                              env=environment or self.environment(), cwd=str(ROOT))

    def test_second_call_reuses_the_cached_export(self):
        first_output = self.root / "run1" / "out.json"
        second_output = self.root / "run2" / "out.json"
        first = self.run_wrapper(first_output)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.invocations(), 1)
        self.assertIn("FAKE-HEADLESS-RAN", first.stdout)
        self.assertEqual(first_output.read_text(), '{"schema_version": "fake", "invocations": 1}')

        second = self.run_wrapper(second_output)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.invocations(), 1, "第二次仍然跑了真身 ⇒ 缓存没生效")
        # 命中时也要：还原输出文件 + 回放上次的输出流（调用方对 stdout 的断言照旧成立）
        self.assertEqual(second_output.read_text(), first_output.read_text())
        self.assertIn("FAKE-HEADLESS-RAN", second.stdout)

    def test_output_path_is_not_part_of_the_key(self):
        """输出路径每次都是新的临时目录 —— 它要是进了键，缓存永远不会命中。"""
        self.run_wrapper(self.root / "a" / "out.json")
        self.run_wrapper(self.root / "b" / "out.json")
        self.assertEqual(self.invocations(), 1)

    def test_session_buffer_does_not_invalidate(self):
        """`*.gbf` 是每次会话新建的缓冲（名字与内容都变）⇒ 不许进键。"""
        self.run_wrapper(self.root / "a" / "out.json")
        self.session_buffer.unlink()
        (self.session_buffer.parent / "db.0002.gbf").write_bytes(b"session-buffer-2")
        self.run_wrapper(self.root / "b" / "out.json")
        self.assertEqual(self.invocations(), 1, "会话缓冲把缓存冲掉了 ⇒ 键里混进了易变文件")

    def test_cached_output_lands_on_the_requested_path(self):
        """命中时要保证**调用方要的那个文件名**存在。

        键里不含输出路径（每个模块都用临时目录），所以"上次输出叫什么名字"可能与这次不同。
        实测踩过：缓存里叫 `out.json`、调用方要 `remaining-getters.json` ⇒ 命中后调用方
        读不到文件、报 FileNotFoundError，看起来像 Ghidra 坏了。
        """
        self.run_wrapper(self.root / "first" / "one-name.json")
        second = self.root / "second" / "another-name.json"
        result = self.run_wrapper(second)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.invocations(), 1, "第二次不该再跑真身")
        self.assertTrue(second.is_file(), "命中后没有把输出落到调用方要求的路径上")
        self.assertIn('"schema_version": "fake"', second.read_text())

    def test_script_change_invalidates(self):
        self.run_wrapper(self.root / "a" / "out.json")
        self.script.write_text("// fake exporter v2 —— 判据改了\n", encoding="utf-8")
        self.run_wrapper(self.root / "b" / "out.json")
        self.assertEqual(self.invocations(), 2, "脚本改了却用旧结果 ⇒ 门禁会拿旧结论当新证据")

    def test_project_state_change_invalidates(self):
        self.run_wrapper(self.root / "a" / "out.json")
        (self.project_dir / "isaac-switch.rep" / "project.prp").write_text(
            "persistent-state-2", encoding="utf-8")
        self.run_wrapper(self.root / "b" / "out.json")
        self.assertEqual(self.invocations(), 2, "工程持久状态变了却命中缓存")

    def test_missing_headless_exits_77_instead_of_pretending(self):
        environment = self.environment()
        environment["ISAAC_GHIDRA_HEADLESS"] = str(self.root / "does-not-exist")
        result = self.run_wrapper(self.root / "a" / "out.json", environment=environment)
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
        self.assertIn("analyze" + "Headless", result.stderr)

    def test_no_cache_escape_hatch_always_runs_the_real_one(self):
        """就地把分析重跑过、又不想改脚本时用这个逃生口。"""
        environment = self.environment()
        environment["ISAAC_GHIDRA_NO_CACHE"] = "1"
        self.run_wrapper(self.root / "a" / "out.json", environment=environment)
        self.run_wrapper(self.root / "b" / "out.json", environment=environment)
        self.assertEqual(self.invocations(), 2)

    def test_failed_export_is_not_cached(self):
        """真身失败（脚本报错等）不许固化进缓存 —— 否则偶发失败会被当成稳定结论。"""
        failing = self.root / ("analyze" + "Headless-fail")
        failing.write_text("#!/usr/bin/env python3\nimport sys\nprint('BOOM', file=sys.stderr)\n"
                           "sys.exit(3)\n", encoding="utf-8")
        failing.chmod(0o755)
        environment = self.environment()
        environment["ISAAC_GHIDRA_HEADLESS"] = str(failing)
        first = self.run_wrapper(self.root / "a" / "out.json", environment=environment)
        second = self.run_wrapper(self.root / "b" / "out.json", environment=environment)
        self.assertEqual((first.returncode, second.returncode), (3, 3))


if __name__ == "__main__":
    unittest.main()
