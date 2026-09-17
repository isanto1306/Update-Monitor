#!/usr/bin/env python3
import ast
import re
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


def first_context(source: str, label: str, pattern: str, context=16, start_at=18000):
    lines = source.splitlines()
    rx = re.compile(pattern, re.I)
    blocks = []
    hits = 0
    for idx, line in enumerate(lines, 1):
        if idx < start_at or not rx.search(line):
            continue
        hits += 1
        lo, hi = max(1, idx-context), min(len(lines), idx+context)
        blocks.append(f"\n--- {label} hit {hits} @ {idx} ---\n" + "\n".join(
            f"{n:06d}: {lines[n-1]}" for n in range(lo, hi+1)
        ))
        if hits >= 6:
            break
    return "\n".join(blocks) if blocks else f"\n--- {label}: NO MATCH ---\n"


def main():
    backend = MAIN.read_text(encoding="utf-8")
    ui = INDEX.read_text(encoding="utf-8", errors="replace")
    parts = ["UPDATE MONITOR v0.3.346 FOCUSED REPAIR DIAGNOSTICS\n"]

    for name in [
        "build_apps",
        "apply_monitor_policy_fields",
        "_verify_restore_runtime",
        "app_update",
        "app_policy",
        "casaos_compose_project_exists",
    ]:
        parts.append("\n" + "="*70 + f"\n{name}\n" + "="*70 + "\n")
        parts.append(function_source(backend, name))

    patterns = [
        ("POST_SCAN", r"pendingPostScans|verifyingApps|verificationBaselines|checkingApps|verificationPhase|restoreVerificationApps"),
        ("UPDATE_BUTTON", r"installUpdate|data-update-index|update-install-button"),
        ("UNINSTALL_BUTTON", r"uninstallButton|data-uninstall-index|uninstall-button"),
        ("POLICY_STATE", r"policyVersionSelect|fixedNotice|effective_status|can_version_update|monitor_policy"),
        ("ERROR_DETAILS", r"headerWarningList|warningTitle|errorStatus|error_count|item\.detail|Docker information|Docker Informationen"),
    ]
    for label, pattern in patterns:
        parts.append("\n" + "="*70 + f"\nUI {label}\n" + "="*70 + "\n")
        parts.append(first_context(ui, label, pattern))

    REPORT.write_text("".join(parts), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
