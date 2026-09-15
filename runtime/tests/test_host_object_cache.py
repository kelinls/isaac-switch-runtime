"""宿主夹具"目标文件缓存"与"并行跑模块"的门禁（**不需要设备**）。

## 为什么要给缓存配门禁

缓存一旦"该失效而没失效"，门禁就会**拿旧目标文件跑新代码** ⇒ 给出**假绿** ——
比慢得多的危害大得多。所以要钉住四件事：

1. **重复构建真的复用**（否则提速是假的）；
2. **源文件改了、旗标改了、头文件改了，缓存必须失效**；
3. 缓存目录**不在仓库里**（在仓库里会被当成未跟踪产物，还可能被发布脚本带上公开仓库 ——
   项目在 2026-09-15 已经栽过一次同类事故）；
4. 并行跑的模块分组判据要准（判错要么白丢并行度，要么让 docker 用例互相抢资源）。
"""

import importlib.util
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUPPORT = ROOT / "runtime" / "tests" / "test_support.py"
RUN_TESTS = ROOT / "tools" / "run_tests.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompilerSpy:
    """记下真正发生的编译调用（命令行带 `-c`），用来证明"缓存命中时没有重新编译"。"""

    def __init__(self):
        self.compiles: list[list[str]] = []
        self._original = subprocess.run

    def __enter__(self):
        outer = self

        def spy(command, *args, **kwargs):
            if isinstance(command, (list, tuple)) and "-c" in command:
                outer.compiles.append(list(command))
            return outer._original(command, *args, **kwargs)

        subprocess.run = spy
        return self

    def __exit__(self, *_exc):
        subprocess.run = self._original
        return False


class ObjectCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.support = load_module("test_support_cache_gate", SUPPORT)
        cls.source_root = ROOT / "runtime" / "source"
        cls.harness = "int main() { return 0; }\n"

    def test_repeat_build_reuses_cached_objects(self):
        """第二次构建同一个夹具：**一次编译都不该发生**（只剩链接）。"""
        with tempfile.TemporaryDirectory(prefix="isaac-cache-") as temporary:
            workdir = Path(temporary)
            self.support.build_lua_harness(          # 先把这批目标文件喂进缓存
                source_root=self.source_root, workdir=workdir, harness_source=self.harness)
            with CompilerSpy() as spy:
                binary = self.support.build_lua_harness(
                    source_root=self.source_root, workdir=workdir, harness_source=self.harness)
            self.assertTrue(binary.is_file())
            self.assertEqual(spy.compiles, [],
                             f"缓存命中时仍在编译：{spy.compiles[:1]}")

    def test_cached_binary_still_runs_and_behaves(self):
        """缓存路径下编出来的二进制必须**真的能跑、行为不变**（防"复用出一个坏链接"）。"""
        source = '#include <cstdio>\nint main() { std::printf("cache-ok\\n"); return 0; }\n'
        with tempfile.TemporaryDirectory(prefix="isaac-cache-") as temporary:
            binary = self.support.build_lua_harness(
                source_root=self.source_root, workdir=Path(temporary), harness_source=source)
            first = subprocess.run([str(binary)], text=True, capture_output=True)
            second = subprocess.run([str(binary)], text=True, capture_output=True)
        self.assertEqual(first.stdout, "cache-ok\n", first.stderr)
        self.assertEqual(second.stdout, "cache-ok\n", second.stderr)

    def test_source_change_invalidates(self):
        """源文件内容变了 ⇒ 键必须变（否则就是拿旧 `.o` 跑新代码）。"""
        with tempfile.TemporaryDirectory(prefix="isaac-key-") as temporary:
            source = Path(temporary) / "a.cpp"
            source.write_text("int a() { return 1; }\n", encoding="utf-8")
            before = self.support.object_cache_key(
                source=source, tool="c++", flags=("-O0",), includes=("/tmp",),
                header_fingerprint="fp")
            source.write_text("int a() { return 2; }\n", encoding="utf-8")
            after = self.support.object_cache_key(
                source=source, tool="c++", flags=("-O0",), includes=("/tmp",),
                header_fingerprint="fp")
        self.assertNotEqual(before, after)

    def test_flag_include_and_compiler_change_invalidate(self):
        with tempfile.TemporaryDirectory(prefix="isaac-key-") as temporary:
            source = Path(temporary) / "a.cpp"
            source.write_text("int a() { return 1; }\n", encoding="utf-8")
            keys = {
                self.support.object_cache_key(
                    source=source, tool="c++", flags=("-O0",), includes=("/tmp",),
                    header_fingerprint="fp"),
                self.support.object_cache_key(
                    source=source, tool="c++", flags=("-O2",), includes=("/tmp",),
                    header_fingerprint="fp"),
                self.support.object_cache_key(
                    source=source, tool="c++", flags=("-O0",), includes=("/tmp", "/usr/include"),
                    header_fingerprint="fp"),
                self.support.object_cache_key(
                    source=source, tool="cc", flags=("-O0",), includes=("/tmp",),
                    header_fingerprint="fp"),
            }
        self.assertEqual(len(keys), 4)

    def test_header_change_invalidates_everything(self):
        """头文件一变，整批缓存失效 —— 这一条是"绝不假绿"的关键。

        用一棵**仿造的 runtime 树**来验（改真树会干扰并行跑的其他用例）：
        `source_root`、`source_root.parent/src`、vendored Lua 三个位置都要算进指纹，
        而且名字要按相对路径算（指纹表达"树的内容"，不是"树在磁盘哪个位置"）。
        """
        with tempfile.TemporaryDirectory(prefix="isaac-tree-") as temporary:
            root = Path(temporary) / "runtime" / "source"
            (root / "third_party" / "lua-5.3.3" / "src").mkdir(parents=True)
            (root.parent / "src").mkdir(parents=True)
            (root / "layout.hpp").write_text("#pragma once\nconstexpr int kOffset = 0x18;\n",
                                             encoding="utf-8")
            (root / "third_party" / "lua-5.3.3" / "src" / "lua.h").write_text(
                "#pragma once\n", encoding="utf-8")
            unit_header = root.parent / "src" / "api.hpp"
            unit_header.write_text("#pragma once\n", encoding="utf-8")

            first = self.support.runtime_header_fingerprint(root)
            self.assertEqual(first, self.support.runtime_header_fingerprint(root),
                             "同进程内重复计算应当命中进程内缓存")

            unit_header.write_text("#pragma once\n// 改了内容\n", encoding="utf-8")
            self.support._HEADER_FINGERPRINT_CACHE.clear()
            self.assertNotEqual(first, self.support.runtime_header_fingerprint(root),
                                "改了 runtime/src 下的头文件，指纹没变 ⇒ 会拿旧 .o 跑新代码")

    def test_relative_header_names_are_used_not_absolute_paths(self):
        """两棵**内容相同、位置不同**的树，指纹必须相同（指纹是内容寻址的）。"""
        fingerprints = []
        for name in ("tree-a", "tree-b"):
            with tempfile.TemporaryDirectory(prefix=f"isaac-{name}-") as temporary:
                root = Path(temporary) / "runtime" / "source"
                root.mkdir(parents=True)
                (root / "layout.hpp").write_text("#pragma once\n", encoding="utf-8")
                fingerprints.append(self.support.runtime_header_fingerprint(root))
                self.support._HEADER_FINGERPRINT_CACHE.clear()
        self.assertEqual(fingerprints[0], fingerprints[1])

    def test_cache_lives_outside_the_repository(self):
        """缓存目录不许落在仓库里（否则会被发布脚本当成未跟踪产物带上公开仓库）。"""
        root = self.support.host_object_cache_root().resolve()
        repository = ROOT.resolve()
        self.assertFalse(str(root).startswith(str(repository)),
                         f"缓存目录 {root} 在仓库 {repository} 里")

    def test_lua_objects_are_shared_on_disk(self):
        """Lua 的目标文件要落在**磁盘缓存**里（跨进程共享）——
        否则并行跑模块时，每个模块进程都要重编这 7 个 `.c`（实测每个 ~1.2 s）。"""
        with tempfile.TemporaryDirectory(prefix="isaac-lua-") as temporary:
            objects = self.support.lua_harness_objects(self.source_root, Path(temporary))
        self.assertTrue(objects, "没有编出任何 Lua 目标文件")
        cache_root = self.support.host_object_cache_root().resolve()
        for path in objects:
            self.assertTrue(path.is_file() and path.stat().st_size > 0, path)
            self.assertTrue(str(path.resolve()).startswith(str(cache_root)),
                            f"{path} 不在磁盘缓存目录里 ⇒ 跨进程无法共享")

    def test_warm_build_is_much_faster_than_a_cold_one(self):
        """提速必须**可观测**：温缓存的一次构建要明显快于冷缓存。

        阈值放宽到 1/3 是为了不把测量噪声当回归：这条只防"缓存其实没生效"这类结构性错误。
        """
        source = "int main() { return 0; }\n"
        original_root = self.support.host_object_cache_root
        with tempfile.TemporaryDirectory(prefix="isaac-cold-") as cold, \
                tempfile.TemporaryDirectory(prefix="isaac-warm-") as warm:
            self.support.host_object_cache_root = lambda: Path(cold) / "objects"
            try:
                started = time.time()
                self.support.build_lua_harness(
                    source_root=self.source_root, workdir=Path(cold), harness_source=source)
                cold_seconds = time.time() - started
            finally:
                self.support.host_object_cache_root = original_root

            self.support.host_object_cache_root = lambda: Path(warm) / "objects"
            try:
                self.support.build_lua_harness(          # 先把 warm 那份缓存喂热
                    source_root=self.source_root, workdir=Path(warm), harness_source=source)
                started = time.time()
                self.support.build_lua_harness(
                    source_root=self.source_root, workdir=Path(warm), harness_source=source)
                warm_seconds = time.time() - started
            finally:
                self.support.host_object_cache_root = original_root

        self.assertLess(warm_seconds, cold_seconds / 3,
                        f"温缓存 {warm_seconds:.2f}s 相对冷缓存 {cold_seconds:.2f}s 没有明显提速")


