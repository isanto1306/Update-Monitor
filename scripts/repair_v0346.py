#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
REPORT = ROOT / "repair-v0346-report.txt"

RANGES = [
    ("APP_NORMALIZATION", 19290, 19470),
    ("POLICY_DIALOG", 19920, 20095),
    ("CARD_RENDER", 20830, 21035),
    ("RESTORE_UI", 21795, 21870),
    ("ACTION_BUTTONS", 22340, 22440),
    ("DOCKER_DETAILS", 22530, 22630),
    ("CHECK_VERIFY_START", 23430, 23595),
    ("POLL_STATE", 23715, 23870),
    ("VERIFY_CLEANUP", 24115, 24355),
    ("WARNINGS", 24635, 24795),
    ("CLICK_HANDLERS", 25490, 25590),
]


def main():
    lines = INDEX.read_text(encoding="utf-8", errors="replace").splitlines()
    out = ["UPDATE MONITOR v0.3.346 UI REPAIR HANDLERS\n"]
    for label, start, end in RANGES:
        out.append("\n" + "=" * 72 + f"\n{label} {start}-{end}\n" + "=" * 72 + "\n")
        for i in range(start, min(end, len(lines)) + 1):
            out.append(f"{i:06d}: {lines[i-1]}\n")
    REPORT.write_text("".join(out), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
