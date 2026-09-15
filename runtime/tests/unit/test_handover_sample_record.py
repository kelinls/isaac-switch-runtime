"""交接线程取样记录（ISAACWT1）与延迟快照（readIndex=2）的约定。

背景：交接线程此前在两种结局里都写不出任何东西——"没等到入口"那条分支直接驻停、
什么都不写，"写盘失败"同样没有痕迹，于是文件侧完全无法区分。读取工具里早就定义了
`ISAACWT1`（"等运行时入口 + 交接"）的布局，插件侧却从未实现。

这里钉住三件事：

* 插件按解码器**既有**的 `ISAACWT1` 布局逐字段写（偏移逐一对上），读取工具零改动；
* 线程体在**两条路径**上都调用同一个落盘出口（既有护栏禁止函数体出现 `Append*` 家族
  字面量，所以出口集中在一个外层函数里）；
* 同一次取样按既有 `ISAACSN1` 格式补一条 `readIndex = 2` 的快照——加载线程读的那条
  （readIndex = 1）必定赶在安装之前，安装报告只有这里读得到。
"""
import importlib.util
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
PLUGIN = ROOT / "src" / "host_plugin" / "saltynx_host_plugin.cpp"
DECODER = REPO / "tools" / "read_host_plugin_bridge.py"


