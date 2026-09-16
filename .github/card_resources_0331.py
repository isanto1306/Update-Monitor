from pathlib import Path
import re

main_path = Path("app/main.py")
html_path = Path("static/index.html")
readme_path = Path("README.md")

main = main_path.read_text(encoding="utf-8")
html = html_path.read_text(encoding="utf-8")
readme = readme_path.read_text(encoding="utf-8")

if 'VERSION = "0.3.328"' not in main:
    raise SystemExit("Unexpected backend VERSION")
main = main.replace('VERSION = "0.3.328"', 'VERSION = "0.3.331"', 1)

old = '''    snapshot = docker_runtime_snapshot()\n\n    with scan_lock:\n        apps_snapshot = list(scan_state.get("apps") or [])\n\n    result = []'''
new = '''    snapshot = docker_runtime_snapshot()\n\n    with scan_lock:\n        apps_snapshot = list(scan_state.get("apps") or [])\n\n    # One no-stream Docker stats call feeds CPU/RAM for every running app card.\n    resource_names = [\n        str(row.get("name") or "").strip()\n        for row in snapshot\n        if str(row.get("state") or "").strip().lower() == "running"\n        and str(row.get("name") or "").strip()\n    ]\n    resource_stats = _docker_stats_snapshot(resource_names)\n\n    result = []'''
if old not in main:
    raise SystemExit("live runtime anchor missing")
main = main.replace(old, new, 1)

old = '''        else:\n            display_count = current_count if current_count > 0 else cached_count\n\n        result.append({'''
new = '''        else:\n            display_count = current_count if current_count > 0 else cached_count\n\n        resource_cpu = 0.0\n        resource_memory = 0.0\n        resource_seen = False\n        resource_usage_parts = []\n        for row in rows:\n            container_name = str(row.get("name") or "").strip()\n            stat = resource_stats.get(container_name) or {}\n            if not stat:\n                continue\n            cpu_match = re.search(r"-?\\d+(?:\\.\\d+)?", str(stat.get("cpu_percent") or ""))\n            memory_match = re.search(r"-?\\d+(?:\\.\\d+)?", str(stat.get("memory_percent") or ""))\n            if cpu_match:\n                resource_cpu += float(cpu_match.group(0))\n                resource_seen = True\n            if memory_match:\n                resource_memory += float(memory_match.group(0))\n                resource_seen = True\n            memory_usage = str(stat.get("memory_usage") or "").strip()\n            if memory_usage:\n                resource_seen = True\n                resource_usage_parts.append(\n                    f"{container_name}: {memory_usage}" if len(rows) > 1 else memory_usage\n                )\n\n        result.append({'''
if old not in main:
    raise SystemExit("display anchor missing")
main = main.replace(old, new, 1)

old = '''            "container_count": display_count,\n            "self_protected": is_update_monitor_self_app(app_item),'''
new = '''            "container_count": display_count,\n            "cpu_percent": round(resource_cpu, 2) if resource_seen else None,\n            "memory_percent": round(resource_memory, 2) if resource_seen else None,\n            "memory_usage": " · ".join(resource_usage_parts) if resource_usage_parts else None,\n            "self_protected": is_update_monitor_self_app(app_item),'''
if old not in main:
    raise SystemExit("payload anchor missing")
main = main.replace(old, new, 1)
main = main.replace(
    '"""Read one no-stream stats snapshot only when the user opens Docker info."""',
    '"""Read one no-stream stats snapshot for the requested containers."""',
    1,
)

if "0.3.330" not in html:
    raise SystemExit("v0.3.330 frontend missing")
html = html.replace("0.3.330", "0.3.331")

