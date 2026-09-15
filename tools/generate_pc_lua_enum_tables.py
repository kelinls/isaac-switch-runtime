#!/usr/bin/env python3
"""Generate compact PC Lua enum tables from the fixed PC enum contract."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


# Generation order is also the order the runtime registers the globals in;
# ModCallbacks stays last because game mods read it after the value tables.
TABLES = (
    "CollectibleType",
    "ItemPoolType",
    "RoomType",
    "LevelStage",
    "PickupVariant",
    "ItemType",
    "PlayerForm",
    "PlayerType",
    "PillColor",
    "PillEffect",
    "EntityType",
    "UseFlag",
    "TrinketType",
    "Challenge",
    "LevelCurse",
    "GridEntityType",
    "EffectVariant",
    "EntityPartition",
    "Card",
    "ModCallbacks",
)
# One `KEY = <expression>[,]` line of a table body. The expression is captured
# verbatim (comments are stripped before matching) and evaluated by
# `_evaluate_expression`; nothing is truncated.
ENTRY = re.compile(r"^\s*(?P<key>[A-Z0-9_]+)\s*=\s*(?P<expression>.+?)\s*,?\s*$")
# Same shape for the qualified assignments outside the table bodies
# (`PlayerType.PLAYER_XXX = 4`, see `enums.lua:4784`). The expression must end at
# a comma or the end of the line so that a long expression can never be read as
# its leading literal.
# Longest name first so a table name that is a prefix of another one (for
# example `Card` inside `CardX`) can never win the alternation.
QUALIFIED_ENTRY = re.compile(
    r"(?m)^(?P<table>" + "|".join(sorted(TABLES, key=len, reverse=True)) + r")"
    r"\.(?P<key>[A-Z0-9_]+)\s*=\s*(?P<expression>[^,\n]+?)\s*(?:,|$)"
)
# A bare identifier on the right-hand side is an alias of an earlier key in the
# same table (`FLAG_LASER_POP = 1<<19` style aliases are numeric, this is the
# `KEY = OTHER_KEY` form).
ALIAS = re.compile(r"^[A-Z][A-Z0-9_]*$")

# The only expression forms `enums.lua` uses for enum values that this generator
# accepts. It is deliberately **not** a general Lua expression evaluator: no
# names (other than a whole-value alias), no calls, no other operators, no
# floats. Anything outside this grammar raises, so an unrecognised future form
# fails the generation loudly instead of being truncated to its first literal
# (which is exactly how `1<<3` used to become `1`).
_TOKEN = re.compile(r"\s*(0[xX][0-9a-fA-F]+|\d+|<<|>>|[()+\-|&~])")
# `int32_t` is what the generated tables store, so a value outside it is a bug.
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


class _ExpressionError(ValueError):
    """A value expression the constrained evaluator does not accept."""


class _Expression:
    """Token stream of one enum value expression."""

    def __init__(self, text: str) -> None:
        self._text = text.strip()
        self._tokens: list[str] = []
        cursor = 0
        while cursor < len(self._text):
            match = _TOKEN.match(self._text, cursor)
            if match is None:
                raise _ExpressionError(f"unsupported token at {self._text[cursor:]!r}")
            self._tokens.append(match.group(1))
            cursor = match.end()
        self._index = 0

    def peek(self) -> str | None:
        return self._tokens[self._index] if self._index < len(self._tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise _ExpressionError("unexpected end of expression")
        self._index += 1
        return token

    def done(self) -> bool:
        return self._index == len(self._tokens)


def _parse_or(expression: _Expression) -> int:
    value = _parse_and(expression)
    while expression.peek() == "|":
        expression.take()
        value |= _parse_and(expression)
    return value


def _parse_and(expression: _Expression) -> int:
    value = _parse_shift(expression)
    while expression.peek() == "&":
        expression.take()
        value &= _parse_shift(expression)
    return value


def _parse_shift(expression: _Expression) -> int:
    value = _parse_additive(expression)
    while expression.peek() in ("<<", ">>"):
        operator = expression.take()
        amount = _parse_additive(expression)
        if amount < 0:
            raise _ExpressionError("negative shift count")
        value = value << amount if operator == "<<" else value >> amount
    return value


def _parse_additive(expression: _Expression) -> int:
    value = _parse_unary(expression)
    while expression.peek() in ("+", "-"):
        operator = expression.take()
        operand = _parse_unary(expression)
        value = value + operand if operator == "+" else value - operand
    return value


def _parse_unary(expression: _Expression) -> int:
    token = expression.peek()
    if token in ("-", "+", "~"):
        expression.take()
        operand = _parse_unary(expression)
        if token == "-":
            return -operand
        return ~operand if token == "~" else operand
    return _parse_primary(expression)


def _parse_primary(expression: _Expression) -> int:
    token = expression.take()
    if token == "(":
        value = _parse_or(expression)
        if expression.take() != ")":
            raise _ExpressionError("missing closing parenthesis")
        return value
    if token[0].isdigit():
        return int(token, 0)
    raise _ExpressionError(f"unexpected token {token!r}")


def _evaluate_expression(expression: str, table: str, key: str) -> int:
    """Value of one `enums.lua` right-hand side, restricted to integer arithmetic."""
    stream = _Expression(expression)
    try:
        value = _parse_or(stream)
        if not stream.done():
            raise _ExpressionError("trailing tokens")
    except _ExpressionError as error:
        raise ValueError(
            f"unsupported enum value expression: {table}.{key}={expression.strip()!r} ({error})"
        ) from error
    if not _INT32_MIN <= value <= _INT32_MAX:
        raise ValueError(f"enum value out of int32 range: {table}.{key}={value}")
    return value


def _table_body(source: str, table: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(table)}\s*=\s*\{{", source)
    if match is None:
        raise ValueError(f"missing enum table: {table}")
    start = source.index("{", match.start())
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise ValueError(f"unterminated enum table: {table}")


def _parse_table(source: str, table: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in _table_body(source, table).splitlines():
        # Strip the Lua comment first: `enums.lua` puts them after the comma and
        # they routinely contain text (`D4 only: Reroll ...`) that would
        # otherwise reach the value parser.
        code = line.split("--", 1)[0].strip()
        if not code:
            continue
        match = ENTRY.match(code)
        if match is None:
            raise ValueError(f"unparsed enum entry: {table}: {code!r}")
        key = match.group("key")
        if key in values:
            raise ValueError(f"duplicate enum key: {table}.{key}")
        expression = match.group("expression").strip()
        if ALIAS.match(expression):
            if expression not in values:
                raise ValueError(f"unresolved enum alias: {table}.{key}={expression}")
            values[key] = values[expression]
            continue
        values[key] = _evaluate_expression(expression, table, key)
    if not values:
        raise ValueError(f"enum table parsed to no entries: {table}")
    return values


def _parse_values(source: str) -> dict[str, dict[str, int]]:
    values = {table: _parse_table(source, table) for table in TABLES}
    for match in QUALIFIED_ENTRY.finditer(source):
        table = match.group("table")
        key = match.group("key")
        expression = match.group("expression").strip()
        if ALIAS.match(expression):
            if expression not in values[table]:
                raise ValueError(f"unresolved enum alias: {table}.{key}={expression}")
            value = values[table][expression]
        else:
            value = _evaluate_expression(expression, table, key)
        existing = values[table].get(key)
        if existing is not None and existing != value:
            raise ValueError(f"duplicate enum key: {table}.{key}")
        values[table][key] = value
    return values


def generate(enum_source: Path, header: Path, implementation: Path) -> None:
    enum_bytes = enum_source.read_bytes()
    values = _parse_values(enum_bytes.decode("utf-8"))
    digest = hashlib.sha256(enum_bytes).hexdigest()

    header_lines = [
        "// Generated by tools/generate_pc_lua_enum_tables.py; do not edit manually.",
        f"// enums.lua SHA-256: {digest}",
        "#pragma once",
        "",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        "namespace PcLuaEnumData {",
        "struct Value { const char* key; std::int32_t value; };",
        "struct Table { const char* name; const Value* values; std::size_t count; };",
        "const Table* Tables(std::size_t* count);",
        "} // namespace PcLuaEnumData",
        "",
    ]
    source_lines = [
        "// Generated by tools/generate_pc_lua_enum_tables.py; do not edit manually.",
        f"// enums.lua SHA-256: {digest}",
        "#include \"pc_lua_enum_data.hpp\"",
        "",
        "namespace PcLuaEnumData {",
    ]
    for table in TABLES:
        source_lines.append(f"namespace {{ constexpr Value k{table}Values[] = {{")
        for key, value in sorted(values[table].items()):
            source_lines.append(f'    {{"{key}", {value}}},')
        source_lines.extend(["};", "}"])
    source_lines.append("namespace {")
    source_lines.append("constexpr Table kTables[] = {")
    for table in TABLES:
        source_lines.append(
            f'    {{"{table}", k{table}Values, sizeof(k{table}Values) / sizeof(k{table}Values[0])}},'
        )
    source_lines.extend([
        "};",
        "}",
        "",
        # `lua_runtime.cpp` 直接 include 本 .cpp（枚举数据的唯一使用者），而 Makefile 又会把
        # 本文件当独立 TU 编译一次——两个定义必须允许合一，所以这里保持 `inline`（vague
        # linkage）。2026-09-12 重写生成器时漏掉 `inline`，链接期立刻报
        # `multiple definition of PcLuaEnumData::Tables`。
        "inline const Table* Tables(std::size_t* count) {",
        "    if (count != nullptr) *count = sizeof(kTables) / sizeof(kTables[0]);",
        "    return kTables;",
        "}",
        "} // namespace PcLuaEnumData",
        "",
    ])
    header.parent.mkdir(parents=True, exist_ok=True)
    implementation.parent.mkdir(parents=True, exist_ok=True)
    header.write_text("\n".join(header_lines), encoding="utf-8")
    implementation.write_text("\n".join(source_lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enum-source", type=Path, required=True)
    parser.add_argument("--header", type=Path, required=True)
    parser.add_argument("--implementation", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        generate(arguments.enum_source, arguments.header, arguments.implementation)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(f"generate_pc_lua_enum_tables: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
