import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "static" / "index.html"
MAIN = ROOT / "app" / "main.py"
AUDIT = ROOT / "translation-audit-current.json"
LANGUAGES = ["de", "en", "fr", "pt", "es"]


def fail(message):
    raise SystemExit("TRANSLATION AUDIT FAILED: " + str(message))


def placeholders(value):
    return sorted(re.findall(r"\{([A-Za-z0-9_]+)\}", str(value or "")))


main = MAIN.read_text(encoding="utf-8")
version_match = re.search(r'^VERSION = "([^"]+)"', main, re.MULTILINE)
if not version_match:
    fail("backend VERSION marker not found")
version = version_match.group(1)

html = INDEX.read_text(encoding="utf-8")
start = html.find("const translations = ")
end = html.find("\nfunction getUiLocale()", start)
if start < 0 or end < 0:
    fail("translation setup markers not found")

setup = html[start:end]
if "translations.fr=" not in setup or "translations.pt=" not in setup or "translations.es=" not in setup:
    fail("French, Portuguese or Spanish translation dictionary is missing")

node_source = r"""
const fs=require('fs');
const vm=require('vm');
const src=fs.readFileSync(process.argv[2],'utf8');
const a=src.indexOf('const translations = ');
const b=src.indexOf('\nfunction getUiLocale()',a);
if(a<0||b<0)throw new Error('translation setup markers not found');
const setup=src.slice(a,b).replace('const translations = ','globalThis.translations = ');
const ctx={};
vm.createContext(ctx);
vm.runInContext(setup,ctx);
process.stdout.write(JSON.stringify(ctx.translations));
"""

with tempfile.TemporaryDirectory() as td:
    script = Path(td) / "extract-translations.js"
    script.write_text(node_source, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script), str(INDEX)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        fail("could not evaluate translation dictionaries: " + result.stderr[:2000])
    try:
        translations = json.loads(result.stdout)
    except Exception as exc:
        fail("could not parse evaluated translation dictionaries: " + str(exc))

if not isinstance(translations, dict):
    fail("evaluated translations are not an object")

reference = translations.get("en") or {}
if not isinstance(reference, dict) or not reference:
    fail("English translation dictionary is missing or empty")
reference_keys = sorted(reference)

errors = []
counts = {}
for lang in LANGUAGES:
    dictionary = translations.get(lang)
    if not isinstance(dictionary, dict):
        errors.append(f"{lang}: dictionary missing")
        continue
    keys = sorted(dictionary)
    counts[lang] = len(keys)
    missing = sorted(set(reference_keys) - set(keys))
    extra = sorted(set(keys) - set(reference_keys))
    if missing:
        errors.append(f"{lang}: missing keys: {', '.join(missing)}")
    if extra:
        errors.append(f"{lang}: extra keys: {', '.join(extra)}")
    for key in reference_keys:
        value = dictionary.get(key)
        if not str(value if value is not None else "").strip():
            errors.append(f"{lang}: empty value for {key}")
            continue
        expected_placeholders = placeholders(reference.get(key))
        actual_placeholders = placeholders(value)
        if actual_placeholders != expected_placeholders:
            errors.append(
                f"{lang}: placeholder mismatch for {key}: "
                f"{actual_placeholders} != {expected_placeholders}"
            )

if errors:
    fail("\n".join(errors[:100]))

report = {
    "version": "v" + version,
    "languages": LANGUAGES,
    "translation_keys": len(reference_keys),
    "dictionary_parity": "ok",
    "placeholder_parity": "ok",
    "translations": {lang: translations[lang] for lang in LANGUAGES},
}
AUDIT.write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps({
    "version": report["version"],
    "languages": report["languages"],
    "translation_keys": report["translation_keys"],
    "counts": counts,
    "dictionary_parity": "ok",
    "placeholder_parity": "ok",
}, ensure_ascii=False))
