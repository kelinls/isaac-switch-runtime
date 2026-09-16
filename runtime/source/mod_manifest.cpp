#include "mod_manifest.hpp"

#if !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13

#include <array>
#include <cstddef>
#include <cstring>

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize ("Os")
#endif

namespace {

constexpr std::size_t kMaximumNesting = 16;

struct ParsedString {
    std::size_t length{};
    bool overflow{};
    bool containsNul{};
};

bool IsWhitespace(char value) {
    return value == ' ' || value == '\t' || value == '\r' || value == '\n';
}

bool IsDigit(char value) {
    return value >= '0' && value <= '9';
}

bool IsHexDigit(char value, std::uint32_t* digit) {
    if (value >= '0' && value <= '9') {
        *digit = static_cast<std::uint32_t>(value - '0');
        return true;
    }
    if (value >= 'a' && value <= 'f') {
        *digit = static_cast<std::uint32_t>(value - 'a' + 10);
        return true;
    }
    if (value >= 'A' && value <= 'F') {
        *digit = static_cast<std::uint32_t>(value - 'A' + 10);
        return true;
    }
    return false;
}

class Parser {
public:
    Parser(const char* json, std::size_t length)
        : cursor_(reinterpret_cast<const unsigned char*>(json)), end_(cursor_ + length) {}

    ModManifest::ParseResult Parse(ModManifest::SelectedMod* selected) {
        ModManifest::SelectedMods selectedMods{};
        const ModManifest::ParseResult result = Parse(&selectedMods);
        if (result == ModManifest::ParseResult::Success) {
            *selected = selectedMods.mods[0];
        }
        return result;
    }

    // 收下**全部**启用的 Mod（多模组加载，2026-09-16）。`SelectFirstEnabled` 走上面那个
    // 薄包装 —— 两者共用同一份解析逻辑，不会出现"单模组路径修了、多模组路径没修"的分叉。
    ModManifest::ParseResult Parse(ModManifest::SelectedMods* selected) {
        SkipWhitespace();
        if (!ParseRoot(1)) {
            return failure_;
        }
        SkipWhitespace();
        if (cursor_ != end_) {
            return ModManifest::ParseResult::InvalidJson;
        }
        if (selectedCount_ == 0) {
            return ModManifest::ParseResult::NoEnabledMod;
        }
        // `count` 与 `mods[]` 必须一起给出去：解析期间只累加 `selectedCount_`（成员），
        // 这里才把它落到结果对象上 —— 漏了这一行会得到"结构体里明明有 Mod、count 却是 0"
        // 的诡异现象（本文件的第一次实现在测试里就是这么红的）。
        selectedMods_.count = selectedCount_;
        *selected = selectedMods_;
        return ModManifest::ParseResult::Success;
    }

private:
    void SkipWhitespace() {
        while (cursor_ != end_ && IsWhitespace(static_cast<char>(*cursor_))) {
            ++cursor_;
        }
    }

    bool Consume(char expected) {
        if (cursor_ == end_ || *cursor_ != static_cast<unsigned char>(expected)) {
            return false;
        }
        ++cursor_;
        return true;
    }

    bool ConsumeLiteral(const char* literal, std::size_t length) {
        if (static_cast<std::size_t>(end_ - cursor_) < length || std::memcmp(cursor_, literal, length) != 0) {
            return false;
        }
        cursor_ += length;
        return true;
    }

    bool AppendByte(unsigned char value, char* output, std::size_t capacity, ParsedString* parsed) {
        if (value == 0) {
            parsed->containsNul = true;
        }
        if (output != nullptr) {
            if (parsed->length + 1 < capacity) {
                output[parsed->length] = static_cast<char>(value);
            } else {
                parsed->overflow = true;
            }
        }
        ++parsed->length;
        return true;
    }

