#!/usr/bin/env python3
"""Reject release HTML if any inline JavaScript has invalid syntax."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

html = Path("static/index.html").read_text(encoding="utf-8")
scripts = list(re.finditer(r"<script\b([^>]*)>([\s\S]*?)</script\s*>", html, re.I))
if not scripts:
    raise SystemExit("No HTML script blocks found: unexpected release input")

count = 0
with tempfile.TemporaryDirectory(prefix="um-js-check-") as tmpdir:
    for i, match in enumerate(scripts, start=1):
        attributes, source = match.groups()
        if re.search(r"\bsrc\s*=", attributes, re.I):
            continue
        if not source.strip():
            continue
        js_path = Path(tmpdir) / f"index-inline-{i}.js"
        js_path.write_text(source, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
        if result.returncode:
            start_line = html.count("\n", 0, match.start()) + 1
            sys.stderr.write(f"Invalid JavaScript in HTML inline script {i} near line {start_line}:\n")
            sys.stderr.write(result.stderr)
            raise SystemExit(result.returncode)
        count += 1

print(f"Verified syntax of {count} inline JavaScript blocks.")
if count < 3:
    raise SystemExit(f"Unexpectedly few inline JavaScript blocks: {count}")
