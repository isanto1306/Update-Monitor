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
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if not candidates:
        return f"[MISSING FUNCTION {name}]\n"
    node = candidates[0]
    lines = source.splitlines()
    start = max(1, node.lineno - 3)
    end = min(len(lines), (node.end_lineno or node.lineno) + 3)
    numbered = [f"{idx:05d}: {lines[idx-1]}" for idx in range(start, end + 1)]
    return "\n".join(numbered) + "\n"


def keyword_context(source: str, patterns, context=3, max_hits=60):
    lines = source.splitlines()
    compiled = [(label, re.compile(pattern, re.I)) for label, pattern in patterns]
    hits = []
    seen = set()
    for idx, line in enumerate(lines, start=1):
        for label, rx in compiled:
            if rx.search(line):
                key = (idx, label)
                if key in seen:
                    continue
                seen.add(key)
                lo = max(1, idx-context)
                hi = min(len(lines), idx+context)
                block = [f"[{label}] line {idx}"] + [
                    f"{n:06d}: {lines[n-1]}" for n in range(lo, hi+1)
                ]
                hits.append("\n".join(block))
                if len(hits) >= max_hits:
                    return hits
    return hits


def main():
    main_source = MAIN.read_text(encoding="utf-8")
    index_source = INDEX.read_text(encoding="utf-8", errors="replace")

    parts = []
    parts.append("UPDATE MONITOR v0.3.346 REPAIR DIAGNOSTICS\n")
    version = re.search(r'^VERSION\s*=\s*"([^"]+)"', main_source, re.M)
    parts.append(f"Current backend version: {version.group(1) if version else 'UNKNOWN'}\n")
    parts.append(f"main.py bytes: {len(main_source.encode('utf-8'))}\n")
    parts.append(f"index.html bytes: {len(index_source.encode('utf-8'))}\n")

    names = [
        "schedule_app_scan",
        "_scan_thread_entry",
        "_start_scan_with_operation_lock",
        "build_apps",
        "apply_monitor_policy_fields",
        "refresh_scan_policy_fields",
        "_verify_restore_runtime",
        "restore_app_backup",
        "app_update",
        "app_policy",
        "casaos_compose_project_exists",
        "container_stack_metadata",
    ]
    for name in names:
        parts.append("\n" + "="*90 + f"\nFUNCTION {name}\n" + "="*90 + "\n")
        parts.append(function_source(main_source, name))

    ui_patterns = [
        ("INSTALL_LITERAL", r"\bINSTALL\b"),
        ("UNINSTALL", r"Uninstall|Deinstall"),
        ("CHECK_ERROR", r"Check error|Prüffehler|check_error|checkError"),
        ("SELECT_VERSION", r"Select version|Version auswählen|selectVersion"),
        ("NEW_VERSION", r"New version|Neue Version|newVersion"),
        ("NOTICES", r"Notices|Hinweise|notice"),
        ("PENDING_SCAN", r"pending_app|pendingApp|post.scan|postScan"),
        ("VERIFY_TEXT", r"being checked|wird überprüft|verifying|verification"),
        ("ERROR_DETAIL", r"error_details|errorDetails|\.detail\b|detail:"),
    ]
    parts.append("\n" + "="*90 + "\nUI KEYWORD CONTEXT\n" + "="*90 + "\n")
    hits = keyword_context(index_source, ui_patterns, context=4, max_hits=140)
    parts.extend(hit + "\n\n" for hit in hits)
    parts.append(f"UI hits written: {len(hits)}\n")

    REPORT.write_text("".join(parts), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
