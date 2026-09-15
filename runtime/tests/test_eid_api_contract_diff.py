"""`tools/eid_api_contract_diff.py` 的行为门禁。

这个工具是"② 语义维"的收口入口：它把 IsaacDocs 的 PC 契约解析出来，和我们的
Catalog / 绑定表 / 偏离表并排比对，逐条给出判定。它一旦悄悄坏掉（解析出 0 条契约、
判定全变成"缺失"），报告会**看起来很正常但全是错的** —— 所以这里把它的性质钉住：

1. 契约解析必须真的有量（IsaacDocs 快照的 1555 条条目）；
2. 已知条目的契约要逐字段对得上（拿真实例子钉，不拿构造的）；
3. 判定逻辑的关键分支要能复现（缺失 / 已登记偏离 / 低置信 / 形态不符）。
"""

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "eid_api_contract_diff.py"
#: PC 侧契约来自 IsaacDocs 快照；它**不在仓库里**（是分析输入），所以缺它时按仓库惯例
#: `skipTest` 而不是报错 —— 隔离副本（只复制被 git 跟踪的文件）里必然没有它。
DOCS = ROOT / "analysis" / "isaacdocs-snapshot" / "docs"


def load_tool():
    spec = importlib.util.spec_from_file_location("eid_api_contract_diff", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EidApiContractDiffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()
        cls.contracts = cls.tool.load_pc_contracts()

    def require_snapshot(self):
        if not DOCS.is_dir():
            self.skipTest(f"缺少 IsaacDocs 快照夹具：{DOCS}")

    def test_the_pc_contracts_are_actually_parsed(self):
        """IsaacDocs 快照里有 1555 条，解析结果不能是 0 或个位数。

        这条是防"分节方式变了导致锚点匹配不上"那类静默失效：解析出 0 条时，
        报告里所有条目都会变成"缺失"，而报告本身看不出异常。
        """
        self.require_snapshot()
        self.assertGreater(
            len(self.contracts), 1500,
            f"只解析出 {len(self.contracts)} 条契约 —— IsaacDocs 快照读法或解析规则坏了",
        )

    def test_known_function_contracts_match_field_by_field(self):
        """拿真实条目逐字段钉住（返回类型、参数个数与文本都来自 IsaacDocs 原文）。"""
        self.require_snapshot()
        cases = {
            "Level:GetAbsoluteStage": ("Functions", "LevelStage", 0),
            "Level:IsNextStageAvailable": ("Functions", "boolean", 0),
            "Level:GetRoomByIdx": ("Functions", "RoomDescriptor", 2),
            "Room:WorldToScreenPosition": ("Functions", "Vector", 1),
            "EntityPlayer:GetCollectibleCount": ("Functions", "int", 0),
            "Level:IsAltStage": ("Functions", "boolean", 0),
        }
        for api, (kind, returns, param_count) in cases.items():
            with self.subTest(api=api):
                contract = self.contracts.get(api)
                self.assertIsNotNone(contract, f"IsaacDocs 里应当有 {api}")
                self.assertEqual(contract["kind"], kind)
                self.assertEqual(contract["returns"], returns)
                self.assertEqual(len(contract["params"]), param_count, contract["params"])

    def test_variable_contracts_are_parsed_without_parameter_lists(self):
        """变量节没有括号（`#### boolean Clear {: .copyable aria-label='Variables' }`）。"""
        self.require_snapshot()
        contract = self.contracts.get("RoomDescriptor:Clear")
        self.assertIsNotNone(contract, "RoomDescriptor:Clear 应当被解析到")
        self.assertEqual(contract["kind"], "Variables")
        self.assertEqual(contract["returns"], "boolean")
        self.assertEqual(contract["params"], [])

    def test_classification_branches_reproduce(self):
        """判定逻辑的关键分支：低置信优先、缺失、已登记偏离、形态不符。"""
        catalog = {"Level:GetStage": "0x03010001", "Options:HUDOffset": "0x00020001"}
        bound = {"0X03010001"}
        deviations = {"0x00020001": {"kind": "Placeholder", "text": "……"}}
        index_names = {"HUDOffset"}

        # ① 低置信条目不给结论（owner 对不上时凭名字定不了类型）
        verdict, _ = self.tool.classify("Mod:RemoveCallback", None, None, bound, deviations,
                                        index_names, False)
        self.assertEqual(verdict, "low-confidence")

        # ② Catalog 里没有 ⇒ 缺失
        verdict, _ = self.tool.classify("Level:GetRoomByIdx", {"kind": "Functions"},
                                        None, bound, deviations, index_names, True)
        self.assertEqual(verdict, "missing")

        # ③ 已登记偏离优先于其它判定（这是"语义不满足"的标记来源）
        verdict, why = self.tool.classify("Options:HUDOffset", {"kind": "Variables"},
                                          "0x00020001", bound, deviations, index_names, True)
        self.assertEqual(verdict, "deviation")
        self.assertIn("Placeholder", why)

        # ④ PC 是变量、我们登记成方法 ⇒ 形态不符
        verdict, _ = self.tool.classify("Level:GetStage", {"kind": "Variables"},
                                        "0x03010001", bound, deviations, index_names, True)
        self.assertEqual(verdict, "kind-mismatch")

        # ⑤ 形态对得上、没登记偏离 ⇒ 只到"待人工核对"，**不假装语义正确**
        verdict, why = self.tool.classify("Level:GetStage", {"kind": "Functions"},
                                          "0x03010001", bound, deviations, index_names, True)
        self.assertEqual(verdict, "review")
        self.assertIn("尚未人工核对", why)


if __name__ == "__main__":
    unittest.main()
