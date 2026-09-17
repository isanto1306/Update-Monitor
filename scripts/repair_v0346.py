#!/usr/bin/env python3
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
REPORT = ROOT / "repair-v0346-report.txt"


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = max(1, node.lineno - 3)
            end = min(len(lines), (node.end_lineno or node.lineno) + 3)
            return "\n".join(f"{i:06d}: {lines[i-1]}" for i in range(start, end + 1)) + "\n"
    return f"[MISSING FUNCTION {name}]\n"


def main():
    backend = MAIN.read_text(encoding="utf-8")
    parts = ["UPDATE MONITOR v0.3.346 COMPOSE REPAIR HELPERS\n"]
    for name in [
        "casaos_compose_project_exists",
        "casaos_compose_yaml",
        "casaos_apply_compose",
        "casaos_recreate_container",
        "wait_for_version_group_compose_update",
        "find_scanned_app",
        "status_payload",
        "status",
        "automation_status",
        "scan_all",
    ]:
        parts.append("\n" + "="*78 + f"\n{name}\n" + "="*78 + "\n")
        parts.append(function_source(backend, name))
    REPORT.write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
