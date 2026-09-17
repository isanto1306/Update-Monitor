#!/usr/bin/env python3
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
REPORT = ROOT / "repair-v0346-report.txt"

TOKENS = [
    "pendingPostScans",
    "verifyingApps",
    "verificationBaselines",
    "checkingApps",
    "checkBaselines",
    "restoreVerificationApps",
    "installUpdate",
    "uninstallButton",
    "data-update-index",
    "data-uninstall-index",
    "policyVersionSelect",
    "fixedNotice",
    "effective_status",
    "can_version_update",
    "can_image_update",
    "headerWarningList",
    "headerWarningButton",
    "errorStatus",
    "error_count",
    "item.detail",
    "actionProgress",
]


def main():
    lines = INDEX.read_text(encoding="utf-8", errors="replace").splitlines()
    out = ["UPDATE MONITOR v0.3.346 UI TOKEN LOCATIONS\n"]
    for token in TOKENS:
        hits = [i for i, line in enumerate(lines, 1) if token in line and i >= 18000]
        out.append(f"{token}: {hits}\n")
    REPORT.write_text("".join(out), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
