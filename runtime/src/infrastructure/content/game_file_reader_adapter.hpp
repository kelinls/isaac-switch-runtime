#pragma once

#include "ports/content_port.hpp"

#include "game_file_reader.hpp"

#include <cstddef>
#include <cstdint>

namespace isaac::runtime {

// Bridges IContentPort onto the verified game file bindings. Paths are copied
// into a bounded buffer because the game API takes a C string, and an
// over-long path is rejected instead of truncated.
class GameFileReaderAdapter final : public IContentPort {
public:
    static constexpr std::size_t kMaximumPathLength = 192;

    explicit GameFileReaderAdapter(const GameFileReader::Bindings& bindings) noexcept
        : bindings_(bindings) {}

    [[nodiscard]] Status Size(std::string_view path, std::size_t* size) noexcept override;
    [[nodiscard]] Status Read(std::string_view path, std::uint8_t* target,
                              std::size_t capacity, std::size_t* readCount) noexcept override;

private:
    GameFileReader::Bindings bindings_{};
};

} // namespace isaac::runtime
