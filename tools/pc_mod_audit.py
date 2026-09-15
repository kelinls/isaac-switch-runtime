"""Conservatively extract explicit Lua dependencies from PC Isaac Mods."""

from __future__ import annotations

from pathlib import Path
import re

from tools.pc_mod_manifest import discover_mods


VERIFIED_METHODS = frozenset(
    {
        "Mod:AddCallback",
        "Game:IsPaused",
        "MusicManager:GetCurrentMusicID",
        "MusicManager:Pause",
        "MusicManager:Resume",
    }
)
# 有派发点的回调：Runtime 真的会在引擎时机里调用它们（2026-09-12 复核）。
DISPATCHED_CALLBACKS = frozenset(
    {"MC_POST_UPDATE", "MC_POST_RENDER", "MC_INPUT_ACTION", "MC_PRE_GET_COLLECTIBLE"}
)
# 兼容性口径（2026-09-12 起）：其余 `ModCallbacks` 种类**允许登记但不会触发**，
# 因为 PC Mod 会在加载阶段一次性注册十几种回调，任何一种报错都会让整包加载失败
# （EID 就是这种）。所以审计不再把它们判成"不可用"，而是分成两档：
#   registered_only_callback:<name>  → 能注册、收不到事件（功能静默缺失）
#   unknown_callback:<name>          → 连名字都不在 PC 表里（多半是 Repentogon 扩展）
# `VERIFIED_CALLBACKS` 保留旧名，供既有调用方与测试继续使用。
VERIFIED_CALLBACKS = DISPATCHED_CALLBACKS
def _pc_callback_names() -> frozenset[str]:
    """PC `ModCallbacks` 表里的名字，从生成的枚举数据里读，避免再抄一份清单。"""
    source = Path(__file__).resolve().parents[1] / "runtime/source/program/pc_lua_enum_data.cpp"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    match = re.search(r"constexpr Value kModCallbacksValues\[\] = \{(.*?)\};", text, re.S)
    if match is None:
        # 生成器还没把 ModCallbacks 加进去时，退回到"只知道有派发点的那几个"。
        return frozenset(DISPATCHED_CALLBACKS)
    return frozenset(re.findall(r'\{"(MC_[A-Z0-9_]+)"', match.group(1)))


PC_CALLBACK_NAMES = _pc_callback_names()

_REQUIRE = re.compile(r'\brequire\s*(?:\(\s*)?["\']([^"\'\r\n]+)["\']\s*\)?')
_CALLBACK = re.compile(r"\bModCallbacks\.(MC_[A-Z0-9_]+)\b")
_ASSIGNMENT = re.compile(r"\blocal\s+([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\(")
_METHOD_CALL = re.compile(r"(?=([A-Za-z_]\w*)\s*(?:\(\s*\))?\s*:\s*([A-Za-z_]\w*))")
_ISAAC_FUNCTION = re.compile(r"\bIsaac\.([A-Za-z_]\w*)\s*\(")
_KNOWN_CONSTRUCTORS = frozenset({"Game", "MusicManager", "SFXManager", "Sprite", "Vector", "KColor", "RNG"})


