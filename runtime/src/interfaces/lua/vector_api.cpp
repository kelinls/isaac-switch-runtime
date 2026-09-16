#include "interfaces/lua/vector_api.hpp"

#include "interfaces/lua/owner_binding.hpp"

#include "lua_object_handles.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>

extern "C" {
#include <lauxlib.h>
}

namespace isaac::runtime {
namespace {

using LuaRuntime::kVectorMetatable;
using LuaRuntime::VectorHandle;

constexpr char kVectorOwner[] = "Vector";

// `KAGE::Math::Vector2` is exactly two floats with no vtable, so the Lua value *is* the value:
// nothing here has to consult the game, which is why this family needs no published binding.
constexpr float kPi = 3.14159265358979323846f;

VectorHandle* CheckVector(lua_State* state, int index) {
    return static_cast<VectorHandle*>(luaL_checkudata(state, index, kVectorMetatable));
}

void PushVector(lua_State* state, float x, float y) {
    auto* vector = static_cast<VectorHandle*>(lua_newuserdata(state, sizeof(VectorHandle)));
    vector->x = x;
    vector->y = y;
    luaL_getmetatable(state, kVectorMetatable);
    lua_setmetatable(state, -2);
}

float LengthOf(const VectorHandle& vector) {
    return std::sqrt(vector.x * vector.x + vector.y * vector.y);
}

// PC keeps direction when the length is zero (there is no direction to scale), so the
// degenerate case is a no-op instead of a division by zero that would hand a Mod NaNs.
VectorHandle ScaledTo(const VectorHandle& vector, float length) {
    const float current = LengthOf(vector);
    if (current <= 0.0f) {
        return vector;
    }
    const float factor = length / current;
    return VectorHandle{vector.x * factor, vector.y * factor};
}

float AngleDegrees(const VectorHandle& vector) {
    return std::atan2(vector.y, vector.x) * (180.0f / kPi);
}

VectorHandle RotatedBy(const VectorHandle& vector, float degrees) {
    const float radians = degrees * (kPi / 180.0f);
    const float cosine = std::cos(radians);
    const float sine = std::sin(radians);
    return VectorHandle{vector.x * cosine - vector.y * sine, vector.x * sine + vector.y * cosine};
}

// --- Methods (catalog-attached) -------------------------------------------

int VectorLength(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Vector:Length accepts no arguments");
    }
    lua_pushnumber(state, static_cast<lua_Number>(LengthOf(*vector)));
    return 1;
}

int VectorLengthSquared(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Vector:LengthSquared accepts no arguments");
    }
    lua_pushnumber(state, static_cast<lua_Number>(vector->x * vector->x + vector->y * vector->y));
    return 1;
}

int VectorDistance(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    VectorHandle* other = CheckVector(state, 2);
    if (lua_gettop(state) != 2) {
        return luaL_error(state, "Vector:Distance accepts one Vector");
    }
    const float dx = vector->x - other->x;
    const float dy = vector->y - other->y;
    lua_pushnumber(state, static_cast<lua_Number>(std::sqrt(dx * dx + dy * dy)));
    return 1;
}

int VectorDistanceSquared(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    VectorHandle* other = CheckVector(state, 2);
    if (lua_gettop(state) != 2) {
        return luaL_error(state, "Vector:DistanceSquared accepts one Vector");
    }
    const float dx = vector->x - other->x;
    const float dy = vector->y - other->y;
    lua_pushnumber(state, static_cast<lua_Number>(dx * dx + dy * dy));
    return 1;
}

int VectorDot(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    VectorHandle* other = CheckVector(state, 2);
    if (lua_gettop(state) != 2) {
        return luaL_error(state, "Vector:Dot accepts one Vector");
    }
    lua_pushnumber(state, static_cast<lua_Number>(vector->x * other->x + vector->y * other->y));
    return 1;
}

// PC documents this as the 2x2 determinant, i.e. the z of the 3D cross product with z = 0.
int VectorCross(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    VectorHandle* other = CheckVector(state, 2);
    if (lua_gettop(state) != 2) {
        return luaL_error(state, "Vector:Cross accepts one Vector");
    }
    lua_pushnumber(state, static_cast<lua_Number>(vector->x * other->y - vector->y * other->x));
    return 1;
}

int VectorGetAngleDegrees(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Vector:GetAngleDegrees accepts no arguments");
    }
    lua_pushnumber(state, static_cast<lua_Number>(AngleDegrees(*vector)));
    return 1;
}

