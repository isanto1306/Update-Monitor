from pathlib import Path
import re

OLD_VERSION = "0.3.331"
NEW_VERSION = "0.3.332"

index_path = Path("static/index.html")
main_path = Path("app/main.py")
zima_path = Path("docker-compose.zimaos.yml")

index = index_path.read_text(encoding="utf-8")
main = main_path.read_text(encoding="utf-8")
zima = zima_path.read_text(encoding="utf-8")

# 1) Self update: reload as soon as the replacement backend is reachable.
self_update_pattern = re.compile(
    r"async function waitForSelfUpdateRestart\(stackKey,previousStartedAt,timeoutMs=6\*60\*1000\)\{.*?\n\}\n\nasync function installImageUpdate",
    re.S,
)
self_update_replacement = r'''async function waitForSelfUpdateRestart(stackKey,previousStartedAt,timeoutMs=6*60*1000){
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

    const startedAt=String(d&&d.service_runtime&&d.service_runtime.started_at||'');
    const restarted=(previous&&startedAt&&startedAt!==previous)||(!previous&&sawOffline&&startedAt);
    if(restarted){
      // The replacement Update Monitor backend is reachable. The targeted
      // verification continues server-side; reload the browser immediately.
      return true;
    }

    await new Promise(resolve=>setTimeout(resolve,1000));
  }

  return false;
}

async function installImageUpdate'''
index, count = self_update_pattern.subn(self_update_replacement, index, count=1)
if count != 1:
    raise SystemExit(f"self update function patch count={count}, expected 1")

index = index.replace(
    "      // The replacement container is running and its targeted verification is complete.\n      // Reload this browser tab so Edge/other browsers immediately load the new frontend.",
    "      // The replacement container is running and the new backend is reachable.\n      // Reload now; targeted verification continues independently on the server.",
    1,
)

