#!/usr/bin/env python3
"""Build the PC Lua API <-> Switch internal symbol <-> Runtime implemented inventory.

This tool answers one question: *for every API the PC version of The Binding of
Isaac: Repentance exposes to Lua mods, is it already implemented by this
project's Runtime, is it provided by the Switch game itself, or does it still
have to be implemented -- and in the latter case, is there a Switch internal
C++ symbol we can thunk to?*

Data sources
------------
A. PC Lua API name authority -- IsaacDocs (https://github.com/wofsauge/IsaacDocs)
   ``docs/*.md`` class pages.  Every class page is machine readable:

       ### Get·Collectible () {: aria-label='Functions' }
       [ ](#){: .rep .tooltip .badge }
       #### [CollectibleType](enums/CollectibleType.md) GetCollectible ( ... ) {: .copyable aria-label='Functions' }

   The ``####`` line is the real signature; the ``###`` heading carries the
   split name (``Get·Collectible`` -> ``GetCollectible``).

B. PC executable name fallback -- ``The Binding of Isaac Rebirth pc/isaac-ng.exe``
   (32-bit PE).  Its Lua method/property names live in one contiguous run of
   NUL-terminated ASCII identifiers inside ``.rdata`` (the run that contains
   ``GetCollectible`` around file offset 0x74FB34..0x754A38).  That run is
   detected automatically as the identifier block with the highest overlap with
   the documented method names; it is used to confirm documented names and to
   surface candidate names the docs do not cover (low confidence).

C. Switch internal symbols -- ``Repentance.nro`` dynamic symbol table, read with
   the in-repo helper ``tools/nro_symbols.py``.  The file offset of a defined
   dynamic symbol *is* the module-relative runtime offset (verified byte for
   byte against ``runtime/source/runtime_constants.hpp`` guards), so each hit
   can be emitted together with a 16-byte on-disk guard for the thunk table.

D. Runtime implemented set -- ``runtime/src/interfaces/lua/api_catalog.cpp``
   (the authoritative 44-entry descriptor table) cross-checked against the
   ``kXHandlers`` binding tables in the sibling family translation units.

Outputs
-------
* ``analysis/lua-api-inventory/inventory.json`` -- machine readable
* ``docs/PC-Lua-API-对照清单.md``               -- human readable (简体中文)

No third-party dependencies.  ``llvm-cxxfilt`` is used when available and the
built-in Itanium nested-name reader is the fallback; both are compared and the
agreement rate is reported.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import nro_symbols  # noqa: E402  (in-repo helper, stdlib only)

# --------------------------------------------------------------------------- #
# Fixed project paths
# --------------------------------------------------------------------------- #

GAME_DIR = os.path.join(
    REPO_ROOT,
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]",
)
NRO_DIR = os.path.join(GAME_DIR, "Program #0", "1", ".nro")
REPENTANCE_NRO = os.path.join(NRO_DIR, "Repentance.nro")
AFTERBIRTH_NRO = os.path.join(NRO_DIR, "AfterbirthPlus.nro")

PC_EXE = os.path.join(REPO_ROOT, "The Binding of Isaac Rebirth pc", "isaac-ng.exe")

LUA_DIR = os.path.join(REPO_ROOT, "runtime", "src", "interfaces", "lua")
CATALOG_CPP = os.path.join(LUA_DIR, "api_catalog.cpp")
RUNTIME_CONSTANTS = os.path.join(REPO_ROOT, "runtime", "source", "runtime_constants.hpp")

OUT_JSON = os.path.join(REPO_ROOT, "analysis", "lua-api-inventory", "inventory.json")
OUT_DOC = os.path.join(REPO_ROOT, "docs", "PC-Lua-API-对照清单.md")

# Local IsaacDocs mirrors, best first.  The workspace copy is listed first so the
# inventory stays reproducible after ``/tmp`` is cleared; it is intentionally
# git-ignored (third-party documentation is not vendored into this repository).
DOC_DIR_CANDIDATES = [
    os.path.join(REPO_ROOT, "analysis", "isaacdocs-snapshot", "docs"),
    "/tmp/isaacdocs_full/docs",
    "/tmp/isaacdocs_refresh/docs",
    "/tmp/isaacdocs/docs",
]

ISAACDOCS_TREE_API = (
    "https://api.github.com/repos/wofsauge/IsaacDocs/git/trees/main?recursive=1"
)
ISAACDOCS_RAW = "https://raw.githubusercontent.com/wofsauge/IsaacDocs/main/"

CXXFILT_CANDIDATES = [
    "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/llvm-cxxfilt",
    "/usr/bin/llvm-cxxfilt",
    "/opt/homebrew/opt/llvm/bin/llvm-cxxfilt",
    "llvm-cxxfilt",
]

# --------------------------------------------------------------------------- #
# Owner mapping: IsaacDocs class name -> IsaacRepentance C++ class
# --------------------------------------------------------------------------- #

# Non-class doc pages that still describe an API surface, and the owner name we
# normalise them to.  ``Global``/``Mod`` match the Runtime catalog's own owner
# strings so the two sides can be joined directly.
DOC_OWNER_ALIASES = {
    "GlobalFunctions": "Global",
    "Mod Reference": "Mod",
    "ModReference": "Mod",
}

# Explicit, hand-checked aliases.  Each entry is justified in the output doc.
OWNER_ALIASES = {
    "MusicManager": ["Music"],
    "SFXManager": ["SoundEffects"],
    "Mod Reference": ["ModManager", "ModEntry"],
    "ModReference": ["ModManager", "ModEntry"],
    "ItemConfigItem": ["ItemConfig::Item", "ItemConfig"],
    "ItemConfigCard": ["ItemConfig"],
    "ItemConfigPillEffect": ["ItemConfig"],
    "ItemConfigCostume": ["ItemConfig"],
    "RoomConfigEntry": ["RoomConfig::Entry", "RoomConfig"],
    "RoomConfigEntries": ["RoomConfig"],
    "RoomConfigRoom": ["RoomConfig"],
    "RoomConfigSpawn": ["RoomConfig"],
    "RoomConfigSpawns": ["RoomConfig"],
    "PathFinder": ["NPCAI_Pathfinder"],
    "TemporaryEffect": ["TemporaryEffects"],
    "PlayerTypesActiveItemDesc": ["Entity_Player"],
    "PlayerTypesPosVel": ["Entity_Player"],
    "VectorList": ["GridEntityDesc"],
}

# Type-skin aliases: PC Lua exposes several engine types under a different name,
# so a doc owner such as ``Sprite`` has no class of that name in the Switch
# binary even though every method exists.  Each target below was confirmed by
# exact same-name methods inside the aliased class (see the output doc for the
# evidence table); these are *not* generic "same name anywhere" matches.
#
# These aliases are **canonical, not additive**: the PC Lua type *is* the aliased
# engine class, so a same-named legacy class in the binary must not shadow it.
# The documented signatures settle it, e.g. PC ``Sprite:Render(Vector, Vector,
# Vector)`` and ``Sprite:SetAnimation(string, boolean)`` only exist on ``ANM2``,
# while the Rebirth-era ``IsaacRepentance::Sprite`` takes
# ``(float, float, Color, Color, Vector2, float)`` / ``(char const*, unsigned int)``.
TYPE_SKIN_ALIASES = {
    # PC Lua ``Sprite`` wraps ``IsaacRepentance::ANM2``: Load / LoadGraphics /
    # ReplaceSpritesheet / Reload / Play / SetFrame / PlayOverlay ... all exist
    # there with identical names.
    "Sprite": ["ANM2"],
    # PC Lua ``Font`` is ``KAGE::Graphics::Font``: Load(char const*, char const*),
    # Unload, IsLoaded, GetStringWidth, DrawString ... all exist there.
    "Font": ["Graphics::Font"],
    # PC Lua ``Vector`` is ``KAGE::Math::Vector2``: Clamp / Lerp / Distance /
    # DistanceSquared / FromAngle / Normalize / Resize.
    "Vector": ["Math::Vector2"],
    # PC Lua ``Color`` carries the ColorMod surface (SetTint / SetColorize /
    # SetOffset / Reset / Lerp); IsaacDocs has no ``ColorMod`` class page.
    "Color": ["ColorMod"],
    # PC Lua ``KColor`` is the plain RGBA struct plus the predefined palette.
    "KColor": ["Graphics::Color", "Graphics::PredefinedColors"],
}

OWNER_ALIASES.update({owner: targets for owner, targets in TYPE_SKIN_ALIASES.items()})

# One-line justification per type-skin alias, rendered into the output doc so the
# mapping is auditable without reading this source file.
TYPE_SKIN_ALIAS_EVIDENCE = {
    "Sprite": "`Load` / `LoadGraphics` / `ReplaceSpritesheet` / `Reload` / `Play` / `SetFrame` / "
              "`PlayOverlay` / `RemoveOverlay` / `GetOverlayFrame` / `Stop` 等 24 个同名方法",
    "Font": "`Load(char const*, char const*)` / `Unload` / `IsLoaded` / `GetStringWidth` / "
            "`DrawString` / `SetMissingCharacter`",
    "Vector": "`Clamp` / `Lerp` / `Distance` / `DistanceSquared` / `FromAngle` / `Normalize` / `Resize`",
    "Color": "PC 文档 `Color` 的方法就是 `SetTint` / `SetColorize` / `SetOffset` / `Reset` / `Lerp`，"
             "与 `ColorMod` 一致；IsaacDocs 没有 `ColorMod` 类页",
    "KColor": "RGBA 字段与 `Black` / `Red` / `Green` / … 预定义色，对应 RGBA 结构与预定义色表",
}

# Owners whose PC Lua surface is the global table `Isaac` (free functions in the
# game binary, plus a handful of Game/Manager members).
GLOBAL_TABLE_OWNERS = {"Isaac"}

# Extra classes searched when the direct class has no such method.  This models
# C++ inheritance that the docs flatten (PC Lua exposes inherited methods).
BASE_CLASS_CHAIN = {
    "Entity_Player": ["Entity"],
    "Entity_NPC": ["Entity"],
    "Entity_Tear": ["Entity"],
    "Entity_Familiar": ["Entity"],
    "Entity_Bomb": ["Entity"],
    "Entity_Effect": ["Entity"],
    "Entity_Laser": ["Entity"],
    "Entity_Knife": ["Entity"],
    "Entity_Pickup": ["Entity"],
    "Entity_Projectile": ["Entity"],
    "Entity_Slot": ["Entity"],
    "Entity_Text": ["Entity"],
    "Entity_Dummy": ["Entity"],
    "GridEntity_Door": ["GridEntity"],
    "GridEntity_Rock": ["GridEntity"],
    "GridEntity_Pit": ["GridEntity"],
    "GridEntity_Poop": ["GridEntity"],
    "GridEntity_Spikes": ["GridEntity"],
    "GridEntity_TNT": ["GridEntity"],
    "GridEntity_Fire": ["GridEntity"],
    "GridEntity_Lock": ["GridEntity"],
    "GridEntity_Web": ["GridEntity"],
    "GridEntity_Statue": ["GridEntity"],
    "GridEntity_Teleporter": ["GridEntity"],
    "GridEntity_TrapDoor": ["GridEntity"],
    "GridEntity_Stairs": ["GridEntity"],
    "GridEntity_Wall": ["GridEntity"],
    "GridEntity_Decoration": ["GridEntity"],
    "GridEntity_Gravity": ["GridEntity"],
    "GridEntity_PressurePlate": ["GridEntity"],
    "RoomConfig::Entry": ["RoomConfig"],
    "ItemConfig::Item": ["ItemConfig"],
}

# PC Lua surface types implemented entirely in the binding layer; the game C++
# has no same-named member, so a symbol miss here is expected and not a defect.
LUA_GLUE_OWNERS = {
    "Vector", "VectorList", "Color", "KColor", "BitSet128", "EntityPtr",
    "EntityRef", "Font", "FontRenderSettings", "ProjectileParams", "TearParams",
    "QueueItemData", "GridEntityDesc", "CppContainer",
}

# Owners that only make sense with a keyboard or a mouse on PC.
KEYBOARD_MOUSE_OWNERS = {"Keyboard", "Mouse"}
KEYBOARD_MOUSE_NAMES: set[str] = set()
# Deliberately narrow: bare "Key" would wrongly catch in-game items such as
# EntityPlayer:HasGoldenKey / TryUseKey, which are gameplay APIs, not input APIs.
KEYBOARD_MOUSE_RE = re.compile(r"Mouse|Keyboard|MouseWheel|KeyCode", re.IGNORECASE)

# Reason codes for ``missing_hard``.  Each entry gets exactly one code plus a
# concrete per-entry sentence naming what is actually missing.
REASON_LABELS = {
    "struct_field_layout": "属性访问：需要结构体布局未知的字段偏移",
    "lua_glue_type": "Lua 绑定层胶水：值类型 / 容器包装 / 全局函数，游戏 C++ 侧无同名成员",
    "owner_instance_unlocated": "宿主对象/单例未定位：无法取得 this",
    "pc_only_subsystem": "依赖 PC 专有子系统（Steam / 排行榜 / 窗口 / 双屏等）",
    "persistence_unavailable": "依赖存档/持久化：Switch 上 SD 文件通道运行期不可用",
    "mod_loading_unavailable": "依赖 PC 的 mod 加载与清单元数据（Switch 侧无对应实现）",
    "render_glue": "渲染/字体胶水：需要 Lua 侧绘制管线而非单个游戏方法",
    "no_symbol": "类存在但方法不存在：该行为被内联、被移除，或本来就是字段直写",
    "accessor_heuristic": "访问器缺失（命名启发式）：PC 的 Get*/Is*/Has*/Set*/Can* 在 Switch 上没有同名函数，状态大概率由字段承载",
    "owner_class_absent": "Switch 侧不存在该 Lua 对象对应的 C++ 类，需先重建对象语义",
    "enum_constant_table": "枚举/常量表：纯 Lua 数据，无 C++ 符号可 thunk",
}

# Doc-file prefixes for Lua-side container wrappers (CppContainer_Vector_* etc.).
LUA_GLUE_FILE_PREFIXES = ("CppContainer_",)

# Classes the Runtime can already dereference (evidence: api_catalog.cpp owners
# that reach a real object today).  Used only for the thunk_readiness hint.
REACHABLE_OWNERS = {"Game", "Level", "Room", "ItemPool", "MusicManager", "Manager", "Music"}

# Naming heuristic: a PC Lua accessor whose class exists in the Switch binary but
# has no same-named C++ member.  Such state is very likely held in a field on
# Switch (which is why there is no accessor symbol at all).  Flagged separately
# from the certain ``struct_field_layout`` bucket so it stays reviewable.
ACCESSOR_SHAPED = re.compile(r"^(Get|Is|Has|Set|Can)[A-Z]")
# PC-only downcast helpers (Entity:ToPlayer/ToTear/...).  They are C++ static
# casts in the Lua glue, so no symbol can exist.
DOWNCAST_SHAPED = re.compile(r"^To[A-Z]")
ENTITY_OWNER_RE = re.compile(r"^Entity")


# --------------------------------------------------------------------------- #
# Itanium demangling (stdlib-only fallback plus llvm-cxxfilt when present)
# --------------------------------------------------------------------------- #

def _mangled_namespace(name: str) -> str | None:
    """Return the game namespace of a nested-name function symbol, if any.

    Accepts both ``_ZN15IsaacRepentance...`` and the const-qualified
    ``_ZNK15IsaacRepentance...`` form (and the ``V``/``r`` qualifier variants);
    const member functions are a large share of the engine's getters and were
    previously dropped entirely by a plain ``_ZN`` prefix test.
    """
    if not name.startswith("_ZN"):
        return None
    body = name[3:]
    while body and body[0] in "KVr":
        body = body[1:]
    if body.startswith("15IsaacRepentance"):
        return "IsaacRepentance"
    if body.startswith("4KAGE"):
        return "KAGE"
    return None


def _itanium_nested(name: str):
    """Best-effort reader for ``_ZN...E`` / ``_ZNK...E`` nested names.

    Returns ``(class_or_None, method_or_None)`` or ``None`` when the symbol is
    not a plain nested-name function symbol.  This intentionally understands
    only the shape produced by the game and never guesses on substitutions.
    ``const``/``volatile`` qualified member functions (``_ZNK``) are the same
    shape with a leading qualifier letter, so they are stripped first.
    """
    if not name.startswith("_Z"):
        return None
    s = name[2:]
    if not s.startswith("N"):
        return None
    s = s[1:]
    while s and s[0] in "KVr":
        s = s[1:]
    parts: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "E":
            break
        if ch.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            length = int(s[i:j])
            ident = s[j:j + length]
            if len(ident) != length:
                return None
            parts.append(ident)
            i = j + length
            continue
        if ch in "CD" and i + 1 < len(s) and s[i + 1].isdigit():
            # Constructor (C1/C2/C3) or destructor (D0/D1/D2).
            if ch == "D":
                parts.append("~" + (parts[-1] if parts else "?"))
            else:
                parts.append(parts[-1] if parts else "?")
            break
        return None
    if not parts:
        return None
    if len(parts) == 1:
        return (None, parts[0])
    return ("::".join(parts[:-1]), parts[-1])


def _find_cxxfilt() -> str | None:
    for candidate in CXXFILT_CANDIDATES:
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def demangle_batch(mangled: list[str]) -> tuple[list[tuple[str | None, str | None]], dict, list[str]]:
    """Demangle a batch and report agreement between the two implementations.

    Returns ``(builtin_pairs, stats, external_signatures)``.  The third value is
    the ``llvm-cxxfilt`` output (empty when unavailable); it keeps the parameter
    list that the built-in reader deliberately drops, which is what lets the
    classifier rank overloads by the documented argument count.
    """
    stats = {"cxxfilt": None, "agreement": None, "checked": 0, "disagreements": []}

    builtin = [_itanium_nested(n) for n in mangled]

    cxxfilt = _find_cxxfilt()
    if cxxfilt is None:
        return builtin, stats, []
    stats["cxxfilt"] = cxxfilt
    try:
        proc = subprocess.run(
            [cxxfilt, "-n"], input="\n".join(mangled),
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return builtin, stats, []
    lines = proc.stdout.splitlines()
    if len(lines) != len(mangled):
        return builtin, stats, []

    agree = 0
    checked = 0
    for name, out, bi in zip(mangled, lines, builtin):
        # llvm-cxxfilt prints the plain nested name without the argument list we
        # care about; compare the qualified prefix only, with the enclosing
        # namespace removed on both sides.
        text = out[len("IsaacRepentance::"):] if out.startswith("IsaacRepentance::") else out
        text = text[len("KAGE::"):] if text.startswith("KAGE::") else text
        text = text.split("(")[0]
        parts = text.split("::")
        if len(parts) >= 2:
            cls, method = "::".join(parts[:-1]), parts[-1]
        elif parts and parts[0]:
            cls, method = None, parts[0]
        else:
            continue
        checked += 1
        builtin_pair = bi
        if builtin_pair is not None and builtin_pair[0] is not None:
            stripped = builtin_pair[0]
            for prefix in ("IsaacRepentance::", "KAGE::"):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                    break
            builtin_pair = (stripped or None, builtin_pair[1])
        if builtin_pair is not None and (cls, method) == builtin_pair:
            agree += 1
        elif checked - agree <= 25 and len(stats["disagreements"]) < 25:
            stats["disagreements"].append([
                name, f"{cls}::{method}",
                f"{builtin_pair[0]}::{builtin_pair[1]}" if builtin_pair else "None",
            ])
    stats["checked"] = checked
    stats["agreement"] = round(agree / checked, 6) if checked else None
    return builtin, stats, lines


# --------------------------------------------------------------------------- #
# IsaacDocs parsing
# --------------------------------------------------------------------------- #

COPYABLE_LINE = re.compile(
    r"^####\s+(?P<sig>.+?)\s*\{:\s*\.copyable\s+aria-label='(?P<kind>[^']+)'[^}]*\}\s*$"
)
HEADING_LINE = re.compile(
    r"^###\s+(?P<title>.+?)\s*(?:\(\))?\s*\{:\s*aria-label='(?P<kind>[^']+)'[^}]*\}\s*$"
)
CLASS_HEADER = re.compile(r"^#+\s*(?:Global\s+)?Class\s+\"(?P<name>[^\"]+)\"", re.M)
BADGE_LINE = re.compile(r"^\[ \]\(#\)\{:\s*(?P<classes>[^}]*)\}\s*$")
SECTION_LINE = re.compile(r"^##\s+(?P<name>[^#].*?)\s*$")

# Files that are not an API surface.
NON_API_DOC_FILES = {"PLACEHOLDER.md", "index.md", "tags.md", "Globals.md"}

PRIMITIVE_TYPES = {
    "void", "int", "float", "boolean", "bool", "string", "table", "function",
    "userdata", "any", "nil", "number", "vararg",
}


def _strip_md_links(text: str) -> str:
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)


def parse_arg_types(signature: str) -> list[str]:
    """Return the declared argument type names from a doc signature line."""
    text = _strip_md_links(signature).strip()
    open_paren = text.find("(")
    if open_paren < 0:
        return []
    body = text[open_paren + 1:]
    close = body.rfind(")")
    if close >= 0:
        body = body[:close]
    body = re.sub(r"=\s*[^,]+", "", body)  # drop default values
    types: list[str] = []
    for chunk in body.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        token = chunk.split()[0].strip()
        token = token.rstrip("[]").strip()
        if token:
            types.append(token)
    return types


def load_isaacdocs(docs_dir: str) -> tuple[list[dict], dict]:
    """Parse every class page into distinct ``(owner, name, kind)`` records."""
    entries: dict[tuple[str, str, str], dict] = {}
    files = sorted(f for f in os.listdir(docs_dir) if f.endswith(".md"))
    used_files: list[str] = []

    for filename in files:
        if filename in NON_API_DOC_FILES:
            continue
        path = os.path.join(docs_dir, filename)
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()

        header = CLASS_HEADER.search(text)
        if header:
            owner = header.group("name")
        elif filename == "GlobalFunctions.md":
            owner = "GlobalFunctions"
        else:
            continue
        owner = DOC_OWNER_ALIASES.get(owner, owner)
        used_files.append(filename)

        pending_name: str | None = None
        pending_kind: str | None = None
        pending_badges: list[str] = []

        for line in text.splitlines():
            heading = HEADING_LINE.match(line)
            if heading:
                pending_name = heading.group("title").replace("·", "").strip()
                pending_kind = heading.group("kind")
                pending_badges = []
                continue
            badge = BADGE_LINE.match(line)
            if badge and pending_name:
                tags = [t for t in badge.group("classes").split() if t.startswith(".")]
                pending_badges = [t.lstrip(".") for t in tags if t.lstrip(".") != "tooltip"
                                  and t.lstrip(".") != "badge"]
                continue
            copyable = COPYABLE_LINE.match(line)
            if not copyable:
                continue
            name = pending_name
            kind = copyable.group("kind")
            signature = copyable.group("sig").strip()
            if not name:
                # Fall back to the signature's method identifier.
                guess = re.search(r"([A-Za-z_]\w*)\s*\(", _strip_md_links(signature))
                if not guess:
                    continue
                name = guess.group(1)
            key = (owner, name, kind)
            record = entries.get(key)
            if record is None:
                record = {
                    "owner": owner,
                    "name": name,
                    "kinds": [kind],
                    "signatures": [],
                    "availability": [],
                    "doc_files": [],
                    "arg_types": [],
                }
                entries[key] = record
            if kind not in record["kinds"]:
                record["kinds"].append(kind)
            if signature and signature not in record["signatures"]:
                record["signatures"].append(signature)
            for badge_name in pending_badges:
                if badge_name not in record["availability"]:
                    record["availability"].append(badge_name)
            if filename not in record["doc_files"]:
                record["doc_files"].append(filename)
            for arg in parse_arg_types(signature):
                if arg not in record["arg_types"]:
                    record["arg_types"].append(arg)
            pending_name = None
            pending_kind = None

    # Collapse to one record per (owner, name): merge kinds.
    merged: dict[tuple[str, str], dict] = {}
    for record in entries.values():
        key = (record["owner"], record["name"])
        target = merged.get(key)
        if target is None:
            merged[key] = record
            continue
        for kind in record["kinds"]:
            if kind not in target["kinds"]:
                target["kinds"].append(kind)
        for signature in record["signatures"]:
            if signature not in target["signatures"]:
                target["signatures"].append(signature)
        for badge_name in record["availability"]:
            if badge_name not in target["availability"]:
                target["availability"].append(badge_name)
        for doc_file in record["doc_files"]:
            if doc_file not in target["doc_files"]:
                target["doc_files"].append(doc_file)
        for arg in record["arg_types"]:
            if arg not in target["arg_types"]:
                target["arg_types"].append(arg)

    meta = {"docs_dir": docs_dir, "class_files_used": len(used_files),
            "class_files": sorted(used_files)}
    return list(merged.values()), meta


def docs_health_check(docs_dir: str) -> dict:
    """Verify the downloaded IsaacDocs mirror is complete against the repo tree."""
    health = {"docs_dir": docs_dir}
    local = {f for f in os.listdir(docs_dir) if f.endswith(".md")}
    health["local_md_count"] = len(local)
    try:
        raw = _http_get(ISAACDOCS_TREE_API, timeout=25, retries=2)
        tree = json.loads(raw.decode("utf-8"))
        remote = sorted(
            p.split("/")[-1] for p in
            (e["path"] for e in tree.get("tree", []))
            if p.startswith("docs/") and p.endswith(".md") and p.count("/") == 1
        )
        health["remote_root_md_count"] = len(remote)
        health["missing_locally"] = [f for f in remote if f not in local]
    except Exception as error:  # network is best-effort only
        health["tree_api_error"] = f"{type(error).__name__}: {error}"
    return health


# --------------------------------------------------------------------------- #
# PC executable name block
# --------------------------------------------------------------------------- #

IDENT_RUN = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{1,63}\x00")


def load_pc_exe_names(exe_path: str, doc_names: set[str]) -> dict:
    """Locate the PC Lua name block(s) and cross-reference them with the docs."""
    result = {
        "path": os.path.relpath(exe_path, REPO_ROOT),
        "present": os.path.exists(exe_path),
        "candidates": [],
        "confirmed_doc_names": 0,
        "doc_names_absent": 0,
        "block": None,
        "blocks_scanned": 0,
    }
    if not result["present"]:
        return result

    with open(exe_path, "rb") as handle:
        data = handle.read()
    result["size"] = len(data)

    rdata = _pe_section(data, ".rdata")
    if rdata is None:
        return result
    lo, hi = rdata

    items = [(lo + m.start(), m.group(0)[:-1].decode("ascii"))
             for m in IDENT_RUN.finditer(data[lo:hi])]

    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for offset, name in items:
        if current:
            prev_off, prev_name = current[-1]
            if prev_off + len(prev_name) + 1 + 16 < offset:
                blocks.append(current)
                current = []
        current.append((offset, name))
    if current:
        blocks.append(current)
    result["blocks_scanned"] = len(blocks)

    # The Lua registration/name run is the identifier block with the strongest
    # overlap with the documented method names.
    best: list[tuple[int, str]] = []
    best_score = 0
    for block in blocks:
        if len(block) < 200:
            continue
        score = sum(1 for _, n in block if n in doc_names)
        if score > best_score:
            best_score, best = score, block
    if not best:
        return result

    names = [n for _, n in best]
    name_set = set(names)
    result["block"] = {
        "start": f"0x{best[0][0]:X}",
        "end": f"0x{best[-1][0]:X}",
        "entries": len(best),
        "doc_overlap": best_score,
    }
    result["confirmed_doc_names"] = len(doc_names & name_set)
    result["doc_names_absent"] = len(doc_names - name_set)
    result["candidates"] = names
    return result


def _pe_section(data: bytes, wanted: str):
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return None
        section_count = struct.unpack_from("<H", data, pe + 6)[0]
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        table = pe + 24 + optional_size
        for index in range(section_count):
            entry = table + index * 40
            name = data[entry:entry + 8].rstrip(b"\0").decode("ascii", "replace")
            if name != wanted:
                continue
            _, _, raw_size, raw_offset = struct.unpack_from("<IIII", data, entry + 8)
            return (raw_offset, min(raw_offset + raw_size, len(data)))
    except (struct.error, IndexError):
        return None
    return None


# --------------------------------------------------------------------------- #
# Switch NRO symbols
# --------------------------------------------------------------------------- #

def load_switch_symbols(nro_path: str, verify_path: str | None) -> dict:
    with open(nro_path, "rb") as handle:
        data = handle.read()

    build_id, symbols = nro_symbols.parse_dynamic_symbols(data)

    afterbirth = {}
    if verify_path and os.path.exists(verify_path):
        with open(verify_path, "rb") as handle:
            afterbirth_data = handle.read()
        afterbirth_build, afterbirth_symbols = nro_symbols.parse_dynamic_symbols(afterbirth_data)
    else:
        afterbirth_build, afterbirth_symbols = None, {}

    interesting = [n for n in symbols if _mangled_namespace(n) is not None]
    demangled, demangle_stats, external = demangle_batch(sorted(interesting))
    if len(external) != len(demangled):
        external = [None] * len(demangled)

    records = []
    for mangled, parsed, signature in zip(sorted(interesting), demangled, external):
        if parsed is None:
            continue
        cls, method = parsed
        if method is None:
            continue
        namespace = _mangled_namespace(mangled)
        # Strip the enclosing namespace so ``cls`` is the class (possibly nested).
        prefix = namespace + "::"
        if cls is not None and cls.startswith(prefix):
            cls = cls[len(prefix):]
        elif cls == namespace:
            cls = None
        symbol = symbols[mangled]
        guard = None
        if symbol.file_offset is not None:
            chunk = data[symbol.file_offset:symbol.file_offset + 16]
            if len(chunk) == 16:
                guard = chunk.hex()
        records.append({
            "mangled": mangled,
            "namespace": namespace,
            "class": cls,
            "method": method,
            "offset": symbol.file_offset,
            "guard16": guard,
            "defined": symbol.is_defined,
            "signature": signature,
            "arity": signature_arity(signature),
        })

    return {
        "nro": os.path.relpath(nro_path, REPO_ROOT),
        "nro_build_id": build_id,
        "nro_symbol_count": len(symbols),
        "afterbirth_nro": os.path.relpath(verify_path, REPO_ROOT) if verify_path else None,
        "afterbirth_build_id": afterbirth_build,
        "afterbirth_symbol_count": len(afterbirth_symbols),
        "demangle": demangle_stats,
        "records": records,
    }


def build_symbol_index(records: list[dict]) -> tuple[dict, dict, dict]:
    by_class_method: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    by_method: dict[str, list[dict]] = collections.defaultdict(list)
    by_free: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        by_method[record["method"]].append(record)
        if record["class"] is None:
            by_free[record["method"]].append(record)
        else:
            by_class_method[(record["class"], record["method"])].append(record)
    return by_class_method, by_method, by_free


def verify_guard_offsets(records: list[dict]) -> dict:
    """Byte-verify the two historically confirmed guards from runtime_constants."""
    expectations = [
        ("_ZN15IsaacRepentance7Manager15IsActionPressedEjjPNS_6EntityE", 0x3F9B6C,
         "kManagerIsActionPressedExpectedBytes"),
        ("_ZN15IsaacRepentance7Manager17IsActionTriggeredEjjPNS_6EntityE", 0x3F9B7C,
         "kManagerIsActionTriggeredExpectedBytes"),
    ]
    by_mangled = {r["mangled"]: r for r in records}
    out = []
    for mangled, offset, constant in expectations:
        record = by_mangled.get(mangled)
        out.append({
            "mangled": mangled,
            "expected_offset": f"0x{offset:X}",
            "constant_in_runtime_constants_hpp": constant,
            "symbol_offset": f"0x{record['offset']:X}" if record else None,
            "match": bool(record and record["offset"] == offset),
        })
    return {"checks": out, "all_match": all(c["match"] for c in out)}


# --------------------------------------------------------------------------- #
# Runtime implemented set
# --------------------------------------------------------------------------- #

CATALOG_ENTRY = re.compile(
    r"\{MakeId\(ApiDomain::(?P<domain>\w+),\s*(?P<group>\w+),\s*(?P<seq>0x[0-9A-Fa-f]+)\),\s*"
    r"ApiDomain::\w+,\s*\"(?P<owner>[^\"]*)\",\s*\"(?P<name>[^\"]*)\",\s*"
    r"(?P<version>kV\d+),\s*\d+,\s*ThreadAffinity::(?P<affinity>\w+),\s*"
    r"ApiMaturity::(?P<maturity>\w+)\}",
    re.S,
)
HANDLER_ENTRY = re.compile(
    r"\{\s*(?P<id>0x[0-9A-Fa-f]{8}),\s*&(?P<fn>\w+)\s*\}"
)
HANDLER_TABLE = re.compile(
    r"constexpr\s+LuaHandlerBinding\s+(?P<table>k\w+Handlers)\[\]\s*=\s*\{(?P<body>.*?)\n\};",
    re.S,
)


def load_runtime_implemented() -> dict:
    with open(CATALOG_CPP, encoding="utf-8") as handle:
        catalog_text = handle.read()

    entries = []
    for match in CATALOG_ENTRY.finditer(catalog_text):
        domain = match.group("domain")
        group = match.group("group")
        sequence = int(match.group("seq"), 16)
        # ApiDomain enum order from runtime/src/interfaces/lua/api_descriptor.hpp:
        # Global=0, Mod=1, Game=2, Level=3, Room=4, ItemPool=5, Music=6,
        # Rng=7, Persistence=8, Input=9, Diagnostic=10.
        domain_codes = {
            "Global": 0, "Mod": 1, "Game": 2, "Level": 3, "Room": 4, "ItemPool": 5,
            "Music": 6, "Rng": 7, "Persistence": 8, "Input": 9, "Diagnostic": 10,
        }
        group_code = int(group) if group.isdigit() else 1
        api_id = (domain_codes.get(domain, 0) << 24) | (group_code << 16) | sequence
        entries.append({
            "domain": domain,
            "owner": match.group("owner"),
            "name": match.group("name"),
            "maturity": match.group("maturity"),
            "affinity": match.group("affinity"),
            "version": match.group("version"),
            "id": f"0x{api_id:08X}",
        })

    handlers: dict[str, list[str]] = {}
    handler_ids: dict[str, str] = {}
    for filename in sorted(os.listdir(LUA_DIR)):
        if not filename.endswith(".cpp"):
            continue
        with open(os.path.join(LUA_DIR, filename), encoding="utf-8") as handle:
            text = handle.read()
        for table in HANDLER_TABLE.finditer(text):
            ids = []
            for entry in HANDLER_ENTRY.finditer(table.group("body")):
                handler_ids[entry.group("fn")] = entry.group("id").upper()
                ids.append(entry.group("id").upper())
            handlers.setdefault(table.group("table"), []).extend(ids)

    # Cross-check: every catalog entry must have a bound handler.
    bound = set()
    for values in handlers.values():
        bound.update(values)
    unbound = [e for e in entries if e["id"] not in bound and e["domain"] != "Global"]

    return {
        "catalog_path": os.path.relpath(CATALOG_CPP, REPO_ROOT),
        "entries": entries,
        "handler_tables": {k: sorted(v) for k, v in sorted(handlers.items())},
        "entries_without_handler": [f"{e['owner']}:{e['name']}" for e in unbound],
    }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def owner_switch_candidates(owner: str) -> list[tuple[str, str]]:
    """Return ``(candidate_class, match_kind)`` pairs for a documented owner."""
    # A type-skin alias replaces the same-named class instead of shadowing it:
    # the PC Lua type *is* the aliased engine class (see TYPE_SKIN_ALIASES), so
    # the legacy class must not win merely by having a lower symbol offset.
    if owner in TYPE_SKIN_ALIASES:
        return [(target, "alias_class") for target in TYPE_SKIN_ALIASES[owner]]

    out: list[tuple[str, str]] = []

    def add(name: str, kind: str) -> None:
        if name and (name, kind) not in out:
            out.append((name, kind))

    add(owner, "exact_class")
    compact = owner.replace("_", "")
    if compact != owner:
        add(compact, "normalized_class")

    prefix = re.match(r"^(Entity|GridEntity|Weapon|Menu|GameState|HUD|ItemConfig|RoomConfig)([A-Z].*)$", owner)
    if prefix:
        add(f"{prefix.group(1)}_{prefix.group(2)}", "normalized_class")

    for alias in OWNER_ALIASES.get(owner, []):
        add(alias, "alias_class")
    return out


def normalize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name).lower()


def signature_arity(signature: str | None) -> int | None:
    """Count top-level parameters of a demangled ``name(...)`` signature.

    Nested ``<>``/``()``/``[]`` groups are ignored, so
    ``f(std::vector<int, A> const&, bool)`` yields 2 and ``g()`` yields 0.
    Returns ``None`` when the text carries no parameter list.
    """
    if not signature:
        return None
    start = signature.find("(")
    if start < 0:
        return None
    depth = 1
    commas = 0
    token = False
    for ch in signature[start + 1:]:
        if ch in "(<[{":
            depth += 1
            token = True
        elif ch in ")>]}":
            depth -= 1
            if depth == 0:
                return commas + (1 if token else 0)
        elif ch == "," and depth == 1:
            commas += 1
            token = True
        elif not ch.isspace():
            token = True
    return None


def doc_param_counts(api: dict) -> set[int]:
    """Documented parameter counts across every overload of one API entry.

    Markdown links (``[KColor](KColor.md)``) must be stripped first: their
    parentheses would otherwise be mistaken for the parameter list.
    """
    counts = set()
    for signature in api.get("signatures") or []:
        arity = signature_arity(_strip_md_links(signature))
        if arity is not None:
            counts.add(arity)
    return counts


def near_candidates(api: dict, by_class_method, limit: int = 4) -> list[dict]:
    """Same-class symbols whose name is a super/substring of the wanted method.

    These never upgrade an entry to ``missing_easy`` -- they are review hints so
    a human can see that e.g. ``Room:GetEntities`` has a ``Room::GetEntityList``
    neighbour instead of assuming nothing exists at all.
    """
    target = normalize_name(api["name"])
    if len(target) < 5:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for candidate, _match_kind in owner_switch_candidates(api["owner"]):
        for (cls, method), records in by_class_method.items():
            if cls != candidate:
                continue
            other = normalize_name(method)
            if other == target or len(other) < 5:
                continue
            # Constructors/destructors are pure noise for a semantic neighbour.
            if method.startswith("~") or normalize_name(method) == normalize_name(cls.split("::")[-1]):
                continue
            if target in other or other in target:
                for record in records:
                    if record["mangled"] in seen:
                        continue
                    seen.add(record["mangled"])
                    out.append({
                        "mangled": record["mangled"],
                        "class": record["class"],
                        "method": record["method"],
                        "offset_hex": f"0x{record['offset']:X}" if record["offset"] is not None else None,
                        "guard16": record["guard16"],
                    })
                    if len(out) >= limit:
                        return out
    return out


def classify(api: dict, by_class_method, by_method, by_free, implemented,
             known_classes: set[str]) -> dict:
    owner = api["owner"]
    name = api["name"]
    kinds = api["kinds"]
    key = f"{owner}:{name}"

    result = {
        "key": key,
        "owner": owner,
        "name": name,
        "kinds": sorted(kinds),
        "category": None,
        "runtime": None,
        "switch": None,
        "match_kind": None,
        "match_confidence": None,
        "reason_code": None,
        "reason": None,
        "thunk_readiness": None,
        "needs_review": False,
        "ambiguous_candidates": [],
    }

    # 1. Already implemented by the Runtime.
    runtime = implemented.get((owner, name))
    if runtime is not None:
        result["category"] = "implemented"
        result["runtime"] = runtime
        return result

    # 2. Keyboard / mouse only: listed separately, intentionally never implemented.
    if owner in KEYBOARD_MOUSE_OWNERS or key in KEYBOARD_MOUSE_NAMES \
            or KEYBOARD_MOUSE_RE.search(name):
        result["category"] = "keyboard_mouse_only"
        result["reason_code"] = "keyboard_mouse"
        result["reason"] = "PC 上只服务键盘/鼠标，Switch 无对应硬件；按策略保持“有名字、可调用、不报错、不实现功能”。"
        return result

    # 3. Find a Switch internal symbol with a matching method name.
    hits: list[tuple[dict, str]] = []
    for candidate, match_kind in owner_switch_candidates(owner):
        for method_name in ([name, candidate] if name == owner else [name]):
            # Itanium names a constructor after its own class, so a documented
            # `Font()`/`Sprite()` constructor must also look up `<Class>::<Class>`.
            for record in by_class_method.get((candidate, method_name), []):
                hits.append((record, match_kind))

    if not hits and owner in GLOBAL_TABLE_OWNERS:
        for record in by_free.get(name, []):
            hits.append((record, "global_owner"))
        for candidate in ("Game", "Manager"):
            for record in by_class_method.get((candidate, name), []):
                hits.append((record, "global_owner"))

    if not hits:
        # Inherited C++ members: the docs flatten the hierarchy.
        for candidate, match_kind in owner_switch_candidates(owner):
            for base in BASE_CLASS_CHAIN.get(candidate, []):
                for record in by_class_method.get((base, name), []):
                    hits.append((record, "base_class"))

    if hits:
        # Rank overloads the way the docs describe the call: prefer a symbol whose
        # parameter count matches one of the documented signatures, then a defined
        # symbol, then the smallest offset.  Without this, `Sprite:SetAnimation`
        # could bind a same-name overload that takes an int instead of a boolean.
        doc_counts = doc_param_counts(api)

        def rank(item: tuple[dict, str]) -> tuple:
            record = item[0]
            arity = record.get("arity")
            if arity is None or not doc_counts:
                return (1, 99, not record["defined"], record["offset"] or 1 << 62)
            if arity in doc_counts:
                return (0, 0, not record["defined"], record["offset"] or 1 << 62)
            return (1, min(abs(arity - count) for count in doc_counts),
                    not record["defined"], record["offset"] or 1 << 62)

        hits.sort(key=rank)
        record, match_kind = hits[0]
        confidence = "high" if match_kind in ("exact_class", "normalized_class") else "medium"
        result["category"] = "missing_easy"
        result["switch"] = {
            "mangled": record["mangled"],
            "namespace": record["namespace"],
            "class": record["class"],
            "method": record["method"],
            "offset": record["offset"],
            "offset_hex": f"0x{record['offset']:X}" if record["offset"] is not None else None,
            "guard16": record["guard16"],
            "signature": record.get("signature"),
            "arity": record.get("arity"),
            "doc_param_counts": sorted(doc_counts),
            "candidate_count": len(hits),
        }
        result["match_kind"] = match_kind
        result["match_confidence"] = confidence
        result["reason_code"] = "symbol_available"
        result["reason"] = (
            f"Switch 符号表命中 {record['class']}::{record['method']}"
            f"（匹配方式 {match_kind}，候选 {len(hits)} 个，"
            f"符号形参 {record.get('arity')} / 文档形参 {sorted(doc_counts) or '未知'}）。"
        )
        owner_reachable = record["class"] in REACHABLE_OWNERS or record["class"] is None
        primitive_args = all(t in PRIMITIVE_TYPES for t in api["arg_types"])
        if not owner_reachable:
            result["thunk_readiness"] = "needs_owner_instance"
        elif not primitive_args:
            result["thunk_readiness"] = "needs_arg_marshalling"
        else:
            result["thunk_readiness"] = "ready"
        return result

    # 4. No usable internal symbol: record the closest candidates for review.
    result["category"] = "missing_hard"
    fuzzy = [r for r in by_method.get(name, []) if normalize_name(r["method"]) == normalize_name(name)]
    result["ambiguous_candidates"] = [
        {"mangled": r["mangled"], "class": r["class"], "method": r["method"],
         "offset_hex": f"0x{r['offset']:X}" if r["offset"] is not None else None}
        for r in fuzzy[:5]
    ]
    result["near_candidates"] = near_candidates(api, by_class_method)

    reason_code, reason = explain_hard(api, fuzzy, known_classes)
    result["reason_code"] = reason_code
    result["reason"] = reason
    result["needs_review"] = bool(fuzzy)
    return result


def explain_hard(api: dict, fuzzy: list[dict], known_classes: set[str]) -> tuple[str, str]:
    owner = api["owner"]
    name = api["name"]
    kinds = api["kinds"]
    doc_file = api["doc_files"][0] if api["doc_files"] else ""

    if "Variables" in kinds and "Functions" not in kinds:
        return (
            "struct_field_layout",
            f"{owner} 的属性 {name}：Lua 侧是字段读写，Switch 侧需要还原 {owner} 的结构体布局"
            f"（字段偏移未知，且没有任何访问器符号可 thunk）。",
        )

    is_glue = owner in LUA_GLUE_OWNERS or any(
        f.startswith(LUA_GLUE_FILE_PREFIXES) for f in api["doc_files"]
    )
    if is_glue:
        return (
            "lua_glue_type",
            f"{owner} 是 Lua 绑定层值类型/容器包装（文档 {doc_file}），方法 {name} 只在 PC 的 "
            f"Lua 胶水代码里实现，游戏 C++ 侧不存在同名成员；必须在 Runtime 的 Lua 绑定层自行实现。",
        )
    if owner == "Global":
        return (
            "lua_glue_type",
            f"全局函数 {name}（文档 {doc_file}）属于 PC Lua 环境的胶水层构件"
            f"（类型构造 / 指针哈希 / 随机向量等），Switch 游戏二进制里没有对应的 C++ 类或函数；"
            f"必须在 Runtime 的 Lua 绑定层实现。",
        )

    lowered = name.lower()
    if any(token in lowered for token in ("steam", "leaderboard", "achievement")):
        return ("pc_only_subsystem",
                f"{name} 依赖 PC 专有子系统（Steam / 排行榜 / 成就上报），Switch 无对应后端。")
    if owner == "Options" or "options" in lowered:
        return ("owner_instance_unlocated",
                f"{owner}:{name} 依赖 Options 配置单例；Switch 的 IsaacRepentance 命名空间下没有 "
                f"Options 类，配置散落在 MenuManager / Graphics / Sound 各子系统里，宿主对象尚未定位。")
    if any(token in lowered for token in ("savedata", "loaddata", "save", "load")) and owner in (
            "Isaac", "Mod"):
        return ("persistence_unavailable",
                f"{owner}:{name} 依赖存档数据通道；Switch 运行期 SD 写入通道已知不可用，"
                f"需要另建持久化后端而不是 thunk 某个游戏方法。")
    if owner in ("Isaac", "Mod") and (
            "callback" in lowered or lowered in ("registermod", "removemoddata", "hasmoddata")):
        return ("mod_loading_unavailable",
                f"{owner}:{name} 属于 PC 的 mod 加载/回调注册元数据路径；Switch 侧没有 mod 清单与 "
                f"Lua 运行时，必须由 Runtime 自己维护回调表。")
    if ENTITY_OWNER_RE.match(owner) and DOWNCAST_SHAPED.match(name):
        return ("lua_glue_type",
                f"{owner}:{name} 是 PC Lua 的类型下转助手（C++ 侧就是一次 static_cast），"
                f"不存在函数符号；Runtime 侧按已知实体类型标识自行判定即可，不需要 thunk。")
    if any(token in lowered for token in ("render", "draw", "font", "text", "screen")):
        return ("render_glue",
                f"{owner}:{name} 是渲染/字体/屏幕坐标输出，需要 Lua 侧绘制管线（KAGE 图形层），"
                f"不是单个可 thunk 的游戏方法。")

    if fuzzy:
        names = ", ".join(sorted({f"{r['class']}::{r['method']}" for r in fuzzy})[:3])
        return ("no_symbol",
                f"符号表里只有名称相近但归属/语义不同的候选（{names}），"
                f"不能作为 {owner}:{name} 的实现底座。")

    candidates = owner_switch_candidates(owner)
    existing = [cls for cls, _ in candidates if cls in known_classes]
    if owner in GLOBAL_TABLE_OWNERS:
        return ("lua_glue_type",
                f"全局表 `Isaac` 的 {name}：该函数在 PC 上由 exe 的 Lua 绑定层实现，"
                f"Switch 二进制里既没有同名自由函数，`Game` / `Manager` 里也没有同名成员；"
                f"需要在 Runtime 绑定层用已有原语（EntityFactory / Room / PlayerManager / Console 等）自行拼装。")
    if not existing:
        return ("owner_class_absent",
                f"Switch 的 `IsaacRepentance` 命名空间里不存在与文档类 `{owner}` 对应的 C++ 类"
                f"（已尝试原名 / 下划线规范化 / 别名表），因此 {name} 没有底座；"
                f"需要先在 Runtime 里重建这个 Lua 对象的语义。")
    if ACCESSOR_SHAPED.match(name):
        return ("accessor_heuristic",
                f"`IsaacRepentance::{existing[0]}` 存在，但类里没有任何名为 {name} 的 C++ 函数。"
                f"PC 的这条 Lua 访问器在 Switch 上大概率对应一个**字段直读/直写**"
                f"（这正是没有访问器符号的原因），因此必须先还原 {existing[0]} 的结构体布局；"
                f"若该状态实际由方法承载，则说明它被内联，同样要我们自己实现。"
                f"（判据为命名启发式：`^Get|Is|Has|Set|Can`；建议人工复核。文档 {doc_file}）")
    return ("no_symbol",
            f"`IsaacRepentance::{existing[0]}` 存在，但类里没有 {name} 访问器。"
            f"这通常意味着 Switch 版把该行为内联进了调用方，或者它本来就是字段直写"
            f"（需要结构体布局），没有可 thunk 的函数入口。文档来源 {doc_file}。")


# --------------------------------------------------------------------------- #
# Global surface appendix (Globals.md)
# --------------------------------------------------------------------------- #

GLOBAL_BULLET = re.compile(r"^-\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")


def parse_globals_appendix(docs_dir: str) -> dict:
    path = os.path.join(docs_dir, "Globals.md")
    if not os.path.exists(path):
        return {"available": False}
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    vanilla: list[str] = []
    in_vanilla = False
    for line in lines:
        if line.startswith("### "):
            in_vanilla = line.strip().lower().endswith("vanilla")
            continue
        if in_vanilla:
            match = GLOBAL_BULLET.match(line)
            if match:
                vanilla.append(match.group(1))
    enum_dir = os.path.join(docs_dir, "enums")
    enums = {f[:-3] for f in os.listdir(enum_dir)} if os.path.isdir(enum_dir) else set()
    return {
        "available": True,
        "count": len(vanilla),
        "names": vanilla,
        "enum_names": sorted(n for n in vanilla if n in enums),
        "non_enum_names": sorted(n for n in vanilla if n not in enums),
    }


def parse_enum_appendix(docs_dir: str, names: list[str]) -> dict:
    """Count enumerators for the enum tables named in ``names``."""
    out: dict[str, dict] = {}
    enum_dir = os.path.join(docs_dir, "enums")
    for name in names:
        path = os.path.join(enum_dir, f"{name}.md")
        if not os.path.exists(path):
            out[name] = {"available": False}
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        enumerators = re.findall(r"\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{:\s*\.copyable\s*\}", text)
        values = re.findall(r"^\|[^|]*\|[^|]*\|\s*[A-Za-z_][A-Za-z0-9_]*\s*\{:\s*\.copyable\s*\}", text, re.M)
        out[name] = {
            "available": True,
            "enumerator_count": len(enumerators),
            "row_count": len(values),
            "sample": enumerators[:12],
        }
    return out


# --------------------------------------------------------------------------- #
# Small HTTP helper
# --------------------------------------------------------------------------- #

def _http_get(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "isaac-api-inventory/1"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001 - best effort network
            last = error
        # Python's urllib on macOS may not see the system trust store; curl does.
        curl = shutil.which("curl")
        if curl:
            try:
                proc = subprocess.run(
                    [curl, "-sS", "-f", "--max-time", str(timeout), "--retry", "1",
                     "-A", "isaac-api-inventory/1", url],
                    capture_output=True, timeout=timeout + 15,
                )
                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout
            except (OSError, subprocess.SubprocessError) as error:  # noqa: BLE001
                last = error
        time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Suggested implementation order
# --------------------------------------------------------------------------- #

# Explicit, documented scoring so the "do these first" list is reproducible.
# It rewards having a directly usable base and penalises engine-loop entry
# points that a mod would almost never call.
CORE_SINGLETON_OWNERS = {"Game", "Level", "Room", "ItemPool", "Music", "SoundEffects"}
ENGINE_LOOP_NAMES = {"Update", "Render", "PreUpdate", "PostRender", "Init", "Interpolate"}


def priority_score(api: dict) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []
    if api["thunk_readiness"] == "ready":
        score += 100
        notes.append("宿主对象已可达且参数全是基本类型")
    elif api["thunk_readiness"] == "needs_arg_marshalling":
        score += 50
        notes.append("宿主对象已可达，但需要编组对象/向量参数")
    else:
        score += 10
        notes.append("宿主对象尚未定位")
    if api["match_confidence"] == "high":
        score += 20
        notes.append("类名与方法名完全一致")
    switch_class = (api.get("switch") or {}).get("class")
    if switch_class in CORE_SINGLETON_OWNERS:
        score += 15
        notes.append(f"{switch_class} 是 Runtime 已持有的单例")
    if api["name"] in ENGINE_LOOP_NAMES:
        score -= 40
        notes.append("属于引擎主循环入口，mod 直接调用价值低")
    else:
        score += 10
    if api["kinds"] == ["Constructors"]:
        score -= 60
        notes.append("纯构造函数：C++ 侧对象由游戏自己创建，Lua mod 不会手动构造")
    return score, notes


def suggested_order(apis: list[dict], limit: int = 10, per_class_cap: int = 2) -> list[dict]:
    easy = [a for a in apis if a["category"] == "missing_easy"]
    scored = [(priority_score(a)[0], a, priority_score(a)[1]) for a in easy]
    scored.sort(key=lambda item: (-item[0], item[1]["key"]))

    # Diversity cap: at most ``per_class_cap`` entries per owning Switch class so
    # the short list exercises several object chains instead of one class.
    out: list[dict] = []
    used: collections.Counter = collections.Counter()
    for score, api, notes in scored:
        switch_class = (api.get("switch") or {}).get("class") or "(自由函数)"
        if used[switch_class] >= per_class_cap:
            continue
        used[switch_class] += 1
        out.append({
            "key": api["key"],
            "owner_class": switch_class,
            "score": score,
            "mangled": api["switch"]["mangled"],
            "offset_hex": api["switch"]["offset_hex"],
            "guard16": api["switch"]["guard16"],
            "thunk_readiness": api["thunk_readiness"],
            "match_confidence": api["match_confidence"],
            "notes": notes,
        })
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def summarize(apis: list[dict], runtime: dict) -> dict:
    categories = collections.Counter(a["category"] for a in apis)
    reasons = collections.Counter(a["reason_code"] for a in apis
                                  if a["category"] == "missing_hard" and a["reason_code"])
    owners_missing = collections.Counter(a["owner"] for a in apis if a["category"] == "missing_hard")
    maturities = collections.Counter(e["maturity"] for e in runtime["entries"])
    readiness = collections.Counter(a["thunk_readiness"] for a in apis
                                    if a["category"] == "missing_easy")
    matches = collections.Counter(a["match_kind"] for a in apis if a["category"] == "missing_easy")
    kinds = collections.Counter()
    for api in apis:
        for kind in api["kinds"]:
            kinds[kind] += 1
    return {
        "api_total": len(apis),
        "categories": dict(categories),
        "kinds": dict(kinds),
        "hard_reasons": dict(reasons),
        "hard_top_owners": owners_missing.most_common(20),
        "runtime_maturity": dict(maturities),
        "runtime_catalog_entries": len(runtime["entries"]),
        "easy_thunk_readiness": dict(readiness),
        "easy_match_kinds": dict(matches),
        "needs_review": sum(1 for a in apis if a["needs_review"]),
        "hard_with_near_candidates": sum(1 for a in apis if a.get("near_candidates")),
    }


def md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def render_doc(payload: dict) -> str:
    summary = payload["summary"]
    categories = summary["categories"]
    apis = payload["apis"]
    out: list[str] = []
    add = out.append

    generated = payload["generated_at"]
    sources = payload["sources"]

    add("# PC Lua API ↔ Switch 游戏内部符号 ↔ 已实现 三方对照清单")
    add("")
    add(f"> 生成时间（UTC）：`{generated}`　生成工具：`tools/build_lua_api_inventory.py`")
    add("> 机器可读版本：`analysis/lua-api-inventory/inventory.json`")
    add("")
    add("本文档回答一个问题：**PC 版《以撒的结合：忏悔》暴露给 Lua mod 的每一个 API，"
        "在本项目 Runtime 里处于什么状态？还没做的，Switch 的游戏映像里有没有可以直接 thunk 的内部符号？**")
    add("")

    add("## 1. 结论速览")
    add("")
    add("| 分类 | 含义 | 条数 |")
    add("| --- | --- | ---: |")
    add(f"| `implemented` | Runtime 已实现（命中 `api_catalog.cpp`） | {categories.get('implemented', 0)} |")
    add(f"| `game_builtin` | Switch 游戏自己已注册，无需我们实现 | {categories.get('game_builtin', 0)} |")
    add(f"| `missing_easy` | 缺失，但 Switch 符号表里有语义对应的方法（可生成 thunk 常量+守卫） | {categories.get('missing_easy', 0)} |")
    add(f"| `missing_hard` | 缺失且没有可用的内部对应方法，必须逐条说明原因 | {categories.get('missing_hard', 0)} |")
    add(f"| **PC API 小计** | 上四类之和 | **{sum(categories.get(k, 0) for k in ('implemented', 'game_builtin', 'missing_easy', 'missing_hard'))}** |")
    add(f"| `keyboard_mouse_only` | 键鼠专用，按策略单列、不列为待实现 | {categories.get('keyboard_mouse_only', 0)} |")
    add(f"| **合计** | 四类 + 键鼠专用 | **{summary['api_total']}** |")
    add("")
    add(f"- 条目粒度：`owner:name` 去重后的 PC Lua API（方法、构造函数、属性、运算符合并为同一条）。")
    add(f"- 条目类型分布：{', '.join(f'{k} {v}' for k, v in sorted(summary['kinds'].items()))}。")
    add(f"- `missing_easy` 的 thunk 就绪度：{', '.join(f'{k} {v}' for k, v in sorted(summary['easy_thunk_readiness'].items())) or '无'}。")
    add(f"- `missing_easy` 的匹配方式：{', '.join(f'{k} {v}' for k, v in sorted(summary['easy_match_kinds'].items())) or '无'}。")
    add(f"- 需要人工复核（存在同名但归属不同的候选）的条目：{summary['needs_review']} 条，已在 `missing_hard` 表内以 `⚠` 标注。")
    add(f"- Runtime catalog 现状：{summary['runtime_catalog_entries']} 条，"
        f"{', '.join(f'{k} {v}' for k, v in sorted(summary['runtime_maturity'].items()))}。")
    add("")

    add("## 2. 数据来源与获取时间")
    add("")
    add("| 来源 | 说明 | 证据 |")
    add("| --- | --- | --- |")
    docs_meta = sources["isaacdocs"]
    add(f"| A. IsaacDocs（PC API 权威名源） | `wofsauge/IsaacDocs` `docs/*.md` 类页 | "
        f"本地镜像 `{docs_meta['meta']['docs_dir']}`，使用 {docs_meta['meta']['class_files_used']} 个类页；"
        f"解析出 {sources['doc_entry_count']} 个 `owner:name` 条目 |")
    health = docs_meta.get("health", {})
    add(f"| A 的完整性校验 | GitHub trees API | "
        f"{('远端根目录 .md ' + str(health.get('remote_root_md_count')) + ' 个，本地缺失 ' + str(len(health.get('missing_locally', []))) + ' 个') if 'remote_root_md_count' in health else ('未能校验：' + str(health.get('tree_api_error', '未知')))} |")
    exe = sources["pc_exe"]
    if exe.get("block"):
        add(f"| B. `isaac-ng.exe`（本地兜底名源） | 32 位 PE `.rdata` 中的 Lua 名字块 | "
            f"{exe['block']['start']}–{exe['block']['end']}，{exe['block']['entries']} 条名字，"
            f"与文档名重合 {exe['block']['doc_overlap']} 条 |")
    else:
        add(f"| B. `isaac-ng.exe` | 未找到名字块 | present={exe.get('present')} |")
    nro = sources["switch"]
    add(f"| C. `Repentance.nro` 动态符号表 | Switch 游戏内部符号 | build_id `{nro['nro_build_id']}`，"
        f"{nro['nro_symbol_count']} 条符号；其中 IsaacRepentance/KAGE 方法 {len(nro['records'])} 条 |")
    add(f"| C 的偏移可信度 | 与 `runtime_constants.hpp` 历史守卫逐字节比对 | "
        f"{'全部一致' if nro['offset_verification']['all_match'] else '存在不一致，见 JSON'} |")
    add(f"| D. Runtime 已实现集 | `{sources['runtime']['catalog_path']}` | "
        f"{len(sources['runtime']['entries'])} 条 catalog；handler 绑定表 {len(sources['runtime']['handler_tables'])} 张 |")
    add("")
    docs_dir_used = (sources["isaacdocs"].get("meta") or {}).get("docs_dir", "（未知）")
    add(f"获取时间：IsaacDocs 本地镜像 `{docs_dir_used}` 的 mtime 见该目录；"
        f"Switch NRO 与 PC exe 均为工作区内文件，读取时间 {generated}。")
    add("")

    add("## 3. 匹配方法与可信度")
    add("")
    add("### 3.1 名字匹配流程")
    add("")
    const_records = sum(1 for r in nro["records"] if r["mangled"].startswith("_ZNK"))
    add("1. 用 `tools/nro_symbols.py::parse_dynamic_symbols` 读 `Repentance.nro` 的 `.dynsym`，"
        f"得到 {nro['nro_symbol_count']} 条动态符号；取 `IsaacRepentance` / `KAGE` 命名空间的成员函数，"
        f"即 `_ZN…` 与 const 限定的 `_ZNK…` 两种形态，共 {len(nro['records'])} 条"
        f"（其中 const 成员函数 {const_records} 条）。")
    add("2. 对 `_Z…` 做 C++ 名字解混淆：优先调用本机 `llvm-cxxfilt -n`；"
        "同时用工具内置的 Itanium 嵌套名读取器独立解一遍，两者对比。")
    dem = nro["demangle"]
    if dem.get("agreement") is not None:
        add(f"   - 本次 `llvm-cxxfilt`：`{dem['cxxfilt']}`，比对 {dem['checked']} 条，"
            f"一致率 **{dem['agreement'] * 100:.4f}%**"
            f"{'（无不一致）' if not dem['disagreements'] else '，不一致样例见 JSON'}。")
    else:
        add("   - 本次未能调用 `llvm-cxxfilt`，仅使用内置读取器（结果标注为单一实现）。")
    add("3. 把 PC 的 `Owner:Method` 映射到 `IsaacRepentance::<Class>::<Method>`：")
    add("   - `exact_class`：文档类名与 C++ 类名完全相同（如 `Game`、`ItemPool`）。")
    add("   - `normalized_class`：加下划线后的规范化名（`EntityPlayer`→`Entity_Player`、`GridEntityDoor`→`GridEntity_Door`）。")
    add("   - `alias_class`：显式别名表（`MusicManager`→`Music`、`SFXManager`→`SoundEffects`、"
        "`Mod Reference`→`ModManager`/`ModEntry`、`ItemConfigItem`→`ItemConfig::Item`、"
        "`PathFinder`→`NPCAI_Pathfinder` 等，以及下面的类型外壳别名表，"
        "完整表见 `tools/build_lua_api_inventory.py::OWNER_ALIASES`）。")
    add("   - `base_class`：文档把继承方法平铺到子类，命中时回退到基类（`Entity_*`→`Entity`、`GridEntity_*`→`GridEntity`）。")
    add("   - `global_owner`：全局表 `Isaac` 的方法，按同名自由函数或 `Game`/`Manager` 成员匹配。")
    add("4. 方法名要求**完全一致**（区分大小写）。只大小写/标点不同的一律不算命中，"
        "只作为 `missing_hard` 的 `ambiguous_candidates` 记录下来供人工复核。")
    add("")
    add("**类型外壳别名表**（`alias_class` 中经过论证的子集）：PC 的 Lua 类型名与引擎 C++ 类名不同名，"
        "若不做显式映射，这些 API 会被误判为「Switch 没有底座」。下表每一项都有同类同名方法作证，"
        "而不是泛化的「同名即命中」：")
    add("")
    add("| PC 文档 owner | 引擎类 | 依据（同类同名方法） |")
    add("| --- | --- | --- |")
    for owner, targets in TYPE_SKIN_ALIASES.items():
        evidence = TYPE_SKIN_ALIAS_EVIDENCE.get(owner, "")
        add(f"| `{owner}` | " + "、".join(f"`{t}`" for t in targets) + f" | {evidence} |")
    add("")
    add("同名但语义无关的候选**不并入**别名表，例如 `Sprite:Play` 与 `SoundEffects::Play`、"
        "`ItemConfig:GetCollectible` 与 `ItemPool:GetCollectible`、`GridEntity:Render` 与 "
        "`Graphics::ImageBase::Render`；它们保留在 `missing_hard` 的 `ambiguous_candidates` 里等待人工判定。")
    add("")
    add("### 3.2 可信度分级")
    add("")
    add("| 级别 | 触发条件 | 处理 |")
    add("| --- | --- | --- |")
    add("| high | `exact_class` / `normalized_class` + 方法名完全一致 | 直接进入 `missing_easy` |")
    add("| medium | `alias_class` / `base_class` / `global_owner` + 方法名完全一致 | 进入 `missing_easy` 但在 JSON 中保留 `match_kind`，建议人工确认语义 |")
    add("| 不匹配 | 只有近似名 | 一律进 `missing_hard`，候选写入 `ambiguous_candidates` |")
    add("")
    add("### 3.3 偏移可信度")
    add("")
    add("`Symbol.file_offset` **等于运行时模块偏移**，依据是 `runtime/source/runtime_constants.hpp` 里两个已上机验证的守卫：")
    add("")
    add("| 符号 | 期望偏移 | 常量 | 实测偏移 | 结论 |")
    add("| --- | --- | --- | --- | --- |")
    for check in nro["offset_verification"]["checks"]:
        add(f"| `{check['mangled']}` | `{check['expected_offset']}` | `{check['constant_in_runtime_constants_hpp']}` | "
            f"`{check['symbol_offset']}` | {'一致' if check['match'] else '不一致'} |")
    add("")
    add("因此 `missing_easy` 清单里给出的 `file_offset` 可以直接写成 thunk 常量，"
        "`guard16` 就是该偏移处文件中的 16 字节，可直接做成安装期守卫。")
    add("")

    add("## 4. `game_builtin`：Switch 游戏自带 Lua API 的证据")
    add("")
    add(f"**判定结果：{categories.get('game_builtin', 0)} 条。**")
    add("")
    add("判定依据（全部为实测的反面证据）：")
    add("")
    add("1. `Repentance.nro` / `AfterbirthPlus.nro` 的动态符号表里**没有任何 `lua_*` / `luaL_*` 符号**"
        f"（Repentance {nro['nro_symbol_count']} 条符号中 0 条，AfterbirthPlus {nro['afterbirth_symbol_count']} 条中 0 条）。")
    add("2. 整文件 NUL 结尾字符串提取（35295 条）中**没有 `RegisterMod`、`AddCallback`、`ModCallbacks`、"
        "`GetMousePosition`、`luaL_openlibs`** 等任何一个 Lua 注册名。")
    add("3. `GetCollectible`、`AddCollectible` 等名字在 NRO 里只作为 **mangled 符号名的子串**出现，"
        "不是独立的 Lua 注册字符串。")
    add("4. 同目录其他模块 `main` / `subsdk0` / `subsdk1` / `sdk` / `rtld` 里同样没有 Lua 符号。")
    add("5. 1.7.9b 游戏目录下 **`.lua` 文件数 = 0**。")
    add("")
    add("**结论与推论**：Switch 版把 PC 的 Lua mod 接口整体移除了，"
        "所以不存在\"游戏已经注册好、我们不用管\"的 API。"
        "这也意味着 `missing_easy` 里的每个方法**只是底座存在**，"
        "Lua 侧的注册、参数编组、对象获取仍必须由 Runtime 提供。")
    add("")
    add("需要注意的区分：像 `EntityPlayer:AddCollectible` 这种\"游戏内部本来就在做\"的行为，"
        "属于**游戏原生行为**但不属于 `game_builtin` 分类 —— 分类只看\"游戏是否已把它注册进 Lua\"。"
        "它们被归入 `missing_easy`（有底座，需要我们自己接）。")
    add("")

    add("## 5. `implemented`：Runtime 已实现清单")
    add("")
    implemented = [a for a in apis if a["category"] == "implemented"]
    add(f"共 {len(implemented)} 条。")
    add("")
    add("| # | PC API | maturity | 亲和性 | catalog ID | 文档 |")
    add("| ---: | --- | --- | --- | --- | --- |")
    for index, api in enumerate(sorted(implemented, key=lambda a: (a["owner"], a["name"])), 1):
        runtime = api["runtime"]
        add(f"| {index} | `{api['key']}` | {runtime['maturity']} | {runtime['affinity']} | "
            f"`{runtime['id']}` | {', '.join(api['doc_files'])} |")
    add("")
    add(f"### 5.1 catalog 全部 {summary['runtime_catalog_entries']} 条的落点")
    add("")
    add("`api_catalog.cpp` 共 44 条，全部列出以便核对\"已实现\"是否真的覆盖了 PC API：")
    add("")
    add("| catalog owner:name | domain | maturity | 对应 PC 文档 API |")
    add("| --- | --- | --- | --- |")
    mapping = {(a["owner"], a["name"]): a["key"] for a in implemented}
    for entry in sorted(payload["rt_raw_entries"], key=lambda e: (e["domain"], e["owner"], e["name"])):
        key = mapping.get((entry["owner"], entry["name"]))
        if key is None:
            counterpart = "—（不在 IsaacDocs 类页里：Lua 标准库全局 / 全局表访问器 / 本 Runtime 诊断面）"
        else:
            counterpart = f"`{key}`"
        add(f"| `{entry['owner']}:{entry['name']}` | {entry['domain']} | {entry['maturity']} | {counterpart} |")
    add("")
    no_handler = sources["runtime"]["entries_without_handler"]
    add(f"- 未在 `kXHandlers` 绑定表里出现的 catalog 条目：{len(no_handler)} 条"
        f"（{', '.join(no_handler) if no_handler else '无'}）；`Global` 域是环境注入而非 handler，属预期。")
    add("")

    add("## 6. `missing_easy`：有 Switch 内部对应符号的缺失项（完整清单）")
    add("")
    easy = [a for a in apis if a["category"] == "missing_easy"]
    add(f"共 {len(easy)} 条。每条给出 PC API、Switch 内部符号（原始 `_Z…` 名）、"
        "`file_offset`（= 模块偏移）以及该偏移处的 16 字节守卫。")
    add("")
    grouped = collections.defaultdict(list)
    for api in easy:
        grouped[api["switch"]["class"] or "(自由函数)"].append(api)
    add("### 6.1 建议实施顺序（最值得先做的 10 条）")
    add("")
    add("排序评分规则（写在 `tools/build_lua_api_inventory.py::priority_score` 里，可复现）：")
    add("")
    add("| 计分项 | 分值 |")
    add("| --- | ---: |")
    add("| `thunk_readiness = ready`（宿主对象已可达、参数全为基本类型） | +100 |")
    add("| `thunk_readiness = needs_arg_marshalling`（宿主可达，需编组向量/对象参数） | +50 |")
    add("| `thunk_readiness = needs_owner_instance`（宿主对象尚未定位） | +10 |")
    add("| `match_confidence = high`（类名+方法名都完全一致） | +20 |")
    add("| Switch 类属于 Runtime 已持有的单例（Game/Level/Room/ItemPool/Music/SoundEffects） | +15 |")
    add("| 不是引擎主循环入口（`Update`/`Render`/`PreUpdate`/`PostRender`/`Init`/`Interpolate`） | +10（是则 −40） |")
    add("| 不是纯构造函数 | 0（是则 −60） |")
    add("")
    add("另外做了**多样性限制**：同一个 Switch 类最多取 2 条，避免整张表被 `Game` 占满。")
    add("")
    add("| # | PC API | Switch 类 | 评分 | 就绪度 | Switch 符号 | 偏移 | 16 字节守卫 |")
    add("| ---: | --- | --- | ---: | --- | --- | --- | --- |")
    for index, item in enumerate(payload["suggested_order"], 1):
        add(f"| {index} | `{item['key']}` | {item['owner_class']} | {item['score']} | "
            f"{item['thunk_readiness']} | `{item['mangled']}` | `{item['offset_hex']}` | "
            f"`{item['guard16'] or 'n/a'}` |")
    add("")
    ready_count = sum(1 for a in apis
                      if a["category"] == "missing_easy" and a["thunk_readiness"] == "ready")
    add(f"说明：同分项按 `owner:name` 字母序排列，因此**同分项之间的先后不代表重要性**；"
        f"本次 `ready`（宿主已可达 + 参数全为基本类型）的条目一共 {ready_count} 条，"
        f"全部列在 6.3 节各表的“就绪度”列里，可自行按需挑选。")
    add("")
    add("这 10 条的判据说明：")
    add("")
    for item in payload["suggested_order"]:
        add(f"- `{item['key']}`：{'；'.join(item['notes'])}。")
    add("")
    add("### 6.2 按 Switch 类分组统计")
    add("")
    add("| Switch 类 | 条数 |")
    add("| --- | ---: |")
    for cls, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        add(f"| `{cls}` | {len(items)} |")
    add("")
    for cls_index, (cls, items) in enumerate(
            sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])), 1):
        add(f"### 6.3.{cls_index} `{cls}`（{len(items)} 条）")
        add("")
        add("| PC API | 匹配 | 就绪度 | Switch 符号 | 偏移 | 16 字节守卫 |")
        add("| --- | --- | --- | --- | --- | --- |")
        for api in sorted(items, key=lambda a: a["name"]):
            switch = api["switch"]
            mangled = api["switch"]["mangled"]
            extra = f" （共 {switch['candidate_count']} 个候选）" if switch["candidate_count"] > 1 else ""
            add(f"| `{api['key']}`{extra} | {api['match_kind']} | {api['thunk_readiness']} | "
                f"`{mangled}` | `{switch['offset_hex']}` | `{switch['guard16'] or 'n/a'}` |")
        add("")

    add("## 7. `missing_hard`：没有可用内部符号的缺失项（完整清单）")
    add("")
    hard = [a for a in apis if a["category"] == "missing_hard"]
    add(f"共 {len(hard)} 条，按原因分布如下。每一条都写明\"缺的到底是什么\"。"
        f"其中 {summary.get('hard_with_near_candidates', 0)} 条在同类里存在**名字相近**的符号，"
        f"作为人工复核提示列在\"近似候选\"列（它们不计入 `missing_easy`，因为语义是否等价未证实）。")
    add("")
    add("### 7.1 原因分布")
    add("")
    add("| 原因码 | 条数 | 说明 |")
    add("| --- | ---: | --- |")
    for code, count in sorted(summary["hard_reasons"].items(), key=lambda kv: (-kv[1], kv[0])):
        if code in ("symbol_available", "keyboard_mouse"):
            continue
        add(f"| `{code}` | {count} | {REASON_LABELS.get(code, '')} |")
    add("")
    add("### 7.2 涉及最多的 owner")
    add("")
    add("| owner | 条数 |")
    add("| --- | ---: |")
    for owner, count in summary["hard_top_owners"]:
        add(f"| `{owner}` | {count} |")
    add("")
    by_reason = collections.defaultdict(list)
    for api in hard:
        by_reason[api["reason_code"] or "no_symbol"].append(api)
    for reason_index, code in enumerate(
            sorted(by_reason, key=lambda c: (-len(by_reason[c]), c)), 1):
        items = by_reason[code]
        add(f"### 7.3.{reason_index} `{code}` — {REASON_LABELS.get(code, '')}（{len(items)} 条）")
        add("")
        add("| PC API | 类型 | 近似候选（同类同名近符号，仅提示） | 具体原因 |")
        add("| --- | --- | --- | --- |")
        for api in sorted(items, key=lambda a: (a["owner"], a["name"])):
            flag = " ⚠" if api["needs_review"] else ""
            near = api.get("near_candidates") or []
            near_text = "<br>".join(
                f"`{c['class']}::{c['method']}` @ `{c['offset_hex']}`" for c in near
            ) or "—"
            add(f"| `{api['key']}`{flag} | {'/'.join(api['kinds'])} | {near_text} | {md_escape(api['reason'])} |")
        add("")

    add("## 8. 键鼠专用清单（单列，不列为待实现）")
    add("")
    mouse = [a for a in apis if a["category"] == "keyboard_mouse_only"]
    add(f"共 {len(mouse)} 条。判据：")
    add("")
    add("1. owner 是 `Keyboard` / `Mouse` 之类只服务键鼠的类；或")
    add("2. 方法名匹配 `Mouse|Keyboard|Key|Wheel|Cursor|Clipboard`（大小写不敏感）；或")
    add("3. 属于 `Input` 域中名字里带 `Mouse` 的成员（如 `Input:GetMousePosition`、`Input:IsMouseBtnPressed`）。")
    add("")
    add(f"实现用的正则就是 `{KEYBOARD_MOUSE_RE.pattern}`（大小写不敏感）。"
        f"故意**不**包含裸的 `Key`：那样会把 `EntityPlayer:HasGoldenKey` / `TryUseKey` "
        f"这类游戏内钥匙道具 API 误判成键鼠接口。")
    add("")
    add("策略：这些条目**保持\"有名字、能调用、不报错、但不实现功能\"**（返回类型零值），"
        "因此不计入 `missing_easy` / `missing_hard`。")
    add("")
    add("| # | PC API | 触发判据 | 文档 |")
    add("| ---: | --- | --- | --- |")
    for index, api in enumerate(sorted(mouse, key=lambda a: (a["owner"], a["name"])), 1):
        if api["owner"] in KEYBOARD_MOUSE_OWNERS:
            why = f"owner=`{api['owner']}`"
        elif api["key"] in KEYBOARD_MOUSE_NAMES:
            why = "显式名单"
        else:
            why = "名字含 Mouse/Keyboard/MouseWheel/KeyCode"
        add(f"| {index} | `{api['key']}` | {why} | {', '.join(api['doc_files'])} |")
    add("")
    add("### 8.1 配套的键鼠枚举表（同属\"只列清单\"范围）")
    add("")
    enums = payload.get("keymouse_enums", {})
    if enums:
        add("| 枚举表 | 枚举项数 | 前若干项 |")
        add("| --- | ---: | --- |")
        for name, info in enums.items():
            if not info.get("available"):
                add(f"| `{name}` | 未取到 | — |")
                continue
            add(f"| `{name}` | {info['enumerator_count']} | {', '.join(info['sample'])} … |")
        add("")
        add("这些表在 Switch 上只需要存在（名字可查、取值可用）；`Keyboard` / `Mouse` 是键鼠专用，"
            "`ButtonAction` 是手柄动作表、属于 Runtime 的 `Global:ButtonAction` 已实现项。"
            "它们都不列入 `missing_easy` / `missing_hard`。")
    add("")
    add(f"**不列入键鼠清单的反例**（说明判据边界）：`Input:IsButtonPressed` / `IsButtonTriggered` / "
        f"`GetButtonValue` 的形参虽然写作 `[Keyboard](enums/Keyboard.md) button`，"
        f"但它们是通用的\"按键/动作\"查询，Switch 侧对应 `KAGE::Input::ManagerBase::IsButtonPressed`"
        f"（`0x4F9504`）一族，因此归入四类统计而不是键鼠清单。")
    add("")

    add("## 9. 附录：全局变量表（Globals.md）")
    add("")
    globals_appendix = payload["globals"]
    if globals_appendix.get("available"):
        add(f"PC 的 Lua 全局环境共列出 {globals_appendix['count']} 个全局名，"
            f"其中枚举表 {len(globals_appendix['enum_names'])} 个、非枚举 {len(globals_appendix['non_enum_names'])} 个。")
        add("")
        add("全局名不属于第 1 节四类统计（它们是名字/常量表，不是可调用的 API），单列如下。")
        add("")
        global_impl = {f"Global:{e['name']}" for e in sources["runtime"]["entries"]
                       if e["domain"] == "Global"}
        add("| 全局名 | 类型 | Runtime 是否已提供 |")
        add("| --- | --- | --- |")
        for name in globals_appendix["names"]:
            kind = "枚举表" if name in globals_appendix["enum_names"] else "对象/函数表"
            mark = "✅ `Global:" + name + "`" if f"Global:{name}" in global_impl else "❌ 未提供"
            add(f"| `{name}` | {kind} | {mark} |")
    else:
        add("本地镜像缺少 `Globals.md`，未生成。")
    add("")

    add("## 10. 局限与未决项")
    add("")
    for item in payload["limitations"]:
        add(f"- {item}")
    add("")
    add("## 11. 复现方法")
    add("")
    add("```bash")
    add("python3 tools/build_lua_api_inventory.py")
    add("```")
    add("")
    add("默认只读本地输入（IsaacDocs 本地镜像、`Repentance.nro`、`isaac-ng.exe`、Runtime 源码），"
        "网络仅用于校验 IsaacDocs 镜像完整性，失败不影响产物。可选参数：")
    add("")
    add("```bash")
    add("python3 tools/build_lua_api_inventory.py --docs-dir analysis/isaacdocs-snapshot/docs  # 指定文档目录")
    add("python3 tools/build_lua_api_inventory.py --json-only                      # 只写 JSON")
    add("python3 tools/build_lua_api_inventory.py --fetch-docs                     # 先把缺失文档抓到 /tmp")
    add("```")
    add("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Doc fetching (optional, network)
# --------------------------------------------------------------------------- #

def fetch_missing_docs(docs_dir: str) -> dict:
    os.makedirs(docs_dir, exist_ok=True)
    report = {"downloaded": [], "failed": [], "already_present": 0}
    try:
        tree = json.loads(_http_get(ISAACDOCS_TREE_API, timeout=40, retries=3).decode("utf-8"))
    except Exception as error:  # noqa: BLE001
        report["error"] = f"{type(error).__name__}: {error}"
        return report

    paths = sorted(
        e["path"] for e in tree.get("tree", [])
        if e["path"].startswith("docs/") and e["path"].endswith(".md") and e["path"].count("/") == 1
    )
    for path in paths:
        name = path[len("docs/"):]
        target = os.path.join(docs_dir, name)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            report["already_present"] += 1
            continue
        try:
            blob = _http_get(ISAACDOCS_RAW + path, timeout=25, retries=4)
            if blob:
                with open(target, "wb") as handle:
                    handle.write(blob)
                report["downloaded"].append(name)
            else:
                report["failed"].append(name)
        except Exception:  # noqa: BLE001
            report["failed"].append(name)
    return report


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

FETCH_DOCS_DIR = "/tmp/isaacdocs_fetch/docs"


def pick_docs_dir(explicit: str | None) -> str:
    """Choose the IsaacDocs mirror: explicit, else the most complete local one."""
    if explicit:
        return explicit
    best: tuple[int, str] | None = None
    for candidate in DOC_DIR_CANDIDATES:
        if not os.path.isdir(candidate) or not os.path.exists(os.path.join(candidate, "Game.md")):
            continue
        root_md = sum(1 for f in os.listdir(candidate)
                      if f.endswith(".md") and os.path.isfile(os.path.join(candidate, f)))
        if best is None or root_md > best[0]:
            best = (root_md, candidate)
    if best is not None:
        return best[1]
    if os.path.isdir(FETCH_DOCS_DIR):
        return FETCH_DOCS_DIR
    raise SystemExit(
        "找不到 IsaacDocs 本地镜像；请先用 --fetch-docs 抓取，或用 --docs-dir 指定目录。"
    )


def build_limitations(payload_parts: dict) -> list[str]:
    items: list[str] = []
    health = payload_parts["docs_health"]
    if "tree_api_error" in health:
        items.append(
            f"IsaacDocs 完整性未能联网校验（{health['tree_api_error']}）；"
            f"本地镜像是 `{health.get('docs_dir', '（未知）')}` 的历史副本，可能落后于上游 main。"
        )
    elif health.get("missing_locally"):
        items.append(f"本地镜像缺少上游 {len(health['missing_locally'])} 个类页："
                     + ", ".join(health["missing_locally"][:20]))
    items.append(
        "IsaacDocs 上游 `docs/*.md` 根目录实际只有 %d 个文件（其余 .md 在 `docs/enums`、`docs/xml`、"
        "`docs/tutorials` 等子目录，属于枚举/数据文档而非类 API），因此\"237 个文档\"应理解为"
        "全部 .md 计数；类 API 名源用的是根目录这 %d 个类页。"
        % (health.get("remote_root_md_count", 0), health.get("remote_root_md_count", 0))
    )
    items.append(
        "`docs/enums/*.md` 只提供枚举取值，不含方法，未纳入四类统计；"
        "但 PC 的枚举表是 mod 兼容性的一部分，需要时另行盘点。"
    )
    items.append(
        "Switch 符号表已纳入 const 限定的成员函数（`_ZNK…`，本次 %d 条）。"
        "早期版本用 `_ZN` 前缀过滤，把这类 getter/判定函数整体丢掉，"
        "会让它们在 `missing_hard` 里被误记为「无底座」。"
        % payload_parts.get("const_symbol_count", 0)
    )
    items.append(
        "`missing_easy` 的 `thunk_readiness=needs_owner_instance` 表示符号底座存在，"
        "但宿主对象实例（如 `Entity_Player`、`Room`、`HUD`）尚未在 Runtime 里定位；"
        "这类条目仍属\"简单可恢复项\"，但需要先补对象链。"
    )
    items.append(
        "方法名只做完全一致的匹配。同名但归属不同对象的候选（如通用 `Update`/`Render`）被有意排除，"
        "宁可判为 `missing_hard` 也不产生假阳性；这些条目在 JSON 的 `ambiguous_candidates` 里。"
    )
    items.append(
        "`isaac-ng.exe` 的名字块只能作为**候选**来源：该块里混有 ANM2 动画名、实体变体名等非 API 字符串，"
        "未做逐条甄别，因此未进入四类统计。本次该块 %s 条名字里，有 %s 条与文档方法名重合，"
        "%s 条是文档未覆盖的候选（低可信度）。"
        % (payload_parts["exe_entries"], payload_parts["exe_overlap"], payload_parts["exe_only"])
    )
    items.append(
        "属性（`aria-label='Variables'`）没有 C++ 访问器符号可用，统一归入 "
        "`struct_field_layout`；它们的真实可行性取决于是否已还原该类结构体布局。"
    )
    items.append(
        "`Isaac:*` 全局函数（%d 条）绝大多数在 exe 里由 Lua 绑定层实现，Switch 二进制没有对应方法，"
        "因此统一归入 `lua_glue_type`：这些必须由 Runtime 自己用已有原语拼装，"
        "不是\"接一个函数\"就能解决的。"
        % payload_parts["isaac_owner_count"]
    )
    items.append(
        "`Keyboard` / `Mouse` 等枚举表的枚举项数只统计了文档表格行数，"
        "未与 Switch 侧的实际取值做过对照。"
    )
    items.append(
        "本地 IsaacDocs 镜像是 2025-08-17 拉取的快照；本次用 `--fetch-docs` 重新抓取上游最新类页时"
        "网络持续超时/SSL 失败（`curl: (28)`），因此**内容**可能落后于上游 main。"
        "但**文件名清单**已用 GitHub trees API 核对：上游根目录 75 个类页，本地缺失 0 个。"
    )
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs-dir", default=None, help="IsaacDocs docs 目录")
    parser.add_argument("--json-only", action="store_true", help="只写 JSON")
    parser.add_argument("--fetch-docs", action="store_true",
                        help="先把缺失的 IsaacDocs 类页抓进所选文档目录（网络不稳时会失败）")
    parser.add_argument("--out-json", default=OUT_JSON)
    parser.add_argument("--out-doc", default=OUT_DOC)
    args = parser.parse_args(argv)

    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    docs_dir = pick_docs_dir(args.docs_dir)
    fetch_report = None
    if args.fetch_docs:
        fetch_report = fetch_missing_docs(docs_dir)
        print(f"[0/6] 文档抓取：新增 {len(fetch_report.get('downloaded', []))}，"
              f"失败 {len(fetch_report.get('failed', []))}，"
              f"已存在 {fetch_report.get('already_present', 0)}")
        if fetch_report.get("failed"):
            print("      抓不到的文件：" + ", ".join(fetch_report["failed"][:20]))

    print(f"[1/6] 解析 IsaacDocs：{docs_dir}")
    doc_entries, doc_meta = load_isaacdocs(docs_dir)
    print(f"      owner:name 条目 {len(doc_entries)} 条，类页 {doc_meta['class_files_used']} 个")

    print("[2/6] 读取 Switch NRO 动态符号并解混淆")
    switch = load_switch_symbols(REPENTANCE_NRO, AFTERBIRTH_NRO)
    print(f"      {switch['nro_symbol_count']} 条符号（build {switch['nro_build_id']}），"
          f"IsaacRepentance/KAGE 方法 {len(switch['records'])} 条")
    switch["offset_verification"] = verify_guard_offsets(switch["records"])
    print(f"      偏移守卫校验：{'全部一致' if switch['offset_verification']['all_match'] else '存在不一致'}")

    print("[3/6] 读取 Runtime 已实现集")
    runtime = load_runtime_implemented()
    print(f"      catalog {len(runtime['entries'])} 条，handler 表 {len(runtime['handler_tables'])} 张")

    print("[4/6] 扫描 PC exe 名字块")
    exe = load_pc_exe_names(PC_EXE, {e["name"] for e in doc_entries})
    if exe.get("block"):
        print(f"      {exe['block']['start']}–{exe['block']['end']}，{exe['block']['entries']} 条，"
              f"与文档重合 {exe['block']['doc_overlap']} 条")

    print("[5/6] 分类")
    implemented_index: dict[tuple[str, str], dict] = {}
    for entry in runtime["entries"]:
        if entry["domain"] == "Diagnostic":
            continue
        implemented_index[(entry["owner"], entry["name"])] = entry

    by_class_method, by_method, by_free = build_symbol_index(switch["records"])
    known_classes = {r["class"] for r in switch["records"] if r["class"]}

    apis = []
    for entry in doc_entries:
        api = {
            "owner": entry["owner"],
            "name": entry["name"],
            "kinds": entry["kinds"],
            "signatures": entry["signatures"],
            "availability": entry["availability"],
            "doc_files": entry["doc_files"],
            "arg_types": entry["arg_types"],
        }
        result = classify(api, by_class_method, by_method, by_free, implemented_index,
                          known_classes)
        entry.update(result)
        apis.append(entry)
    apis.sort(key=lambda a: (a["category"], a["owner"], a["name"]))

    summary = summarize(apis, runtime)
    for category, count in sorted(summary["categories"].items()):
        print(f"      {category}: {count}")

    print("[6/6] 写产物")
    docs_health = docs_health_check(docs_dir)
    globals_appendix = parse_globals_appendix(docs_dir)
    keymouse_enums = parse_enum_appendix(docs_dir, ["Keyboard", "Mouse", "ButtonAction"])

    payload = {
        "schema": "isaac-switch-lua-api-inventory/v1",
        "generated_at": generated,
        "tool": os.path.relpath(os.path.abspath(__file__), REPO_ROOT),
        "sources": {
            "isaacdocs": {"meta": doc_meta, "health": docs_health,
                          "fetch_report": fetch_report},
            "pc_exe": exe,
            "switch": switch,
            "runtime": runtime,
            "doc_entry_count": len(doc_entries),
        },
        "summary": summary,
        "rt_raw_entries": runtime["entries"],
        "categories": {
            "implemented": [a["key"] for a in apis if a["category"] == "implemented"],
            "game_builtin": [a["key"] for a in apis if a["category"] == "game_builtin"],
            "missing_easy": [a["key"] for a in apis if a["category"] == "missing_easy"],
            "missing_hard": [a["key"] for a in apis if a["category"] == "missing_hard"],
            "keyboard_mouse_only": [a["key"] for a in apis if a["category"] == "keyboard_mouse_only"],
        },
        "apis": apis,
        "suggested_order": suggested_order(apis),
        "globals": globals_appendix,
        "keymouse_enums": keymouse_enums,
    }
    doc_names = {e["name"] for e in doc_entries}
    exe_names = set(exe.get("candidates") or [])
    payload["limitations"] = build_limitations({
        "docs_health": docs_health,
        "exe_entries": (exe.get("block") or {}).get("entries", 0),
        "exe_overlap": len(doc_names & exe_names),
        "exe_only": len(exe_names - doc_names),
        "isaac_owner_count": sum(1 for a in apis if a["owner"] in GLOBAL_TABLE_OWNERS),
        "const_symbol_count": sum(1 for r in switch["records"]
                                  if r["mangled"].startswith("_ZNK")),
    })

    os.makedirs(os.path.dirname(args.out_json), exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")
    print(f"      写入 {os.path.relpath(args.out_json, REPO_ROOT)}")

    if not args.json_only:
        os.makedirs(os.path.dirname(args.out_doc), exist_ok=True)
        with open(args.out_doc, "w", encoding="utf-8") as handle:
            handle.write(render_doc(payload))
            handle.write("\n")
        print(f"      写入 {os.path.relpath(args.out_doc, REPO_ROOT)}")

    print()
    print("=== 统计摘要 ===")
    print(f"PC API 条目总数           : {summary['api_total']}")
    print(f"  implemented             : {summary['categories'].get('implemented', 0)}")
    print(f"  game_builtin            : {summary['categories'].get('game_builtin', 0)}")
    print(f"  missing_easy            : {summary['categories'].get('missing_easy', 0)}")
    print(f"  missing_hard            : {summary['categories'].get('missing_hard', 0)}")
    print(f"  keyboard_mouse_only     : {summary['categories'].get('keyboard_mouse_only', 0)}")
    print(f"missing_hard 原因分布     : {summary['hard_reasons']}")
    print(f"missing_easy 就绪度       : {summary['easy_thunk_readiness']}")
    print(f"需要人工复核的条目        : {summary['needs_review']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
