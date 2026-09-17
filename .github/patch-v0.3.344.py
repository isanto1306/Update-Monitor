from pathlib import Path
import re
import subprocess
import tempfile

INDEX = Path("static/index.html")
MAIN = Path("app/main.py")
ZIMA = Path("docker-compose.zimaos.yml")
README = Path("README.md")

index = INDEX.read_text(encoding="utf-8")
main = MAIN.read_text(encoding="utf-8")
zima = ZIMA.read_text(encoding="utf-8")
readme = README.read_text(encoding="utf-8")

old_transport = """function isUpdateTransportFailure(err){
  const message=String(err&&err.message?err.message:'').trim().toLowerCase();
  return (
    message==='failed to fetch'
    ||message.includes('networkerror')
    ||message.includes('network error')
    ||message.includes('network request failed')
    ||message.includes('load failed')
  );
}
"""
new_transport = """function isUpdateTransportFailure(err){
  const message=String(err&&err.message?err.message:'').trim().toLowerCase();
  // ZimaOS/reverse proxies can return a gateway error while the backend update
  // request continues and finishes successfully. Treat these responses like a
  // transport interruption and let the existing targeted verification decide
  // whether the update really succeeded before showing an error.
  const gatewayHttpFailure=/(?:^|\\s)http\\s+(502|503|504)(?:\\b|$)/.test(message);
  return (
    message==='failed to fetch'
    ||message.includes('networkerror')
    ||message.includes('network error')
    ||message.includes('network request failed')
    ||message.includes('load failed')
    ||gatewayHttpFailure
    ||message.includes('bad gateway')
    ||message.includes('gateway timeout')
    ||message.includes('service unavailable')
  );
}
"""

if index.count(old_transport) != 1:
    raise SystemExit(f"transport function anchor count: {index.count(old_transport)}")
index = index.replace(old_transport, new_transport, 1)

# Keep release metadata synchronized.
if index.count("0.3.343") < 2:
    raise SystemExit(f"frontend version hits too low: {index.count('0.3.343')}")
index = index.replace("0.3.343", "0.3.344")
if "0.3.343" in index:
    raise SystemExit("stale frontend version 0.3.343 remains")

old_main = 'VERSION = "0.3.343"'
if main.count(old_main) != 1:
    raise SystemExit(f"backend VERSION anchor count: {main.count(old_main)}")
main = main.replace(old_main, 'VERSION = "0.3.344"', 1)

old_zima = '  version: "0.3.343"'
if zima.count(old_zima) != 1:
    raise SystemExit(f"ZimaOS version anchor count: {zima.count(old_zima)}")
zima = zima.replace(old_zima, '  version: "0.3.344"', 1)

# README was still one release behind; bring it to the real current release.
readme = re.sub(r"Current release: \*\*v0\.3\.\d+\*\*", "Current release: **v0.3.344**", readme, count=1)
if "Current release: **v0.3.344**" not in readme:
    raise SystemExit("README release marker not updated")

INDEX.write_text(index, encoding="utf-8")
MAIN.write_text(main, encoding="utf-8")
ZIMA.write_text(zima, encoding="utf-8")
README.write_text(readme, encoding="utf-8")

# JavaScript syntax check for all inline scripts.
blocks = re.findall(r"<script(?:\\s[^>]*)?>(.*?)</script>", index, flags=re.I | re.S)
if not blocks:
    raise SystemExit("No inline scripts found")
for i, block in enumerate(blocks):
    path = Path(tempfile.gettempdir()) / f"update-monitor-v0344-inline-{i}.js"
    path.write_text(block, encoding="utf-8")
    subprocess.run(["node", "--check", str(path)], check=True)

# Functional classifier regression test in Node.
match = re.search(r"function isUpdateTransportFailure\(err\)\{.*?\n\}", index, flags=re.S)
if not match:
    raise SystemExit("Transport classifier function not found after patch")
classifier = match.group(0)
test_js = classifier + r'''
const cases = [
  ['HTTP 504', true],
  ['ZimaOS App Management HTTP 504', true],
  ['HTTP 502', true],
  ['HTTP 503', true],
  ['Gateway Timeout', true],
  ['Bad Gateway', true],
  ['Service Unavailable', true],
  ['Failed to fetch', true],
  ['HTTP 404', false],
  ['Invalid backup mode', false],
];
for (const [message, expected] of cases) {
  const actual = isUpdateTransportFailure(new Error(message));
  if (actual !== expected) {
    console.error(message, actual, expected);
    process.exit(1);
  }
}
'''
test_path = Path(tempfile.gettempdir()) / "update-monitor-v0344-transport-test.js"
test_path.write_text(test_js, encoding="utf-8")
subprocess.run(["node", str(test_path)], check=True)

required = [
    "deferredTransportError=String(err.message||'Failed to fetch')",
    "deferredTransportError&&appUpdateVerifiedCurrent(refreshedApp)",
    "const gatewayHttpFailure=/(?:^|\\s)http\\s+(502|503|504)(?:\\b|$)/.test(message);",
    "const UPDATE_MONITOR_VERSION = 'v0.3.344';",
]
for needle in required:
    if needle not in index:
        raise SystemExit(f"Missing regression marker: {needle}")

print("v0.3.344 transport timeout recovery patch validated")
