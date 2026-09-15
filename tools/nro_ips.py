"""NRO 模块 ID 与经典 IPS 补丁格式工具。"""

MODULE_ID_OFFSET = 0x40
MODULE_ID_SIZE = 0x20
IPS_HEADER = b"PATCH"
IPS_EOF = b"EOF"


def read_module_id(nro: bytes) -> bytes:
    """读取 NRO 头部中用于 Atmosphere 补丁匹配的 32 字节模块 ID。"""
    if len(nro) < MODULE_ID_OFFSET + MODULE_ID_SIZE:
        raise ValueError("NRO 文件不足 0x60 字节，无法读取模块 ID")
    return nro[MODULE_ID_OFFSET : MODULE_ID_OFFSET + MODULE_ID_SIZE]


def encode_ips(records: list[tuple[int, bytes]]) -> bytes:
    """将非 RLE 记录编码为经典 IPS。"""
    payload = bytearray(IPS_HEADER)
    for offset, data in records:
        if not 0 <= offset <= 0xFFFFFF:
            raise ValueError("IPS 偏移超出 24 位范围")
        if not 0 < len(data) <= 0xFFFF:
            raise ValueError("IPS 记录长度必须在 1 到 65535 字节之间")
        payload.extend(offset.to_bytes(3, "big"))
        payload.extend(len(data).to_bytes(2, "big"))
        payload.extend(data)
    payload.extend(IPS_EOF)
    return bytes(payload)


def decode_ips(blob: bytes) -> list[tuple[int, bytes]]:
    """解码经典 IPS，拒绝本项目不使用的 RLE 记录。"""
    if not blob.startswith(IPS_HEADER):
        raise ValueError("IPS 缺少 PATCH 文件头")

    cursor = len(IPS_HEADER)
    records: list[tuple[int, bytes]] = []
    while True:
        if blob[cursor : cursor + len(IPS_EOF)] == IPS_EOF:
            if cursor + len(IPS_EOF) != len(blob):
                raise ValueError("IPS EOF 后存在额外数据")
            return records
        if cursor + 5 > len(blob):
            raise ValueError("IPS 记录被截断")

        offset = int.from_bytes(blob[cursor : cursor + 3], "big")
        size = int.from_bytes(blob[cursor + 3 : cursor + 5], "big")
        cursor += 5
        if size == 0:
            raise ValueError("本项目不接受 IPS RLE 记录")
        if cursor + size > len(blob):
            raise ValueError("IPS 数据被截断")

        records.append((offset, blob[cursor : cursor + size]))
        cursor += size


def apply_records(image: bytes, records: list[tuple[int, bytes]]) -> bytes:
    """在副本上模拟应用 IPS 记录，不写入输入文件。"""
    patched = bytearray(image)
    for offset, data in records:
        if offset < 0 or offset + len(data) > len(patched):
            raise ValueError("IPS 记录越过目标 NRO 文件末尾")
        patched[offset : offset + len(data)] = data
    return bytes(patched)
