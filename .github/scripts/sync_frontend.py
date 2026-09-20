import re
from pathlib import Path

main = Path("app/main.py").read_text(encoding="utf-8")
match = re.search(r'^VERSION = "([^"]+)"', main, re.MULTILINE)
if not match:
    raise SystemExit("Backend VERSION marker not found")
target = match.group(1)

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

# v0.3.363 running-backup cancellation UI.
if "data-backup-cancel-stack" not in text:
    replace_once(
        "</head>",
        """<style id="um-backup-cancel-v0363">
.backup-cancel-running-button{
    width:auto;align-self:center;margin-top:-12px;margin-bottom:12px;
    padding:5px 8px;border:1px solid rgba(214,82,82,.62);border-radius:7px;
    background:rgba(151,42,42,.12);color:#ffb2ad;font:inherit;
    font-size:9.4px;font-weight:800;letter-spacing:.025em;white-space:nowrap;cursor:pointer;
}
.backup-cancel-running-button:hover:not(:disabled){border-color:rgba(231,101,101,.78);background:rgba(163,47,47,.20);color:#ffd0cd}
.backup-cancel-running-button:disabled{opacity:.58;cursor:wait}
.backup-cancel-running-button[hidden]{display:none!important}
</style>
</head>""",
        "backup cancel style",
    )

    replace_once(
        "backupPhase:'BACKUP WIRD ERSTELLT…', backupPhaseProgress:'BACKUP {progress} %', installPhase:",
        "backupPhase:'BACKUP WIRD ERSTELLT…', backupPhaseProgress:'BACKUP {progress} %', backupCancelRunning:'BACKUP ABBRECHEN', backupCancelling:'BACKUP WIRD ABGEBROCHEN…', backupCancelled:'Backup wurde abgebrochen. Update wurde nicht gestartet.', backupCancelFailed:'Backup konnte nicht abgebrochen werden: {error}', installPhase:",
        "German backup cancellation translations",
    )
    replace_once(
        "backupPhase:'CREATING BACKUP…', backupPhaseProgress:'BACKUP {progress} %', installPhase:",
        "backupPhase:'CREATING BACKUP…', backupPhaseProgress:'BACKUP {progress} %', backupCancelRunning:'CANCEL BACKUP', backupCancelling:'CANCELLING BACKUP…', backupCancelled:'Backup was cancelled. The update was not started.', backupCancelFailed:'Could not cancel backup: {error}', installPhase:",
        "English backup cancellation translations",
    )

    replace_once(
        """  if(kind==='update'){
    if(p.startsWith('backup')){
""",
        """  if(kind==='update'){
    if(p==='backup_cancelling')return t('backupCancelling');
    if(p.startsWith('backup')){
""",
        "backup cancelling progress label",
    )

    replace_once(
        """    button.textContent=actionProgressLabel(kind,progress,String(info&&info.phase||''));
  });
}
""",
        """    button.textContent=actionProgressLabel(kind,progress,String(info&&info.phase||''));
  });

  document.querySelectorAll('[data-backup-cancel-stack]').forEach(button=>{
    if(String(button.dataset.backupCancelStack||'')!==key)return;
    const cancellable=!!(info&&info.kind==='update'&&!info.finished&&info.backup_cancel_available);
    const requested=!!(info&&info.backup_cancel_requested);
    button.hidden=!cancellable;
    button.disabled=!cancellable||requested;
    button.textContent=requested?t('backupCancelling'):t('backupCancelRunning');
  });
}
""",
        "live backup cancel button state",
    )

    polling = """async function finishActionProgressPolling(stackKey){
  const key=String(stackKey||'');
  if(!key)return;
  await refreshActionProgress(key);
  // Keep a real 100% result visible briefly before the card changes/rescans.
  const info=state.actionProgress.get(key);
  if(info&&info.determinate&&Number(info.progress)>=100){
    await new Promise(resolve=>setTimeout(resolve,300));
  }
  stopActionProgressPolling(key);
}
"""
    replace_once(
        polling,
        polling + """
async function requestBackupCancellation(stackKey){
  const key=String(stackKey||'').trim();
  if(!key)throw new Error('Missing app key');
  return api('/api/backup-cancel',{method:'POST',body:JSON.stringify({stack_key:key})});
}
async function cancelBackupForStack(stackKey){
  const key=String(stackKey||'').trim();
  if(!key)return;
  const info=state.actionProgress.get(key);
  if(!info||!info.backup_cancel_available||info.backup_cancel_requested)return;
  info.backup_cancel_requested=true;
  info.phase='backup_cancelling';
  state.actionProgress.set(key,info);
  applyActionProgressToDom(key);
  try{
    await requestBackupCancellation(key);
  }catch(err){
    info.backup_cancel_requested=false;
    state.actionProgress.set(key,info);
    applyActionProgressToDom(key);
    if(err.message!=='auth'){
      state.updateMessages.set(key,{ok:false,kind:'backup_cancel',text:t('backupCancelFailed').replace('{error}',err.message||'-')});
      render();
    }
  }
}
document.addEventListener('click',event=>{
  const button=event.target&&event.target.closest?event.target.closest('[data-backup-cancel-stack]'):null;
  if(!button)return;
  event.preventDefault();
  event.stopPropagation();
  cancelBackupForStack(String(button.dataset.backupCancelStack||''));
});
""",
        "backup cancellation API frontend",
    )

    replace_once(
        "  const storedAutoError=(",
        """  const backupCancelButton=`<button class="backup-cancel-running-button" type="button" data-backup-cancel-stack="${esc(app.stack_key)}" hidden>${esc(t('backupCancelRunning'))}</button>`;
  const storedAutoError=(""",
        "card backup cancel button",
    )
    replace_once(
        '<div class="update-card-actions">${cardActionButton}<button class="update-details-button"',
        '<div class="update-card-actions">${cardActionButton}${backupCancelButton}<button class="update-details-button"',
        "card backup cancel placement",
    )

    replace_once(
        """    const updateResult=await api('/api/app-update',{
      method:'POST',
      body:JSON.stringify({stack_key:stackKey,backup_mode:normalizedBackupMode})
    });
    if(updateResult&&updateResult.self_update_handoff){
""",
        """    const updateResult=await api('/api/app-update',{
      method:'POST',
      body:JSON.stringify({stack_key:stackKey,backup_mode:normalizedBackupMode})
    });
    if(updateResult&&updateResult.cancelled){
      state.updateMessages.set(stackKey,{ok:true,kind:'backup_cancelled',text:t('backupCancelled')});
      return;
    }
    if(updateResult&&updateResult.self_update_handoff){
""",
        "manual update cancellation response",
    )

    replace_once(
        """      state.autoLiveActions.delete(key);
      state.updatingApps.delete(key);
      beginVerificationState(key,state.verificationBaselines.get(key));

      // Installation is finished, but the authoritative app recheck is not.
""",
        """      state.autoLiveActions.delete(key);
      state.updatingApps.delete(key);
      if(info.cancelled){
        state.verifyingApps.delete(key);
        state.verificationBaselines.delete(key);
        state.updateMessages.set(key,{ok:true,kind:'backup_cancelled',text:t('backupCancelled')});
        state.actionProgress.delete(key);
        return;
      }
      beginVerificationState(key,state.verificationBaselines.get(key));

      // Installation is finished, but the authoritative app recheck is not.
""",
        "automatic update cancellation response",
    )

    replace_once(
        """function applyImageSourceProgressToButton(){
  const button=document.getElementById('imageSourceApply');
  if(!button||!imageSourceState.busy)return;

  const info=imageSourceProgressInfo;
""",
        """function syncImageSourceBackupCancel(){
  const button=document.getElementById('imageSourceCancel');
  if(!button)return;
  if(!imageSourceState.busy){button.disabled=false;button.textContent=imageSourceText('Abbrechen','Cancel');return;}
  const info=imageSourceProgressInfo||{};
  const cancellable=!!(info.backup_cancel_available&&!info.finished);
  const requested=!!info.backup_cancel_requested;
  button.disabled=!cancellable||requested;
  button.textContent=requested
    ?imageSourceText('Abbruch läuft…','Cancelling…')
    :cancellable?imageSourceText('Backup abbrechen','Cancel backup'):imageSourceText('Abbrechen','Cancel');
}
async function cancelImageSourceBackup(){
  const key=String(imageSourceState.stackKey||'').trim();
  const info=imageSourceProgressInfo||{};
  if(!key||!info.backup_cancel_available||info.backup_cancel_requested)return;
  info.backup_cancel_requested=true;
  info.phase='backup_cancelling';
  imageSourceProgressInfo=info;
  syncImageSourceBackupCancel();
  try{
    await requestBackupCancellation(key);
  }catch(err){
    info.backup_cancel_requested=false;
    imageSourceProgressInfo=info;
    imageSourceState.error=imageSourceText(
      'Backup konnte nicht abgebrochen werden: '+String(err&&err.message||'-'),
      'Could not cancel backup: '+String(err&&err.message||'-')
    );
    renderImageSourceDialog();
  }
}
function applyImageSourceProgressToButton(){
  const button=document.getElementById('imageSourceApply');
  if(!button||!imageSourceState.busy)return;

  const info=imageSourceProgressInfo;
""",
        "image source backup cancellation helpers",
    )
    replace_once(
        """  button.textContent=imageSourceProgressLabel(info);
}
""",
        """  button.textContent=imageSourceProgressLabel(info);
  syncImageSourceBackupCancel();
}
""",
        "image source cancel live state",
    )
    replace_once(
        """          <button class="image-source-action secondary" id="imageSourceCancel" type="button" ${imageSourceState.busy?'disabled':''}>${esc(
            imageSourceText('Abbrechen','Cancel')
          )}</button>
""",
        """          <button class="image-source-action secondary" id="imageSourceCancel" type="button">${esc(
            imageSourceText('Abbrechen','Cancel')
          )}</button>
""",
        "image source cancel button",
    )
    replace_once(
        """  const cancel=document.getElementById('imageSourceCancel');
  if(cancel)cancel.onclick=()=>closeImageSourceDialog();

  const apply=document.getElementById('imageSourceApply');
""",
        """  const cancel=document.getElementById('imageSourceCancel');
  if(cancel){cancel.onclick=()=>imageSourceState.busy?cancelImageSourceBackup():closeImageSourceDialog();}
  syncImageSourceBackupCancel();

  const apply=document.getElementById('imageSourceApply');
""",
        "image source cancel click",
    )
    replace_once(
        """    await api('/api/image-source-switch',{
      method:'POST',
      body:JSON.stringify({
        stack_key:stackKey,
        image_key:imageKey,
        source_id:String(selected.source_id||''),
        accept_warnings:selected.level==='warn',
        backup_mode:'full'
      })
    });

    await refreshImageSourceProgress(stackKey);
""",
        """    const switchResult=await api('/api/image-source-switch',{
      method:'POST',
      body:JSON.stringify({
        stack_key:stackKey,
        image_key:imageKey,
        source_id:String(selected.source_id||''),
        accept_warnings:selected.level==='warn',
        backup_mode:'full'
      })
    });
    if(switchResult&&switchResult.cancelled){
      stopImageSourceProgressPolling();
      closeImageSourceDialog(true);
      state.updateMessages.set(stackKey,{
        ok:true,
        kind:'backup_cancelled',
        text:imageSourceText(
          'Backup wurde abgebrochen. Image Quelle wurde nicht geändert.',
          'Backup was cancelled. The image source was not changed.'
        )
      });
      render();
      return;
    }

    await refreshImageSourceProgress(stackKey);
""",
        "image source cancelled response",
    )

path.write_text(text, encoding="utf-8")