def _strip_lua_comments(source: str) -> str:
    """Remove line and basic long comments while preserving quoted strings."""
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        character = source[index]
        if quote is not None:
            result.append(character)
            if character == "\\" and index + 1 < len(source):
                result.append(source[index + 1])
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in ("'", '"'):
            quote = character
            result.append(character)
            index += 1
            continue
        if source.startswith("--[[", index):
            end = source.find("]]", index + 4)
            end = len(source) if end == -1 else end + 2
            result.extend("\n" if item == "\n" else " " for item in source[index:end])
            index = end
            continue
        if source.startswith("--", index):
            end = source.find("\n", index + 2)
            end = len(source) if end == -1 else end
            result.extend(" " for _ in source[index:end])
            index = end
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _strip_lua_strings(source: str) -> str:
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        character = source[index]
        if quote is None and character in ("'", '"'):
            quote = character
            result.append(" ")
            index += 1
            continue
        if quote is not None:
            if character == "\\" and index + 1 < len(source):
                result.extend((" ", " "))
                index += 2
                continue
            if character == quote:
                quote = None
            result.append("\n" if character == "\n" else " ")
            index += 1
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _extract_lua_dependencies(source: str) -> dict[str, set[str] | bool]:
    without_comments = _strip_lua_comments(source)
    code = _strip_lua_strings(without_comments)
    aliases: dict[str, str] = {}
    constructors: set[str] = set()
    for variable, constructor in _ASSIGNMENT.findall(code):
        if constructor == "RegisterMod":
            aliases[variable] = "Mod"
        elif constructor in _KNOWN_CONSTRUCTORS:
            aliases[variable] = constructor
            constructors.add(constructor)

    methods: set[str] = set()
    methods.update(f"Isaac.{function}" for function in _ISAAC_FUNCTION.findall(code))
    for match in _METHOD_CALL.finditer(code):
        variable, method = match.groups()
        if method == "AddCallback":
            methods.add("Mod:AddCallback")
            continue
        constructor = aliases.get(variable)
        if constructor is not None and constructor != "Mod":
            methods.add(f"{constructor}:{method}")
            continue
        if variable in _KNOWN_CONSTRUCTORS:
            constructors.add(variable)
            methods.add(f"{variable}:{method}")
            continue
        if code[: match.start(1)].rstrip().endswith(":"):
            methods.add(f"{variable}:{method}")

    return {
        "callbacks": set(_CALLBACK.findall(code)),
        "requires": set(_REQUIRE.findall(without_comments)),
        "constructors": constructors,
        "methods": methods,
        "repentogon": bool(re.search(r"\bREPENTOGON\b", code)),
    }


def _resource_directories(files: object) -> list[str]:
    if not isinstance(files, list):
        return []
    found = set()
    for value in files:
        if not isinstance(value, dict):
            continue
        path = value.get("path")
        if not isinstance(path, str):
            continue
        for directory in ("resources", "content"):
            if path == directory or path.startswith(f"{directory}/"):
                found.add(directory)
    return sorted(found)


def _candidate_reasons(mod: dict[str, object], audit: dict[str, set[str] | bool]) -> list[str]:
    reasons: list[str] = []
    if not bool(mod.get("enabled")):
        reasons.append("disabled")
    if mod.get("entry") is None:
        reasons.append("missing_entry")
    if _resource_directories(mod.get("files")):
        reasons.append("resource_directories")
    if audit["repentogon"]:
        reasons.append("repentogon")
    callbacks = audit["callbacks"]
    methods = audit["methods"]
    if isinstance(callbacks, set):
        for value in sorted(callbacks):
            if value in DISPATCHED_CALLBACKS:
                continue
            if value in PC_CALLBACK_NAMES:
                reasons.append(f"registered_only_callback:{value}")
            else:
                reasons.append(f"unknown_callback:{value}")
    if isinstance(methods, set):
        reasons.extend(f"unverified_api:{value}" for value in sorted(methods - VERIFIED_METHODS))
    return reasons


def audit_mods(mods_root: Path) -> list[dict[str, object]]:
    """Return a stable static-dependency report for direct Mod subdirectories."""
    audited: list[dict[str, object]] = []
    for mod in discover_mods(mods_root):
        files = mod.get("files")
        directory = mod.get("directory")
        if not isinstance(files, list) or not isinstance(directory, str):
            raise ValueError("invalid PC Mod manifest model")
        lua_files = sorted(
            file["path"] for file in files if isinstance(file, dict) and isinstance(file.get("path"), str) and file["path"].endswith(".lua")
        )
        combined = {"callbacks": set(), "requires": set(), "constructors": set(), "methods": set(), "repentogon": False}
        for relative_path in lua_files:
            source = (mods_root / directory / relative_path).read_text(encoding="utf-8")
            extracted = _extract_lua_dependencies(source)
            for key in ("callbacks", "requires", "constructors", "methods"):
                combined[key].update(extracted[key])
            combined["repentogon"] = combined["repentogon"] or bool(extracted["repentogon"])
        reasons = _candidate_reasons(mod, combined)
        metadata = mod.get("metadata")
        audited.append(
            {
                "directory": directory,
                "name": metadata.get("name") if isinstance(metadata, dict) else None,
                "enabled": bool(mod.get("enabled")),
                "lua_files": lua_files,
                "resource_directories": _resource_directories(files),
                "callbacks": sorted(combined["callbacks"]),
                "requires": sorted(combined["requires"]),
                "constructors": sorted(combined["constructors"]),
                "methods": sorted(combined["methods"]),
                "repentogon": bool(combined["repentogon"]),
                "candidate_reasons": reasons,
                "candidate": not reasons,
            }
        )
    return audited
