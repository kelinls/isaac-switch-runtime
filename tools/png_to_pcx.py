"""PNG → PCX（Switch 版贴图格式）转换，供打包阶段使用。

**为什么在打包阶段做**：Switch 版引擎的 `IsaacRepentance::Manager::LoadImage` 会把路径里的
`.png` 改写成 `.pcx` 再交给 `KAGE::Graphics::ImageManager::LoadImage`，而后者**按扩展名**挑
解码器。真机两轮验证表明：只在运行时把请求改回 `.png` 不够（`Load` 会返回成功，但画面空白）；
**磁盘上是真实存在的 `.pcx` 时，自带 `.anm2` 才能正常显示**（第九轮起一直成立）。
所以这里把 Mod 自带的 PNG **额外**写一份同名 `.pcx`（原 PNG 保留：字体等按 `.png` 名引用它）。

PCX 形态与本体资源一致：8bpp、4 planes（RGBA）、逐行逐平面 RLE、128 字节头、无尾部调色板
（用本体 `*.pcx` 逐字段核对过；本文件产出的字节与已验证的实现逐字节一致）。
"""

from __future__ import annotations

import struct
import zlib

__all__ = ["png_to_pcx", "PngFormatError"]


class PngFormatError(ValueError):
    """PNG 形态不受支持（调色板 / 16 位 / 隔行等）。"""


def _read_chunks(data: bytes):
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngFormatError("not a PNG file")
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        if offset + 12 + length > len(data):
            raise PngFormatError("truncated chunk")
        yield chunk_type, data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IEND":
            break


def _paeth(left: int, up: int, up_left: int) -> int:
    p = left + up - up_left
    pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
    if pa <= pb and pa <= pc:
        return left
    return up if pb <= pc else up_left


def _decode(data: bytes) -> tuple[int, int, bytes]:
    width = height = 0
    bit_depth = color_type = interlace = None
    idat = bytearray()
    for chunk_type, body in _read_chunks(data):
        if chunk_type == b"IHDR":
            if len(body) < 13:
                raise PngFormatError("short IHDR")
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack_from(
                ">IIBBBBB", body
            )
        elif chunk_type == b"IDAT":
            idat += body
    if bit_depth != 8 or interlace != 0:
        raise PngFormatError("only 8-bit non-interlaced PNG is supported")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise PngFormatError(f"unsupported PNG color type {color_type}")
    if not idat:
        raise PngFormatError("no IDAT data")

    row_bytes = width * channels
    raw = zlib.decompress(bytes(idat))
    if len(raw) != (row_bytes + 1) * height:
        raise PngFormatError("unexpected decompressed size")

    previous = bytearray(row_bytes)
    rgba = bytearray(width * height * 4)
    for y in range(height):
        filter_type = raw[y * (row_bytes + 1)]
        line = bytearray(raw[y * (row_bytes + 1) + 1 : (y + 1) * (row_bytes + 1)])
        if filter_type == 1:
            for index in range(channels, row_bytes):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(row_bytes):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(row_bytes):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(row_bytes):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                up_left = previous[index - channels] if index >= channels else 0
                line[index] = (line[index] + _paeth(left, up, up_left)) & 0xFF
        elif filter_type != 0:
            raise PngFormatError(f"unknown PNG filter {filter_type}")
        base = y * width * 4
        for x in range(width):
            at = x * channels
            if color_type == 0:
                r = g = b = line[at]
                a = 255
            elif color_type == 2:
                r, g, b = line[at], line[at + 1], line[at + 2]
                a = 255
            elif color_type == 4:
                r = g = b = line[at]
                a = line[at + 1]
            else:
                r, g, b, a = line[at], line[at + 1], line[at + 2], line[at + 3]
            rgba[base + x * 4 : base + x * 4 + 4] = bytes((r, g, b, a))
        previous = line
    return width, height, bytes(rgba)


def _encode_pcx(width: int, height: int, rgba: bytes) -> bytes:
    header = bytearray(128)
    header[0] = 10          # magic
    header[1] = 5           # version
    header[2] = 1           # RLE
    header[3] = 8           # bits per pixel
    struct.pack_into("<HHHH", header, 4, 0, 0, width - 1, height - 1)
    struct.pack_into("<HH", header, 12, width, height)
    header[64] = 1          # 与本体的保留字节一致
    header[65] = 4          # 4 planes = RGBA
    struct.pack_into("<H", header, 66, width)
    struct.pack_into("<H", header, 68, 1)

    body = bytearray()
    for y in range(height):
        row_base = y * width * 4
        for plane in range(4):
            x = 0
            while x < width:
                value = rgba[row_base + x * 4 + plane]
                run = 1
                while (
                    x + run < width
                    and run < 63
                    and rgba[row_base + (x + run) * 4 + plane] == value
                ):
                    run += 1
                if run > 1 or value >= 0xC0:
                    body += bytes((0xC0 | run, value))
                else:
                    body.append(value)
                x += run
    return bytes(header) + bytes(body)


def png_to_pcx(data: bytes) -> bytes:
    """把 PNG 字节转换成 PCX 字节；形态不支持时抛 `PngFormatError`。"""
    width, height, rgba = _decode(data)
    if width == 0 or height == 0 or width > 0xFFFF or height > 0xFFFF:
        raise PngFormatError("unsupported image size")
    return _encode_pcx(width, height, rgba)
