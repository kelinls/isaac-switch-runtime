"""引擎内存可读性缓存（`interfaces/lua/engine_memory_guard.hpp`）的宿主行为测试。

这一层是 2026-09-13"物品信息显示时卡顿"的修复核心：`svcQueryMemory` 的返回值描述的是一个
连续且权限一致的映射，所以整个区间都能复用那一次结论；缓存只在一次受管回调派发内有效。
宿主没有 `svcQueryMemory`（宿主上四个 API 族的入口只做 `EngineGuardRangeUsable` 那一层
空地址/回绕判定），所以这里直接测缓存数据结构本身、作用域开关与区间判定 —— 那正是设备上
会决定"要不要进内核"的逻辑。
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import shutil


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


def host_compilers() -> tuple[str, str] | None:
    """(C 编译器, C++ 编译器)；缺任何一个就整体 skip，不伪装成通过。"""
    cc = shutil.which("cc")
    if cc is None:
        return None
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return cc, found
    return None

HARNESS = r'''
#include "interfaces/lua/engine_memory_guard.hpp"

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <thread>

using isaac::runtime::EngineGuardCacheEnabled;
using isaac::runtime::EngineGuardDispatchActive;
using isaac::runtime::EngineGuardEnterDispatch;
using isaac::runtime::EngineGuardLeaveDispatch;
using isaac::runtime::EngineGuardRegionCache;
using isaac::runtime::EngineGuardRangeUsable;
using isaac::runtime::EngineGuardSetCacheEnabled;

namespace {

int g_Failures = 0;

void check(bool condition, const char* name) {
    if (!condition) {
        ++g_Failures;
        std::printf("GUARD_FAIL %s\n", name);
    }
}

} // namespace

int main() {
    // 1) 空缓存：什么都不覆盖。
    EngineGuardRegionCache().Clear();
    check(!EngineGuardRegionCache().Covers(0x1000, 4), "empty-cache");
    check(EngineGuardRegionCache().count == 0, "empty-count");

    // 2) 插入一段之后：区间内命中，跨出边界不算命中。
    EngineGuardRegionCache().Insert(0x1000, 0x2000);
    check(EngineGuardRegionCache().Covers(0x1000, 1), "insert-begin");
    check(EngineGuardRegionCache().Covers(0x1FFC, 4), "insert-end");
    check(!EngineGuardRegionCache().Covers(0x1FFE, 4), "insert-overrun");
    check(!EngineGuardRegionCache().Covers(0x2000, 1), "insert-after");
    check(!EngineGuardRegionCache().Covers(0x0FFC, 8), "insert-before");

    // 3) 容量轮转：第 9 段覆盖第 1 段。
    EngineGuardRegionCache().Clear();
    for (std::uintptr_t index = 0; index < 9; ++index) {
        EngineGuardRegionCache().Insert(0x10000 + index * 0x1000, 0x10000 + (index + 1) * 0x1000);
    }
    check(EngineGuardRegionCache().count == 8, "capacity-count");
    check(!EngineGuardRegionCache().Covers(0x10000, 4), "capacity-evicted-first");
    check(EngineGuardRegionCache().Covers(0x18000, 4), "capacity-keeps-last");

    // 4) 空区间/倒置区间不写入。
    EngineGuardRegionCache().Clear();
    EngineGuardRegionCache().Insert(0x2000, 0x2000);
    EngineGuardRegionCache().Insert(0x3000, 0x2000);
    check(EngineGuardRegionCache().count == 0, "reject-degenerate");

    // 5) 作用域：非派发期间缓存不可用（主机上没有 `svcQueryMemory` 这条路径，所以这里直接
    //    检查两个开关的语义）。
    check(!EngineGuardDispatchActive(), "scope-starts-closed");
    EngineGuardEnterDispatch();
    check(EngineGuardDispatchActive(), "scope-enter");
    EngineGuardRegionCache().Insert(0x1000, 0x2000);
    check(EngineGuardRegionCache().count == 1, "scope-insert");
    EngineGuardLeaveDispatch();
    check(!EngineGuardDispatchActive(), "scope-leave");
    check(EngineGuardRegionCache().count == 0, "scope-cleared");

    // 6) 每次进入派发都要丢掉上一轮的条目，否则换局/换房间后可能命中过期区间。
    EngineGuardEnterDispatch();
    EngineGuardRegionCache().Insert(0x1000, 0x2000);
    EngineGuardEnterDispatch();
    check(EngineGuardRegionCache().count == 0, "reenter-clears");
    EngineGuardLeaveDispatch();

    // 7) 关掉缓存必须同时清空（A/B 探针切换相时用）。
    EngineGuardEnterDispatch();
    EngineGuardRegionCache().Insert(0x1000, 0x2000);
    EngineGuardSetCacheEnabled(false);
    check(!EngineGuardCacheEnabled(), "disable-flag");
    check(EngineGuardRegionCache().count == 0, "disable-clears");
    EngineGuardSetCacheEnabled(true);
    check(EngineGuardCacheEnabled(), "enable-flag");
    EngineGuardLeaveDispatch();

    // 8) 区间判定的宿主语义：空地址/长度 0/回绕一律不可用，正常区间可用。
    check(!EngineGuardRangeUsable(0, 4), "check-null");
    check(!EngineGuardRangeUsable(0x1000, 0), "check-zero-length");
    check(!EngineGuardRangeUsable(static_cast<std::uintptr_t>(-8), 16), "check-overflow");
    check(EngineGuardRangeUsable(0x1000, 8), "check-ok");

    // 9) 并发：写侧是游戏线程，读侧可能是宿主插件的线程（绑定解析阶段）。
    //
    //    修复前 `entries`/`count` 是普通成员：读侧先读到某个槽的 `begin`、写侧随后把该槽的
    //    `begin`/`end` 都换掉、读侧再读到新的 `end`，就会拼出一段两个真实区间之间的"缝隙"
    //    区间，于是 `Covers()` 对并不可读的地址返回 true。这里交替写入两种布局（A 低位、
    //    B 高位），缝隙地址在两种布局里都不被任何单个区间覆盖，但 A 的 `begin` 配 B 的 `end`
    //    会覆盖它。断言假命中一次都不许出现。
    //
    //    说明：这是压力型回归守卫，不是形式化证明 —— 撕裂窗口只有 `begin`/`end` 两次写之间的
    //    几条指令。实测（2026-09-13）：换回修复前的头文件连跑 3 次全部失败，修复版通过。
    {
        EngineGuardEnterDispatch();
        constexpr std::uintptr_t base = 0x1000000;
        constexpr std::uintptr_t step = 0x10000;
        constexpr std::uintptr_t layoutShift = 0x4000;
        constexpr int rounds = 20000;

        std::atomic<bool> stop{false};
        std::atomic<unsigned long long> falseHits{0};
        std::atomic<unsigned long long> reads{0};

        std::thread reader([&]() {
            while (!stop.load(std::memory_order_relaxed)) {
                for (std::uintptr_t index = 0; index < 8; ++index) {
                    // 缝隙中点：布局 A 覆盖 [0, 0x1000)，布局 B 覆盖 [0x4000, 0x5000)，
                    // 0x2000 两边都不覆盖；而 A.begin + B.end = [0, 0x5000) 会覆盖它。
                    const std::uintptr_t probe = base + index * step + 0x2000;
                    if (EngineGuardRegionCache().Covers(probe, 4)) {
                        falseHits.fetch_add(1, std::memory_order_relaxed);
                    }
                    reads.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });

        for (int round = 0; round < rounds; ++round) {
            const std::uintptr_t shift = ((round % 2) == 0) ? 0 : layoutShift;
            EngineGuardRegionCache().Clear();
            for (std::uintptr_t index = 0; index < 8; ++index) {
                const std::uintptr_t begin = base + index * step + shift;
                EngineGuardRegionCache().Insert(begin, begin + 0x1000);
            }
        }
        stop.store(true);
        reader.join();

        check(reads.load() > 0, "concurrent-reader-ran");
        check(falseHits.load() == 0, "concurrent-no-false-positive");
        EngineGuardLeaveDispatch();
    }

    if (g_Failures == 0) {
        std::printf("ENGINE_GUARD_OK\n");
    }
    return g_Failures == 0 ? 0 : 1;
}
'''


class EngineMemoryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compilers = host_compilers()
        if compilers is None:
            raise unittest.SkipTest("需要宿主 C/C++ 编译器（本用例不需要 docker）")
        _, cxx = compilers
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-engine-guard-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "engine_guard_harness.cpp"
        harness.write_text(HARNESS.lstrip())
        cls.binary = temporary / "engine_guard_harness"
        build = subprocess.run(
            [cxx, "-std=c++23", "-Wall", "-Wextra", "-Werror", "-pthread",
             "-DEXL_LAYERED_RUNTIME=1",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             str(harness), "-o", str(cls.binary)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_region_cache_and_dispatch_scope(self):
        result = subprocess.run([str(self.binary)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ENGINE_GUARD_OK", result.stdout)
        self.assertNotIn("GUARD_FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