int VectorNormalize(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Vector:Normalize accepts no arguments");
    }
    *vector = ScaledTo(*vector, 1.0f);
    return 0;
}

int VectorNormalized(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 1) {
        return luaL_error(state, "Vector:Normalized accepts no arguments");
    }
    const VectorHandle result = ScaledTo(*vector, 1.0f);
    PushVector(state, result.x, result.y);
    return 1;
}

int VectorResize(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 2 || !lua_isnumber(state, 2)) {
        return luaL_error(state, "Vector:Resize accepts a new length");
    }
    *vector = ScaledTo(*vector, static_cast<float>(lua_tonumber(state, 2)));
    return 0;
}

int VectorResized(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 2 || !lua_isnumber(state, 2)) {
        return luaL_error(state, "Vector:Resized accepts a new length");
    }
    const VectorHandle result = ScaledTo(*vector, static_cast<float>(lua_tonumber(state, 2)));
    PushVector(state, result.x, result.y);
    return 1;
}

int VectorRotated(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 2 || !lua_isnumber(state, 2)) {
        return luaL_error(state, "Vector:Rotated accepts an angle in degrees");
    }
    const VectorHandle result =
        RotatedBy(*vector, static_cast<float>(lua_tonumber(state, 2)));
    PushVector(state, result.x, result.y);
    return 1;
}

// PC: "clamps the vector based on left, top, right, bottom boundings; doesn't keep direction",
// i.e. each component is clamped independently.
int VectorClamp(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 5 || !lua_isnumber(state, 2) || !lua_isnumber(state, 3) ||
        !lua_isnumber(state, 4) || !lua_isnumber(state, 5)) {
        return luaL_error(state, "Vector:Clamp accepts minX, minY, maxX, maxY");
    }
    const float minX = static_cast<float>(lua_tonumber(state, 2));
    const float minY = static_cast<float>(lua_tonumber(state, 3));
    const float maxX = static_cast<float>(lua_tonumber(state, 4));
    const float maxY = static_cast<float>(lua_tonumber(state, 5));
    vector->x = vector->x < minX ? minX : (vector->x > maxX ? maxX : vector->x);
    vector->y = vector->y < minY ? minY : (vector->y > maxY ? maxY : vector->y);
    return 0;
}

int VectorClamped(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    if (lua_gettop(state) != 5 || !lua_isnumber(state, 2) || !lua_isnumber(state, 3) ||
        !lua_isnumber(state, 4) || !lua_isnumber(state, 5)) {
        return luaL_error(state, "Vector:Clamped accepts minX, minY, maxX, maxY");
    }
    const float minX = static_cast<float>(lua_tonumber(state, 2));
    const float minY = static_cast<float>(lua_tonumber(state, 3));
    const float maxX = static_cast<float>(lua_tonumber(state, 4));
    const float maxY = static_cast<float>(lua_tonumber(state, 5));
    const float x = vector->x < minX ? minX : (vector->x > maxX ? maxX : vector->x);
    const float y = vector->y < minY ? minY : (vector->y > maxY ? maxY : vector->y);
    PushVector(state, x, y);
    return 1;
}

// PC: in place, `t = 0` leaves the receiver at the first vector, `t = 1` moves it to the second.
int VectorLerp(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    VectorHandle* other = CheckVector(state, 2);
    if (lua_gettop(state) != 3 || !lua_isnumber(state, 3)) {
        return luaL_error(state, "Vector:Lerp accepts a Vector and a factor");
    }
    const float t = static_cast<float>(lua_tonumber(state, 3));
    vector->x = vector->x * (1.0f - t) + other->x * t;
    vector->y = vector->y * (1.0f - t) + other->y * t;
    return 0;
}


constexpr LuaHandlerBinding kVectorHandlers[] = {
    {0x0C010001, &VectorLength},
    {0x0C010002, &VectorLengthSquared},
    {0x0C010003, &VectorDistance},
    {0x0C010004, &VectorDistanceSquared},
    {0x0C010005, &VectorDot},
    {0x0C010006, &VectorCross},
    {0x0C010007, &VectorGetAngleDegrees},
    {0x0C010008, &VectorNormalize},
    {0x0C010009, &VectorNormalized},
    {0x0C01000A, &VectorResize},
    {0x0C01000B, &VectorResized},
    {0x0C01000C, &VectorRotated},
    {0x0C01000D, &VectorClamp},
    {0x0C01000E, &VectorClamped},
    {0x0C01000F, &VectorLerp},
    {0x0C010010, &VectorFromAngle},
};

}  // namespace

