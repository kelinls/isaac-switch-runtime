"""`json` 模块（`json.encode` / `json.decode`）的宿主行为测试。

EID 在加载阶段无条件执行 `local json = require("json")`，所以 Runtime 必须在该 Mod 的
chunk 运行之前把这两个函数发布出去。本测试只验证 `json_api.{hpp,cpp}` 这一个 TU 的
**语义**，不依赖 Runtime 的其它单元：宿主部分把 Lua 5.3.3 的 C 源文件、`json_api.cpp`
和一个 harness 链接起来，在宿主上跑断言脚本；目标部分在 `devkitpro/devkita64` 容器里
按生产编译参数交叉编译同一份源码，核对只读段体积与「没有新的平台依赖」。

断言都落在真实语义上（编码文本逐字比较、解码回来的值与 `math.type` 子类型、错误必须
以 Lua 错误形式出现且进程不崩），而不是只断言「没报错」。docker 或镜像不可用时目标部分
按 skip 处理，不伪装通过。
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
UNIT_ROOT = ROOT / "runtime" / "src"
LUA_ROOT = SOURCE / "third_party/lua-5.3.3/src"
JSON_SOURCE = UNIT_ROOT / "interfaces/lua/json_api.cpp"
JSON_HEADER = UNIT_ROOT / "interfaces/lua/json_api.hpp"
COMMON_MAKE = ROOT / "runtime" / "misc" / "mk" / "common.mk"

DOCKER_IMAGE = "devkitpro/devkita64:latest"

# Lua stand-alone units the host harness must not link (they bring io/os/package entry points
# that the Runtime never exposes). Same set as the sibling Lua family tests.
LUA_HOST_EXCLUDES = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}

# The only global the module publishes, and the only C API it may call: the undefined symbols of
# the target object must stay inside this set, otherwise a new platform dependency (libm, the C++
# runtime, the exception/RTTI support routines) has crept in.
ALLOWED_UNDEFINED = {
    "memcmp",
    "memcpy",
    "snprintf",
    "strlen",
    "strpbrk",
}

# Measured read-only footprint of the bytecode-optimized target object is ~6.2 KiB (5312 bytes of
# `.text` + 1021 bytes of `.rodata`); 16 KiB keeps the assertion meaningful (a table-driven or
# string-heavy implementation would blow past it) without pinning an exact byte count.
TARGET_READ_ONLY_LIMIT = 16 * 1024

# A `data`/`bss` allowance this small is what "no static scratch buffer" means in bytes: the sink
# lives in a Lua userdata, so nothing larger than padding may land in the writable segments.
TARGET_STATIC_LIMIT = 64

# Production compile parameters for the target. Paths are the container's view (`/work` is the
# repository mounted read-only), so a host path containing spaces never reaches the shell.
TARGET_COMPILE_FLAGS = " ".join(
    [
        "aarch64-none-elf-g++ -std=gnu++23 -Oz -Wall -Wextra -Werror",
        "-fno-rtti -fno-exceptions -fno-asynchronous-unwind-tables -fno-unwind-tables",
        "-ffunction-sections -fdata-sections -fPIC -fvisibility=hidden",
        "-march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft",
        "-DLUA_C89_NUMBERS -D__SWITCH__",
        '-I/work/runtime/src',
        '-I/work/runtime/source/third_party/lua-5.3.3/src',
    ]
)

HARNESS = r'''
#include "interfaces/lua/json_api.hpp"

#include <cstdio>
#include <cstring>

extern "C" {
#include <lua.h>
#include <lauxlib.h>
#include <lualib.h>
}

// Opens exactly the libraries `OpenSafeLibraries` opens in the Runtime, then publishes `json`
// through the entry point the Runtime calls. Everything the contract script needs (assert/pcall/
// type/next, table/string/math, coroutine for the thread case) is available, and io/os/package
// are deliberately absent.
int main() {
    lua_State* state = luaL_newstate();
    if (state == nullptr) {
        return 2;
    }
    luaL_requiref(state, "_G", luaopen_base, 1);
    lua_pop(state, 1);
    luaL_requiref(state, LUA_COLIBNAME, luaopen_coroutine, 1);
    lua_pop(state, 1);
    luaL_requiref(state, LUA_TABLIBNAME, luaopen_table, 1);
    lua_pop(state, 1);
    luaL_requiref(state, LUA_STRLIBNAME, luaopen_string, 1);
    lua_pop(state, 1);
    luaL_requiref(state, LUA_MATHLIBNAME, luaopen_math, 1);
    lua_pop(state, 1);
    luaL_requiref(state, LUA_UTF8LIBNAME, luaopen_utf8, 1);
    lua_pop(state, 1);
    isaac::runtime::RegisterJsonModule(state);

    const char* script = R"LUA(__CONTRACT_SCRIPT__)LUA";
    if (luaL_loadbuffer(state, script, std::strlen(script), "@json-module-contract.lua") != LUA_OK) {
        std::fprintf(stderr, "load: %s\n", lua_tostring(state, -1));
        lua_close(state);
        return 3;
    }
    if (lua_pcallk(state, 0, 0, 0, 0, nullptr) != LUA_OK) {
        std::fprintf(stderr, "run: %s\n", lua_tostring(state, -1));
        lua_close(state);
        return 4;
    }
    lua_close(state);
    return 0;
}
'''

CONTRACT_SCRIPT = r'''
local checks = 0
local function check(condition, label)
  checks = checks + 1
  if not condition then error('failed: ' .. label, 2) end
end

check(type(json) == 'table', 'module table')
check(type(json.encode) == 'function' and type(json.decode) == 'function', 'members')

-- 编码：标量、字符串转义、数组/对象判定
check(json.encode(nil) == 'null', 'nil')
check(json.encode(true) == 'true', 'true')
check(json.encode(false) == 'false', 'false')
check(json.encode(1) == '1', 'integer')
check(json.encode(-42) == '-42', 'negative integer')
check(json.encode(1.5) == '1.5', 'float')
check(json.encode(2.0) == '2.0', 'integral float keeps its subtype')
check(json.encode(math.maxinteger) == tostring(math.maxinteger), 'maxinteger')
check(json.encode(math.mininteger) == tostring(math.mininteger), 'mininteger')
check(json.encode('') == '""', 'empty string')
check(json.encode('a"b\\c') == '"a\\"b\\\\c"', 'quote and backslash')
check(json.encode('\b\f\n\r\t') == '"\\b\\f\\n\\r\\t"', 'short escapes')
check(json.encode('\1') == '"\\u0001"', 'control character')
check(json.encode('中') == '"中"', 'utf-8 passthrough')
check(json.encode({}) == '{}', 'empty table')
check(json.encode({1, 2, 3}) == '[1,2,3]', 'sequence')
check(json.encode({n = 1}) == '{"n":1}', 'object')

-- 解码：标量、数字子类型、转义
check(json.decode('null') == nil, 'null')
check(json.decode('true') == true, 'decode true')
check(json.decode('false') == false, 'decode false')
check(json.decode('42') == 42 and math.type(json.decode('42')) == 'integer', 'decode integer')
check(json.decode('-7') == -7, 'decode negative')
check(json.decode('1.5') == 1.5 and math.type(json.decode('1.5')) == 'float', 'decode float')
check(math.type(json.decode('2.0')) == 'float', 'decode 2.0 stays float')
check(json.decode('1e3') == 1000.0 and math.type(json.decode('1e3')) == 'float', 'decode exponent')
check(json.decode('-12.5e-1') == -1.25, 'decode signed exponent')
check(json.decode('9223372036854775807') == math.maxinteger, 'decode big integer')
check(json.decode('123456789012345678901234567890') > 1e29, 'decode overflowing integer as float')
check(json.decode('"\\u4e2d"') == '中', 'unicode escape')
check(json.decode('"\\ud83d\\ude00"') == '\240\159\152\128', 'surrogate pair')
check(json.decode('"\\/"') == '/', 'escaped slash')
check(json.decode('"\\u0000"') == '\0' and #json.decode('"\\u0000"') == 1, 'nul escape')
check(json.decode('  \t\r\n [ 1 , 2 ] \n ')[2] == 2, 'whitespace')
check(type(json.decode('[]')) == 'table' and next(json.decode('[]')) == nil, 'empty array')
check(type(json.decode('{}')) == 'table' and next(json.decode('{}')) == nil, 'empty object')

local function count(table_value)
  local total = 0
  for _ in pairs(table_value) do total = total + 1 end
  return total
end
check(count(json.decode('{"a":null,"b":1}')) == 1, 'null member dropped')
check(json.decode('{"a":null,"b":1}').b == 1, 'null member keeps its sibling')
check(json.decode('[1,null,3]')[3] == 3 and json.decode('[1,null,3]')[2] == nil, 'null array item')

-- 往返：数组（含浮点/布尔/字符串）、对象、嵌套、空表
local sequence = { 1, 2.5, true, false, 'text' }
local restoredSequence = json.decode(json.encode(sequence))
check(json.encode(sequence) == '[1,2.5,true,false,"text"]', 'sequence text')
check(restoredSequence[1] == 1 and restoredSequence[2] == 2.5, 'round trip numbers')
check(restoredSequence[3] == true and restoredSequence[4] == false, 'round trip booleans')
check(restoredSequence[5] == 'text', 'round trip string')
check(math.type(restoredSequence[2]) == 'float', 'round trip float subtype')
check(restoredSequence[6] == nil, 'round trip length')

local document = {
  nested = { list = { 'deep', 'deeper' }, map = { key = 'v' }, empty = {} },
  quoted = 'a"b\\c\nd',
  unicode = '中',
  flag = true,
}
local restored = json.decode(json.encode(document))
check(restored.nested.list[1] == 'deep' and restored.nested.list[2] == 'deeper', 'round trip nesting list')
check(restored.nested.map.key == 'v', 'round trip nesting map')
check(count(restored.nested.empty) == 0, 'round trip empty table')
check(restored.quoted == 'a"b\\c\nd', 'round trip escapes')
check(restored.unicode == '中', 'round trip utf-8')
check(restored.flag == true, 'round trip flag')

-- 混合 table 不是序列，按对象编码，其整数键变成字符串（`['1']` 取回原第一个元素，`[1]` 为 nil）。
-- 这与 PC 的规则一致：只有恰好是 1..n 的 table 才是数组。
local mixed = json.decode(json.encode({ 'a', 'b', extra = 'c' }))
check(mixed['1'] == 'a' and mixed['2'] == 'b' and mixed.extra == 'c' and mixed[1] == nil, 'mixed table')

-- 共享（非循环）子表可以重复编码
local shared = { x = 1 }
check(json.encode({ a = shared, b = shared }) ~= nil, 'shared table')

-- 缓冲区增长路径：编码结果远大于初始容量（128 字节）
local wide = {}
for index = 1, 200 do wide[index] = index * 0.5 end
local wideEncoded = json.encode(wide)
check(#wideEncoded > 512, 'sink growth')
local wideRestored = json.decode(wideEncoded)
check(#wideRestored == 200 and wideRestored[200] == 100.0, 'wide sequence round trip')

local longEscaped = string.rep('a"b\\c\nd\t', 40)
check(json.decode(json.encode(longEscaped)) == longEscaped, 'long escaped string round trip')

local longKey = string.rep('k', 300)
local longObject = json.decode(json.encode({ [longKey] = longEscaped }))
check(longObject[longKey] == longEscaped, 'long key round trip')

-- 编码错误：函数、userdata、线程、NaN/无穷、循环引用、过深嵌套
local function encodeFails(value, label)
  local ok, err = pcall(json.encode, value)
  check(not ok, 'encode error: ' .. label)
  check(type(err) == 'string', 'encode error message: ' .. label)
end
encodeFails(function() end, 'lua function')
encodeFails(print, 'c function')
encodeFails(coroutine.create(function() end), 'thread')
encodeFails(0 / 0, 'nan')
encodeFails(math.huge, 'infinity')
encodeFails(-math.huge, 'negative infinity')
local cyclic = {}
cyclic.self = cyclic
encodeFails(cyclic, 'direct cycle')
local outer = {}
outer[1] = { parent = outer }
encodeFails(outer, 'indirect cycle')

local function buildNested(levels)
  local root = {}
  local node = root
  for _ = 1, levels do
    local child = {}
    node[1] = child
    node = child
  end
  return root
end
local function nestingDepth(value)
  local depth = 0
  local node = value
  while node[1] ~= nil do
    node = node[1]
    depth = depth + 1
  end
  return depth
end

-- 合法但很深的嵌套必须成功：编码每一层都用 `lua_next` 遍历、解码每一层都留着一个 table，
-- 两边都必须为栈预留空间而不是越过栈顶写（`lua_next` 自己不会扩栈）。
check(nestingDepth(json.decode(json.encode(buildNested(60)))) == 60, 'deep legal encode nesting')
-- 120 层括号包住一个空对象：解码后正好是 120 层 table（`{}` 没有 [1]，遍历到此为止）。
check(nestingDepth(json.decode(string.rep('[', 120) .. '{}' .. string.rep(']', 120))) == 120,
  'deep legal decode nesting')

encodeFails(buildNested(200), 'encode depth limit')

-- 出错后状态仍然可用（错误经 Lua 的 longjmp 展开，不能破坏 Lua 栈或缓冲区）
check(json.encode({ ok = true }) == '{"ok":true}', 'encode after error')
check(json.decode('{"ok":true}').ok == true, 'decode after encode error')

-- 解码错误：截断、非法 token、尾随内容、超深嵌套
local function decodeFails(text, label)
  local ok, err = pcall(json.decode, text)
  check(not ok, 'decode error: ' .. label)
  check(type(err) == 'string', 'decode error message: ' .. label)
end
decodeFails('', 'empty input')
decodeFails(' ', 'only whitespace')
decodeFails('{', 'truncated object')
decodeFails('[1,2', 'truncated array')
decodeFails('{"a":}', 'missing value')
decodeFails('{"a" 1}', 'missing colon')
decodeFails('{"a":1,}', 'trailing object comma')
decodeFails('[1,]', 'trailing array comma')
decodeFails('"abc', 'unterminated string')
decodeFails('"\\q"', 'unknown escape')
decodeFails('"\\u12"', 'truncated escape')
decodeFails('tru', 'bad literal')
decodeFails('nul', 'bad null')
decodeFails('01', 'leading zero')
decodeFails('.5', 'leading dot')
decodeFails('1.', 'dangling dot')
decodeFails('1e', 'dangling exponent')
decodeFails('+1', 'leading plus')
decodeFails("'x'", 'single quotes')
decodeFails('{a:1}', 'unquoted key')
decodeFails('1 2', 'trailing garbage')
decodeFails('[]]', 'trailing bracket')
decodeFails(json.encode({ 1, 2, 3 }) .. '"', 'trailing quote')

-- 超深输入必须以 Lua 错误结束，不能耗尽 C 栈或写坏 Lua 栈；重复触发也要稳定
for _ = 1, 5 do
  local ok = pcall(json.decode, string.rep('[', 5000))
  check(not ok, 'repeated deep array failure')
end
for _ = 1, 5 do
  local ok = pcall(json.decode, string.rep('{', 4000))
  check(not ok, 'repeated deep object failure')
end
check(json.decode('[1,2,3]')[3] == 3, 'decode after decode error')

-- 参数类型
check(not pcall(json.decode, {}), 'table argument rejected')
check(not pcall(json.decode), 'missing argument rejected')
check(not pcall(json.encode), 'encode without argument rejected')

print('json-module-contract: OK ' .. checks .. ' checks')
'''


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


class LuaJsonModuleHostTests(unittest.TestCase):
    """宿主语义测试：真实编译 `json_api.cpp` 并在宿主上跑断言脚本。"""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-json-")
        temporary = Path(cls.temporary.name)
        lua_objects = []
        for lua_source in sorted(LUA_ROOT.glob("*.c")):
            if lua_source.name in LUA_HOST_EXCLUDES:
                continue
            output = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(LUA_ROOT), "-c",
                 str(lua_source), "-o", str(output)],
                text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)

        harness = temporary / "lua_json_module_harness.cpp"
        harness.write_text(HARNESS.replace("__CONTRACT_SCRIPT__", CONTRACT_SCRIPT).lstrip())
        cls.harness = temporary / "lua_json_module_harness"
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-I", str(UNIT_ROOT), "-I", str(LUA_ROOT), str(harness), str(JSON_SOURCE),
             *map(str, lua_objects), "-o", str(cls.harness)],
            text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_contract_script_passes_in_a_host_lua_state(self):
        result = subprocess.run([str(self.harness)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        marker = re.search(r"^json-module-contract: OK (\d+) checks$", result.stdout, re.M)
        self.assertIsNotNone(marker, result.stdout + result.stderr)
        # The count guards against a script that silently stops early and still reports success.
        self.assertGreaterEqual(int(marker.group(1)), 120)

    def test_header_declares_the_entry_point_lua_runtime_calls(self):
        header = JSON_HEADER.read_text(encoding="utf-8")
        self.assertIn("namespace isaac::runtime", header)
        self.assertIsNotNone(
            re.search(r"^void RegisterJsonModule\(lua_State\* state\);$", header, re.M),
            header,
        )
        self.assertIn('extern "C" {\n#include <lua.h>\n}', header)

    def test_translation_unit_is_self_contained(self):
        """The unit may only include its own header, the Lua C API and hosted standard headers."""
        allowed = {
            '"interfaces/lua/json_api.hpp"',
            "<lua.h>",
            "<lauxlib.h>",
            "<cmath>",
            "<cstddef>",
            "<cstring>",
            "<stdio.h>",
        }
        includes = re.findall(r"^#include\s+(\S+)$", JSON_SOURCE.read_text(encoding="utf-8"), re.M)
        self.assertTrue(includes)
        self.assertEqual(
            sorted(set(includes) - allowed), [],
            "json_api.cpp 引入了白名单之外的依赖（family TU 必须自包含）",
        )
        self.assertIn('extern "C" {\n#include <lua.h>\n#include <lauxlib.h>\n}', JSON_SOURCE.read_text(encoding="utf-8"))

    def test_module_does_not_register_into_the_api_catalog(self):
        """`json` 是一个全局表，不是 owner/catalog 家族，两处都不应出现它。"""
        for path in (UNIT_ROOT / "interfaces/lua/api_catalog.cpp",
                     UNIT_ROOT / "interfaces/lua/api_catalog.hpp"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("json", text.lower(), f"{path.name} 不应登记 json 模块")
            self.assertNotIn("JsonModule", text)

    def test_family_unit_joins_the_size_optimized_object_list(self):
        makefile = COMMON_MAKE.read_text(encoding="utf-8")
        match = re.search(r"LUA_FAMILY_OPTIMIZED_CPPFILES :=(.*?)\n\n", makefile, re.S)
        self.assertIsNotNone(match, "common.mk 里找不到 LUA_FAMILY_OPTIMIZED_CPPFILES")
        self.assertIn("json_api.o", match.group(1))

    def test_layer_runtime_host_inputs_include_the_module(self):
        """`lua_runtime.cpp` 调用入口点后，共用这套源码清单的 13 个宿主 harness 也需要该 TU。"""
        sources = [path.name for path in layered_lua_runtime_sources(SOURCE)]
        self.assertIn("json_api.cpp", sources)


@unittest.skipUnless(docker_image_available(), "需要 devkitpro/devkita64 镜像")
class LuaJsonModuleTargetTests(unittest.TestCase):
    """目标编译参数下的编译与只读段核对；无 docker/镜像时 skip。"""

    def test_cross_compiles_within_the_read_only_budget_and_adds_no_dependency(self):
        with tempfile.TemporaryDirectory(prefix="isaac-lua-json-target-") as name:
            output = Path(name)
            os.chmod(output, 0o777)
            command = " && ".join(
                [
                    ". /opt/devkitpro/devkita64.sh",
                    f"{TARGET_COMPILE_FLAGS} -c /work/runtime/src/interfaces/lua/json_api.cpp -o /out/json_api.o",
                    "aarch64-none-elf-size -A /out/json_api.o",
                    "aarch64-none-elf-nm -u /out/json_api.o",
                ]
            )
            result = subprocess.run(
                ["docker", "run", "--rm", "-v", f"{ROOT}:/work:ro", "-v", f"{output}:/out",
                 "-w", "/work", DOCKER_IMAGE, "sh", "-lc", command],
                capture_output=True, text=True, timeout=900,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            sections = {}
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) == 3 and fields[1].isdigit():
                    sections[fields[0]] = int(fields[1])
            self.assertIn(".text", sections, result.stdout)
            read_only = sum(size for section, size in sections.items()
                            if section.startswith(".text") or section.startswith(".rodata"))
            static = sum(size for section, size in sections.items()
                         if section.startswith(".data") or section.startswith(".bss"))
            self.assertLessEqual(
                read_only, TARGET_READ_ONLY_LIMIT,
                f"json_api.o 只读段 {read_only} 字节超过 {TARGET_READ_ONLY_LIMIT} 字节预算",
            )
            self.assertLessEqual(
                static, TARGET_STATIC_LIMIT,
                f"json_api.o 在 .data/.bss 里有 {static} 字节：缓冲区应当放在 Lua userdata 里",
            )

            undefined = {line.split()[-1] for line in result.stdout.splitlines() if " U " in line}
            self.assertTrue(undefined, "没有解析到未定义符号，nm 输出格式可能变了")
            unexpected = sorted(
                symbol for symbol in undefined
                if not symbol.startswith("lua") and symbol not in ALLOWED_UNDEFINED
            )
            self.assertEqual(
                unexpected, [],
                "json_api.o 出现了白名单之外的未定义符号（新平台依赖或 C++ 运行时支持例程）",
            )


if __name__ == "__main__":
    unittest.main()