    bool AppendCodePoint(std::uint32_t codePoint, char* output, std::size_t capacity, ParsedString* parsed) {
        if (codePoint <= 0x7F) {
            return AppendByte(static_cast<unsigned char>(codePoint), output, capacity, parsed);
        }
        if (codePoint <= 0x7FF) {
            return AppendByte(static_cast<unsigned char>(0xC0 | (codePoint >> 6)), output, capacity, parsed) &&
                   AppendByte(static_cast<unsigned char>(0x80 | (codePoint & 0x3F)), output, capacity, parsed);
        }
        if (codePoint <= 0xFFFF) {
            return AppendByte(static_cast<unsigned char>(0xE0 | (codePoint >> 12)), output, capacity, parsed) &&
                   AppendByte(static_cast<unsigned char>(0x80 | ((codePoint >> 6) & 0x3F)), output, capacity, parsed) &&
                   AppendByte(static_cast<unsigned char>(0x80 | (codePoint & 0x3F)), output, capacity, parsed);
        }
        return AppendByte(static_cast<unsigned char>(0xF0 | (codePoint >> 18)), output, capacity, parsed) &&
               AppendByte(static_cast<unsigned char>(0x80 | ((codePoint >> 12) & 0x3F)), output, capacity, parsed) &&
               AppendByte(static_cast<unsigned char>(0x80 | ((codePoint >> 6) & 0x3F)), output, capacity, parsed) &&
               AppendByte(static_cast<unsigned char>(0x80 | (codePoint & 0x3F)), output, capacity, parsed);
    }

    bool ParseHexQuad(std::uint32_t* value) {
        if (static_cast<std::size_t>(end_ - cursor_) < 4) {
            return false;
        }
        std::uint32_t result = 0;
        for (std::size_t index = 0; index < 4; ++index) {
            std::uint32_t digit = 0;
            if (!IsHexDigit(static_cast<char>(*cursor_++), &digit)) {
                return false;
            }
            result = (result << 4) | digit;
        }
        *value = result;
        return true;
    }

    bool ParseEscapedCodePoint(std::uint32_t* codePoint) {
        std::uint32_t first = 0;
        if (!ParseHexQuad(&first)) {
            return false;
        }
        if (first >= 0xDC00 && first <= 0xDFFF) {
            return false;
        }
        if (first < 0xD800 || first > 0xDBFF) {
            *codePoint = first;
            return true;
        }
        if (!Consume('\\') || !Consume('u')) {
            return false;
        }
        std::uint32_t second = 0;
        if (!ParseHexQuad(&second) || second < 0xDC00 || second > 0xDFFF) {
            return false;
        }
        *codePoint = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00);
        return true;
    }

    bool AppendRawUtf8(char* output, std::size_t capacity, ParsedString* parsed) {
        const unsigned char first = *cursor_;
        std::size_t count = 0;
        if (first >= 0xC2 && first <= 0xDF) {
            count = 2;
        } else if (first >= 0xE0 && first <= 0xEF) {
            count = 3;
        } else if (first >= 0xF0 && first <= 0xF4) {
            count = 4;
        } else {
            return false;
        }
        if (static_cast<std::size_t>(end_ - cursor_) < count) {
            return false;
        }
        const unsigned char second = cursor_[1];
        if ((second & 0xC0) != 0x80 ||
            (first == 0xE0 && second < 0xA0) || (first == 0xED && second >= 0xA0) ||
            (first == 0xF0 && second < 0x90) || (first == 0xF4 && second >= 0x90)) {
            return false;
        }
        for (std::size_t index = 2; index < count; ++index) {
            if ((cursor_[index] & 0xC0) != 0x80) {
                return false;
            }
        }
        for (std::size_t index = 0; index < count; ++index) {
            AppendByte(cursor_[index], output, capacity, parsed);
        }
        cursor_ += count;
        return true;
    }

    bool ParseString(char* output, std::size_t capacity, ParsedString* parsed) {
        if (!Consume('"')) {
            return false;
        }
        *parsed = {};
        while (cursor_ != end_) {
            const unsigned char value = *cursor_++;
            if (value == '"') {
                if (output != nullptr) {
                    if (parsed->length >= capacity) {
                        parsed->overflow = true;
                    } else {
                        output[parsed->length] = '\0';
                    }
                }
                return true;
            }
            if (value == '\\') {
                if (cursor_ == end_) {
                    return false;
                }
                const unsigned char escape = *cursor_++;
                unsigned char decoded = 0;
                switch (escape) {
                    case '"': decoded = '"'; break;
                    case '\\': decoded = '\\'; break;
                    case '/': decoded = '/'; break;
                    case 'b': decoded = '\b'; break;
                    case 'f': decoded = '\f'; break;
                    case 'n': decoded = '\n'; break;
                    case 'r': decoded = '\r'; break;
                    case 't': decoded = '\t'; break;
                    case 'u': {
                        std::uint32_t codePoint = 0;
                        if (!ParseEscapedCodePoint(&codePoint) || !AppendCodePoint(codePoint, output, capacity, parsed)) {
                            return false;
                        }
                        continue;
                    }
                    default: return false;
                }
                AppendByte(decoded, output, capacity, parsed);
                continue;
            }
            if (value < 0x20) {
                return false;
            }
            if (value < 0x80) {
                AppendByte(value, output, capacity, parsed);
                continue;
            }
            --cursor_;
            if (!AppendRawUtf8(output, capacity, parsed)) {
                return false;
            }
        }
        return false;
    }

