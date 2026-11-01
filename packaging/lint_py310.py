#!/usr/bin/env python3
"""Catch the one syntax difference that makes CI red on Python 3.10/3.11 only.

Before PEP 701 (Python 3.12) an f-string expression part may not contain a
backslash, so this line is legal on 3.12+ and a SyntaxError on 3.10:

    vtxt = f"link from {re.sub(r'^https?://(www\\.)?', '', via)}"

`ast.parse(..., feature_version=(3, 10))` does not catch it (the restriction is
tokenizer-level, not grammar-level), so this walker looks for a backslash inside
a replacement field of any formatted string literal.

Usage:  python3 packaging/lint_py310.py [repo-root]
Exit:   0 clean, 1 a file uses a 3.12-only f-string
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _fields(node: ast.JoinedStr) -> str:
    """Source-ish text of every {...} field, enough to spot a backslash."""
    out = []
    for value in node.values:
        if isinstance(value, ast.FormattedValue):
            try:
                out.append(ast.unparse(value.value))
            except Exception:  # pragma: no cover
                out.append("")
    return " ".join(out)


def check(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: cannot parse: {exc.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and "\\" in _fields(node):
            col = getattr(node, "col_offset", 0)
            problems.append(
                f"{path}:{node.lineno}:{col}: backslash inside an f-string field "
                f"(Python < 3.12 rejects this) — hoist the expression into a variable"
            )
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts and "dist-repo" not in p.parts
    )
    problems: list[str] = []
    for path in files:
        problems.extend(check(path))
    if problems:
        print("\n".join(problems))
        print(f"FAIL — {len(problems)} issue(s); CI runs this on Python 3.10")
        return 1
    print(f"ok — {len(files)} files are Python 3.10-compatible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