class ParallelRunnerTests(unittest.TestCase):
    """并行脚本：分组判据与摘要格式（摘要格式还要被 stage10 那条用例解析）。"""

    @classmethod
    def setUpClass(cls):
        cls.runner = load_module("run_tests_gate", RUN_TESTS)

    def test_docker_modules_are_detected_by_real_usage_not_by_comment(self):
        """判据必须是"代码里真的调容器"，而不是"文件里出现过 docker 这个词"。

        实测过两头的错：按词匹配会把 12 个模块划成独占（其中 10 个只是注释里写了
        "不需要 docker"），白丢并行度；而本文件里那句 `'["docker", "run"]'`
        又会被当成真调用，把这个门禁模块自己误判成独占。
        """
        self.assertTrue(self.runner.module_uses_container('subprocess.run(["docker", "run", "x"])'))
        self.assertTrue(self.runner.module_uses_container("docker_image_available()"))
        self.assertTrue(self.runner.module_uses_container('run_in_toolchain("make")'))
        self.assertTrue(self.runner.module_uses_container("image = DOCKER_IMAGE"))
        self.assertFalse(self.runner.module_uses_container('raise unittest.SkipTest("本用例不需要 docker")'))
        self.assertFalse(self.runner.module_uses_container(
            """self.assertIsNotNone(pattern.search('["docker", "run"]'))"""))

    def test_ghidra_modules_are_exclusive_too(self):
        """Ghidra 模块必须串行：**35 个模块共用同一个 Ghidra 工程**，并发会撞工程锁。

        实测：第一版只把 docker 模块划成独占，结果 22 个 Ghidra 模块同时红
        （`analyzeHeadless` 返回码 1）。
        """
        self.assertTrue(self.runner.module_uses_ghidra(
            'GHIDRA = Path("/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless")'))
        self.assertFalse(self.runner.module_uses_ghidra("x = 1"))
        # 两个判据都要汇总到同一个入口上，别漏掉任何一个
        self.assertTrue(self.runner.module_needs_exclusive_resource('subprocess.run(["docker"])'))
        self.assertTrue(self.runner.module_needs_exclusive_resource('G = "analyzeHeadless"'))

    def test_discovery_splits_modules_into_two_groups(self):
        parallel, exclusive = self.runner.discover_modules()
        self.assertGreater(len(parallel), 100, "并行批次太少了，判据可能过严")
        self.assertIn("runtime.tests.test_architecture_layout", exclusive)
        self.assertNotIn("runtime.tests.test_architecture_layout", parallel)
        self.assertEqual(set(parallel) & set(exclusive), set())

    def test_summary_lines_stay_parseable_by_the_artifact_isolation_test(self):
        """`test_game_file_reader_stage10` 的内层两遍会解析摘要行，格式不能变。

        它要的是两件事：`Ran <N> tests in ` 里的数量，以及单独一行的 `OK`/`OK (skipped=N)`。
        这条门禁把格式钉住 —— 否则改了输出就会把那条用例搞红，而红的原因还很难看出来。
        """
        import re

        ran = re.search(r"Ran (\d+) tests? in ", "Ran 870 tests in 12.345s（156 个模块，8 并发）")
        self.assertIsNotNone(ran, "`Ran N tests in` 这行被改坏了")
        self.assertEqual(ran.group(1), "870")
        status = re.search(r"^(OK|FAILED)(?: \([^\n]+\))?$", "OK (skipped=21)", re.M)
        self.assertIsNotNone(status, "`OK (skipped=N)` 这行被改坏了")

    def test_runner_and_legacy_discovery_agree_on_the_module_set(self):
        """并行脚本发现的模块集合必须与 `unittest discover` 一致（不能漏跑/多跑）。"""
        import unittest as unittest_module

        discovered = {
            test.__class__.__module__
            for suite in (unittest_module.TestLoader().discover(
                str(ROOT / "runtime" / "tests"), top_level_dir=str(ROOT)),)
            for test in _flatten(suite)
        }
        parallel, exclusive = self.runner.discover_modules()
        self.assertEqual(discovered, set(parallel) | set(exclusive))


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


if __name__ == "__main__":
    unittest.main()