# 2) CPU / RAM block: reproduce the reference layout with two blue bars and
# a RAM usage tooltip while keeping live updates.
resource_pattern = re.compile(
    r"function formatCardResourcePercent\(value\)\{.*?\n\}\nfunction renderAppPortStat\(app\)\{",
    re.S,
)
resource_replacement = r'''function formatCardResourcePercent(value){
  const number=Number(value);
  if(!Number.isFinite(number))return '–';
  const digits=Math.abs(number)>=10?0:1;
  try{
    return number.toLocaleString(getUiLocale(),{minimumFractionDigits:digits,maximumFractionDigits:digits})+' %';
  }catch(_error){
    return number.toFixed(digits)+' %';
  }
}
function cardResourceBarPercent(value){
  const number=Number(value);
  if(!Number.isFinite(number))return 0;
  return Math.max(0,Math.min(100,number));
}
function cardRamTooltip(memoryUsage){
  const raw=String(memoryUsage||'').trim();
  const language=String(state.language||'de');
  const labels={de:'RAM Verbrauch',en:'RAM usage',fr:'Utilisation RAM',pt:'Uso de RAM',es:'Uso de RAM'};
  const joins={de:'von',en:'of',fr:'sur',pt:'de',es:'de'};
  const result={title:labels[language]||labels.en,detail:raw};
  if(!raw)return result;

  const match=raw.match(/^([0-9]+(?:[.,][0-9]+)?)\s*([KMGT]i?B)\s*\/\s*([0-9]+(?:[.,][0-9]+)?)\s*([KMGT]i?B)$/i);
  if(!match)return result;

  const normalizeUnit=(unit)=>String(unit||'').replace(/iB$/i,'B').toUpperCase();
  const formatNumber=(value)=>{
    const number=Number(String(value).replace(',','.'));
    if(!Number.isFinite(number))return String(value);
    try{
      return number.toLocaleString(getUiLocale(),{minimumFractionDigits:2,maximumFractionDigits:2});
    }catch(_error){
      return number.toFixed(2);
    }
  };
  result.detail=`${formatNumber(match[1])} ${normalizeUnit(match[2])} ${joins[language]||joins.en} ${formatNumber(match[3])} ${normalizeUnit(match[4])}`;
  return result;
}
function renderAppResourceStat(app){
  const cpuText=formatCardResourcePercent(app.cpu_percent);
  const memoryText=formatCardResourcePercent(app.memory_percent);
  const cpuBar=cardResourceBarPercent(app.cpu_percent);
  const memoryBar=cardResourceBarPercent(app.memory_percent);
  const ramTip=cardRamTooltip(app.memory_usage);
  const tooltipHidden=ramTip.detail?'':' hidden';
  return `<div class="update-card-stat update-card-stat-resource" data-resource-stack="${esc(String(app.stack_key||''))}">
    <div class="update-card-stat-label update-resource-title">CPU / RAM</div>
    <div class="update-resource-bars">
      <div class="update-resource-row update-resource-cpu-row">
        <span class="update-resource-name">CPU</span>
        <span class="update-resource-track"><span class="update-resource-fill" data-resource-cpu-bar style="width:${cpuBar}%"></span></span>
        <span class="update-resource-percent" data-resource-cpu>${esc(cpuText)}</span>
      </div>
      <div class="update-resource-row update-resource-ram-row" tabindex="0">
        <span class="update-resource-name">RAM</span>
        <span class="update-resource-track"><span class="update-resource-fill" data-resource-ram-bar style="width:${memoryBar}%"></span></span>
        <span class="update-resource-percent" data-resource-ram>${esc(memoryText)}</span>
        <span class="update-resource-tooltip" data-resource-ram-tooltip${tooltipHidden}>
          <strong data-resource-ram-tip-title>${esc(ramTip.title)}</strong>
          <span data-resource-ram-tip-detail>${esc(ramTip.detail||'–')}</span>
        </span>
      </div>
    </div>
  </div>`;
}
function syncAppResourceStat(app){
  const key=String(app.stack_key||'');
  const cpuText=formatCardResourcePercent(app.cpu_percent);
  const memoryText=formatCardResourcePercent(app.memory_percent);
  const cpuBarValue=cardResourceBarPercent(app.cpu_percent);
  const ramBarValue=cardResourceBarPercent(app.memory_percent);
  const ramTip=cardRamTooltip(app.memory_usage);
  document.querySelectorAll('[data-resource-stack]').forEach(node=>{
    if(String(node.dataset.resourceStack||'')!==key)return;
    const cpu=node.querySelector('[data-resource-cpu]');
    const ram=node.querySelector('[data-resource-ram]');
    const cpuBar=node.querySelector('[data-resource-cpu-bar]');
    const ramBar=node.querySelector('[data-resource-ram-bar]');
    const tip=node.querySelector('[data-resource-ram-tooltip]');
    const tipTitle=node.querySelector('[data-resource-ram-tip-title]');
    const tipDetail=node.querySelector('[data-resource-ram-tip-detail]');
    if(cpu)cpu.textContent=cpuText;
    if(ram)ram.textContent=memoryText;
    if(cpuBar)cpuBar.style.width=`${cpuBarValue}%`;
    if(ramBar)ramBar.style.width=`${ramBarValue}%`;
    if(tipTitle)tipTitle.textContent=ramTip.title;
    if(tipDetail)tipDetail.textContent=ramTip.detail||'–';
    if(tip)tip.hidden=!ramTip.detail;
  });
}
function renderAppPortStat(app){'''
index, count = resource_pattern.subn(resource_replacement, index, count=1)
if count != 1:
    raise SystemExit(f"resource function patch count={count}, expected 1")

