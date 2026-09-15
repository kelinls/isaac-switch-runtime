#include "interfaces/lua/json_api.hpp"

#include <cmath>
#include <cstddef>
#include <cstring>

// `lua_number2str` / `lua_integer2str` expand to `snprintf` (Lua 5.3's `l_sprintf`), and that
// macro reaches for the name in the global namespace, so `<stdio.h>` rather than `<cstdio>`.
#include <stdio.h>

extern "C" {
#include <lua.h>
#include <lauxlib.h>
}

// `json.encode` / `json.decode`, the PC-Lua module EID requires at load time.
//
// Both directions walk the value recursively with an explicit depth budget and stop with a Lua
// error, so a Mod can hand us a cycle, a function or a truncated document without taking the game
// down. Neither direction keeps C++ state across a `luaL_error` (which unwinds with a longjmp and
// would skip destructors), so every local here is a plain scalar or a POD aggregate.
namespace isaac::runtime {
namespace {

// Byte values 0x00..0x0F in lower-case hex, for the `\u00XX` form of short control characters.
constexpr const char* kHexDigits = "0123456789abcdef";

// A table is an array only when it is exactly `1..n`, so the encoder needs a bounded walk; PC's own
// encoder produces `[1,2]` for a sequence and `{}` for an empty table. Mod data is a handful of
// levels deep, which makes this budget (and the cycle check that uses the same counter) cheap.
constexpr int kEncodeMaximumDepth = 64;

// Deep input has to be rejected *before* the C stack runs out: `[[[[...` is one character per
// level, so a length-limited parser alone would still smash the stack.
constexpr int kDecodeMaximumDepth = 128;

constexpr std::size_t kInitialSinkCapacity = 128;

// A hard ceiling keeps the doubling math away from `size_t` wraparound; the Lua allocator reports
// the real out-of-memory condition long before this.
constexpr std::size_t kMaximumSinkBytes = 64u * 1024u * 1024u;

// ---------------------------------------------------------------------------
// Growable byte sink
// ---------------------------------------------------------------------------
//
// `luaL_Buffer` cannot be used here. In Lua 5.3.3 the buffer's `UBox` userdata sits on the *top*
// of the stack and grows through `resizebox(L, -1, newsize)` (see
// `source/third_party/lua-5.3.3/src/lauxlib.c`), while encoding a table iterates it with
// `lua_next`, which keeps the pending key above everything else. The first growth inside that
// window would resize the key instead of the box.
//
// This sink instead owns one plain Lua userdata pinned to an *absolute* stack slot. Growing
// allocates a larger userdata and swaps it in with `lua_replace`, which leaves every other slot
// untouched, so an iteration in progress cannot be disturbed. The bytes come from the Lua
// allocator (nothing lands in the read-only segment) and the collector reclaims them even when an
// encode error unwinds with a longjmp.

struct ByteSinkHeader {
    std::size_t length;
    std::size_t capacity;
};

struct ByteSink {
    lua_State* state;
    int slot;  // absolute stack index of the userdata that carries the bytes
};

ByteSinkHeader* SinkHeader(const ByteSink* sink) {
    return static_cast<ByteSinkHeader*>(lua_touserdata(sink->state, sink->slot));
}

char* SinkBytes(ByteSinkHeader* header) {
    return reinterpret_cast<char*>(header + 1);
}

void CreateSink(lua_State* state, ByteSink* sink, std::size_t capacity) {
    sink->state = state;
    void* block = lua_newuserdata(state, sizeof(ByteSinkHeader) + capacity);
    auto* header = static_cast<ByteSinkHeader*>(block);
    header->length = 0;
    header->capacity = capacity;
    sink->slot = lua_gettop(state);
}

void ResetSink(ByteSink* sink) {
    SinkHeader(sink)->length = 0;
}

// Writable region of at least `size` bytes; the logical length is unchanged, so the caller decides
// how much of it is real. The pointer is only valid until the next sink call.
char* SinkReserve(ByteSink* sink, std::size_t size) {
    lua_State* state = sink->state;
    ByteSinkHeader* header = SinkHeader(sink);
    if (header->capacity - header->length < size) {
        if (size > kMaximumSinkBytes - header->length) {
            luaL_error(state, "json: text is too large");
            return nullptr;  // unreachable: luaL_error unwinds with a longjmp
        }
        std::size_t capacity = header->capacity * 2;
        if (capacity - header->length < size) {
            capacity = header->length + size;
        }
        if (capacity > kMaximumSinkBytes) {
            capacity = kMaximumSinkBytes;
        }
        void* block = lua_newuserdata(state, sizeof(ByteSinkHeader) + capacity);
        auto* grown = static_cast<ByteSinkHeader*>(block);
        std::memcpy(SinkBytes(grown), SinkBytes(header), header->length);
        grown->length = header->length;
        grown->capacity = capacity;
        lua_replace(state, sink->slot);
        header = grown;
    }
    return SinkBytes(header) + header->length;
}

void AppendRaw(ByteSink* sink, const char* text, std::size_t length) {
    char* destination = SinkReserve(sink, length);
    std::memcpy(destination, text, length);
    SinkHeader(sink)->length += length;
}

void AppendChar(ByteSink* sink, char value) {
    char* destination = SinkReserve(sink, 1);
    *destination = value;
    SinkHeader(sink)->length += 1;
}

// ---------------------------------------------------------------------------
// encode
// ---------------------------------------------------------------------------

struct EncodeContext {
    ByteSink* sink;
    const void* ancestors[kEncodeMaximumDepth];
    int depth;
};

void AppendEncodedValue(lua_State* state, int index, EncodeContext* context);

void AppendEscapedString(ByteSink* sink, const char* text, std::size_t length) {
    AppendChar(sink, '"');
    const char* runStart = text;
    for (std::size_t index = 0; index < length; ++index) {
        const unsigned char byte = static_cast<unsigned char>(text[index]);
        const char* escape = nullptr;
        std::size_t escapeLength = 0;
        char unicodeEscape[6];
        switch (byte) {
        case '"':
            escape = "\\\"";
            escapeLength = 2;
            break;
        case '\\':
            escape = "\\\\";
            escapeLength = 2;
            break;
        case '\b':
            escape = "\\b";
            escapeLength = 2;
            break;
        case '\f':
            escape = "\\f";
            escapeLength = 2;
            break;
        case '\n':
            escape = "\\n";
            escapeLength = 2;
            break;
        case '\r':
            escape = "\\r";
            escapeLength = 2;
            break;
        case '\t':
            escape = "\\t";
            escapeLength = 2;
            break;
        default:
            // Everything else below 0x20 has no short form; bytes >= 0x20 (including UTF-8
            // continuation bytes) are valid JSON as they are.
            if (byte < 0x20) {
                unicodeEscape[0] = '\\';
                unicodeEscape[1] = 'u';
                unicodeEscape[2] = '0';
                unicodeEscape[3] = '0';
                unicodeEscape[4] = kHexDigits[byte >> 4];
                unicodeEscape[5] = kHexDigits[byte & 0x0f];
                escape = unicodeEscape;
                escapeLength = sizeof(unicodeEscape);
            }
            break;
        }
        if (escape == nullptr) {
            continue;
        }
        AppendRaw(sink, runStart, static_cast<std::size_t>(text + index - runStart));
        AppendRaw(sink, escape, escapeLength);
        runStart = text + index + 1;
    }
    AppendRaw(sink, runStart, static_cast<std::size_t>(text + length - runStart));
    AppendChar(sink, '"');
}

// Lua keeps integer and float subtypes apart, and so does PC's `json`: `1` stays `1` while `1.5`
// stays `1.5`. An integral float gets a `.0` so it comes back as a float instead of silently
// changing subtype on the round trip.
void AppendNumber(ByteSink* sink, lua_State* state, int index) {
    char text[64];
    if (lua_isinteger(state, index) != 0) {
        lua_integer2str(text, sizeof(text), lua_tointeger(state, index));
        AppendRaw(sink, text, std::strlen(text));
        return;
    }
    const lua_Number number = lua_tonumber(state, index);
    if (!std::isfinite(number)) {  // JSON has no spelling for NaN or the infinities
        luaL_error(state, "json.encode: a NaN or infinite number has no JSON form");
        return;
    }
    lua_number2str(text, sizeof(text), number);
    std::size_t length = std::strlen(text);
    if (std::strpbrk(text, ".eE") == nullptr) {
        text[length++] = '.';
        text[length++] = '0';
        text[length] = '\0';
    }
    AppendRaw(sink, text, length);
}

void AppendMemberName(lua_State* state, int index, EncodeContext* context) {
    if (lua_type(state, index) == LUA_TSTRING) {
        std::size_t length = 0;
        const char* text = lua_tolstring(state, index, &length);
        AppendEscapedString(context->sink, text, length);
        return;
    }
    if (lua_type(state, index) == LUA_TNUMBER) {
        // PC stringifies numeric keys (`{ [1.5] = x }` becomes `{"1.5":x}`).
        AppendChar(context->sink, '"');
        AppendNumber(context->sink, state, index);
        AppendChar(context->sink, '"');
        return;
    }
    luaL_error(state, "json.encode: table keys must be strings or numbers");
}

// A table is a JSON array exactly when its keys are `1..n` with nothing else, which `lua_rawlen`
// alone cannot tell (`{[1]=1,[2]=2,[4]=4}` reports a border of 2 or 4 but is not a sequence).
bool TableIsSequence(lua_State* state, int index, lua_Unsigned length) {
    if (length == 0) {
        return false;
    }
    lua_Unsigned keys = 0;
    lua_pushnil(state);
    while (lua_next(state, index) != 0) {
        ++keys;
        const bool isIntegerKey = lua_isinteger(state, -2) != 0;
        const lua_Integer key = isIntegerKey ? lua_tointeger(state, -2) : 0;
        lua_pop(state, 1);  // pop the value, keep the key for the next lua_next
        if (!isIntegerKey || key < 1 || static_cast<lua_Unsigned>(key) > length) {
            lua_pop(state, 1);  // discard the pending key
            return false;
        }
    }
    return keys == length;
}

void AppendEncodedTable(lua_State* state, int index, EncodeContext* context) {
    const void* identity = lua_topointer(state, index);
    for (int level = 0; level < context->depth; ++level) {
        if (context->ancestors[level] == identity) {
            luaL_error(state, "json.encode: a table contains itself");
            return;
        }
    }
    if (context->depth >= kEncodeMaximumDepth) {
        luaL_error(state, "json.encode: table nesting is deeper than %d levels", kEncodeMaximumDepth);
        return;
    }
    context->ancestors[context->depth] = identity;
    ++context->depth;

    const lua_Unsigned length = static_cast<lua_Unsigned>(lua_rawlen(state, index));
    if (TableIsSequence(state, index, length)) {
        AppendChar(context->sink, '[');
        for (lua_Unsigned item = 1; item <= length; ++item) {
            if (item > 1) {
                AppendChar(context->sink, ',');
            }
            lua_rawgeti(state, index, static_cast<lua_Integer>(item));
            AppendEncodedValue(state, lua_gettop(state), context);
            lua_pop(state, 1);
        }
        AppendChar(context->sink, ']');
    } else {
        AppendChar(context->sink, '{');
        bool first = true;
        lua_pushnil(state);
        while (lua_next(state, index) != 0) {
            if (!first) {
                AppendChar(context->sink, ',');
            }
            first = false;
            // Sink growth only ever swaps its own userdata in place, so the key stays at
            // `top - 1` and the value at `top` across both calls below.
            AppendMemberName(state, lua_gettop(state) - 1, context);
            AppendChar(context->sink, ':');
            AppendEncodedValue(state, lua_gettop(state), context);
            lua_pop(state, 1);  // pop the value, keep the key for the next lua_next
        }
        AppendChar(context->sink, '}');
    }
    --context->depth;
}

void AppendEncodedValue(lua_State* state, int index, EncodeContext* context) {
    // `lua_next` writes the next key/value pair at the free slots above the key it is handed and
    // never grows the stack itself, so every level has to reserve space before it pushes. Six
    // covers this level's pushes (table + key + value + the sink's growth userdata) with margin.
    luaL_checkstack(state, 6, "json.encode: value is too deeply nested");
    switch (lua_type(state, index)) {
    case LUA_TNIL:
        AppendRaw(context->sink, "null", 4);
        return;
    case LUA_TBOOLEAN:
        if (lua_toboolean(state, index) != 0) {
            AppendRaw(context->sink, "true", 4);
        } else {
            AppendRaw(context->sink, "false", 5);
        }
        return;
    case LUA_TNUMBER:
        AppendNumber(context->sink, state, index);
        return;
    case LUA_TSTRING: {
        std::size_t length = 0;
        const char* text = lua_tolstring(state, index, &length);
        AppendEscapedString(context->sink, text, length);
        return;
    }
    case LUA_TTABLE:
        AppendEncodedTable(state, index, context);
        return;
    default:
        // Functions, userdata, threads and light userdata: a Mod must not get a silently dropped
        // value, and PC raises here as well.
        luaL_error(state, "json.encode: cannot encode a %s value", luaL_typename(state, index));
        return;
    }
}

int JsonEncode(lua_State* state) {
    luaL_checkany(state, 1);
    ByteSink sink;
    CreateSink(state, &sink, kInitialSinkCapacity);
    EncodeContext context;
    context.sink = &sink;
    context.depth = 0;
    AppendEncodedValue(state, 1, &context);
    ByteSinkHeader* header = SinkHeader(&sink);
    lua_pushlstring(state, SinkBytes(header), header->length);
    lua_replace(state, sink.slot);  // the userdata becomes the result string
    return 1;
}

// ---------------------------------------------------------------------------
// decode
// ---------------------------------------------------------------------------

struct DecodeCursor {
    const char* text;
    std::size_t length;
    std::size_t position;
};

struct DecodeContext {
    ByteSink* scratch;
    int depth;
};

void FailDecode(lua_State* state, const DecodeCursor* cursor, const char* detail) {
    luaL_error(state, "json.decode: %s (byte %d)", detail, static_cast<int>(cursor->position) + 1);
}

bool AtEnd(const DecodeCursor* cursor) {
    return cursor->position >= cursor->length;
}

char CurrentByte(const DecodeCursor* cursor) {
    return cursor->text[cursor->position];
}

void SkipWhitespace(DecodeCursor* cursor) {
    while (!AtEnd(cursor)) {
        const char byte = CurrentByte(cursor);
        if (byte != ' ' && byte != '\t' && byte != '\n' && byte != '\r') {
            return;
        }
        ++cursor->position;
    }
}

void ExpectLiteral(lua_State* state, DecodeCursor* cursor, const char* literal, std::size_t length) {
    if (cursor->length - cursor->position < length ||
        std::memcmp(cursor->text + cursor->position, literal, length) != 0) {
        FailDecode(state, cursor, "invalid literal");
        return;
    }
    cursor->position += length;
}

int HexDigitValue(char byte) {
    if (byte >= '0' && byte <= '9') {
        return byte - '0';
    }
    if (byte >= 'a' && byte <= 'f') {
        return byte - 'a' + 10;
    }
    if (byte >= 'A' && byte <= 'F') {
        return byte - 'A' + 10;
    }
    return -1;
}

bool ReadHexCodeUnit(lua_State* state, DecodeCursor* cursor, unsigned int* code) {
    if (cursor->length - cursor->position < 4) {
        FailDecode(state, cursor, "truncated \\u escape");
        return false;
    }
    unsigned int value = 0;
    for (int index = 0; index < 4; ++index) {
        const int digit = HexDigitValue(cursor->text[cursor->position + index]);
        if (digit < 0) {
            FailDecode(state, cursor, "invalid \\u escape");
            return false;
        }
        value = (value << 4) | static_cast<unsigned int>(digit);
    }
    cursor->position += 4;
    *code = value;
    return true;
}

void AppendUtf8(ByteSink* sink, unsigned int code) {
    char bytes[4];
    std::size_t length = 0;
    if (code < 0x80) {
        bytes[length++] = static_cast<char>(code);
    } else if (code < 0x800) {
        bytes[length++] = static_cast<char>(0xc0 | (code >> 6));
        bytes[length++] = static_cast<char>(0x80 | (code & 0x3f));
    } else if (code < 0x10000) {
        bytes[length++] = static_cast<char>(0xe0 | (code >> 12));
        bytes[length++] = static_cast<char>(0x80 | ((code >> 6) & 0x3f));
        bytes[length++] = static_cast<char>(0x80 | (code & 0x3f));
    } else {
        bytes[length++] = static_cast<char>(0xf0 | (code >> 18));
        bytes[length++] = static_cast<char>(0x80 | ((code >> 12) & 0x3f));
        bytes[length++] = static_cast<char>(0x80 | ((code >> 6) & 0x3f));
        bytes[length++] = static_cast<char>(0x80 | (code & 0x3f));
    }
    AppendRaw(sink, bytes, length);
}

// Parses one JSON string starting at its opening quote and pushes the result. The scratch sink is
// reused for every string in the document, so a large decode does not allocate per string.
void ParseJsonString(lua_State* state, DecodeCursor* cursor, ByteSink* scratch) {
    ++cursor->position;  // consume the opening quote
    ResetSink(scratch);
    const char* runStart = cursor->text + cursor->position;
    while (true) {
        if (AtEnd(cursor)) {
            FailDecode(state, cursor, "unterminated string");
            return;
        }
        const unsigned char byte = static_cast<unsigned char>(CurrentByte(cursor));
        if (byte == '"') {
            AppendRaw(scratch, runStart, static_cast<std::size_t>(cursor->text + cursor->position - runStart));
            ++cursor->position;
            break;
        }
        if (byte == '\\') {
            AppendRaw(scratch, runStart, static_cast<std::size_t>(cursor->text + cursor->position - runStart));
            ++cursor->position;
            if (AtEnd(cursor)) {
                FailDecode(state, cursor, "unterminated escape sequence");
                return;
            }
            const char escape = cursor->text[cursor->position++];
            switch (escape) {
            case '"':
                AppendChar(scratch, '"');
                break;
            case '\\':
                AppendChar(scratch, '\\');
                break;
            case '/':
                AppendChar(scratch, '/');
                break;
            case 'b':
                AppendChar(scratch, '\b');
                break;
            case 'f':
                AppendChar(scratch, '\f');
                break;
            case 'n':
                AppendChar(scratch, '\n');
                break;
            case 'r':
                AppendChar(scratch, '\r');
                break;
            case 't':
                AppendChar(scratch, '\t');
                break;
            case 'u': {
                unsigned int code = 0;
                if (!ReadHexCodeUnit(state, cursor, &code)) {
                    return;
                }
                if (code >= 0xd800 && code <= 0xdbff && cursor->length - cursor->position >= 6 &&
                    cursor->text[cursor->position] == '\\' && cursor->text[cursor->position + 1] == 'u') {
                    const std::size_t saved = cursor->position;
                    cursor->position += 2;
                    unsigned int low = 0;
                    if (!ReadHexCodeUnit(state, cursor, &low)) {
                        return;
                    }
                    if (low >= 0xdc00 && low <= 0xdfff) {
                        code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                    } else {
                        // Not a pair after all: rewind and let the loop parse the next escape.
                        cursor->position = saved;
                        code = 0xfffd;
                    }
                } else if (code >= 0xd800 && code <= 0xdfff) {
                    // A lone surrogate has no UTF-8 form; emit U+FFFD instead of invalid bytes.
                    code = 0xfffd;
                }
                AppendUtf8(scratch, code);
                break;
            }
            default:
                FailDecode(state, cursor, "unknown escape sequence");
                return;
            }
            runStart = cursor->text + cursor->position;
            continue;
        }
        if (byte < 0x20) {
            FailDecode(state, cursor, "unescaped control character in string");
            return;
        }
        ++cursor->position;
    }
    lua_pushlstring(state, SinkBytes(SinkHeader(scratch)), SinkHeader(scratch)->length);
}

// Strict RFC 8259 grammar: no leading `+`, no leading zeros, a digit required on both sides of the
// decimal point and after the exponent. The validated token is handed to `lua_stringtonumber`, so
// integers stay Lua integers and anything that does not fit becomes a float, exactly like the Lua
// lexer.
void ParseJsonNumber(lua_State* state, DecodeCursor* cursor, ByteSink* scratch) {
    const std::size_t start = cursor->position;
    if (CurrentByte(cursor) == '-') {
        ++cursor->position;
        if (AtEnd(cursor)) {
            FailDecode(state, cursor, "truncated number");
            return;
        }
    }
    if (CurrentByte(cursor) == '0') {
        ++cursor->position;
    } else if (CurrentByte(cursor) >= '1' && CurrentByte(cursor) <= '9') {
        do {
            ++cursor->position;
        } while (!AtEnd(cursor) && CurrentByte(cursor) >= '0' && CurrentByte(cursor) <= '9');
    } else {
        FailDecode(state, cursor, "invalid number");
        return;
    }
    if (!AtEnd(cursor) && CurrentByte(cursor) == '.') {
        ++cursor->position;
        if (AtEnd(cursor) || CurrentByte(cursor) < '0' || CurrentByte(cursor) > '9') {
            FailDecode(state, cursor, "invalid fraction in number");
            return;
        }
        while (!AtEnd(cursor) && CurrentByte(cursor) >= '0' && CurrentByte(cursor) <= '9') {
            ++cursor->position;
        }
    }
    if (!AtEnd(cursor) && (CurrentByte(cursor) == 'e' || CurrentByte(cursor) == 'E')) {
        ++cursor->position;
        if (!AtEnd(cursor) && (CurrentByte(cursor) == '+' || CurrentByte(cursor) == '-')) {
            ++cursor->position;
        }
        if (AtEnd(cursor) || CurrentByte(cursor) < '0' || CurrentByte(cursor) > '9') {
            FailDecode(state, cursor, "invalid exponent in number");
            return;
        }
        while (!AtEnd(cursor) && CurrentByte(cursor) >= '0' && CurrentByte(cursor) <= '9') {
            ++cursor->position;
        }
    }

    const std::size_t tokenLength = cursor->position - start;
    ResetSink(scratch);
    char* token = SinkReserve(scratch, tokenLength + 1);
    std::memcpy(token, cursor->text + start, tokenLength);
    token[tokenLength] = '\0';
    if (lua_stringtonumber(state, token) == 0) {
        FailDecode(state, cursor, "number is out of range");
        return;
    }
}

void ParseJsonValue(lua_State* state, DecodeCursor* cursor, DecodeContext* context, bool* isNull);

void ParseJsonObject(lua_State* state, DecodeCursor* cursor, DecodeContext* context) {
    if (context->depth >= kDecodeMaximumDepth) {
        FailDecode(state, cursor, "nesting is deeper than the supported limit");
        return;
    }
    ++context->depth;
    ++cursor->position;  // consume '{'
    lua_newtable(state);
    const int tableIndex = lua_gettop(state);
    SkipWhitespace(cursor);
    if (!AtEnd(cursor) && CurrentByte(cursor) == '}') {
        ++cursor->position;
        --context->depth;
        return;
    }
    while (true) {
        SkipWhitespace(cursor);
        if (AtEnd(cursor) || CurrentByte(cursor) != '"') {
            FailDecode(state, cursor, "object keys must be strings");
            return;
        }
        ParseJsonString(state, cursor, context->scratch);  // pushes the key
        SkipWhitespace(cursor);
        if (AtEnd(cursor) || CurrentByte(cursor) != ':') {
            FailDecode(state, cursor, "expected ':' after an object key");
            return;
        }
        ++cursor->position;
        bool memberIsNull = false;
        ParseJsonValue(state, cursor, context, &memberIsNull);
        if (memberIsNull) {
            lua_pop(state, 1);  // a JSON null member is not stored: Lua has no nil value to keep
        } else {
            lua_rawset(state, tableIndex);  // pops the key and the value
        }
        SkipWhitespace(cursor);
        if (AtEnd(cursor)) {
            FailDecode(state, cursor, "unterminated object");
            return;
        }
        const char separator = CurrentByte(cursor);
        if (separator == ',') {
            ++cursor->position;
            continue;
        }
        if (separator == '}') {
            ++cursor->position;
            break;
        }
        FailDecode(state, cursor, "expected ',' or '}' after an object member");
        return;
    }
    --context->depth;
}

void ParseJsonArray(lua_State* state, DecodeCursor* cursor, DecodeContext* context) {
    if (context->depth >= kDecodeMaximumDepth) {
        FailDecode(state, cursor, "nesting is deeper than the supported limit");
        return;
    }
    ++context->depth;
    ++cursor->position;  // consume '['
    lua_newtable(state);
    const int tableIndex = lua_gettop(state);
    SkipWhitespace(cursor);
    if (!AtEnd(cursor) && CurrentByte(cursor) == ']') {
        ++cursor->position;
        --context->depth;
        return;
    }
    lua_Integer item = 0;
    while (true) {
        ++item;  // every element consumes at least one byte, so this cannot overflow
        bool itemIsNull = false;
        ParseJsonValue(state, cursor, context, &itemIsNull);
        if (!itemIsNull) {
            lua_rawseti(state, tableIndex, item);
        }
        SkipWhitespace(cursor);
        if (AtEnd(cursor)) {
            FailDecode(state, cursor, "unterminated array");
            return;
        }
        const char separator = CurrentByte(cursor);
        if (separator == ',') {
            ++cursor->position;
            continue;
        }
        if (separator == ']') {
            ++cursor->position;
            break;
        }
        FailDecode(state, cursor, "expected ',' or ']' after an array element");
        return;
    }
    --context->depth;
}

// Pushes exactly one Lua value for the parsed JSON value, except for `null`, which pushes nothing
// and sets `*isNull`: that is what lets an object drop a null member while an array keeps the
// element position.
void ParseJsonValue(lua_State* state, DecodeCursor* cursor, DecodeContext* context, bool* isNull) {
    *isNull = false;
    // Each nesting level keeps a table plus, while a container is being built, a key and a value
    // slot alive; reserving six here is what keeps a deep-but-legal document from writing past
    // the Lua stack (`lua_next` does not grow it on its own).
    luaL_checkstack(state, 6, "json.decode: document is too deeply nested");
    SkipWhitespace(cursor);
    if (AtEnd(cursor)) {
        FailDecode(state, cursor, "unexpected end of input");
        return;
    }
    switch (CurrentByte(cursor)) {
    case '{':
        ParseJsonObject(state, cursor, context);
        return;
    case '[':
        ParseJsonArray(state, cursor, context);
        return;
    case '"':
        ParseJsonString(state, cursor, context->scratch);
        return;
    case 't':
        ExpectLiteral(state, cursor, "true", 4);
        lua_pushboolean(state, 1);
        return;
    case 'f':
        ExpectLiteral(state, cursor, "false", 5);
        lua_pushboolean(state, 0);
        return;
    case 'n':
        ExpectLiteral(state, cursor, "null", 4);
        *isNull = true;
        return;
    default:
        ParseJsonNumber(state, cursor, context->scratch);
        return;
    }
}

int JsonDecode(lua_State* state) {
    std::size_t length = 0;
    const char* text = luaL_checklstring(state, 1, &length);
    DecodeCursor cursor;
    cursor.text = text;
    cursor.length = length;
    cursor.position = 0;
    ByteSink scratch;
    CreateSink(state, &scratch, kInitialSinkCapacity);
    DecodeContext context;
    context.scratch = &scratch;
    context.depth = 0;
    bool isNull = false;
    ParseJsonValue(state, &cursor, &context, &isNull);
    SkipWhitespace(&cursor);
    if (cursor.position != cursor.length) {
        FailDecode(state, &cursor, "trailing characters after the JSON value");
        return 0;
    }
    if (isNull) {
        lua_pushnil(state);
    }
    lua_replace(state, scratch.slot);  // the scratch userdata becomes the result value
    return 1;
}

} // namespace

void RegisterJsonModule(lua_State* state) {
    lua_newtable(state);
    lua_pushcfunction(state, JsonEncode);
    lua_setfield(state, -2, "encode");
    lua_pushcfunction(state, JsonDecode);
    lua_setfield(state, -2, "decode");
    lua_setglobal(state, "json");
}

} // namespace isaac::runtime
