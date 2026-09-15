#!/usr/bin/env python3
"""Generate the verified minimal main.npdm overlay for the target game build."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path


TARGET_NPDM_SHA256 = "d325e6e0fdec64053c0efd7e2bf1cd679215b10772c410a91247e1d41bae4c5e"
SOURCE_NPDM_SIZE = 0x624
SYSCALL_DESCRIPTOR_TYPE = 4
SYSCALL_GROUP_SIZE = 0x18
REQUIRED_SYSCALLS = frozenset({0x74, 0x75})
GROUP4_DESCRIPTOR = 0x8600000F

META_ACI0_OFFSET = 0x70
META_ACI0_SIZE = 0x74
META_ACID_OFFSET = 0x78
META_ACID_SIZE = 0x7C
ACID_SIGNED_HEADER_SIZE = 0x200
KERNEL_CAPABILITY_OFFSET = 0x30
KERNEL_CAPABILITY_SIZE = 0x34


@dataclass(frozen=True)
class SyscallDescriptor:
    offset: int
    raw: int
    group: int
    base: int
    mask: int


@dataclass(frozen=True)
class KernelCapabilityTable:
    section_range: range
    kernel_capability_range: range
    syscall_descriptors: tuple[SyscallDescriptor, ...]


@dataclass(frozen=True)
class Npdm:
    aci0: KernelCapabilityTable
    acid: KernelCapabilityTable


@dataclass(frozen=True)
class PatchReport:
    source_sha256: str
    output_sha256: str
    replaced_offsets: frozenset[int]


def _read_u32(data: bytes, offset: int, field: str) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"malformed NPDM: {field} is out of range")
    return int.from_bytes(data[offset:offset + 4], "little")


def _range(start: int, size: int, limit: int, field: str) -> range:
    if start < 0 or size < 0 or start > limit or size > limit - start:
        raise ValueError(f"malformed NPDM: {field} is out of range")
    return range(start, start + size)


def _trailing_ones(value: int) -> int:
    count = 0
    while count < 32 and value & (1 << count):
        count += 1
    return count


def _parse_kernel_capabilities(data: bytes, section: range, header_offset: int) -> KernelCapabilityTable:
    kernel_offset = _read_u32(data, header_offset + KERNEL_CAPABILITY_OFFSET, "KAC offset")
    kernel_size = _read_u32(data, header_offset + KERNEL_CAPABILITY_SIZE, "KAC size")
    if kernel_size == 0 or kernel_size % 4:
        raise ValueError("malformed NPDM: invalid KAC size")
    kernel_range = _range(section.start + kernel_offset, kernel_size, section.stop, "KAC")

    descriptors = []
    groups = set()
    for offset in range(kernel_range.start, kernel_range.stop, 4):
        raw = _read_u32(data, offset, "kernel capability descriptor")
        if _trailing_ones(raw) != SYSCALL_DESCRIPTOR_TYPE:
            continue
        # Four trailing one bits implies the required zero separator at bit 4.
        group = raw >> 29
        if group in groups:
            raise ValueError("malformed NPDM: duplicate syscall group")
        groups.add(group)
        mask = (raw >> 5) & 0xFFFFFF
        descriptors.append(
            SyscallDescriptor(
                offset=offset,
                raw=raw,
                group=group,
                base=group * SYSCALL_GROUP_SIZE,
                mask=mask,
            )
        )
    return KernelCapabilityTable(section, kernel_range, tuple(descriptors))


def parse_npdm(data: bytes) -> Npdm:
    """Parse the two NPDM kernel-capability tables needed by the overlay."""
    if len(data) < 0x80 or data[:4] != b"META":
        raise ValueError("malformed NPDM: META header is missing")

    aci0_range = _range(
        _read_u32(data, META_ACI0_OFFSET, "ACI0 offset"),
        _read_u32(data, META_ACI0_SIZE, "ACI0 size"),
        len(data),
        "ACI0 section",
    )
    acid_range = _range(
        _read_u32(data, META_ACID_OFFSET, "ACID offset"),
        _read_u32(data, META_ACID_SIZE, "ACID size"),
        len(data),
        "ACID section",
    )
    if aci0_range.start < acid_range.stop or data[aci0_range.start:aci0_range.start + 4] != b"ACI0":
        raise ValueError("malformed NPDM: invalid ACI0 section")

    acid_header = acid_range.start + ACID_SIGNED_HEADER_SIZE
    if acid_header + 4 > acid_range.stop or data[acid_header:acid_header + 4] != b"ACID":
        raise ValueError("malformed NPDM: invalid ACID section")

    aci0 = _parse_kernel_capabilities(data, aci0_range, aci0_range.start)
    acid = _parse_kernel_capabilities(data, acid_range, acid_header)
    return Npdm(aci0=aci0, acid=acid)


def _enabled_syscalls(table: KernelCapabilityTable) -> set[int]:
    return {
        descriptor.base + bit
        for descriptor in table.syscall_descriptors
        for bit in range(24)
        if descriptor.mask & (1 << bit)
    }


def _validate_source(parsed: Npdm) -> None:
    aci0_descriptors = tuple((item.group, item.mask) for item in parsed.aci0.syscall_descriptors)
    acid_descriptors = tuple((item.group, item.mask) for item in parsed.acid.syscall_descriptors)
    if aci0_descriptors != acid_descriptors:
        raise ValueError("malformed NPDM: ACI0 and ACID syscall descriptors differ")
    if any(item.group == 4 for item in parsed.aci0.syscall_descriptors):
        raise ValueError("NPDM already permits group 4 syscalls")
    if REQUIRED_SYSCALLS & (_enabled_syscalls(parsed.aci0) | _enabled_syscalls(parsed.acid)):
        raise ValueError("NPDM already permits required syscalls")


def _write_u32(data: bytearray, offset: int, value: int) -> None:
    data[offset:offset + 4] = value.to_bytes(4, "little")


def _increment_u32(data: bytearray, offset: int) -> None:
    _write_u32(data, offset, _read_u32(data, offset, "size field") + 4)


def _expected_output(source: bytes, parsed: Npdm) -> tuple[bytes, frozenset[int]]:
    if len(source) != SOURCE_NPDM_SIZE:
        raise ValueError(f"unexpected NPDM length: expected {SOURCE_NPDM_SIZE:#x}")
    if parsed.acid.kernel_capability_range.stop + 4 > parsed.aci0.section_range.start:
        raise ValueError("malformed NPDM: no ACID KAC expansion room")
    if source[parsed.acid.kernel_capability_range.stop:parsed.acid.kernel_capability_range.stop + 4] != bytes(4):
        raise ValueError("malformed NPDM: ACID KAC tail is not padding")
    if parsed.aci0.kernel_capability_range.stop != len(source):
        raise ValueError("malformed NPDM: ACI0 KAC is not at EOF")

    output = bytearray(source)
    acid_header = parsed.acid.section_range.start + ACID_SIGNED_HEADER_SIZE
    _increment_u32(output, META_ACI0_SIZE)
    _increment_u32(output, META_ACID_SIZE)
    _increment_u32(output, acid_header + 4)
    _increment_u32(output, acid_header + KERNEL_CAPABILITY_SIZE)
    _increment_u32(output, parsed.aci0.section_range.start + KERNEL_CAPABILITY_SIZE)
    _write_u32(output, parsed.acid.kernel_capability_range.stop, GROUP4_DESCRIPTOR)
    output.extend(GROUP4_DESCRIPTOR.to_bytes(4, "little"))

    replaced = frozenset({
        META_ACI0_SIZE,
        META_ACID_SIZE,
        acid_header + 4,
        acid_header + KERNEL_CAPABILITY_SIZE,
        parsed.acid.kernel_capability_range.stop,
        parsed.acid.kernel_capability_range.stop + 1,
        parsed.acid.kernel_capability_range.stop + 2,
        parsed.acid.kernel_capability_range.stop + 3,
        parsed.aci0.section_range.start + KERNEL_CAPABILITY_SIZE,
    })
    return bytes(output), replaced


def _validate_output(source: bytes, output: bytes, replaced: frozenset[int]) -> None:
    if len(output) != len(source) + 4 or output[len(source):] != GROUP4_DESCRIPTOR.to_bytes(4, "little"):
        raise ValueError("NPDM patch did not append the group 4 descriptor")
    changed = {
        offset
        for offset, (before, after) in enumerate(zip(source, output[:len(source)]))
        if before != after
    }
    if not changed <= replaced:
        raise ValueError("NPDM patch changed an unexpected source byte")
    parsed_source = parse_npdm(source)
    parsed_output = parse_npdm(output)
    expected_syscalls = _enabled_syscalls(parsed_source.aci0) | _enabled_syscalls(parsed_source.acid) | REQUIRED_SYSCALLS
    actual_syscalls = _enabled_syscalls(parsed_output.aci0) | _enabled_syscalls(parsed_output.acid)
    if actual_syscalls != expected_syscalls:
        raise ValueError("NPDM patch granted unexpected syscalls")
    for table in (parsed_output.aci0, parsed_output.acid):
        group4 = [item for item in table.syscall_descriptors if item.group == 4]
        if len(group4) != 1 or group4[0].raw != GROUP4_DESCRIPTOR:
            raise ValueError("NPDM patch did not add exactly one group 4 descriptor per table")


def patch_npdm(source: Path, destination: Path) -> PatchReport:
    """Validate *source*, create the minimal overlay at *destination*, and report it."""
    source_path = source.resolve()
    destination_path = destination.resolve()
    if source_path == destination_path:
        raise ValueError("source and destination are the same file")
    if destination.exists() and source_path.samefile(destination):
        raise ValueError("source and destination are the same file")

    source_data = source.read_bytes()
    source_sha256 = hashlib.sha256(source_data).hexdigest()
    if source_sha256 != TARGET_NPDM_SHA256:
        raise ValueError("source NPDM SHA-256 does not match the supported game build")
    parsed = parse_npdm(source_data)
    _validate_source(parsed)
    output, replaced = _expected_output(source_data, parsed)
    _validate_output(source_data, output, replaced)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(output)
    return PatchReport(source_sha256, hashlib.sha256(output).hexdigest(), replaced)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, dest="source")
    parser.add_argument("--output", required=True, type=Path, dest="destination")
    args = parser.parse_args()
    report = patch_npdm(args.source, args.destination)
    print(f"source_sha256={report.source_sha256}")
    print(f"output_sha256={report.output_sha256}")
    print("replaced_offsets=" + ",".join(f"{offset:#x}" for offset in sorted(report.replaced_offsets)))


if __name__ == "__main__":
    main()
