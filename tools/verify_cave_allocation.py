#!/usr/bin/env python3
"""Runtime 中继的代码洞 / 回调槽分配门禁。

背景（2026-09-12）：`0x68CD40` / `0x68CD78` 曾被两个中继同时占用——
运行时安装侧的 `kImagePathRelayCodeOffset` / `kImagePathRelaySlotOffset` 与
stage48 IPS 侧的 `STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET` / `…_SLOT_OFFSET`
洞与槽完全相同。image-path-relay 已按"方案被打包期 PNG→PCX 取代"删除；本工具让这类
**两个不同中继抢同一段地址**以后在构建或测试期直接失败，而不是等到真机上互相覆盖。

数据来源是真源本身（不引入第二份手抄表）：

* `tools/build_patches.py`：IPS 侧每个 kind 的 `*_CODE_OFFSET(S)` / `*_SLOT_OFFSET`
  / `*_LENGTH` / `*_CAVE_ORIGINAL`；
* `tools/stage127_save_load_observation_relay.py` 与
  `tools/stage128_save_data_manager_observation_relay.py`：这两个观察中继的
  `SAVE_RELAY` / `LOAD_RELAY` / `CALLBACK_SLOT`；
* `runtime/source/runtime_constants.hpp`：运行时安装侧的 `k*RelayCodeOffset(s)` /
  `k*RelaySlotOffset`。

**同一个中继在两侧各有一份常量是正常设计**，所以先按 `RELAY_ALIASES` 把它们归并成同一个
"中继身份"，再判定冲突：

1. 一个中继占用的区间 = 洞区间 + 槽区间（槽 8 字节）。洞长度优先取 `*_LENGTH`，其次
   `*_CAVE_ORIGINAL = bytes(N)`，再次"洞起点到槽末尾"。
2. **两个不同中继的区间不得重叠**；唯一合法途径是在 `EXCLUSIVE_GROUPS` 里显式登记，
   并写明互斥理由（例如同一诊断阶段的多个替代包，一轮只装一个）。
3. 归并后的中继必须两侧一致：洞起点集合相同、槽地址相同（只声明一侧也允许，用于
   "只有 IPS 或只有运行时安装"的中继）。不一致说明只改了一侧，直接报错。
4. 登记表不得过期：登记的每一组必须真的有重叠。

退出码：0 通过；1 发现冲突/两侧不一致/过期登记；2 参数错误；3 源文件不可解析。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SLOT_BYTES = 8
DEFAULT_CAVE_LENGTH = 0x40

ROOT = Path(__file__).resolve().parents[1]
PATCH_SOURCE = ROOT / "tools" / "build_patches.py"
EXTRA_SOURCES = (
    ROOT / "tools" / "stage127_save_load_observation_relay.py",
    ROOT / "tools" / "stage128_save_data_manager_observation_relay.py",
)
RUNTIME_CONSTANTS = ROOT / "runtime" / "source" / "runtime_constants.hpp"

# `runtime_constants.hpp` 里的家族名与 IPS 侧家族名的对应关系——同一个中继的两侧视图。
# 只在"两侧确实指同一段地址"时登记；改动任一侧的地址都会让"两侧一致"检查失败。
RELAY_ALIASES: dict[str, str] = {
    "runtime:kManagerRelay": "ManagerUpdateRelay",
    "patches:RELAY": "ManagerUpdateRelay",
    "runtime:kManagerRenderRelay": "ManagerRenderRelay",
    "patches:RENDER_RELAY": "ManagerRenderRelay",
    "runtime:kManagerLoadConfigsRelay": "ManagerLoadConfigsRelay",
    "patches:LOAD_CONFIGS_RELAY": "ManagerLoadConfigsRelay",
    "runtime:kManagerPresentRelay": "ManagerPresentRelay",
    "patches:RENDER_PRESENT_RELAY": "ManagerPresentRelay",
    "runtime:kGameObserverRelay": "GameObserverRelay",
    "patches:GAME_OBSERVER_RELAY": "GameObserverRelay",
    "runtime:kGameUpdateObserverRelay": "GameUpdateObserverRelay",
    "patches:GAME_UPDATE_OBSERVER_RELAY": "GameUpdateObserverRelay",
    "runtime:kGameState2ObserverRelay": "GameState2ObserverRelay",
    "patches:GAME_STATE2_OBSERVER_RELAY": "GameState2ObserverRelay",
    "runtime:kGameIsPausedRenderObserverRelay": "GameIsPausedRenderObserverRelay",
    "patches:GAME_ISPAUSED_RENDER_OBSERVER_RELAY": "GameIsPausedRenderObserverRelay",
    "runtime:kStage48MusicPlayRelay": "Stage48MusicPlayRelay",
    "patches:STAGE48_MUSIC_PLAY_RELAY": "Stage48MusicPlayRelay",
    "runtime:kStage48SoundActorPlayRelay": "Stage48SoundActorPlayRelay",
    "patches:STAGE48_SOUND_ACTOR_PLAY_RELAY": "Stage48SoundActorPlayRelay",
    "runtime:kStage48SoundActorPauseRelay": "Stage48SoundActorPauseRelay",
    "patches:STAGE48_SOUND_ACTOR_PAUSE_RELAY": "Stage48SoundActorPauseRelay",
    "runtime:kPreGetCollectibleRelay": "PreGetCollectibleRelay",
    "patches:PRE_GET_COLLECTIBLE_RELAY": "PreGetCollectibleRelay",
    "runtime:kGameChangeRoomRelay": "GameChangeRoomRelay",
    "patches:GAME_CHANGE_ROOM_RELAY": "GameChangeRoomRelay",
    "runtime:kRebuildMountPointsRelay": "RebuildMountPointsRelay",
    "patches:REBUILD_MOUNT_POINTS_RELAY": "RebuildMountPointsRelay",
    "runtime:kGameStartSavedRelay": "GameStartSavedRelay",
    "patches:GAME_START_SAVED_RELAY": "GameStartSavedRelay",
    "runtime:kGameStartNewRelay": "GameStartNewRelay",
    "patches:GAME_START_NEW_RELAY": "GameStartNewRelay",
    "runtime:kGameStartRelay": "GameStartLifecycleRelay",
    "patches:GAME_START_RELAY": "GameStartLifecycleRelay",
    "runtime:kGameRestartRelay": "GameRestartRelay",
    "patches:GAME_RESTART_RELAY": "GameRestartRelay",
    "runtime:kSaveLoadRelay": "SaveLoadRelay",
    "runtime:kSaveLoadRelaySave": "SaveLoadRelay",
    "runtime:kSaveLoadRelayLoad": "SaveLoadRelay",
    "stage127:SaveLoadRelay": "SaveLoadRelay",
    "runtime:kStage128SaveDataManagerRelay": "SaveDataManagerRelay",
    "runtime:kStage128SaveDataManagerRelaySave": "SaveDataManagerRelay",
    "runtime:kStage128SaveDataManagerRelayLoad": "SaveDataManagerRelay",
    "stage128:SaveDataManagerRelay": "SaveDataManagerRelay",
}


@dataclass(frozen=True)
class ExclusiveGroup:
    reason: str
    relays: frozenset[str]


# 允许互相重叠的中继。每条都必须写明"为什么可以共用"。
EXCLUSIVE_GROUPS: tuple[ExclusiveGroup, ...] = (
    ExclusiveGroup(
        reason=(
            "stage108（lifecycle-relay）/ stage109（restart-relay）/ stage127（save-load 观察）/ "
            "stage128（SaveDataManager 观察）是互斥诊断阶段：一轮只发布其中一个包，四者复用 "
            "0x68CEA0 / 0x68CEF0 两个洞与 0x68CF90 槽；运行时同一轮也只安装其中一个。"
        ),
        relays=frozenset(
            {
                "GameStartSavedRelay",
                "GameStartNewRelay",
                "GameStartLifecycleRelay",
                "GameRestartRelay",
                "SaveLoadRelay",
                "SaveDataManagerRelay",
            }
        ),
    ),
)


class SourceError(Exception):
    """源文件里解析不出预期的常量。"""


@dataclass
class RelayView:
    owner: str
    caves: list[int] = field(default_factory=list)
    slot: int | None = None
    cave_length: int | None = None

    def ranges(self) -> list[tuple[int, int, str]]:
        ordered: list[tuple[int, int, str]] = []
        for start in self.caves:
            if self.slot is not None and self.slot >= start:
                end = self.slot + SLOT_BYTES
            elif self.cave_length is not None:
                end = start + self.cave_length
            else:
                end = start + DEFAULT_CAVE_LENGTH
            ordered.append((start, end, "cave"))
        if self.slot is not None:
            ordered.append((self.slot, self.slot + SLOT_BYTES, "slot"))
        return ordered


@dataclass
class Relay:
    name: str
    views: list[RelayView] = field(default_factory=list)

    def cave_starts(self) -> list[int]:
        return sorted({start for view in self.views for start in view.caves})

    def slot(self) -> int | None:
        slots = {view.slot for view in self.views if view.slot is not None}
        return min(slots) if slots else None

    def declared_length(self) -> int | None:
        lengths = [view.cave_length for view in self.views if view.cave_length is not None]
        return max(lengths) if lengths else None

    def own_starts(self) -> list[int]:
        """本中继占用的起点（洞 + 槽），用于计算"下一个分配起点"。"""
        starts = list(self.cave_starts())
        slot = self.slot()
        if slot is not None:
            starts.append(slot)
        return sorted(starts)


def assign_ranges(relays: dict[str, Relay]) -> dict[str, list[tuple[int, int, str]]]:
    """把洞长度换算成区间。

    洞穴在同一次布局里是紧密排列的，所以任何洞都不可能伸进"下一个分配起点"：显式长度先取
    `min(起点 + 长度, 下一个起点)`；没有显式长度的家族（例如 `GAME_START_SAVED_RELAY` 的长度
    写在 `GAME_START_RELAY_LENGTH` 名下）直接用下一个分配起点封顶 —— 这样既不猜长度，也不会
    把一个洞摊到邻居头上。
    """
    next_start: dict[int, int] = {}
    all_starts = sorted({start for relay in relays.values() for start in relay.own_starts()})
    for index, start in enumerate(all_starts):
        if index + 1 < len(all_starts):
            next_start[start] = all_starts[index + 1]

    ranges: dict[str, list[tuple[int, int, str]]] = {}
    for name, relay in relays.items():
        collected: list[tuple[int, int, str]] = []
        length = relay.declared_length()
        for start in relay.cave_starts():
            boundary = next_start.get(start)
            end = start + length if length is not None else (boundary or start + DEFAULT_CAVE_LENGTH)
            if boundary is not None:
                end = min(end, boundary)
            if end <= start:
                end = boundary if boundary is not None else start + DEFAULT_CAVE_LENGTH
            collected.append((start, end, "cave"))
        slot = relay.slot()
        if slot is not None:
            collected.append((slot, slot + SLOT_BYTES, "slot"))
        ranges[name] = collected
    return ranges


def _value(expression: str, environment: dict[str, int]) -> int:
    expression = expression.strip()
    if re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+", expression):
        return int(expression, 0)
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*\+\s*(0[xX][0-9A-Fa-f]+|\d+)", expression)
    if match and match.group(1) in environment:
        return environment[match.group(1)] + int(match.group(2), 0)
    raise SourceError(f"无法求值的常量表达式：{expression!r}")


def _integer_environment(text: str) -> dict[str, int]:
    environment: dict[str, int] = {}
    for match in re.finditer(r"(?m)^([A-Z][A-Z0-9_]*)\s*=\s*(0[xX][0-9A-Fa-f]+|\d+)\s*$", text):
        environment[match.group(1)] = int(match.group(2), 0)
    for match in re.finditer(
        r"(?m)^([A-Z][A-Z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*\+\s*(0[xX][0-9A-Fa-f]+|\d+)\s*$",
        text,
    ):
        try:
            environment[match.group(1)] = _value(
                f"{match.group(2)} + {match.group(3)}", environment
            )
        except SourceError:
            continue
    return environment


def parse_patch_views(source: Path) -> list[RelayView]:
    text = source.read_text(encoding="utf-8")
    environment = _integer_environment(text)
    cave_lengths = {
        match.group(1): int(match.group(2), 0)
        for match in re.finditer(
            r"(?m)^([A-Z][A-Z0-9_]*)\s*=\s*(?:bytes|bytearray)\((0[xX][0-9A-Fa-f]+|\d+)\)", text
        )
    }

    views: dict[str, RelayView] = {}
    for match in re.finditer(r"(?m)^([A-Z][A-Z0-9_]*)_CODE_OFFSET\s*=", text):
        prefix = match.group(1)
        value = environment.get(f"{prefix}_CODE_OFFSET")
        if value is not None:
            views.setdefault(prefix, RelayView(f"patches:{prefix}")).caves.append(value)
    for match in re.finditer(r"(?m)^([A-Z][A-Z0-9_]*)_CODE_OFFSETS\s*=\s*\(([^)]*)\)", text):
        prefix = match.group(1)
        view = views.setdefault(prefix, RelayView(f"patches:{prefix}"))
        for item in match.group(2).split(","):
            if item.strip():
                view.caves.append(_value(item, environment))
    for match in re.finditer(r"(?m)^([A-Z][A-Z0-9_]*)_SLOT_OFFSET\s*=", text):
        prefix = match.group(1)
        slot = environment.get(f"{prefix}_SLOT_OFFSET")
        if slot is not None:
            views.setdefault(prefix, RelayView(f"patches:{prefix}")).slot = slot

    for prefix, view in views.items():
        view.cave_length = environment.get(f"{prefix}_LENGTH") or cave_lengths.get(
            f"{prefix}_CAVE_ORIGINAL"
        )
    return list(views.values())


def parse_extra_views(source: Path, label: str, numbers: dict[str, str]) -> RelayView:
    text = source.read_text(encoding="utf-8")
    caves: list[int] = []
    for constant, role in numbers.items():
        match = re.search(rf"(?m)^{constant}\s*=\s*(0[xX][0-9A-Fa-f]+)", text)
        if match is None:
            raise SourceError(f"{source} 里找不到 {constant}")
        value = int(match.group(1), 0)
        if role == "cave":
            caves.append(value)
    slot_match = re.search(r"(?m)^CALLBACK_SLOT\s*=\s*(0[xX][0-9A-Fa-f]+)", text)
    if slot_match is None:
        raise SourceError(f"{source} 里找不到 CALLBACK_SLOT")
    return RelayView(f"{label}", caves=sorted(caves), slot=int(slot_match.group(1), 0))


def parse_runtime_views(source: Path) -> list[RelayView]:
    text = source.read_text(encoding="utf-8")
    caves: dict[str, list[int]] = {}
    for match in re.finditer(
        r"(?m)^inline constexpr uintptr_t (k[A-Za-z0-9]+?)CodeOffsets?\s*=\s*(0[xX][0-9A-Fa-f]+);",
        text,
    ):
        caves.setdefault(match.group(1), []).append(int(match.group(2), 0))
    for match in re.finditer(
        r"(?m)^inline constexpr std::array<uintptr_t, \d+> (k[A-Za-z0-9]+?)CodeOffsets\s*=\s*\{([^}]*)\}",
        text,
    ):
        view_caves = caves.setdefault(match.group(1), [])
        for item in match.group(2).split(","):
            if item.strip():
                view_caves.append(int(item.strip(), 0))
    # 只认名字里带 `Relay` 的槽常量：`kContentManagerSlot` 这类不是中继槽。
    slots = {
        match.group(1): int(match.group(2), 0)
        for match in re.finditer(
            r"(?m)^inline constexpr uintptr_t (k[A-Za-z0-9]*Relay[A-Za-z0-9]*?)SlotOffset\s*="
            r"\s*(0[xX][0-9A-Fa-f]+);",
            text,
        )
    }

    views: list[RelayView] = []
    for family in sorted(set(caves) | set(slots)):
        view = RelayView(
            f"runtime:{family}", caves=sorted(caves.get(family, [])), slot=slots.get(family)
        )
        # 只有槽、没有洞常量的家族（例如 kGameStartRelay）只用于校验槽，不占洞区间。
        views.append(view)
    if not views:
        # 解析不到任何中继槽时不能"静默通过"——多半是文件改了形状而正则失配。
        raise SourceError(f"{source} 里没有解析到任何中继槽常量")
    return views


def collect_views(
    patch_source: Path,
    extra_sources: tuple[Path, ...],
    runtime_constants: Path,
) -> list[RelayView]:
    views = parse_patch_views(patch_source)
    views.append(
        parse_extra_views(
            extra_sources[0],
            "stage127:SaveLoadRelay",
            {"SAVE_RELAY": "cave", "LOAD_RELAY": "cave"},
        )
    )
    views.append(
        parse_extra_views(
            extra_sources[1],
            "stage128:SaveDataManagerRelay",
            {"SAVE_RELAY": "cave", "LOAD_RELAY": "cave"},
        )
    )
    views.extend(parse_runtime_views(runtime_constants))
    return views


def merge_relays(views: list[RelayView]) -> dict[str, Relay]:
    relays: dict[str, Relay] = {}
    for view in views:
        name = RELAY_ALIASES.get(view.owner, view.owner)
        relays.setdefault(name, Relay(name)).views.append(view)
    return relays


def _side(owner: str) -> str:
    return owner.split(":", 1)[0]


def check_view_agreement(relay: Relay) -> list[str]:
    """同一中继的两侧视图必须指向同一段地址（只改一侧就是这种不一致）。"""
    problems: list[str] = []
    patch_starts = sorted(
        {start for view in relay.views if _side(view.owner) != "runtime" for start in view.caves}
    )
    runtime_starts = sorted(
        {start for view in relay.views if _side(view.owner) == "runtime" for start in view.caves}
    )
    if patch_starts and runtime_starts and patch_starts != runtime_starts:
        problems.append(
            f"{relay.name} 两侧洞起点不一致（只改了一侧？）："
            f"IPS/工具侧 {[hex(value) for value in patch_starts]}，"
            f"运行时侧 {[hex(value) for value in runtime_starts]}"
        )
    patch_slots = {
        view.slot for view in relay.views if _side(view.owner) != "runtime" and view.slot is not None
    }
    runtime_slots = {
        view.slot for view in relay.views if _side(view.owner) == "runtime" and view.slot is not None
    }
    if patch_slots and runtime_slots and patch_slots != runtime_slots:
        problems.append(
            f"{relay.name} 两侧槽地址不一致（只改了一侧？）："
            f"IPS/工具侧 {[hex(value) for value in sorted(patch_slots)]}，"
            f"运行时侧 {[hex(value) for value in sorted(runtime_slots)]}"
        )
    return problems


def find_conflicts(
    ranges: dict[str, list[tuple[int, int, str]]]
) -> list[tuple[str, str, str]]:
    """返回 (中继 A, 中继 B, 重叠说明) 列表；同一对只报一次。"""
    entries = [
        (name, start, end, kind)
        for name, relay_ranges in ranges.items()
        for start, end, kind in relay_ranges
    ]
    entries.sort(key=lambda item: (item[1], item[2]))
    conflicts: dict[tuple[str, str], list[str]] = {}
    for index, left in enumerate(entries):
        for right in entries[index + 1 :]:
            if right[1] >= left[2]:
                break
            if left[0] == right[0]:
                continue
            key = tuple(sorted((left[0], right[0])))
            conflicts.setdefault(key, []).append(
                f"{left[0]} {left[3]} [{left[1]:#x},{left[2]:#x}) 与 "
                f"{right[0]} {right[3]} [{right[1]:#x},{right[2]:#x})"
            )
    return [(key[0], key[1], "；".join(details)) for key, details in sorted(conflicts.items())]


def evaluate(
    relays: dict[str, Relay], groups: tuple[ExclusiveGroup, ...] | None = None
) -> tuple[list[str], list[str]]:
    registry = EXCLUSIVE_GROUPS if groups is None else groups
    problems: list[str] = []
    for relay in relays.values():
        problems.extend(check_view_agreement(relay))

    ranges = assign_ranges(relays)
    live_groups: set[int] = set()
    for first, second, detail in find_conflicts(ranges):
        for index, group in enumerate(registry):
            if {first, second} <= group.relays:
                live_groups.add(index)
                break
        else:
            problems.append(
                f"未登记的跨中继重叠：{detail}（要么换洞/槽，要么在 EXCLUSIVE_GROUPS 登记互斥理由）"
            )
    stale = [
        f"登记已过期：{sorted(group.relays)} 之间没有实际重叠"
        for index, group in enumerate(registry)
        if index not in live_groups
    ]
    return problems, stale


def format_report(relays: dict[str, Relay], problems: list[str], stale: list[str]) -> str:
    lines = [f"洞/槽分配：{len(relays)} 个中继"]
    for name in sorted(relays):
        relay = relays[name]
        detail = " ".join(
            f"{view.owner}[{','.join(hex(value) for value in sorted(view.caves)) or '-'}"
            f"{'/' + hex(view.slot) if view.slot is not None else ''}]"
            for view in relay.views
        )
        lines.append(f"  {name}: {detail}")
    for problem in problems + stale:
        lines.append(f"分配问题：{problem}")
    return "\n".join(lines)


def runtime(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch-source", type=Path, default=PATCH_SOURCE)
    parser.add_argument("--runtime-constants", type=Path, default=RUNTIME_CONSTANTS)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)

    try:
        views = collect_views(arguments.patch_source, EXTRA_SOURCES, arguments.runtime_constants)
    except (OSError, SourceError) as error:
        print(f"无法解析分配来源：{error}", file=sys.stderr)
        return 3

    relays = merge_relays(views)
    problems, stale = evaluate(relays)
    if arguments.json:
        print(
            json.dumps(
                {
                    "relays": {
                        name: assign_ranges(relays)[name] for name in relays
                    },
                    "problems": problems,
                    "stale": stale,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(format_report(relays, problems, stale))
        if not problems and not stale:
            print("洞/槽分配：未发现未登记的跨中继重叠")
    return 0 if not problems and not stale else 1


if __name__ == "__main__":
    sys.exit(runtime())