def load_decoder():
    spec = importlib.util.spec_from_file_location("read_host_plugin_bridge", DECODER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handover_body(text: str) -> str:
    return text.split("void HostHandoverMain(void*) {", 1)[1].split("} // namespace", 1)[0]


class LayoutAgreesWithDecoderTests(unittest.TestCase):
    """插件侧的写字段偏移必须与解码器既有 `ISAACWT1` 分支读的偏移一致。"""

    def setUp(self):
        self.text = PLUGIN.read_text(encoding="utf-8")

    def test_record_constants_exist(self):
        self.assertIn('constexpr char kHandoverSampleMagic[] = "ISAACWT1";', self.text)
        self.assertIn("constexpr std::size_t kHandoverSampleRecordSize = 96;", self.text)
        self.assertIn("constexpr std::uint8_t kHandoverSnapshotReadIndex = 2;", self.text)

    def test_build_writes_the_offsets_the_decoder_reads(self):
        build = self.text.split("void BuildHandoverSampleRecord(", 1)[1]
        build = build.split("\n}\n", 1)[0]
        self.assertIn("PutU32(record + 20, sample.polls);", build)
        self.assertIn("PutU32(record + 24, sample.registrations);", build)
        self.assertIn("PutU32(record + 28, sample.fileApiMask);", build)
        self.assertIn("PutU64(record + 48, sample.publishedExlMain);", build)
        self.assertIn("PutU64(record + 72, sample.tickNow);", build)
        self.assertIn("PutU32(record + 80, sample.exlMainAttempts);", build)
        self.assertIn("PutU32(record + 88, sample.serviceState);", build)
        self.assertIn(
            "PutU32(record + 92, Checksum(record, kHandoverSampleRecordSize - 4));", build)

    def test_handover_samples_use_their_own_channel(self):
        # 一条通道只允许一个写入者。实测：加载线程与交接线程同时以 "ab" 追加到 bridge.bin 时，
        # 两边都按"打开那一刻的文件尾"定位，后写的把先写的覆盖掉 —— 会话条数因此每次不同
        # （752 / 720 / 624 字节），而记录流本身是干净的。所以交接线程必须写自己的文件。
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn('"sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-handover-samples.bin"',
                      text)
        publish = text.split("void PublishHandoverSample(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("AppendRecord(api, kHandoverSamplePath, record, sizeof(record));", publish)
        self.assertNotIn("kBridgePath", publish)
        # 两条路径必须是不同的文件，否则"各自一个写入者"这条约定无从成立。
        bridge = text.split("constexpr char kBridgePath[] =", 1)[1].split(";", 1)[0]
        sample = text.split("constexpr char kHandoverSamplePath[] =", 1)[1].split(";", 1)[0]
        self.assertNotEqual(bridge.strip(), sample.strip())

    def test_snapshot_rides_the_existing_record_format(self):
        # 快照晚读必须复用既有 ISAACSN1 记录与 readIndex=2，而不是发明新载体；
        # 且它跟着取样一起写进交接线程自己的通道。
        publish = PLUGIN.read_text(encoding="utf-8").split("void PublishHandoverSample(", 1)[1]
        publish = publish.split("\n}\n", 1)[0]
        self.assertIn("AppendSnapshotRecord(api, kHandoverSamplePath, flags, "
                      "kHandoverSnapshotReadIndex", publish)
        self.assertIn("std::uint8_t bytes[kTestRunSnapshotWords]{};", publish)

    def test_thread_keeps_its_writes_to_a_minimum(self):
        # v10 的单一变量：循环里几乎不写盘。前几版线程都在"写了几条记录之后"停住
        # （v7 四条 / v8 九条 / v9 九条），而写盘是循环里唯一随迭代累积的动作 ——
        # 必须排除"诊断本身把线程弄停"这个可能。所以循环内取样关闭、t=0 之后不再有
        # 额外的跨文件写盘，看 30 秒窗口走完后那条"没等到入口"的记录能否出现。
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("constexpr std::uint32_t kHandoverProgressEarlyPolls = 0;", text)
        self.assertIn("constexpr std::uint32_t kHandoverProgressEveryPolls = 1000;", text)
        body = handover_body(text)
        # 五个取样点：t=0、循环入口、polls==1（本轮被上面的常量关闭）、
        # "没等到入口"与"交换完成"各一条。
        self.assertEqual(body.count("PublishHandoverSample("), 5)
        self.assertIn("PublishHandoverSample(api, g_handoverFlags, started, nullptr);", body)
        self.assertIn("PublishHandoverSample(api, g_handoverFlags, entered, nullptr);", body)
        # 线程体里只剩一次探针写盘（交换完成之后那次）；t=0 之后不再有额外的跨文件写盘。
        self.assertEqual(body.count("WriteThreadProbe("), 1)
        # 循环进入那条必须在身份调用**之前**：否则"卡在调用里"就不出记录了。
        loop = body.split("while (waited <= kEntryFirstWaitNanoseconds) {", 1)[1]
        loop = loop.split("\n    }", 1)[0]
        self.assertLess(loop.index("entered, nullptr"), loop.index("g_helper.identity(words, 4)"))
        # 进度取样必须落在循环里、且在"入口非零就跳出"之前，否则卡死时看不到任何进度。
        self.assertIn("if ((polls <= kHandoverProgressEarlyPolls ||", loop)
        self.assertIn("(polls % kHandoverProgressEveryPolls) == 0) &&", loop)
        self.assertLess(loop.index("progressSamples < kHandoverProgressSampleLimit"),
                        loop.index("entryAddress != 0"))
        # 既有护栏的字面量清单原样保留——写盘走外层出口，函数体里不出现它们。
        for forbidden in ("AppendRecord(", "AppendBridgeRecord(", "AppendSnapshotRecord(",
                          "AppendCreateProbe(", "AppendMappingRecord(", "AppendModuleCopyRecord(",
                          "registerFileApi"):
            self.assertNotIn(forbidden, body)

    def test_thread_priority_is_above_the_games_own_threads(self):
        # v8 实测：线程在加载阶段 1.4 秒内跑 6 轮（每轮约 0.3 秒），随后 65 秒一轮没跑，
        # 停点恰好落在游戏主循环起步处 ⇒ 低优先级线程在游戏跑起来后分不到时间片。
        # 这条钉住优先级常量与它在创建调用里的使用，防止被改回 0x2C。
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("constexpr int kHandoverThreadPriority = 0x1C;", text)
        self.assertIn("stackTop, kHandoverThreadPriority, -2);", text)

    def test_the_first_wait_window_is_short_and_separate_from_the_park(self):
        # 第一次等入口的窗口只决定"多久之后把'看不到入口'写成结论"，不该决定玩家要玩多久。
        text = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("constexpr std::int64_t kEntryFirstWaitNanoseconds = 30'000'000'000LL;", text)
        body = handover_body(text)
        self.assertIn("while (waited <= kEntryFirstWaitNanoseconds) {", body)
        # 驻停时长仍是原来那个常量（既有护栏要求它在函数体里出现）。
        self.assertIn("svcSleepThread(kEntryWaitDeadlineNanoseconds);", body)


class DecoderKnowsTheRecordTests(unittest.TestCase):
    def setUp(self):
        self.text = DECODER.read_text(encoding="utf-8")
        self.rb = load_decoder()

    def test_decoder_maps_the_record(self):
        self.assertIn('b"ISAACWT1": (96, "wait for the Runtime entry + hand-over"),', self.text)

    def test_sample_with_a_published_entry_reads_as_a_running_copy(self):
        record = bytearray(96)
        record[0:8] = b"ISAACWT1"
        record[8] = 1
        struct.pack_into("<I", record, 12, 0x00616AA7)
        struct.pack_into("<I", record, 16, 96)
        struct.pack_into("<I", record, 20, 960)          # polls
        struct.pack_into("<I", record, 24, 24)           # registrations（写进表的轮数）
        struct.pack_into("<I", record, 28, 0xF)          # fileApiMask
        struct.pack_into("<Q", record, 48, 0x7100005BC0) # publishedExlMain
        struct.pack_into("<Q", record, 72, 0x1234)       # tickNow
        struct.pack_into("<I", record, 92, self.rb.fnv1a(bytes(record[:92])))
        lines = self.rb.decode_record(b"ISAACWT1", bytes(record), 0, 0)
        text = "\n".join(lines)
        self.assertIn("polls=960 registrations=24", text)
        self.assertIn("publishedExlMain = 0x7100005bc0", text)
        self.assertIn("the Runtime's entry had run", text)

    def test_sample_without_an_entry_says_so(self):
        record = bytearray(96)
        record[0:8] = b"ISAACWT1"
        record[8] = 1
        struct.pack_into("<I", record, 16, 96)
        struct.pack_into("<I", record, 20, 960)          # polls：重查过这么多次
        struct.pack_into("<I", record, 92, self.rb.fnv1a(bytes(record[:92])))
        lines = self.rb.decode_record(b"ISAACWT1", bytes(record), 0, 0)
        text = "\n".join(lines)
        self.assertIn("polls=960 registrations=0", text)
        self.assertIn("the entry never ran inside the wait window", text)


class DelayedSnapshotRecordTests(unittest.TestCase):
    """readIndex=2 的快照必须把安装报告与启动状态机一起带出来。"""

    def setUp(self):
        self.rb = load_decoder()

    def test_second_sample_decodes_the_install_report(self):
        reserved = 3 | (4 << 8) | (2 << 16) | (5 << 24)  # diagAttach=3, installed=4, slot=2, code=5
        record = bytearray(80)
        record[0:8] = b"ISAACSN1"
        record[8] = 1
        record[9] = 2                                    # readIndex = 延迟后的第二次读取
        struct.pack_into("<I", record, 12, 0x00616AA7)
        struct.pack_into("<I", record, 16, 80)
        struct.pack_into("<Q", record, 20, 0x3152544341415349)  # 快照入口返回的 magic
        # 快照 48 字节原样嵌在 +24 起（偏移见解码器的 SNAPSHOT_WORDS）。
        struct.pack_into("<Q", record, 24, 0x3152544341415349)  # raw+0  magic 'ISAACTR1'
        struct.pack_into("<I", record, 36, 1)            # raw+12 state = ExlMainEntered
        struct.pack_into("<I", record, 40, 1)            # raw+16 detail 与 state 配对
        struct.pack_into("<Q", record, 48, 20260913132623)      # raw+24 buildId
        struct.pack_into("<I", record, 64, reserved)     # raw+40 reserved
        struct.pack_into("<I", record, 72, self.rb.fnv1a(bytes(record[:72])))
        lines = self.rb.decode_record(b"ISAACSN1", bytes(record), 0, 0)
        text = "\n".join(lines)
        self.assertIn("readIndex=2 (second sample)", text)
        self.assertIn("hook installed=4 failureSlot=2 failureCode=5", text)
        self.assertIn("diagAttach) = 3", text)


if __name__ == "__main__":
    unittest.main()
