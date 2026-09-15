"""重构期的源码目录与构建入口边界测试。

Runtime、SaltyNX 宿主插件和真机探针是三个不同产物，源码集合互不重叠。
本测试只验证边界本身，不验证任何游戏行为：

* 新分层源码根目录存在；
* Makefile 暴露 runtime_module / host_plugin / probe 三个入口；
* 生产 Runtime 入口不会因为命令行残留的探针变量而编译探针产物；
* 宿主插件入口产出 isaac-runtime.elf，而不是 subsdk9；
* 探针入口必须显式选择阶段，否则直接失败。

Makefile 的解析依赖 devkitA64（`common.mk` 需要 `DEVKITPRO`），因此
Makefile 相关的断言全部在 `devkitpro/devkita64` 容器内执行；没有 Docker
或镜像时按 skip 处理，不伪装成通过。
"""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Iterable

from tools.runtime_layout_budget import DEFAULT_CODE_LIMIT, DEFAULT_RO_END_LIMIT


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
DOCKER_IMAGE = "devkitpro/devkita64:latest"

# Symbols the Runtime actually calls. If the linker collects any of them as
# unreachable, the layered build silently lost a whole code path (this already
# happened once when a Lua registration rewrite changed reachability), so the
# build gate below treats a missing symbol as a hard failure.
REQUIRED_LUA_SYMBOLS = (
    "luaL_newstate",
    "lua_close",
    "lua_pcallk",
    "luaL_ref",
    "luaL_unref",
    "lua_setglobal",
    "luaL_newmetatable",
    "lua_rawgeti",
    "luaL_checkudata",
)

LAYERED_AUDIT_COMMAND = (
    "make -C runtime clean >/dev/null 2>&1 && "
    "make -C runtime runtime_module TEST_BUILD_ID=20260910190000 >/dev/null 2>&1 && "
    + " && ".join(
        f'printf "%s=%s\\n" {symbol} "$(aarch64-none-elf-nm -C --defined-only runtime/runtime.elf | grep -cw {symbol} || true)"'
        for symbol in REQUIRED_LUA_SYMBOLS
    )
    + " && aarch64-none-elf-readelf -lW runtime/runtime.elf | awk '$1==\"LOAD\"{print \"LOAD\", $3, $5, $7, $8}'"
    + " && aarch64-none-elf-nm runtime/runtime.elf | awk '$3==\"__code_end__\"{print \"CODE_END\", $1}'"
    # 只读预算单一真源：caps 在 link.ld 的 ASSERT 里，用量与余量由该工具报告（见
    # test_link_ld_budget_matches_tool_defaults 对两边数值的核对）。
    + " && python3 tools/runtime_layout_budget.py --elf runtime/runtime.elf"
)


def docker_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", DOCKER_IMAGE],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def run_in_toolchain(command: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/work",
            "-w",
            "/work",
            DOCKER_IMAGE,
            "sh",
            "-lc",
            f". /opt/devkitpro/devkita64.sh && {command}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_make(arguments: str, build_directories: Iterable[Path],
             flags: str = "-n") -> subprocess.CompletedProcess:
    """Run make after making sure the recursive build directories exist.

    `make -n` prints the `mkdir` it would run, so the recursive
    `make -C <build dir>` that follows would fail on a pristine checkout that
    has never been built. A CI or isolation copy has no `build*` directory, so
    the test creates one and removes it afterwards instead of depending on
    leftover artifacts.
    """
    created: list[Path] = []
    for directory in build_directories:
        if not directory.exists():
            directory.mkdir(parents=True)
            created.append(directory)
    try:
        prefix = f"{flags} " if flags else ""
        return run_in_toolchain(f"make -C runtime {prefix}{arguments}")
    finally:
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                pass


