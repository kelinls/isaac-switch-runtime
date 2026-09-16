import re
from dataclasses import dataclass
from pathlib import Path


TARGET_BUILD_ID = bytes.fromhex("91C73FDD575061318D68886316AFEAC72388B2AB") + bytes(12)


def makefile_accepted_diagnostic_stages(makefile: str) -> set[int]:
    """`runtime/Makefile` 允许的 `DIAGNOSTIC_STAGE` 阶段号集合。

    为什么需要它：审计用例过去写的是 `assertIn("88 89,$(DIAGNOSTIC_STAGE)", makefile)`
    这种**按阶段分组、每组一行**的字面断言。后来所有允许的阶段号合并进了同一行
    （`ifeq ($(filter 0 1 … 128,$(DIAGNOSTIC_STAGE)),)`），那些字面串不再出现 ⇒ 断言误红，
    而"这个阶段被构建系统接受"这件事并没有变。

    这里改为解析 `$(filter …,$(DIAGNOSTIC_STAGE))` 的阶段号集合再判成员：含义不变，
    也不会因为将来合并/换行/重排而误红；真正的语义变化（阶段号被剔除）仍然会红。
    """
    stages: set[int] = set()
    for group in re.findall(r"\$\(filter ([0-9 ]+),\$\(DIAGNOSTIC_STAGE\)\)", makefile):
        stages.update(int(item) for item in group.split())
    return stages


def layered_lua_runtime_sources(source_root: Path) -> list[Path]:
    """Host-test inputs for the Lua runtime's layered translation units."""
    source_root = Path(source_root)
    runtime_root = source_root.parent
    unit_root = runtime_root / "src"
    return [
        source_root / "lua_runtime.cpp",
        unit_root / "application" / "callback" / "callback_registry.cpp",
        unit_root / "application" / "callback" / "callback_dispatcher.cpp",
        unit_root / "interfaces" / "lua" / "api_catalog.cpp",
        unit_root / "interfaces" / "lua" / "owner_binding.cpp",
        unit_root / "interfaces" / "lua" / "mod_api.cpp",
        unit_root / "interfaces" / "lua" / "game_api.cpp",
        unit_root / "interfaces" / "lua" / "isaac_api.cpp",
        unit_root / "interfaces" / "lua" / "remaining_api.cpp",
        unit_root / "interfaces" / "lua" / "music_api.cpp",
        unit_root / "interfaces" / "lua" / "rng_api.cpp",
        unit_root / "interfaces" / "lua" / "input_api.cpp",
        unit_root / "interfaces" / "lua" / "font_api.cpp",
        unit_root / "interfaces" / "lua" / "color_api.cpp",
        unit_root / "interfaces" / "lua" / "vector_api.cpp",
        unit_root / "interfaces" / "lua" / "sprite_api.cpp",
        # 2026-09-16：运行时自带的模组开关菜单控制面（`RuntimeMods`）——
        # `lua_runtime.cpp` 的准备阶段会调它的注册函数，宿主 harness 因此必须带上它。
        unit_root / "interfaces" / "lua" / "mod_menu_api.cpp",
        # 菜单控制面背后是模组开关服务（读/改/落盘）与它的纯值对象状态。
        unit_root / "application" / "mod" / "mod_toggle_service.cpp",
        unit_root / "domain" / "mod" / "mod_toggle_state.cpp",
        unit_root / "interfaces" / "lua" / "json_api.cpp",
        # 共享**弱默认桩**（`shared_lua_stubs.cpp`）：放进这份清单，任何 harness 都会自动带上。
        # 它们是弱符号，所以老 harness 自己那份强定义照旧优先；而"运行时新增一个观测函数"
        # 只需在那个文件里补一行默认，**不必再动任何测试文件**（见该文件头部说明）。
        source_root.parent / "tests" / "shared_lua_stubs.cpp",
    ]