    bool ParseNumber(bool* exactlyOne = nullptr) {
        const unsigned char* start = cursor_;
        if (cursor_ != end_ && *cursor_ == '-') {
            ++cursor_;
        }
        if (cursor_ == end_) {
            return false;
        }
        if (*cursor_ == '0') {
            ++cursor_;
            if (cursor_ != end_ && IsDigit(static_cast<char>(*cursor_))) {
                return false;
            }
        } else if (*cursor_ >= '1' && *cursor_ <= '9') {
            do {
                ++cursor_;
            } while (cursor_ != end_ && IsDigit(static_cast<char>(*cursor_)));
        } else {
            return false;
        }
        if (cursor_ != end_ && *cursor_ == '.') {
            ++cursor_;
            if (cursor_ == end_ || !IsDigit(static_cast<char>(*cursor_))) {
                return false;
            }
            while (cursor_ != end_ && IsDigit(static_cast<char>(*cursor_))) {
                ++cursor_;
            }
        }
        if (cursor_ != end_ && (*cursor_ == 'e' || *cursor_ == 'E')) {
            ++cursor_;
            if (cursor_ != end_ && (*cursor_ == '+' || *cursor_ == '-')) {
                ++cursor_;
            }
            if (cursor_ == end_ || !IsDigit(static_cast<char>(*cursor_))) {
                return false;
            }
            while (cursor_ != end_ && IsDigit(static_cast<char>(*cursor_))) {
                ++cursor_;
            }
        }
        if (exactlyOne != nullptr) {
            *exactlyOne = cursor_ - start == 1 && *start == '1';
        }
        return true;
    }

    bool ParseValue(std::size_t depth) {
        if (depth > kMaximumNesting) {
            return false;
        }
        SkipWhitespace();
        if (cursor_ == end_) {
            return false;
        }
        if (*cursor_ == '{') {
            return ParseObject(depth);
        }
        if (*cursor_ == '[') {
            return ParseArray(depth);
        }
        if (*cursor_ == '"') {
            ParsedString ignored{};
            return ParseString(nullptr, 0, &ignored);
        }
        if (*cursor_ == 't') {
            return ConsumeLiteral("true", 4);
        }
        if (*cursor_ == 'f') {
            return ConsumeLiteral("false", 5);
        }
        if (*cursor_ == 'n') {
            return ConsumeLiteral("null", 4);
        }
        return ParseNumber();
    }

    bool ParseObject(std::size_t depth) {
        if (!Consume('{')) {
            return false;
        }
        SkipWhitespace();
        if (Consume('}')) {
            return true;
        }
        while (true) {
            ParsedString key{};
            if (!ParseString(nullptr, 0, &key)) {
                return false;
            }
            SkipWhitespace();
            if (!Consume(':') || !ParseValue(depth + 1)) {
                return false;
            }
            SkipWhitespace();
            if (Consume('}')) {
                return true;
            }
            if (!Consume(',')) {
                return false;
            }
            SkipWhitespace();
        }
    }

    bool ParseArray(std::size_t depth) {
        if (!Consume('[')) {
            return false;
        }
        SkipWhitespace();
        if (Consume(']')) {
            return true;
        }
        while (true) {
            if (!ParseValue(depth + 1)) {
                return false;
            }
            SkipWhitespace();
            if (Consume(']')) {
                return true;
            }
            if (!Consume(',')) {
                return false;
            }
            SkipWhitespace();
        }
    }

    static bool Equals(const char* value, const ParsedString& parsed, const char* expected) {
        const std::size_t expectedLength = std::strlen(expected);
        return !parsed.overflow && parsed.length == expectedLength &&
               std::memcmp(value, expected, expectedLength) == 0;
    }