class ArchitectureLayoutTests(unittest.TestCase):
    def test_layered_source_root_directories_exist(self):
        expected = [
            RUNTIME / "src",
            RUNTIME / "probes",
            RUNTIME / "tests" / "unit",
            RUNTIME / "tests" / "integration",
            RUNTIME / "tests" / "contract",
        ]
        for directory in expected:
            with self.subTest(directory=str(directory.relative_to(ROOT))):
                self.assertTrue(directory.is_dir(), f"{directory} 必须存在")

    def test_makefile_declares_three_explicit_entry_points(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        for target in ("runtime_module", "host_plugin", "probe"):
            with self.subTest(target=target):
                self.assertRegex(makefile, rf"(?m)^{target}:$")
        self.assertRegex(makefile, r"(?m)^\.PHONY: runtime_module host_plugin probe$")

        # 生产 Runtime 入口必须把探针/插件变量固定为关闭状态。
        runtime_module_recipe = makefile.split("runtime_module:", 1)[1].split("host_plugin:", 1)[0]
        for pinned in (
            "DIAGNOSTIC_STAGE=",
            "STARTUP_PROBE_STAGE=",
            "SALTYNX_PLUGIN=0",
            "SALTYNX_DIAGNOSTIC_BRIDGE=0",
            "PERSISTENCE_TRACE=0",
            "PERSISTENCE_EVENT_DIAGNOSTIC=0",
            "PERSISTENCE_REQUIRED=0",
        ):
            with self.subTest(pinned=pinned):
                self.assertIn(pinned, runtime_module_recipe)

        # 递归调用必须清除 MAKELEVEL，否则 common.mk 会走构建子目录分支。
        self.assertEqual(runtime_module_recipe.count("env -u MAKELEVEL"), 1)

    def test_production_source_root_is_configurable_and_defaults_to_source(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        common = (RUNTIME / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        self.assertRegex(makefile, r"(?m)^RUNTIME_SOURCE_ROOT \?= source$")
        self.assertIn("ROOT_SOURCE\t:=\t$(TOPDIR)/$(RUNTIME_SOURCE_ROOT)", common)
        self.assertIn("RUNTIME_SOURCE_ROOT := source", common)

    @unittest.skipUnless(docker_image_available(), "需要 devkitpro/devkita64 镜像")
    def test_runtime_module_ignores_probe_and_plugin_requests(self):
        # 命令行故意带上探针阶段：生产入口必须把它清空，落在默认 build/，
        # 且整个 dry run 不得出现探针构建目录或宿主插件产物名。
        result = run_make(
            "runtime_module DIAGNOSTIC_STAGE=13",
            [RUNTIME / "build"],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("-C build -f ../misc/mk/common.mk", result.stdout)
        self.assertNotIn("isaac-runtime.elf", result.stdout)
        self.assertNotIn("build-diagnostic-stage13", result.stdout)

    @unittest.skipUnless(docker_image_available(), "需要 devkitpro/devkita64 镜像")
    def test_host_plugin_builds_the_plugin_artifact(self):
        plugin_build = RUNTIME / "build-saltynx-stage145"
        dry_run = run_make("host_plugin HOST_PLUGIN_STAGE=145", [plugin_build])
        self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
        self.assertIn("-C build-saltynx-stage145 -f ../misc/mk/common.mk", dry_run.stdout)
        self.assertNotIn("subsdk9", dry_run.stdout)

        # 产物名由 Makefile 变量决定，用 -p 数据库确认实际路径。
        result = run_make("host_plugin HOST_PLUGIN_STAGE=145", [plugin_build], flags="-p -n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        outputs = [
            line for line in result.stdout.splitlines()
            if line.startswith("SALTYNX_PLUGIN_OUTPUT")
        ]
        self.assertTrue(outputs, result.stdout)
        for line in outputs:
            with self.subTest(line=line):
                self.assertTrue(line.endswith("isaac-runtime.elf"), line)
                self.assertIn("deploy-saltynx/stage145", line)

    @unittest.skipUnless(docker_image_available(), "需要 devkitpro/devkita64 镜像")
    def test_probe_requires_an_explicit_stage(self):
        refused = run_in_toolchain("make -C runtime probe")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("select a probe explicitly", refused.stdout + refused.stderr)

        selected = run_make("probe DIAGNOSTIC_STAGE=13", [RUNTIME / "build-diagnostic-stage13"])
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        self.assertIn("build-diagnostic-stage13", selected.stdout)

    @unittest.skipUnless(
        os.environ.get("ISAAC_RUNTIME_CONTAINER_BUILD") == "1",
        "设置 ISAAC_RUNTIME_CONTAINER_BUILD=1 以执行容器内 clean build",
    )
    def test_container_clean_build_produces_subsdk9(self):
        result = run_in_toolchain(
            "make -C runtime clean && make -C runtime runtime_module",
            timeout=1800,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = RUNTIME / "deploy/atmosphere/contents/010021C000B6A000/exefs/subsdk9"
        self.assertTrue(artifact.is_file(), f"{artifact} 未生成")
        self.assertGreater(artifact.stat().st_size, 0)

    @unittest.skipUnless(
        os.environ.get("ISAAC_RUNTIME_CONTAINER_BUILD") == "1",
        "设置 ISAAC_RUNTIME_CONTAINER_BUILD=1 以执行容器内 clean build",
    )
    def test_layered_build_keeps_lua_symbols_and_layout(self):
        result = run_in_toolchain(LAYERED_AUDIT_COMMAND, timeout=1800)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for symbol in REQUIRED_LUA_SYMBOLS:
            with self.subTest(symbol=symbol):
                self.assertRegex(
                    result.stdout,
                    rf"(?m)^{re.escape(symbol)}=[1-9]",
                    f"{symbol} 被链接器当作不可达代码回收，分层构建丢失了 Lua 调用路径",
                )

        loads = [
            line.split()
            for line in result.stdout.splitlines()
            if line.startswith("LOAD ")
        ]
        self.assertEqual(len(loads), 3, result.stdout)
        rx_vaddr, rx_filesz = int(loads[0][1], 16), int(loads[0][2], 16)
        self.assertEqual(rx_vaddr, 0)
        ro_vaddr, ro_filesz = int(loads[1][1], 16), int(loads[1][2], 16)
        rw_vaddr = int(loads[2][1], 16)
        # 段之间不得出现未映射空洞：RX 段的映射范围必须一直延伸到只读段起点。
        # exlaunch 的 FindModules() 按 CodeStatic+Rx -> CodeStatic+R -> CodeMutable+Rw
        # 的连续区间识别模块，中间插入未映射页会让状态机复位、Runtime 找不到自己
        # （2026-09-10 真机 Data Abort，栈上是 mem_layout 的 "ModuleIdx < ModuleIndex::End"）。
        rx_mapped_end = rx_vaddr + ((rx_filesz + 0xFFF) & ~0xFFF)
        self.assertEqual(
            rx_mapped_end,
            ro_vaddr,
            "RX 段与只读段之间存在未映射空洞，exlaunch 模块扫描会失败",
        )
        ro_mapped_end = ro_vaddr + ((ro_filesz + 0xFFF) & ~0xFFF)
        self.assertEqual(ro_mapped_end, rw_vaddr, "只读段与读写段之间存在未映射空洞")
        code_ends = [
            line.split()[1]
            for line in result.stdout.splitlines()
            if line.startswith("CODE_END ")
        ]
        self.assertEqual(len(code_ends), 1, result.stdout)
        # 只读预算账本：caps 与结构不变量由 tools/runtime_layout_budget.py 统一判定
        # （link.ld 的 ASSERT 是硬闸门，该工具是同一套数值的报告与核对入口）。
        # 旧口径是“代码结束必须低于 0x54000、只读段结束必须低于 0x75000”，那两个数
        # 是某次真机验证过的历史地址，自 806adbd 起段地址已按体积推导，2026-09-12 换成
        # 失控增长闸门（512 KiB / 1 MiB），不再用历史地址卡功能增长。
        budgets = [
            line.split()
            for line in result.stdout.splitlines()
            if line.startswith("BUDGET ")
        ]
        self.assertEqual(len(budgets), 1, result.stdout)
        fields = dict(item.split("=", 1) for item in budgets[0][1:])
        self.assertEqual(fields["ok"], "yes", result.stdout)
        self.assertEqual(fields["loads"], "3", result.stdout)
        code_end = int(fields["code_end"], 16)
        ro_end = int(fields["ro_end"], 16)
        self.assertEqual(code_end, int(code_ends[0], 16), "工具与 nm 的代码结束位置不一致")
        self.assertLessEqual(code_end, DEFAULT_CODE_LIMIT)
        self.assertLessEqual(ro_end, DEFAULT_RO_END_LIMIT)
        print(
            f"RO_BUDGET code_end=0x{code_end:x}/0x{DEFAULT_CODE_LIMIT:x} "
            f"ro_end=0x{ro_end:x}/0x{DEFAULT_RO_END_LIMIT:x} "
            f"code_reserve={DEFAULT_CODE_LIMIT - code_end} "
            f"ro_reserve={DEFAULT_RO_END_LIMIT - ro_end}"
        )
        for load in loads:
            with self.subTest(load=load):
                self.assertEqual(int(load[1], 16) % 0x1000, 0, "LOAD 起点必须 4 KiB 对齐")

    def test_link_ld_budget_matches_tool_defaults(self):
        """link.ld 的硬闸门与工具默认值必须一致，否则报告会与构建门禁脱节。"""
        script = (RUNTIME / "misc" / "link.ld").read_text(encoding="utf-8")
        self.assertIn(f"{DEFAULT_CODE_LIMIT:#x}", script)
        self.assertIn(f"{DEFAULT_RO_END_LIMIT:#x}", script)
        self.assertIn("只读段必须非空", script)
        # 历史地址 pin 不得再作为放置指令或上限断言出现（只在说明注释里提及它）。
        self.assertNotRegex(script, r"EXL_LAYOUT_PIN\s*(==|!=)")
        self.assertNotIn(". = EXL_LAYOUT_PIN", script)
        # 栈回溯表不得被重新保留（KEEP 会让汇编单元与 libnx 预编译对象的表全部留下）。
        self.assertNotIn("KEEP (*(.eh_frame))", script)
        self.assertIn("*(.eh_frame .eh_frame.*)", script)


if __name__ == "__main__":
    unittest.main()
