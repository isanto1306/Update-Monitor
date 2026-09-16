from pathlib import Path
import re
import subprocess

OLD = "0.3.340"
NEW = "0.3.341"

main_path = Path("app/main.py")
html_path = Path("static/index.html")
main = main_path.read_text(encoding="utf-8")
html = html_path.read_text(encoding="utf-8")

if f'VERSION = "{OLD}"' not in main:
    raise SystemExit("Unexpected backend version")
main = main.replace(f'VERSION = "{OLD}"', f'VERSION = "{NEW}"', 1)

old_runtime = '''    return {
        "checked_at": utc_now(),
        "apps": runtime_apps,
        "removed_stack_keys": removed_stack_keys,
        "scan": live_scan_snapshot(),
        "actions": action_progress_public_snapshot(),
        "automation": {"pending_post_scans": pending_app_scan_keys()},
    }'''
new_runtime = '''    return {
        "version": VERSION,
        "service_runtime": {"started_at": SERVICE_STARTED_AT},
        "checked_at": utc_now(),
        "apps": runtime_apps,
        "removed_stack_keys": removed_stack_keys,
        "scan": live_scan_snapshot(),
        "actions": action_progress_public_snapshot(),
        "automation": {"pending_post_scans": pending_app_scan_keys()},
    }'''
if old_runtime not in main:
    raise SystemExit("runtime-status payload anchor missing")
main = main.replace(old_runtime, new_runtime, 1)

if OLD not in html:
    raise SystemExit("Frontend version anchor missing")
html = html.replace(OLD, NEW)

version_anchor = f"const UPDATE_MONITOR_VERSION = 'v{NEW}';"
if version_anchor not in html:
    raise SystemExit("UPDATE_MONITOR_VERSION anchor missing after bump")
helper = '''const UPDATE_MONITOR_VERSION = 'v0.3.341';
let updateMonitorReloadStarted=false;
function normalizeUpdateMonitorVersion(value){
  return String(value||'').trim().replace(/^v/i,'');
}
function reloadUpdateMonitorPage(){
  if(updateMonitorReloadStarted)return true;
  updateMonitorReloadStarted=true;
  const url=new URL(window.location.href);
  url.searchParams.set('_um_reload',String(Date.now()));
  window.location.replace(url.toString());
  return true;
}
function reloadForBackendVersion(value){
  const backend=normalizeUpdateMonitorVersion(value);
  const frontend=normalizeUpdateMonitorVersion(UPDATE_MONITOR_VERSION);
  if(!backend||!frontend||backend===frontend){
    try{sessionStorage.removeItem('updateMonitorVersionReload');}catch(e){}
    return false;
  }
  const signature=`${frontend}->${backend}`;
  try{
    if(sessionStorage.getItem('updateMonitorVersionReload')===signature)return false;
    sessionStorage.setItem('updateMonitorVersionReload',signature);
  }catch(e){}
  return reloadUpdateMonitorPage();
}'''
html = html.replace(version_anchor, helper, 1)

old_load = "    const d=await api('/api/status');\n    state.data=d.scan;"
new_load = "    const d=await api('/api/status');\n    if(reloadForBackendVersion(d&&d.version))return d;\n    state.data=d.scan;"
if old_load not in html:
    raise SystemExit("loadStatus anchor missing")
html = html.replace(old_load, new_load, 1)

old_runtime_js = "    const data=await api('/api/runtime-status');"
new_runtime_js = "    const data=await api('/api/runtime-status');\n    if(reloadForBackendVersion(data&&data.version))return;"
if old_runtime_js not in html:
    raise SystemExit("refreshRuntimeStatus anchor missing")
html = html.replace(old_runtime_js, new_runtime_js, 1)

wait_pattern = re.compile(
    r'''async function waitForSelfUpdateRestart\(stackKey,previousStartedAt,timeoutMs=6\*60\*1000\)\{.*?\n\}\n\nasync function installImageUpdate''',
    re.S,
)
wait_replacement = r'''async function waitForSelfUpdateRestart(stackKey,previousStartedAt,timeoutMs=6*60*1000){
  const key=String(stackKey||'');
  if(!key)return false;
  const previous=String(previousStartedAt||'');
  const deadline=Date.now()+Math.max(30000,Number(timeoutMs)||0);
  let sawOffline=false;

  while(Date.now()<deadline){
    const d=await loadStatus();
    if(!d){
      sawOffline=true;
      await new Promise(resolve=>setTimeout(resolve,1000));
      continue;
    }

    if(reloadForBackendVersion(d.version))return true;

    const startedAt=String(d&&d.service_runtime&&d.service_runtime.started_at||'');
    if(
      (previous&&startedAt&&startedAt!==previous)
      ||(!previous&&sawOffline&&startedAt)
    ){
      return true;
    }

    await new Promise(resolve=>setTimeout(resolve,1000));
  }

  return false;
}

async function installImageUpdate'''
html, count = wait_pattern.subn(wait_replacement, html, count=1)
if count != 1:
    raise SystemExit(f"waitForSelfUpdateRestart replacement count {count}")

old_reload = '''    if(verified){
      // The replacement container is running and its targeted verification is complete.
      // Reload this browser tab so Edge/other browsers immediately load the new frontend.
      window.setTimeout(()=>window.location.reload(),700);
      return;
    }'''
new_reload = '''    if(verified){
      // Reload as soon as the replacement backend is reachable. Targeted
      // post-update verification continues independently in the new process.
      window.setTimeout(()=>reloadUpdateMonitorPage(),350);
      return;
    }'''
if old_reload not in html:
    raise SystemExit("self update reload caller anchor missing")
html = html.replace(old_reload, new_reload, 1)

main_path.write_text(main, encoding="utf-8")
html_path.write_text(html, encoding="utf-8")

# Static validation of both backend and browser paths.
subprocess.run(["python", "-m", "py_compile", "app/main.py"], check=True)
main = main_path.read_text(encoding="utf-8")
html = html_path.read_text(encoding="utf-8")
assert 'VERSION = "0.3.341"' in main
assert '"version": VERSION' in main
assert '"service_runtime": {"started_at": SERVICE_STARTED_AT}' in main
assert "const UPDATE_MONITOR_VERSION = 'v0.3.341';" in html
assert "function reloadForBackendVersion(value)" in html
assert "reloadForBackendVersion(data&&data.version)" in html
assert "reloadForBackendVersion(d&&d.version)" in html
block = re.search(
    r"async function waitForSelfUpdateRestart.*?\n\}\n\nasync function installImageUpdate",
    html,
    re.S,
)
assert block
assert "appUpdateVerifiedCurrent" not in block.group(0)

# Syntax-check the main browser script with Node.
match = re.search(
    r"<script>\s*(const UPDATE_MONITOR_VERSION = 'v0\.3\.341';.*)</script>\s*</body>",
    html,
    re.S,
)
if not match:
    raise SystemExit("Main JavaScript block not found")
Path("/tmp/update-monitor-main.js").write_text(match.group(1), encoding="utf-8")
subprocess.run(["node", "--check", "/tmp/update-monitor-main.js"], check=True)
print("v0.3.341 self update reload validation passed")
