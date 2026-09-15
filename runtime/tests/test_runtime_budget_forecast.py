"""`tools/runtime_budget_forecast.py` 的算术与判定门禁。

这个工具回答"还能加多少条 API、先撞哪个段"。它的输入是实测常量（每 API 的代码/只读成本），
逻辑本身是纯算术 —— 所以这里直接钉算术与判定边界，不需要真产物，也不会因为机器不同而漂。

背景（为什么值得有门禁）：项目里曾经把布局测试**故意构造**的越限样例当成真实产物的读数，
于是留下了"只读段超预算"这个错误印象（真实产物同一次输出里紧邻的一行是 `ok=yes`）。
本工具把"哪个段先撞"明确打出来，这几条断言就是防止它算反。
"""

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from runtime_budget_forecast import (  # noqa: E402
    ENGINE_METHOD_API_CODE_BYTES,
    FIELD_READ_API_CODE_BYTES,
    RODATA_BYTES_PER_API,
    forecast,
)


class RuntimeBudgetForecastTests(unittest.TestCase):
    def test_capacity_is_limited_by_the_tighter_segment(self):
        """两段余量都很大时，上限取较小者；且明确指出是哪一段。"""
        # 代码余 4900 B（正好 10 条），只读余 2720 B（正好 10 条）—— 两边打平。
        result = forecast(0x80000 - 4900, 0x100000 - 2720, apis=0, kind="engine")
        self.assertEqual(result["capacity_by_code"], 10)
        self.assertEqual(result["capacity_by_rodata"], 10)
        self.assertEqual(result["capacity"], 10)

        # 代码段更紧（余 4900 B ⇒ 10 条），只读段很宽 ⇒ 先撞代码段。
        result = forecast(0x80000 - 4900, 0x100000 - 272000, apis=0, kind="engine")
        self.assertEqual(result["binding_segment"], "code")
        self.assertEqual(result["capacity"], 10)

        # 只读段更紧（余 2720 B ⇒ 10 条），代码段很宽 ⇒ 先撞只读段。
        result = forecast(0x80000 - 49000, 0x100000 - 2720, apis=0, kind="engine")
        self.assertEqual(result["binding_segment"], "rodata")
        self.assertEqual(result["capacity"], 10)

    def test_field_read_apis_are_cheaper_than_guarded_engine_methods(self):
        """字段直读类没有 16 字节守卫校验函数，因此同样的余量能放更多条。"""
        code_end = 0x80000 - 49000
        engine = forecast(code_end, 0x100000, apis=0, kind="engine")
        field = forecast(code_end, 0x100000, apis=0, kind="field")
        self.assertEqual(engine["code_per_api"], ENGINE_METHOD_API_CODE_BYTES)
        self.assertEqual(field["code_per_api"], FIELD_READ_API_CODE_BYTES)
        self.assertGreater(field["capacity_by_code"], engine["capacity_by_code"])

    def test_requested_batch_fit_is_decided_by_the_limit(self):
        """一个批次放不放得下，按上限判 —— 边界值两边都要对。"""
        code_end = 0x80000 - 10 * ENGINE_METHOD_API_CODE_BYTES
        # 只读段留足余量：否则上限会被只读段压到 0，测的就不是代码段这条边界了。
        ro_end = 0x100000 - 100 * RODATA_BYTES_PER_API
        fits = forecast(code_end, ro_end, apis=10, kind="engine")
        self.assertTrue(fits["requested_fits"])
        over = forecast(code_end, ro_end, apis=11, kind="engine")
        self.assertFalse(over["requested_fits"])

    def test_rodata_cost_constant_matches_the_measured_batch(self):
        """只读成本常量必须与实测一致：批次 8 加 4 条 API 涨了 1088 B（= 272 B/条）。

        这条不是"再算一遍加法"，而是把**实测结论**钉在测试里：如果有人把常量改成
        一个更好看的数，这里会红，必须回来说明依据。
        """
        self.assertEqual(RODATA_BYTES_PER_API, 272)
        self.assertEqual(RODATA_BYTES_PER_API * 4, 1088)

    def test_cli_reports_a_clear_error_for_a_missing_artifact(self):
        """产物路径不存在 ⇒ 退出码 2 并说清原因（不假装算出了结果）。"""
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "runtime_budget_forecast.py"),
             "--elf", "does-not-exist.elf"],
            text=True, capture_output=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("找不到产物", result.stderr)


if __name__ == "__main__":
    unittest.main()
