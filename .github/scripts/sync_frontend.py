import ast
import re
from pathlib import Path

main = Path("app/main.py").read_text(encoding="utf-8")
match = re.search(r'^VERSION = "([^"]+)"', main, re.MULTILINE)
if not match:
    raise SystemExit("Backend VERSION marker not found")
target = match.group(1)

tree = ast.parse(main)
runtime_bridge = None
for node in tree.body:
    if not isinstance(node, ast.Assign):
        continue
    if not any(
        isinstance(target_node, ast.Name)
        and target_node.id == "_INDEX_ASYNC_UPDATE_BRIDGE"
        for target_node in node.targets
    ):
        continue
    runtime_bridge = ast.literal_eval(node.value)
    break
if not isinstance(runtime_bridge, str) or "um-async-update-bridge-v0366" not in runtime_bridge:
    raise SystemExit("Async update frontend bridge not found in backend source")

path = Path("static/index.html")
text = path.read_text(encoding="utf-8")


def replace_once(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"Frontend marker not found: {label}")
    text = text.replace(old, new, 1)


# Active release markers.
text = re.sub(r'(\?v=)\d+\.\d+\.\d+', lambda m: m.group(1) + target, text)
text = re.sub(
    r'(<div class="system-info-value" id="systemInfoVersion">v)\d+\.\d+\.\d+(</div>)',
    lambda m: m.group(1) + target + m.group(2),
    text,
)
text = re.sub(
    r'(/update-channel\.js\?v=)\d+\.\d+\.\d+',
    lambda m: m.group(1) + target,
    text,
)
text = re.sub(
    r"(const UPDATE_MONITOR_VERSION = 'v)\d+\.\d+\.\d+(';)",
    lambda m: m.group(1) + target + m.group(2),
    text,
)

# v0.3.366: every backup phase belongs to the backup progress view.
old_image_source_backup = "  if(phase==='backup')label=imageSourceText('Backup','Backup');"
new_image_source_backup = "  if(phase.startsWith('backup'))label=imageSourceText('Backup','Backup');"
if new_image_source_backup not in text:
    replace_once(
        old_image_source_backup,
        new_image_source_backup,
        "image source backup phase label",
    )

# Manual update POSTs return immediately in v0.3.366. The small runtime bridge
# keeps the existing frontend await alive via short action-progress requests,
# avoiding reverse-proxy timeouts without rewriting the application UI logic.
if 'id="um-async-update-bridge-v0366"' not in text:
    if "</body>" not in text:
        raise SystemExit("Frontend marker not found: closing body")
    text = text.replace("</body>", runtime_bridge + "\n</body>", 1)

# Older migration guard kept idempotent.
old_error = """  const storedAutoError=(
    policy.auto_enabled
    &&policy.last_auto_result==='error'
    &&String(policy.last_auto_error||'').trim()
"""
new_error = """  const storedAutoError=(
    !isChecking
    &&policy.auto_enabled
    &&policy.last_auto_result==='error'
    &&String(policy.last_auto_error||'').trim()
"""
if new_error not in text:
    replace_once(old_error, new_error, "stored auto error")

# v0.3.364 inline running-backup cancellation UI.
legacy_style = """<style id="um-backup-cancel-v0363">
.backup-cancel-running-button{
    width:auto;align-self:center;margin-top:-12px;margin-bottom:12px;
    padding:5px 8px;border:1px solid rgba(214,82,82,.62);border-radius:7px;
    background:rgba(151,42,42,.12);color:#ffb2ad;font:inherit;
    font-size:9.4px;font-weight:800;letter-spacing:.025em;white-space:nowrap;cursor:pointer;
}
.backup-cancel-running-button:hover:not(:disabled){border-color:rgba(231,101,101,.78);background:rgba(163,47,47,.20);color:#ffd0cd}
.backup-cancel-running-button:disabled{opacity:.58;cursor:wait}
.backup-cancel-running-button[hidden]{display:none!important}
</style>"""
inline_style = """<style id="um-backup-cancel-v0364">
/* v0.3.364: the existing update progress button is also the running-backup cancel control. */
.update-install-button.backup-cancel-inline{
    opacity:1!important;
    cursor:pointer!important;
    border-color:rgba(255,180,84,.82);
}
.update-install-button.backup-cancel-inline:hover:not(:disabled){
    border-color:rgba(231,101,101,.82);
    background:rgba(163,47,47,.16);
    color:#ffd0cd;
}
.update-install-button.backup-cancel-inline.is-cancelling{
    cursor:wait!important;
    border-color:rgba(231,101,101,.62);
    color:#ffb2ad;
}
</style>"""
if legacy_style in text:
    replace_once(legacy_style, inline_style, "inline backup cancel style")
elif 'id="um-backup-cancel-v0364"' not in text:
    raise SystemExit("Frontend marker not found: inline backup cancel style")

