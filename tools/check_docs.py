"""Documentation checker for Task C.

Verifies that every Python function/method has a docstring with a purpose,
an ``Args:`` section and a ``Returns:`` section, and that every named
JavaScript function in ``static/app.js`` has a JSDoc block with ``@returns``.

Usage:
    python tools/check_docs.py

Exit code 0 means everything is documented; 1 lists what is missing.
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def check_python(paths):
    """Find Python functions whose docstring lacks a purpose, Args or Returns.

    Args:
        paths (list[pathlib.Path]): Python source files to inspect.

    Returns:
        tuple[int, list[str]]: Number of functions checked, and a
        ``file:line name`` entry for each one that is incomplete.
    """
    total, bad = 0, []
    for p in paths:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                d = ast.get_docstring(node) or ""
                if not (d.strip() and "Args:" in d and "Returns:" in d):
                    bad.append(f"{p.relative_to(ROOT)}:{node.lineno} {node.name}")
    return total, bad


def check_js(path):
    """Find named JavaScript functions that are not preceded by a JSDoc block.

    Args:
        path (pathlib.Path): JavaScript file to inspect.

    Returns:
        tuple[int, list[str]]: Number of functions checked, and a
        ``file:line`` entry for each one without a complete JSDoc block.
    """
    pat = re.compile(r"^\s*(?:async\s+)?function\s+[\w$]+|^\s*const\s+[\w$]+\s*=\s*(?:async\s*)?\(")
    lines = path.read_text(encoding="utf-8").split("\n")
    total, bad = 0, []
    for i, line in enumerate(lines):
        if pat.match(line):
            total += 1
            j = i - 1
            block = []
            while j >= 0 and lines[j].strip().startswith(("*", "/**")):
                block.append(lines[j]); j -= 1
            if not (block and any("@returns" in b for b in block)):
                bad.append(f"{path.relative_to(ROOT)}:{i + 1} {line.strip()[:40]}")
    return total, bad


def main():
    """Run both checks and print a report.

    Args:
        None.

    Returns:
        int: Process exit code (0 = fully documented, 1 = something missing).
    """
    py_files = sorted((ROOT / "smartbrew").glob("*.py")) + sorted((ROOT / "tests").glob("*.py")) + \
        sorted((ROOT / "tools").glob("*.py"))
    n_py, bad_py = check_python(py_files)
    n_js, bad_js = check_js(ROOT / "static" / "app.js")
    print(f"Python functions documented: {n_py - len(bad_py)}/{n_py}")
    print(f"JavaScript functions documented: {n_js - len(bad_js)}/{n_js}")
    for b in bad_py + bad_js:
        print("  missing:", b)
    return 1 if bad_py or bad_js else 0


if __name__ == "__main__":
    sys.exit(main())