style_pattern = re.compile(r'<style id="um-card-resource-port-v0331">.*?</style>', re.S)
style_replacement = r'''<style id="um-card-resource-port-v0332">
/* v0.3.332 — CPU/RAM follows the visual reference; status strip height is fixed. */
.update-card-summary > .update-card-stats{
  height:78px !important;
  min-height:78px !important;
  max-height:78px !important;
  align-items:stretch;
}
.update-card-summary > .update-card-stats .update-card-stat{
  min-height:58px;
}
.update-card-summary > .update-card-stats .update-card-stat-resource{
  --status-band-icon:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22black%22%20stroke-width%3D%221.7%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Crect%20x%3D%227%22%20y%3D%227%22%20width%3D%2210%22%20height%3D%2210%22%20rx%3D%222%22%2F%3E%3Cpath%20d%3D%22M9%201v3m6-3v3M9%2020v3m6-3v3M20%209h3m-3%206h3M1%209h3m-3%206h3%22%2F%3E%3C%2Fsvg%3E") !important;
  padding-left:42px !important;
  overflow:visible !important;
  gap:3px !important;
}
.update-card-summary > .update-card-stats .update-card-stat-resource::before{
  left:10px !important;
  top:10px !important;
  transform:none !important;
  width:27px !important;
  height:27px !important;
  background:#69adff !important;
}
.update-card-stat-resource .update-resource-title{
  color:#69adff !important;
  font-size:11px !important;
  font-weight:500 !important;
  line-height:1.1 !important;
  margin:0 0 4px !important;
}
.update-resource-bars{
  width:100%;
  display:flex;
  flex-direction:column;
  gap:5px;
}
.update-resource-row{
  position:relative;
  width:100%;
  display:grid;
  grid-template-columns:31px minmax(48px,1fr) 36px;
  align-items:center;
  column-gap:6px;
  min-height:13px;
}
.update-resource-name{
  color:#72afff;
  font-size:10.5px;
  font-weight:500;
  line-height:1;
  white-space:nowrap;
}
.update-resource-track{
  display:block;
  width:100%;
  height:12px;
  overflow:hidden;
  border-radius:4px;
  background:#22313e;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.015);
}
.update-resource-fill{
  display:block;
  height:100%;
  min-width:0;
  border-radius:4px;
  background:linear-gradient(90deg,#6eaaff 0%,#76b7ff 100%);
  transition:width .22s ease;
}
.update-resource-percent{
  color:#d5e0e8;
  font-size:10px;
  font-weight:500;
  line-height:1;
  text-align:right;
  white-space:nowrap;
}
.update-resource-ram-row{outline:none;}
.update-resource-tooltip{
  position:absolute;
  z-index:50;
  left:43px;
  top:20px;
  min-width:151px;
  padding:7px 9px 8px;
  display:flex;
  flex-direction:column;
  gap:3px;
  border:1px solid #39444d;
  border-radius:5px;
  background:#1d2329;
  box-shadow:0 5px 14px rgba(0,0,0,.34);
  color:#e7edf2;
  font-size:10.5px;
  line-height:1.2;
  white-space:nowrap;
  pointer-events:none;
  opacity:0;
  visibility:hidden;
  transform:translateY(2px);
  transition:opacity .1s ease,transform .1s ease,visibility .1s ease;
}
.update-resource-tooltip strong{
  color:#f0f4f7;
  font-size:11px;
  font-weight:700;
}
.update-resource-tooltip[hidden]{display:none !important;}
.update-resource-ram-row:hover .update-resource-tooltip,
.update-resource-ram-row:focus .update-resource-tooltip,
.update-resource-ram-row:focus-within .update-resource-tooltip{
  opacity:1;
  visibility:visible;
  transform:translateY(0);
}
.update-port-stat{
  display:flex !important;
  flex-direction:column;
  justify-content:flex-start;
  gap:1px;
  min-height:25px;
  max-width:100%;
  white-space:normal !important;
  line-height:1.05;
}
.update-port-line{
  display:block;
  min-width:0;
  min-height:12px;
  max-width:100%;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
</style>'''
index, count = style_pattern.subn(style_replacement, index, count=1)
if count != 1:
    raise SystemExit(f"resource style patch count={count}, expected 1")

# 3) Version bump after structural replacements.
if OLD_VERSION not in index:
    raise SystemExit(f"{OLD_VERSION} not found in index.html")
index = index.replace(OLD_VERSION, NEW_VERSION)

old_main = f'VERSION = "{OLD_VERSION}"'
new_main = f'VERSION = "{NEW_VERSION}"'
if main.count(old_main) != 1:
    raise SystemExit(f"main.py VERSION patch count={main.count(old_main)}, expected 1")
main = main.replace(old_main, new_main, 1)

if 'version: "0.3.321"' not in zima:
    raise SystemExit('docker-compose.zimaos.yml expected version 0.3.321 not found')
zima = zima.replace('version: "0.3.321"', f'version: "{NEW_VERSION}"', 1)
zima = zima.replace('update_at: "2026-09-15"', 'update_at: "2026-09-16"', 1)

index_path.write_text(index, encoding="utf-8")
main_path.write_text(main, encoding="utf-8")
zima_path.write_text(zima, encoding="utf-8")

print(f"Patched Update Monitor to {NEW_VERSION}")