void PushLuaVector(lua_State* state, float x, float y) {
    // 转发给 TU 内部那个匿名命名空间的实现（同一个元表、同一块 `VectorHandle` 内存）。
    PushVector(state, x, y);
}

int CreateVectorHandle(lua_State* state) {
    // 全局 `Vector` 是一个可调用的类表，所以本函数同时要应付两种调用形状：
    //   `Vector(1, 2)` —— 经类表的 `__call`，栈是 (类表, x, y)；
    //   直接作为 C 函数调用 —— 栈是 (x, y)。
    // PC 只文档化了 `Vector(float, float)`；`Vector.Zero`/`Vector.One` 之所以存在，正是因为
    // 没有零参数形式。
    int first = 1;
    if (lua_gettop(state) == 3 && lua_istable(state, 1)) {
        first = 2;
    }
    if (lua_gettop(state) != first + 1 || !lua_isnumber(state, first) ||
        !lua_isnumber(state, first + 1)) {
        return luaL_error(state, "Vector accepts an x and a y coordinate");
    }
    PushVector(state, static_cast<float>(lua_tonumber(state, first)),
               static_cast<float>(lua_tonumber(state, first + 1)));
    return 1;
}

int VectorIndex(lua_State* state) {
    auto* vector = static_cast<VectorHandle*>(luaL_checkudata(state, 1, kVectorMetatable));
    if (lua_type(state, 2) != LUA_TSTRING) {
        lua_pushnil(state);
        return 1;
    }
    const char* field = lua_tostring(state, 2);
    if (std::strcmp(field, "X") == 0) {
        lua_pushnumber(state, static_cast<lua_Number>(vector->x));
        return 1;
    }
    if (std::strcmp(field, "Y") == 0) {
        lua_pushnumber(state, static_cast<lua_Number>(vector->y));
        return 1;
    }
    // 方法表挂在元表的 `__methods` 上（`__index` 必须是函数才能同时服务 X/Y 与方法查找）。
    if (lua_getmetatable(state, 1) == 0) {
        lua_pushnil(state);
        return 1;
    }
    lua_getfield(state, -1, "__methods");
    lua_remove(state, -2);
    if (lua_istable(state, -1)) {
        lua_getfield(state, -1, field);
        if (!lua_isnil(state, -1)) {
            lua_remove(state, -2);
            return 1;
        }
        lua_pop(state, 1);  // 丢掉 nil，继续找运算运算符
    } else {
        lua_pop(state, 1);
    }
    // **运算符要能被 `v:__sub(other)` 这样显式点到**（2026-09-12，真机报告 `01789211254`）。
    //
    // Lua 的运算运算符（`__sub` 等）只在表达式里（`a - b`）由 VM 查元表，`a:__sub(b)` 是
    // 一次普通的方法索引 —— PC 的 `Vector` 是带运算符的 userdata，EID 就**两种写法都用**：
    // 既有 `pos - other`，也有 `entity.Position:__sub(sourcePos)`
    // （`main.lua:1475`、`main.lua:1479` 的 `diff = diff + entityDataRenderOffset` 是表达式写法）。
    // 不把运算符暴露到 `__index` 里，`v:__sub(...)` 就是 "attempt to call a nil value"，
    // 整段描述渲染中断 —— 而我们已经把运算符挂在了元表上，只是 `__index` 看不见。
    //
    // 直接从元表取：`__sub`/`__add`/`__mul`/`__div`/`__unm`/`__eq`/`__tostring`/`__len` 都是
    // 元表字段（见 `lua_runtime.cpp` 的 `RegisterVectorApi`），取出来就是同一个函数对象，
    // 语义与表达式写法完全一致（不需要另写一份实现）。
    if (field[0] == '_' && field[1] == '_') {
        if (lua_getmetatable(state, 1) == 0) {
            lua_pushnil(state);
            return 1;
        }
        lua_getfield(state, -1, field);
        lua_remove(state, -2);
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

int VectorNewIndex(lua_State* state) {
    auto* vector = static_cast<VectorHandle*>(luaL_checkudata(state, 1, kVectorMetatable));
    if (lua_type(state, 2) != LUA_TSTRING) {
        return luaL_error(state, "Vector fields are X and Y");
    }
    const char* field = lua_tostring(state, 2);
    if (std::strcmp(field, "X") == 0 || std::strcmp(field, "Y") == 0) {
        if (!lua_isnumber(state, 3)) {
            return luaL_error(state, "Vector:%s must be a number", field);
        }
        const float value = static_cast<float>(lua_tonumber(state, 3));
        if (field[0] == 'X') {
            vector->x = value;
        } else {
            vector->y = value;
        }
        return 0;
    }
    return luaL_error(state, "Vector has no writable field named %s", field);
}

int VectorAdd(lua_State* state) {
    VectorHandle* left = CheckVector(state, 1);
    VectorHandle* right = CheckVector(state, 2);
    PushVector(state, left->x + right->x, left->y + right->y);
    return 1;
}

int VectorSub(lua_State* state) {
    VectorHandle* left = CheckVector(state, 1);
    VectorHandle* right = CheckVector(state, 2);
    PushVector(state, left->x - right->x, left->y - right->y);
    return 1;
}

int VectorMul(lua_State* state) {
    VectorHandle* left = CheckVector(state, 1);
    if (lua_isnumber(state, 2)) {
        const float factor = static_cast<float>(lua_tonumber(state, 2));
        PushVector(state, left->x * factor, left->y * factor);
        return 1;
    }
    // PC's vector*vector overload is element-wise, not a dot product.
    VectorHandle* right = CheckVector(state, 2);
    PushVector(state, left->x * right->x, left->y * right->y);
    return 1;
}

int VectorDiv(lua_State* state) {
    VectorHandle* left = CheckVector(state, 1);
    if (!lua_isnumber(state, 2)) {
        return luaL_error(state, "Vector / expects a number");
    }
    const float divisor = static_cast<float>(lua_tonumber(state, 2));
    PushVector(state, left->x / divisor, left->y / divisor);
    return 1;
}

int VectorUnaryMinus(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    PushVector(state, -vector->x, -vector->y);
    return 1;
}

int VectorEqual(lua_State* state) {
    VectorHandle* left = CheckVector(state, 1);
    if (luaL_testudata(state, 2, kVectorMetatable) == nullptr) {
        lua_pushboolean(state, 0);
        return 1;
    }
    VectorHandle* right = CheckVector(state, 2);
    lua_pushboolean(state, left->x == right->x && left->y == right->y ? 1 : 0);
    return 1;
}

// PC 文档只给了形状 `Vector(X,Y)`（示例是 `Vector(0.70711,0.70711)`），没有规定精度。这里用
// `%g`（6 位有效数字）：`Vector(1,2)`、`Vector(0.707107,0.707107)`，比 `%f` 的 `1.000000`
// 更接近文档示例，也不会给整数坐标加上无意义的小数尾巴。
int VectorToString(lua_State* state) {
    VectorHandle* vector = CheckVector(state, 1);
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "Vector(%g,%g)", static_cast<double>(vector->x),
                  static_cast<double>(vector->y));
    lua_pushstring(state, buffer);
    return 1;
}

// Static `Vector.FromAngle(degrees)`: a unit vector, 0 degrees = (1, 0), 90 degrees = (0, 1).
int VectorFromAngle(lua_State* state) {
    if (lua_gettop(state) != 1 || !lua_isnumber(state, 1)) {
        return luaL_error(state, "Vector.FromAngle accepts an angle in degrees");
    }
    const float radians = static_cast<float>(lua_tonumber(state, 1)) * (kPi / 180.0f);
    PushVector(state, std::cos(radians), std::sin(radians));
    return 1;
}

int VectorClassIndex(lua_State* state) {
    if (lua_type(state, 2) != LUA_TSTRING) {
        lua_pushnil(state);
        return 1;
    }
    const char* field = lua_tostring(state, 2);
    if (std::strcmp(field, "Zero") == 0) {
        PushVector(state, 0.0f, 0.0f);
        return 1;
    }
    if (std::strcmp(field, "One") == 0) {
        PushVector(state, 1.0f, 1.0f);
        return 1;
    }
    if (std::strcmp(field, "FromAngle") == 0) {
        lua_pushcfunction(state, isaac::runtime::VectorFromAngle);
        return 1;
    }
    lua_pushnil(state);
    return 1;
}

std::size_t AttachVectorMethods(lua_State* state) noexcept {
    return AttachOwnerMethods(state, kVectorOwner, kVectorHandlers,
                              sizeof(kVectorHandlers) / sizeof(kVectorHandlers[0]));
}

}  // namespace isaac::runtime
