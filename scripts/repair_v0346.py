#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
INDEX = ROOT / "static" / "index.html"
REPORT = ROOT / "repair-v0346-report.txt"


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = max(1, node.lineno - 2)
            end = min(len(lines), (node.end_lineno or node.lineno) + 2)
            return "\n".join(f"{i:06d}: {lines[i-1]}" for i in range(start, end + 1)) + "\n"
    return f"[MISSING FUNCTION {name}]\n"


def range_source(source: str, start: int, end: int) -> str:
    lines = source.splitlines()
    end = min(end, len(lines))
    return "\n".join(f"{i:06d}: {lines[i-1]}" for i in range(start, end + 1)) + "\n"


def main():
    backend = MAIN.read_text(encoding="utf-8")
    ui = INDEX.read_text(encoding="utf-8", errors="replace")
    parts = ["UPDATE MONITOR v0.3.346 REPAIR TARGETS\n"]

    for name in [
        "schedule_app_scan",
        "perform_image_update",
        "perform_version_update",
        "restore_app_backup",
        "_verify_restore_runtime",
        "collect_containers",
        "container_stack_metadata",
        "scan_all",
        "status",
        "automation_status",
    ]:
        parts.append("\n" + "="*78 + f"\n{name}\n" + "="*78 + "\n")
        parts.append(function_source(backend, name))

    for label, start, end in [
        ("UI_CARD", 20830, 21040),
        ("UI_VERIFY", 23420, 23590),
        ("UI_UPDATE", 23700, 23900),
        ("UI_POLICY", 23140, 23320),
        ("UI_WARNINGS", 24620, 24730),
        ("UI_DOCKER_DETAILS", 22440, 22680),
    ]:
        parts.append("\n" + "="*78 + f"\n{label}\n" + "="*78 + "\n")
        parts.append(range_source(ui, start, end))

    REPORT.write_text("".join(parts), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