    bool ParseRoot(std::size_t depth) {
        if (depth > kMaximumNesting || !Consume('{')) {
            failure_ = ModManifest::ParseResult::InvalidJson;
            return false;
        }
        bool hasSchema = false;
        bool hasMods = false;
        SkipWhitespace();
        if (Consume('}')) {
            failure_ = ModManifest::ParseResult::InvalidSchema;
            return false;
        }
        while (true) {
            std::array<char, 32> keyBuffer{};
            ParsedString key{};
            if (!ParseString(keyBuffer.data(), keyBuffer.size(), &key)) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
            if (!Consume(':')) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
            if (Equals(keyBuffer.data(), key, "schema_version")) {
                if (hasSchema) {
                    failure_ = ModManifest::ParseResult::InvalidSchema;
                    return false;
                }
                bool exactlyOne = false;
                if (!ParseNumber(&exactlyOne) || !exactlyOne) {
                    failure_ = ModManifest::ParseResult::InvalidSchema;
                    return false;
                }
                hasSchema = true;
            } else if (Equals(keyBuffer.data(), key, "mods")) {
                if (hasMods || !ParseMods(depth + 1)) {
                    if (failure_ == ModManifest::ParseResult::Success) {
                        failure_ = ModManifest::ParseResult::InvalidMod;
                    }
                    return false;
                }
                hasMods = true;
            } else if (!ParseValue(depth + 1)) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
            if (Consume('}')) {
                break;
            }
            if (!Consume(',')) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
        }
        if (!hasSchema || !hasMods) {
            failure_ = ModManifest::ParseResult::InvalidSchema;
            return false;
        }
        return true;
    }

    bool ParseMods(std::size_t depth) {
        if (depth > kMaximumNesting || !Consume('[')) {
            failure_ = ModManifest::ParseResult::InvalidMod;
            return false;
        }
        SkipWhitespace();
        if (Consume(']')) {
            return true;
        }
        while (true) {
            if (!ParseMod(depth + 1)) {
                return false;
            }
            SkipWhitespace();
            if (Consume(']')) {
                return true;
            }
            if (!Consume(',')) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
        }
    }

    static bool IsSafeDirectory(const char* directory, std::size_t directoryLength) {
        if (directoryLength == 0 ||
            (directoryLength == 1 && directory[0] == '.') ||
            (directoryLength == 2 && directory[0] == '.' && directory[1] == '.')) {
            return false;
        }
        for (std::size_t index = 0; index < directoryLength; ++index) {
            if (directory[index] == '/' || directory[index] == '\\' || directory[index] == '\0') {
                return false;
            }
        }
        return true;
    }

    static bool IsSafeEntry(const char* directory, std::size_t directoryLength,
                            const char* entry, std::size_t entryLength) {
        constexpr char prefix[] = "mods/";
        constexpr char suffix[] = ".lua";
        const std::size_t requiredPrefixLength = sizeof(prefix) - 1 + directoryLength + 1;
        if (entryLength < requiredPrefixLength + sizeof(suffix) - 1 ||
            std::memcmp(entry, prefix, sizeof(prefix) - 1) != 0 ||
            std::memcmp(entry + sizeof(prefix) - 1, directory, directoryLength) != 0 ||
            entry[requiredPrefixLength - 1] != '/' ||
            std::memcmp(entry + entryLength - (sizeof(suffix) - 1), suffix, sizeof(suffix) - 1) != 0) {
            return false;
        }

        std::size_t segmentStart = 0;
        for (std::size_t index = 0; index <= entryLength; ++index) {
            if (index < entryLength && entry[index] != '/') {
                if (entry[index] == '\\' || entry[index] == '\0') {
                    return false;
                }
                continue;
            }
            const std::size_t segmentLength = index - segmentStart;
            if (segmentLength == 0 ||
                (segmentLength == 1 && entry[segmentStart] == '.') ||
                (segmentLength == 2 && entry[segmentStart] == '.' && entry[segmentStart + 1] == '.')) {
                return false;
            }
            segmentStart = index + 1;
        }
        return true;
    }