# ---------------------------------------------------------------------------
# 宿主 Lua harness 的共享脚手架（地基一期，2026-09-15）
#
# 问题：每个"某个 API 在宿主上怎么表现"的测试都自己抄一遍同样的三件事 ——
#   * 伪造引擎内存那套 helpers；
#   * **一批引擎观测函数的桩**（`game_observer.hpp` 的那些入口，宿主上没有实现）；
#   * 编译 vendored Lua 5.3.3 的 7 个 .c。
# 结果是 21 个测试文件各自抄一份桩；一旦运行时**新增**一个观测函数，
# 21 个文件会同时链接失败（2026-09-15 实测：本可以给 `Level` 家族加一个观测函数，
# 因为这件事只能绕开写，最后是在 handler 里自己走指针链）。
#
# 做法：桩只写一次，且写成**弱符号**（`__attribute__((weak))`）。
# 测试自己的 TU 里再定义同名函数就是强符号，链接器会用它覆盖弱默认 ——
# 于是"要自定义行为"不需要改契约，而"新增观测函数"也只需要在下面补一行默认桩，
# 老测试照旧能编译、能跑。
# ---------------------------------------------------------------------------

#: Lua 源码相对 `runtime/source` 的位置。
LUA_SOURCE_RELATIVE = "third_party/lua-5.3.3/src"

#: 弱默认桩：签名与 `game_observer.hpp` / `game_file_reader.hpp` 的声明逐字一致。
#: 默认行为一律是"读不到"（对应的降级分支），需要具体值的测试在自己的 TU 里覆盖。
#: 宿主 harness 编译用的固定开关（与既有手写 harness 完全一致，避免行为漂移）。
HARNESS_COMPILE_FLAGS = (
    "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
    "-DEXL_LAYERED_RUNTIME=1", "-DEXL_DIAGNOSTIC_STAGE=14",
    "-DEXL_LOAD_KIND=Module", "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
)