pattern = re.compile(
    r'''function renderAppPortStat\(app\)\{.*?\n\}\n\nfunction appConfiguredTagLabel\(app\)\{''',
    re.S,
)
replacement = r'''function formatCardResourcePercent(value){
  const number=Number(value);
  if(!Number.isFinite(number))return '–';
  const digits=Math.abs(number)>=10?0:1;
  return number.toFixed(digits)+' %';
}
function renderAppResourceStat(app){
  const cpuText=formatCardResourcePercent(app.cpu_percent);
  const memoryText=formatCardResourcePercent(app.memory_percent);
  const memoryUsage=String(app.memory_usage||'').trim();
  const hover=[`CPU ${cpuText}`,`RAM ${memoryText}`,memoryUsage].filter(Boolean).join(' · ');
  return `<div class="update-card-stat update-card-stat-resource" data-resource-stack="${esc(String(app.stack_key||''))}"${hover?` title="${esc(hover)}"`:''}>
    <div class="update-card-stat-label">CPU / RAM</div>
    <div class="update-card-stat-value update-resource-stat-value"><span data-resource-cpu>${esc(cpuText)}</span><span class="update-resource-divider"> / </span><span data-resource-ram>${esc(memoryText)}</span></div>
  </div>`;
}
function syncAppResourceStat(app){
  const key=String(app.stack_key||'');
  const cpuText=formatCardResourcePercent(app.cpu_percent);
  const memoryText=formatCardResourcePercent(app.memory_percent);
  const memoryUsage=String(app.memory_usage||'').trim();
  const hover=[`CPU ${cpuText}`,`RAM ${memoryText}`,memoryUsage].filter(Boolean).join(' · ');
  document.querySelectorAll('[data-resource-stack]').forEach(node=>{
    if(String(node.dataset.resourceStack||'')!==key)return;
    const cpu=node.querySelector('[data-resource-cpu]');
    const ram=node.querySelector('[data-resource-ram]');
    if(cpu)cpu.textContent=cpuText;
    if(ram)ram.textContent=memoryText;
    if(hover)node.title=hover; else node.removeAttribute('title');
  });
}
function renderAppPortStat(app){
  const ports=appPublishedPorts(app);
  const network=ports.length?'':appNetworkModeLabel(app);
  const full=ports.length?ports.join(', '):network;
  let displayHtml=esc(network||'–');
  if(ports.length){
    displayHtml=ports.slice(0,2).map((value,index)=>{
      const suffix=(index===1&&ports.length>2)?' …':'';
      return `<span class="update-port-line">${esc(String(value)+suffix)}</span>`;
    }).join('');
  }
  return `<div class="update-card-stat update-card-stat-port"${full?` title="${esc(full)}"`:''}>
    <div class="update-card-stat-label">${esc(network?t('networkLabel'):t('port'))}</div>
    <div class="update-card-stat-value update-port-stat">${displayHtml}</div>
  </div>`;
}

function appConfiguredTagLabel(app){'''
html, count = pattern.subn(replacement, html, count=1)
if count != 1:
    raise SystemExit(f"port function replacement count {count}")

old = '''        <div class="update-card-stat">\n          <div class="update-card-stat-label">${esc(t('updateSingular'))}</div>\n          <div class="update-card-stat-value">${effectiveUpdateCount}</div>\n        </div>\n        ${renderAppPortStat(app)}'''
new = '''        ${renderAppResourceStat(app)}\n        ${renderAppPortStat(app)}'''
if old not in html:
    raise SystemExit("Update 0 block missing")
html = html.replace(old, new, 1)

old = '''      app.runtime_state=newState;\n      app.running_count=Number(live.running_count||0);\n      app.completed_count=Number(live.completed_count||0);\n      if(Number.isFinite(Number(live.active_container_count))){\n        app.active_container_count=Number(live.active_container_count);\n      }\n      app.self_protected=!!live.self_protected;'''
new = '''      app.runtime_state=newState;\n      app.running_count=Number(live.running_count||0);\n      app.completed_count=Number(live.completed_count||0);\n      if(Number.isFinite(Number(live.active_container_count))){\n        app.active_container_count=Number(live.active_container_count);\n      }\n      app.cpu_percent=(live.cpu_percent===null||live.cpu_percent===undefined)?null:Number(live.cpu_percent);\n      app.memory_percent=(live.memory_percent===null||live.memory_percent===undefined)?null:Number(live.memory_percent);\n      app.memory_usage=String(live.memory_usage||'');\n      app.self_protected=!!live.self_protected;\n      syncAppResourceStat(app);'''
if old not in html:
    raise SystemExit("runtime frontend block missing")
html = html.replace(old, new, 1)

style = '''
<style id="um-card-resource-port-v0331">
/* v0.3.331 — CPU/RAM replaces Update 0; fixed card width and header geometry stay unchanged. */
.update-card-summary > .update-card-stats .update-card-stat-resource{
  --status-band-icon:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22black%22%20stroke-width%3D%221.7%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Crect%20x%3D%227%22%20y%3D%227%22%20width%3D%2210%22%20height%3D%2210%22%20rx%3D%222%22%2F%3E%3Cpath%20d%3D%22M9%201v3m6-3v3M9%2020v3m6-3v3M20%209h3m-3%206h3M1%209h3m-3%206h3%22%2F%3E%3C%2Fsvg%3E") !important;
}
.update-card-stat-resource .update-card-stat-value{white-space:nowrap;}
.update-resource-divider{color:#6f8290;font-weight:600;}
.update-port-stat{display:flex !important;flex-direction:column;gap:1px;max-width:100%;white-space:normal !important;line-height:1.05;}
.update-port-line{display:block;min-width:0;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
</style>
'''
if 'id="um-card-resource-port-v0331"' in html:
    raise SystemExit("style already present")
if "</head>" not in html:
    raise SystemExit("head close missing")
html = html.replace("</head>", style + "\n</head>", 1)

if "Current release: **v0.3.328**" not in readme:
    raise SystemExit("README version unexpected")
readme = readme.replace("Current release: **v0.3.328**", "Current release: **v0.3.331**", 1)

main_path.write_text(main, encoding="utf-8")
html_path.write_text(html, encoding="utf-8")
readme_path.write_text(readme, encoding="utf-8")