    bool ParseMod(std::size_t depth) {
        if (depth > kMaximumNesting || !Consume('{')) {
            failure_ = ModManifest::ParseResult::InvalidMod;
            return false;
        }
        ModManifest::SelectedMod candidate{};
        ParsedString directory{};
        ParsedString entry{};
        bool enabled = false;
        bool hasDirectory = false;
        bool hasEntry = false;
        bool hasEnabled = false;

        SkipWhitespace();
        if (Consume('}')) {
            failure_ = ModManifest::ParseResult::InvalidMod;
            return false;
        }
        while (true) {
            std::array<char, 32> keyBuffer{};
            ParsedString key{};
            if (!ParseString(keyBuffer.data(), keyBuffer.size(), &key)) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
            if (!Consume(':')) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
            if (Equals(keyBuffer.data(), key, "directory")) {
                if (hasDirectory || !ParseString(candidate.directory.data(), candidate.directory.size(), &directory)) {
                    failure_ = ModManifest::ParseResult::InvalidMod;
                    return false;
                }
                hasDirectory = true;
            } else if (Equals(keyBuffer.data(), key, "entry")) {
                if (hasEntry || !ParseString(candidate.entry.data(), candidate.entry.size(), &entry)) {
                    failure_ = ModManifest::ParseResult::InvalidMod;
                    return false;
                }
                hasEntry = true;
            } else if (Equals(keyBuffer.data(), key, "enabled")) {
                if (hasEnabled) {
                    failure_ = ModManifest::ParseResult::InvalidMod;
                    return false;
                }
                if (ConsumeLiteral("true", 4)) {
                    enabled = true;
                } else if (ConsumeLiteral("false", 5)) {
                    enabled = false;
                } else {
                    failure_ = ModManifest::ParseResult::InvalidMod;
                    return false;
                }
                hasEnabled = true;
            } else if (!ParseValue(depth + 1)) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
            if (Consume('}')) {
                break;
            }
            if (!Consume(',')) {
                failure_ = ModManifest::ParseResult::InvalidJson;
                return false;
            }
            SkipWhitespace();
        }

        // `entry` is optional: a PC Mod that ships resources only has no
        // `main.lua`, so the manifest omits the key entirely. The selected Mod is
        // then reported with an empty entry string, which the load path reads as
        // "mount the content, do not initialize Lua". An `entry` that *is*
        // present must still look exactly like `mods/<directory>/<name>.lua`.
        if (!hasDirectory || !hasEnabled || directory.overflow || directory.containsNul ||
            entry.overflow || entry.containsNul ||
            !IsSafeDirectory(candidate.directory.data(), directory.length) ||
            (hasEntry &&
             !IsSafeEntry(candidate.directory.data(), directory.length, candidate.entry.data(),
                          entry.length))) {
            failure_ = ModManifest::ParseResult::InvalidMod;
            return false;
        }
        if (enabled) {
            // 每个启用的 Mod 都要留下（多模组加载）。超上限**不截断**：那会让某个 Mod
            // 安静地不加载，症状是"某个 Mod 没生效"却查不出原因。
            if (selectedCount_ >= ModManifest::kMaximumSelectedMods) {
                failure_ = ModManifest::ParseResult::TooManyMods;
                return false;
            }
            selectedMods_.mods[selectedCount_] = candidate;
            ++selectedCount_;
        }
        return true;
    }

    const unsigned char* cursor_;
    const unsigned char* end_;
    ModManifest::ParseResult failure_{ModManifest::ParseResult::Success};
    ModManifest::SelectedMods selectedMods_{};
    std::size_t selectedCount_{};
};

} // namespace

namespace ModManifest {

ParseResult SelectFirstEnabled(const char* json, std::size_t length, SelectedMod* output) {
    if (json == nullptr || length == 0 || output == nullptr) {
        return ParseResult::InvalidArgument;
    }
    SelectedMod selected{};
    Parser parser(json, length);
    const ParseResult result = parser.Parse(&selected);
    if (result == ParseResult::Success) {
        *output = selected;
    }
    return result;
}

ParseResult SelectAllEnabled(const char* json, std::size_t length, SelectedMods* output) {
    if (json == nullptr || length == 0 || output == nullptr) {
        return ParseResult::InvalidArgument;
    }
    SelectedMods selected{};
    Parser parser(json, length);
    const ParseResult result = parser.Parse(&selected);
    if (result == ParseResult::Success) {
        *output = selected;
    }
    return result;
}

} // namespace ModManifest

#endif
