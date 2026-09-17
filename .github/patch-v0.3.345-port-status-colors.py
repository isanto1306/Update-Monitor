from pathlib import Path
import re
import subprocess

index_path = Path('static/index.html')
main_path = Path('app/main.py')
compose_path = Path('docker-compose.zimaos.yml')
readme_path = Path('README.md')

index = index_path.read_text(encoding='utf-8')
main = main_path.read_text(encoding='utf-8')
compose = compose_path.read_text(encoding='utf-8')
readme = readme_path.read_text(encoding='utf-8')

old_helper = """function portConflictRuntimeStateWord(value){
  const raw=String(value||'').trim().toLowerCase();
  if(raw==='running')return t('runtimeWordRunning');
  if(['exited','stopped','dead','created'].includes(raw))return t('runtimeWordStopped');
  return localizeRuntimeStateWord(raw);
}
"""
new_helper = """function portConflictRuntimeStateWord(value){
  const raw=String(value||'').trim().toLowerCase();
  if(raw==='running')return t('runtimeWordRunning');
  if(['exited','stopped','dead','created'].includes(raw))return t('runtimeWordStopped');
  return localizeRuntimeStateWord(raw);
}

function portConflictRuntimeStateClass(value){
  const raw=String(value||'').trim().toLowerCase();
  if(raw==='running')return 'is-running';
  if(['exited','stopped','dead','created'].includes(raw))return 'is-stopped';
  return 'is-other';
}
"""
if index.count(old_helper) != 1:
    raise SystemExit(f'port status helper anchor count: {index.count(old_helper)}')
index = index.replace(old_helper, new_helper, 1)

old_render = "      ${conflict.apps.map(app=>`<div class=\"port-conflict-app\"><strong>${esc(app.app_name)}</strong><span>${app.containers.map(container=>esc(container.name)+' · '+esc(portConflictRuntimeStateWord(container.state))).join('<br>')}</span></div>`).join('')}\n"
new_render = "      ${conflict.apps.map(app=>`<div class=\"port-conflict-app\"><strong>${esc(app.app_name)}</strong><span>${app.containers.map(container=>esc(container.name)+' · <span class=\"port-conflict-runtime '+portConflictRuntimeStateClass(container.state)+'\">'+esc(portConflictRuntimeStateWord(container.state))+'</span>').join('<br>')}</span></div>`).join('')}\n"
if index.count(old_render) != 1:
    raise SystemExit(f'port render anchor count: {index.count(old_render)}')
index = index.replace(old_render, new_render, 1)

script_anchor = "<script>\nconst UPDATE_MONITOR_VERSION = 'v0.3.344';"
style_block = """<style id=\"um-port-conflict-runtime-colors-v0345\">
/* Port conflict state colors only. Existing conflict layout and geometry stay unchanged. */
.port-conflict-runtime.is-running {
  color: var(--green);
}
.port-conflict-runtime.is-stopped {
  color: var(--red);
}
</style>

<script>
const UPDATE_MONITOR_VERSION = 'v0.3.344';"""
if index.count(script_anchor) != 1:
    raise SystemExit(f'main script anchor count: {index.count(script_anchor)}')
index = index.replace(script_anchor, style_block, 1)

version_hits = index.count('0.3.344')
if version_hits < 2:
    raise SystemExit(f'frontend version hits too low: {version_hits}')
index = index.replace('0.3.344', '0.3.345')
if '0.3.344' in index:
    raise SystemExit('stale frontend 0.3.344 remains')

old_main = 'VERSION = "0.3.344"'
if main.count(old_main) != 1:
    raise SystemExit(f'backend version anchor count: {main.count(old_main)}')
main = main.replace(old_main, 'VERSION = "0.3.345"', 1)

old_compose = '  version: "0.3.344"'
if compose.count(old_compose) != 1:
    raise SystemExit(f'zima compose version anchor count: {compose.count(old_compose)}')
compose = compose.replace(old_compose, '  version: "0.3.345"', 1)

old_readme = 'Current release: **v0.3.344**'
if readme.count(old_readme) != 1:
    raise SystemExit(f'README version anchor count: {readme.count(old_readme)}')
readme = readme.replace(old_readme, 'Current release: **v0.3.345**', 1)

index_path.write_text(index, encoding='utf-8')
main_path.write_text(main, encoding='utf-8')
compose_path.write_text(compose, encoding='utf-8')
readme_path.write_text(readme, encoding='utf-8')

required = [
    "function portConflictRuntimeStateClass(value)",
    "return 'is-running'",
    "return 'is-stopped'",
    'class=\"port-conflict-runtime ",
    '.port-conflict-runtime.is-running',
    'color: var(--green);',
    '.port-conflict-runtime.is-stopped',
    'color: var(--red);',
    "const UPDATE_MONITOR_VERSION = 'v0.3.345';",
]
for needle in required:
    if needle not in index:
        raise SystemExit(f'Missing regression marker: {needle}')
if 'VERSION = "0.3.345"' not in main:
    raise SystemExit('Backend version is not 0.3.345')
if '  version: "0.3.345"' not in compose:
    raise SystemExit('ZimaOS compose version is not 0.3.345')

blocks = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', index, flags=re.I | re.S)
if not blocks:
    raise SystemExit('No inline scripts found')
for i, block in enumerate(blocks):
    path = Path(f'/tmp/update-monitor-inline-{i}.js')
    path.write_text(block, encoding='utf-8')
    subprocess.run(['node', '--check', str(path)], check=True)

print('v0.3.345 port status color patch and syntax checks passed')