#: 不参与编译的 Lua 源码（`lua.c` 等是独立可执行/可选库）。
_LUA_EXCLUDED_SOURCES = frozenset(
    {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
)


def prepare_harness_directory(workdir: Path) -> Path:
    """建好 harness 需要的目录骨架（`compatibility/stdfloat` 是 vendored Lua 的头依赖）。"""
    workdir = Path(workdir)
    compatibility = workdir / "compatibility"
    compatibility.mkdir(parents=True, exist_ok=True)
    (compatibility / "stdfloat").write_text(
        "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n",
        encoding="utf-8",
    )
    return workdir


#: `compatibility/stdfloat` 的内容（上面那个常量）。它会被算进缓存键，所以改它 ⇒ 缓存失效。
_COMPATIBILITY_STDFLOAT = (
    "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
)


# ---------------------------------------------------------------------------
# 磁盘级目标文件缓存（门禁提速，2026-09-15）
#
# **问题**：`build_lua_harness()` 每次都要把同一批运行时翻译单元（18 个 `.cpp`）从零编译，
# 而 `runtime/tests/` 下有 35 个模块各自建夹具 ⇒ 同一批编译被重复上百次。
# 实测：单次夹具构建 **1.9 s**（Lua 的 `.o` 已在进程内缓存时），其中绝大部分就是这批 `.cpp`；
# vendored Lua 的 7 个 `.c` 还要再花 ~1.2 s（原来只做了"进程内"缓存，
# 于是**每个模块进程**都要重编一遍）。
#
# **做法**：把每个翻译单元编成 `.o` 放进磁盘缓存，键必须覆盖**全部输入**，
# 否则就会"拿旧 `.o` 跑新代码"⇒ 门禁给出**假绿**。键的组成：
#   * 编译器自身的版本串（换了 clang 不能复用）
#   * 完整编译旗标 + 完整 `-I` 列表
#   * 该源文件内容的 sha256
#   * **头文件树的内容指纹**（`runtime/source`、`runtime/src`、vendored Lua 下所有 `.h/.hpp`）
#     —— 我们几乎天天只改头文件，这一项漏了缓存就会骗人；代价是"改任何头 ⇒ 整批失效"，
#     保守但绝不会漏。
#
# 缓存目录默认在系统临时目录（**不放仓库里**：既避免被发布脚本当成未跟踪产物，
# 也避免把 `.o` 混进 git）。可用环境变量 `ISAAC_HOST_OBJECT_CACHE` 指定。
# ---------------------------------------------------------------------------

#: 头文件指纹的进程内缓存（同进程内头文件树不会变；跨进程每次重算，几毫秒）。
_HEADER_FINGERPRINT_CACHE: dict[str, str] = {}
#: 编译器版本串的进程内缓存。
_COMPILER_VERSION_CACHE: dict[str, str] = {}


def host_object_cache_root() -> Path:
    """磁盘缓存根目录（可用 `ISAAC_HOST_OBJECT_CACHE` 覆盖）。"""
    import os
    import tempfile

    override = os.environ.get("ISAAC_HOST_OBJECT_CACHE")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "isaac-runtime-host-objects"


def runtime_header_fingerprint(source_root: Path) -> str:
    """`runtime/source` / `runtime/src` / vendored Lua 下所有头文件的内容指纹。

    为什么连无关的头文件也算进来：**宁可贵一点、也不许漏**。改一个头文件就可能改变
    某个 `.cpp` 的编译结果，而"哪些 `.cpp` 依赖哪些头"要靠编译器才能算准；
    全量指纹的做法是"任何头一动，整批 `.o` 全部重编"，逻辑上不会出错。
    头文件都是几百字节到几 KB 的小文件，实测几毫秒。
    """
    import hashlib

    source_root = Path(source_root)
    key = str(source_root)
    cached = _HEADER_FINGERPRINT_CACHE.get(key)
    if cached is not None:
        return cached

    roots = [source_root, source_root.parent / "src", source_root / LUA_SOURCE_RELATIVE]
    digest = hashlib.sha256()
    digest.update(_COMPATIBILITY_STDFLOAT.encode())       # 合成输入：它也是编译输入
    entries: list[tuple[str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in (".h", ".hpp") or not path.is_file():
                continue
            # 名字取**相对路径**：指纹要表达"这棵头文件树的内容"，而不是"它在磁盘哪个位置"。
            entries.append((str(path.relative_to(source_root.parent)), 
                            hashlib.sha256(path.read_bytes()).hexdigest()))
    for name, file_digest in sorted(entries):
        digest.update(name.encode())
        digest.update(file_digest.encode())
    result = digest.hexdigest()
    _HEADER_FINGERPRINT_CACHE[key] = result
    return result


def compiler_version(tool: str) -> str:
    """编译器版本串（把它算进缓存键：换编译器/换版本时旧 `.o` 不许复用）。"""
    import subprocess

    cached = _COMPILER_VERSION_CACHE.get(tool)
    if cached is not None:
        return cached
    try:
        probe = subprocess.run([tool, "--version"], text=True, capture_output=True, timeout=60)
        version = (probe.stdout or probe.stderr or "").strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = "unknown"
    _COMPILER_VERSION_CACHE[tool] = version
    return version


def object_cache_key(*, source: Path, tool: str, flags: tuple[str, ...],
                     includes: tuple[str, ...], header_fingerprint: str) -> str:
    """一个翻译单元的缓存键（纯函数，便于门禁用"注入不同的指纹"来验证失效）。"""
    import hashlib

    digest = hashlib.sha256()
    digest.update(tool.encode())                 # 工具名本身：`cc` 与 `c++` 的 --version 可能一模一样
    digest.update(compiler_version(tool).encode())
    digest.update("\0".join(flags).encode())
    digest.update("\0".join(includes).encode())
    digest.update(header_fingerprint.encode())
    digest.update(Path(source).name.encode())
    digest.update(Path(source).read_bytes())
    return digest.hexdigest()


def cached_compile(source: Path, *, tool: str, flags: tuple[str, ...], includes: tuple[str, ...],
                   header_fingerprint: str | None = None,
                   cache_root: Path | None = None) -> Path:
    """把一个源文件编成 `.o`（命中缓存则直接复用），返回 `.o` 路径。

    并发安全：先编到本进程私有的临时文件，再 `os.replace` 进缓存 —— 多个进程同时编同一个
    翻译单元时，各自算出的内容一致，最后落地的都是完整文件，不会留下半个 `.o`。
    """
    import os
    import subprocess
    import tempfile

    source = Path(source)
    flags = tuple(flags)
    includes = tuple(includes)
    fingerprint = header_fingerprint or runtime_header_fingerprint(
        _infer_source_root(source))
    root = Path(cache_root) if cache_root else host_object_cache_root()
    key = object_cache_key(source=source, tool=tool, flags=flags, includes=includes,
                           header_fingerprint=fingerprint)
    target = root / key[:2] / f"{key}.o"
    if target.is_file() and target.stat().st_size > 0:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="isaac-host-obj-") as staging:
        staged = Path(staging) / "unit.o"
        command = [tool, *flags, *[f"-I{item}" for item in includes],
                   "-c", str(source), "-o", str(staged)]
        build = subprocess.run(command, text=True, capture_output=True)
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)
        os.replace(staged, target)
    return target


def _infer_source_root(source: Path) -> Path:
    """从某个源文件反推 `runtime/source`（只为默认指纹用；调用方通常显式传指纹）。"""
    source = Path(source).resolve()
    for parent in source.parents:
        if parent.name == "source" and (parent / "lua_runtime.cpp").is_file():
            return parent
    return source.parent


def lua_harness_objects(source_root: Path, workdir: Path) -> list[Path]:
    """vendored Lua 5.3.3 的目标文件（**磁盘缓存**，见本节开头的说明）。

    原来只做进程内缓存 ⇒ 每个模块进程都要重编这 7 个 `.c`（实测 ~1.2 s）；
    改成磁盘缓存后，跨进程也只剩"算哈希 + 命中"。
    返回的路径可能不在 `workdir` 里（缓存在别处），调用方只当作链接输入即可。
    """
    source_root = Path(source_root)
    lua_root = source_root / LUA_SOURCE_RELATIVE
    fingerprint = runtime_header_fingerprint(source_root)
    objects: list[Path] = []
    for source in sorted(lua_root.glob("*.c")):
        if source.name in _LUA_EXCLUDED_SOURCES:
            continue
        objects.append(cached_compile(
            source, tool="cc", flags=("-std=c99", "-w", "-DLUA_C89_NUMBERS"),
            includes=(str(lua_root),), header_fingerprint=fingerprint,
        ))
    return objects


def build_lua_harness(*, source_root: Path, workdir: Path, harness_source: str,
                      extra_sources: tuple[Path, ...] = ()) -> Path:
    """编译一个宿主机 harness，返回可执行文件路径。

    组成：`harness_source`（含 `main`，以及需要**覆盖**共享默认桩时自己定义的强符号）
    + `shared_lua_stubs.cpp`（共享弱默认桩，已包含在源码清单里）+ vendored Lua。
    """
    import subprocess

    source_root = Path(source_root)
    workdir = prepare_harness_directory(Path(workdir))
    lua_root = source_root / LUA_SOURCE_RELATIVE

    harness = workdir / "harness.cpp"
    harness.write_text(harness_source.lstrip(), encoding="utf-8")

    includes = (str(workdir / "compatibility"), str(source_root),
                str(source_root.parent / "src"), str(lua_root))
    fingerprint = runtime_header_fingerprint(source_root)
    flags = HARNESS_COMPILE_FLAGS

    objects = [cached_compile(path, tool="c++", flags=flags, includes=includes,
                              header_fingerprint=fingerprint)
               for path in (harness, *extra_sources, *layered_lua_runtime_sources(source_root))]
    objects += lua_harness_objects(source_root, workdir)

    binary = workdir / "harness"
    build = subprocess.run(
        ["c++", *flags, *(str(path) for path in objects), "-lm", "-o", str(binary)],
        text=True, capture_output=True,
    )
    if build.returncode != 0:
        raise AssertionError(build.stdout + build.stderr)
    return binary


def target_address(base: int, offset: int) -> int:
    return base + offset


def verify_bytes(expected: bytes, actual: bytes) -> bool:
    return len(expected) == len(actual) and expected == actual


def orig_after_commit(published_orig: int, commit_succeeded: bool, entry_changed: bool) -> int:
    # Once published, Orig remains callable even if commit observes a concurrent entry change.
    return published_orig


@dataclass(frozen=True)
class SyntheticModule:
    path: str
    build_id: bytes
    text_start: int = 0x7100000000
    text: bytes = b""
    text_size: int = 0
    rodata: bytes = b""


def heartbeat_frames(start: int, end: int, interval: int) -> list[int]:
    return [frame for frame in range(start, end + 1) if frame % interval == 0]


def format_event(event: str, fields: dict[str, str]) -> str:
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    return f"event={event}" + (f" {suffix}" if suffix else "")


def synthetic_module(path: str, build_id: bytes, *, valid_header: bool = True, mod0_in_rodata: bool = True) -> SyntheticModule:
    text_size = 0x100
    text = bytearray(0x60)
    mod0_offset = text_size + 0x54 if mod0_in_rodata else 0x20
    text[4:8] = (mod0_offset if valid_header else 0).to_bytes(4, "little")
    text[0x10:0x14] = (0x304F524E if valid_header else 0).to_bytes(4, "little")
    text[0x40:0x60] = build_id
    rodata = bytearray(max(len(encode_path_record(path)), 0x58))
    rodata[:len(encode_path_record(path))] = encode_path_record(path)
    if valid_header and mod0_in_rodata:
        rodata[0x54:0x58] = (0x30444F4D).to_bytes(4, "little")
    elif valid_header:
        text.extend(b"\x00" * (0x24 - len(text)))
        text[0x20:0x24] = (0x30444F4D).to_bytes(4, "little")
    return SyntheticModule(path=path, build_id=build_id, text=bytes(text), text_size=text_size, rodata=bytes(rodata))


def find_target(module: SyntheticModule, expected_build_id: bytes):
    path = read_path_record(module.rodata)
    if path is None or path.replace("\\", "/").rsplit("/", 1)[-1] != "Repentance.nrs":
        return None
    if len(module.text) < 0x60 or module.text[0x10:0x14] != b"NRO0":
        return None
    mod0_offset = int.from_bytes(module.text[4:8], "little")
    mod0_rodata_offset = mod0_offset - module.text_size
    if mod0_offset < 8 or mod0_rodata_offset < 0 or mod0_rodata_offset + 4 > len(module.rodata):
        return None
    if module.rodata[mod0_rodata_offset:mod0_rodata_offset + 4] != b"MOD0":
        return None
    if module.text[0x40:0x60] != expected_build_id:
        return None
    return module


def find_target_status(module: SyntheticModule, expected_build_id: bytes):
    path = read_path_record(module.rodata)
    if path is None or path.replace("\\", "/").rsplit("/", 1)[-1] != "Repentance.nrs":
        return "NotFound", None
    if len(module.text) < 0x60 or module.text[0x10:0x14] != b"NRO0":
        return "NotFound", None
    mod0_offset = int.from_bytes(module.text[4:8], "little")
    mod0_rodata_offset = mod0_offset - module.text_size
    if mod0_offset < 8 or mod0_rodata_offset < 0 or mod0_rodata_offset + 4 > len(module.rodata):
        return "NotFound", None
    if module.rodata[mod0_rodata_offset:mod0_rodata_offset + 4] != b"MOD0":
        return "NotFound", None
    if module.text[0x40:0x60] != expected_build_id:
        return "BuildMismatch", module
    return "Found", module


def scan_targets(modules: list[SyntheticModule], expected_build_id: bytes):
    build_mismatch = None
    for module in modules:
        status, candidate = find_target_status(module, expected_build_id)
        if status == "Found":
            return status, candidate
        if status == "BuildMismatch":
            build_mismatch = candidate
    if build_mismatch is not None:
        return "BuildMismatch", build_mismatch
    return "NotFound", None


def find_target_compatible(module: SyntheticModule, expected_build_id: bytes, target_offset: int):
    status, candidate = find_target_status(module, expected_build_id)
    if status != "Found" or candidate is None:
        return None
    return candidate if target_window_valid(candidate.text_size, target_offset) else None


def step(state: str, title_ok: bool = False, module: str | None = None, hook_ok: bool = False, *,
         title_checked: bool | None = None, fatal_failure: bool = False,
         build_mismatch: bool = False, hook_attempted: bool | None = None) -> str:
    # The legacy four-argument call means title has already been queried.
    if title_checked is None:
        title_checked = True
    if hook_attempted is None:
        hook_attempted = module is not None
    if state in ("Ready", "Disabled"):
        return state
    if fatal_failure or build_mismatch or (title_checked and not title_ok):
        return "Disabled"
    if state == "Cold":
        return "WaitingForModule" if title_checked else "Cold"
    if module is None:
        return "WaitingForModule"
    if not hook_attempted or not hook_ok:
        return "Disabled"
    return "Ready"


def encode_path_record(path: str) -> bytes:
    encoded = path.encode("ascii")
    return b"\x00" * 4 + len(encoded).to_bytes(4, "little") + encoded


def read_path_record(record: bytes):
    if len(record) < 8:
        return None
    length = int.from_bytes(record[4:8], "little")
    payload = record[8:8 + length]
    if len(payload) != length or b"\x00" in payload:
        return None
    return payload.decode("ascii")


def target_window_valid(text_size: int, offset: int, address_limit: int = (1 << 64) - 1) -> bool:
    if offset < 0 or offset % 4 != 0 or offset > address_limit - 16:
        return False
    return offset + 16 <= text_size