old_de = "backupCancelRunning:'BACKUP ABBRECHEN', backupCancelling:"
new_de = "backupCancelRunning:'Backup abbrechen', backupCancelInline:'{progress} % · Backup abbrechen', backupCancelling:"
if new_de not in text:
    replace_once(old_de, new_de, "German inline backup cancellation translation")

old_en = "backupCancelRunning:'CANCEL BACKUP', backupCancelling:"
new_en = "backupCancelRunning:'Cancel backup', backupCancelInline:'{progress} % · Cancel backup', backupCancelling:"
if new_en not in text:
    replace_once(old_en, new_en, "English inline backup cancellation translation")

legacy_button = """  const backupCancelButton=`<button class="backup-cancel-running-button" type="button" data-backup-cancel-stack="${esc(app.stack_key)}" hidden>${esc(t('backupCancelRunning'))}</button>`;
"""
if legacy_button in text:
    text = text.replace(legacy_button, "", 1)

old_actions = '<div class="update-card-actions">${cardActionButton}${backupCancelButton}<button class="update-details-button"'
new_actions = '<div class="update-card-actions">${cardActionButton}<button class="update-details-button"'
if old_actions in text:
    replace_once(old_actions, new_actions, "remove separate backup cancel placement")

old_apply_start = "function applyActionProgressToDom(stackKey){"
old_apply_end = "\n\nasync function refreshActionProgress(stackKey){"
new_apply = """function applyActionProgressToDom(stackKey){
  const key=String(stackKey||'');
  const info=state.actionProgress.get(key);

  document.querySelectorAll('[data-progress-stack]').forEach(button=>{
    if(String(button.dataset.progressStack||'')!==key)return;

    const kind=String(button.dataset.progressKind||'');
    const busy=kind==='update'
      ? state.updatingApps.has(key)
      :(kind==='restore'
        ? state.restoringApps.has(key)
        : state.uninstallingApps.has(key));
    if(!busy)return;

    const value=Number(info&&info.progress);
    const hasPercent=!!(info&&info.kind===kind&&info.determinate&&Number.isFinite(value));
    const progress=hasPercent?Math.max(0,Math.min(100,Math.round(value))):null;
    const cancellable=!!(
      kind==='update'
      &&info
      &&info.kind==='update'
      &&!info.finished
      &&info.backup_cancel_available
    );
    const requested=!!(cancellable&&info.backup_cancel_requested);

    button.classList.toggle('has-percent',hasPercent);
    if(hasPercent){
      button.style.setProperty('--action-progress',progress+'%');
    }else{
      button.style.removeProperty('--action-progress');
    }

    if(cancellable){
      const label=requested
        ?t('backupCancelling')
        :(progress===null
          ?t('backupCancelRunning')
          :t('backupCancelInline').replace('{progress}',progress));
      button.dataset.backupCancelInline=key;
      button.classList.add('backup-cancel-inline');
      button.classList.toggle('is-cancelling',requested);
      button.disabled=requested;
      button.textContent=label;
      button.title=label;
      button.setAttribute('aria-label',label);
      return;
    }

    delete button.dataset.backupCancelInline;
    button.classList.remove('backup-cancel-inline','is-cancelling');
    button.removeAttribute('title');
    button.removeAttribute('aria-label');
    button.disabled=true;
    button.textContent=actionProgressLabel(kind,progress,String(info&&info.phase||''));
  });
}"""
if "data-backup-cancel-inline" not in text:
    start = text.find(old_apply_start)
    end = text.find(old_apply_end, start)
    if start < 0 or end < 0:
        raise SystemExit("Frontend marker not found: action progress function")
    text = text[:start] + new_apply + text[end:]

old_click = """document.addEventListener('click',event=>{
  const button=event.target&&event.target.closest?event.target.closest('[data-backup-cancel-stack]'):null;
  if(!button)return;
  event.preventDefault();
  event.stopPropagation();
  cancelBackupForStack(String(button.dataset.backupCancelStack||''));
});"""
new_click = """document.addEventListener('click',event=>{
  const button=event.target&&event.target.closest?event.target.closest('[data-backup-cancel-inline]'):null;
  if(!button)return;
  event.preventDefault();
  event.stopImmediatePropagation();
  cancelBackupForStack(String(button.dataset.backupCancelInline||''));
},true);"""
if old_click in text:
    replace_once(old_click, new_click, "inline backup cancellation click handler")

for marker in ("data-backup-cancel-inline", "backupCancelInline", "backup_cancel_available", "/api/backup-cancel"):
    if marker not in text:
        raise SystemExit(f"Frontend marker not found: {marker}")
if "data-backup-cancel-stack" in text or "backup-cancel-running-button" in text:
    raise SystemExit("Legacy separate backup-cancel button is still present")

path.write_text(text, encoding="utf-8")
