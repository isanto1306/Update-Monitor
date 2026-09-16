from pathlib import Path
import re
import subprocess

index_path = Path('static/index.html')
main_path = Path('app/main.py')
index = index_path.read_text(encoding='utf-8')
main = main_path.read_text(encoding='utf-8')

old_filter = "\n".join([
    "      const runtime=String(container&&container.state||'').trim().toLowerCase();",
    "      // Stopped/exited containers only describe configured overlaps; they are",
    "      // not active host-port conflicts. Paused/restarting containers still own",
    "      // their Docker network bindings and remain active for this check.",
    "      if(!['running','paused','restarting'].includes(runtime))return;",
    "      const rawBindings=Array.isArray(container.port_bindings)&&container.port_bindings.length",
]) + "\n"
new_filter = "\n".join([
    "      const runtime=String(container&&container.state||'').trim().toLowerCase();",
    "      // Include configured host-port bindings for running and stopped containers.",
    "      // A stopped container can still conflict with another app when both are started.",
    "      const rawBindings=Array.isArray(container.port_bindings)&&container.port_bindings.length",
]) + "\n"
if index.count(old_filter) != 1:
    raise SystemExit(f'port runtime filter anchor count: {index.count(old_filter)}')
index = index.replace(old_filter, new_filter, 1)

old_collect = "\n".join([
    "        if(!entry.apps.has(appKey))entry.apps.set(appKey,{app_key:appKey,app_name:String(candidate.app&&candidate.app.name||appKey||'-'),containers:new Set()});",
    "        entry.apps.get(appKey).containers.add(String(candidate.container&&candidate.container.name||''));",
]) + "\n"
new_collect = "\n".join([
    "        if(!entry.apps.has(appKey))entry.apps.set(appKey,{app_key:appKey,app_name:String(candidate.app&&candidate.app.name||appKey||'-'),containers:new Map()});",
    "        const containerName=String(candidate.container&&candidate.container.name||'-');",
    "        const containerState=String(candidate.container&&candidate.container.state||'unknown').trim().toLowerCase()||'unknown';",
    "        entry.apps.get(appKey).containers.set(containerName,containerState);",
]) + "\n"
if index.count(old_collect) != 1:
    raise SystemExit(f'port collect anchor count: {index.count(old_collect)}')
index = index.replace(old_collect, new_collect, 1)

old_output = "      containers:Array.from(value.containers).filter(Boolean).sort((a,b)=>a.localeCompare(b))\n"
new_output = "      containers:Array.from(value.containers.entries()).map(([name,state])=>({name,state})).sort((a,b)=>a.name.localeCompare(b.name))\n"
if index.count(old_output) != 1:
    raise SystemExit(f'port output anchor count: {index.count(old_output)}')
index = index.replace(old_output, new_output, 1)

banner_anchor = "function renderPortConflictBanner(){\n"
helper = "\n".join([
    "function portConflictRuntimeStateWord(value){",
    "  const raw=String(value||'').trim().toLowerCase();",
    "  if(raw==='running')return t('runtimeWordRunning');",
    "  if(['exited','stopped','dead','created'].includes(raw))return t('runtimeWordStopped');",
    "  return localizeRuntimeStateWord(raw);",
    "}",
    "",
    "function renderPortConflictBanner(){",
]) + "\n"
if index.count(banner_anchor) != 1:
    raise SystemExit(f'port helper anchor count: {index.count(banner_anchor)}')
index = index.replace(banner_anchor, helper, 1)

old_render = "      ${conflict.apps.map(app=>`<div class=\"port-conflict-app\"><strong>${esc(app.app_name)}</strong><span>${esc(app.containers.join(', '))}</span></div>`).join('')}\n"
new_render = "      ${conflict.apps.map(app=>`<div class=\"port-conflict-app\"><strong>${esc(app.app_name)}</strong><span>${app.containers.map(container=>esc(container.name)+' · '+esc(portConflictRuntimeStateWord(container.state))).join('<br>')}</span></div>`).join('')}\n"
if index.count(old_render) != 1:
    raise SystemExit(f'port render anchor count: {index.count(old_render)}')
index = index.replace(old_render, new_render, 1)

version_hits = index.count('0.3.342')
if version_hits < 2:
    raise SystemExit(f'frontend version hits too low: {version_hits}')
index = index.replace('0.3.342', '0.3.343')
if '0.3.342' in index:
    raise SystemExit('stale frontend 0.3.342 remains')

old_main = 'VERSION = "0.3.342"'
if main.count(old_main) != 1:
    raise SystemExit(f'backend version anchor count: {main.count(old_main)}')
main = main.replace(old_main, 'VERSION = "0.3.343"', 1)

index_path.write_text(index, encoding='utf-8')
main_path.write_text(main, encoding='utf-8')

required = [
    "A stopped container can still conflict with another app when both are started.",
    "containers:new Map()",
    "containers:Array.from(value.containers.entries())",
    "function portConflictRuntimeStateWord(value)",
    "['exited','stopped','dead','created'].includes(raw)",
    "portConflictRuntimeStateWord(container.state)",
    "const UPDATE_MONITOR_VERSION = 'v0.3.343';",
]
for needle in required:
    if needle not in index:
        raise SystemExit(f'Missing regression marker: {needle}')
forbidden = "if(!['running','paused','restarting'].includes(runtime))return;"
if forbidden in index:
    raise SystemExit('Stopped container filter is still present')
if 'VERSION = "0.3.343"' not in main:
    raise SystemExit('Backend version is not 0.3.343')

blocks = re.findall(r'<script(?:\\s[^>]*)?>(.*?)</script>', index, flags=re.I | re.S)
if not blocks:
    raise SystemExit('No inline scripts found')
for i, block in enumerate(blocks):
    path = Path(f'/tmp/update-monitor-inline-{i}.js')
    path.write_text(block, encoding='utf-8')
    subprocess.run(['node', '--check', str(path)], check=True)

print('v0.3.343 port conflict patch and syntax checks passed')
