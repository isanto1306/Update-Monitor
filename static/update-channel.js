(function(){
'use strict';

function channelText(de,en){
  try{
    if(typeof imageSourceText==='function') return imageSourceText(de,en);
  }catch(e){}
  return (localStorage.getItem('updateMonitorLanguage')==='de')?de:en;
}
function channelEsc(value){
  return String(value==null?'':value)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

var style=document.createElement('style');
style.id='um-update-channel-v0356';
style.textContent=[
'.update-channel-entry{margin-top:10px;padding-top:10px;border-top:0;}',
'.update-detail-image:has(+ .update-channel-entry){border-bottom:1px solid rgba(91,156,255,.42) !important;}',
'.update-channel-entry + .image-source-entry{border-top:1px solid rgba(91,156,255,.42);}',
'.update-channel-current{display:inline-flex;margin-left:6px;padding:2px 6px;border:1px solid rgba(91,156,255,.28);border-radius:999px;background:rgba(91,156,255,.06);color:#9fc8ff;font-size:8.5px;font-weight:800;vertical-align:middle;}',
'.update-channel-modal .image-source-option{cursor:pointer;}',
'.update-channel-modal .image-source-option:disabled{cursor:not-allowed;opacity:.58;}',
'body.scan-ui-locked #imageChannelApply{pointer-events:none !important;cursor:not-allowed !important;opacity:.46 !important;}'
].join('');
document.head.appendChild(style);

var backdrop=document.createElement('div');
backdrop.id='imageChannelBackdrop';
backdrop.className='image-source-backdrop';
backdrop.setAttribute('aria-hidden','true');
backdrop.innerHTML=
  '<section class="image-source-dialog update-channel-modal" role="dialog" aria-modal="true" aria-labelledby="imageChannelHeading">'+
    '<div class="image-source-header">'+
      '<div>'+
        '<div id="imageChannelHeading" class="image-source-heading">Update Kanal</div>'+
        '<div id="imageChannelSubtitle" class="image-source-subtitle"></div>'+
      '</div>'+
      '<button id="imageChannelClose" class="image-source-close" type="button" aria-label="Schließen">×</button>'+
    '</div>'+
    '<div id="imageChannelBody" class="image-source-body"></div>'+
  '</section>';
document.body.appendChild(backdrop);

var channelState={
  stackKey:'',
  data:null,
  imageKey:'',
  choice:'',
  busy:false,
  error:''
};
var channelProgressTimer=null;
var channelProgressInfo=null;

function channelLabelForApp(app){
  var tags=[];
  var items=(app&&Array.isArray(app.items))?app.items:[];
  items.forEach(function(item){
    var tag=String(item&&item.tag||'').trim();
    if(tag && tags.indexOf(tag)<0) tags.push(tag);
  });
  if(tags.length===1) return tags[0];
  if(tags.length>1) return channelText(String(tags.length)+' Images',String(tags.length)+' images');
  return '-';
}

window.renderUpdateChannelEntry=function(app){
  if(!app||!app.image_source_available) return '';
  var current=channelLabelForApp(app);
  return '<div class="update-channel-entry">'+
    '<div class="image-source-entry-head">'+
      '<div>'+
        '<div class="image-source-entry-title">'+channelEsc(channelText('Update Kanal','Update channel'))+
          '<span class="update-channel-current">'+channelEsc(current)+'</span></div>'+
        '<div class="image-source-entry-text">'+channelEsc(channelText(
          'Beweglichen Docker Tag auswählen, dem diese App automatisch folgen soll.',
          'Choose the moving Docker tag this app should follow automatically.'
        ))+'</div>'+
      '</div>'+
      '<button class="image-source-entry-button" type="button" data-update-channel-open="'+
        channelEsc(app.stack_key||'')+'">'+channelEsc(channelText('Update Kanal wählen','Choose update channel'))+'</button>'+
    '</div>'+
  '</div>';
};

function images(){
  return channelState.data&&Array.isArray(channelState.data.images)?channelState.data.images:[];
}
function selectedImage(){
  var list=images();
  for(var i=0;i<list.length;i++){
    if(String(list[i].image_key||'')===String(channelState.imageKey||'')) return list[i];
  }
  return list[0]||null;
}
function selectedOption(){
  var image=selectedImage();
  var options=image&&Array.isArray(image.options)?image.options:[];
  for(var i=0;i<options.length;i++){
    if(String(options[i].option_id||'')===String(channelState.choice||'')) return options[i];
  }
  return null;
}
function defaultChoice(image){
  var options=image&&Array.isArray(image.options)?image.options:[];
  for(var r=0;r<options.length;r++){
    if(options[r].source_is_runtime&&options[r].can_apply) return options[r];
  }
  for(var i=0;i<options.length;i++) if(options[i].current&&options[i].available) return options[i];
  for(var j=0;j<options.length;j++) if(options[j].can_apply) return options[j];
  for(var k=0;k<options.length;k++) if(options[k].current) return options[k];
  return options[0]||null;
}
function lockBackground(open){
  var root=document.documentElement;
  var body=document.body;
  if(open){
    root.classList.add('um-background-scroll-locked');
    body.classList.add('um-background-scroll-locked');
    return;
  }
  var other=document.querySelector(
    '#settingsOverlay.visible,#dockerInfoBackdrop.visible,#portConflictBackdrop.visible,'+
    '#backupRestoreBackdrop.visible,#uninstallConfirmBackdrop.visible,#backupPreviewBackdrop.visible,'+
    '#imageSourceBackdrop.visible,.policy-modal-backdrop.visible'
  );
  if(!other){
    root.classList.remove('um-background-scroll-locked');
    body.classList.remove('um-background-scroll-locked');
  }
}
function stopProgress(){
  if(channelProgressTimer){
    clearInterval(channelProgressTimer);
    channelProgressTimer=null;
  }
}
function progressLabel(){
  try{
    if(typeof imageSourceProgressLabel==='function') return imageSourceProgressLabel(channelProgressInfo);
  }catch(e){}
  return channelText('Wechsel läuft…','Switching…');
}
function applyProgress(){
  var button=document.getElementById('imageChannelApply');
  if(!button||!channelState.busy) return;
  var info=channelProgressInfo||{};
  var value=Number(info.progress);
  var hasPercent=!!(info.determinate&&Number.isFinite(value));
  var percent=hasPercent?Math.max(0,Math.min(100,Math.round(value))):0;
  button.classList.toggle('image-source-switch-progress',hasPercent);
  if(hasPercent) button.style.setProperty('--image-source-progress',percent+'%');
  else button.style.removeProperty('--image-source-progress');
  button.textContent=progressLabel();
}
async function refreshProgress(stackKey){
  try{
    var payload=await api('/api/action-progress?stack_key='+encodeURIComponent(stackKey));
    var info=payload&&payload.progress;
    if(info&&info.kind==='update'){
      channelProgressInfo=info;
      applyProgress();
    }
    return info||null;
  }catch(err){
    if(err&&err.message!=='auth') console.error('Update channel progress:',err);
    return null;
  }
}
function startProgress(stackKey){
  stopProgress();
  channelProgressInfo={kind:'update',progress:null,determinate:false,phase:'starting',finished:false};
  applyProgress();
  refreshProgress(stackKey);
  channelProgressTimer=setInterval(function(){refreshProgress(stackKey);},500);
}

function closeDialog(force){
  if(channelState.busy&&!force) return;
  stopProgress();
  channelProgressInfo=null;
  backdrop.classList.remove('visible');
  backdrop.setAttribute('aria-hidden','true');
  lockBackground(false);
  channelState={stackKey:'',data:null,imageKey:'',choice:'',busy:false,error:''};
}

function selectImage(key){
  if(channelState.busy) return;
  var list=images();
  var image=null;
  for(var i=0;i<list.length;i++){
    if(String(list[i].image_key||'')===String(key||'')){image=list[i];break;}
  }
  if(!image) return;
  channelState.imageKey=String(image.image_key||'');
  var preferred=defaultChoice(image);
  channelState.choice=String(preferred&&preferred.option_id||'');
  channelState.error='';
  renderDialog();
}

function renderDialog(){
  var body=document.getElementById('imageChannelBody');
  var subtitle=document.getElementById('imageChannelSubtitle');
  var close=document.getElementById('imageChannelClose');
  if(!body) return;
  if(close) close.disabled=!!channelState.busy;

  if(!channelState.data){
    body.innerHTML='<div class="image-source-loading">'+channelEsc(
      channelState.error||channelText(
        'Update Kanäle werden aus der Registry gelesen…',
        'Update channels are being read from the registry…'
      )
    )+'</div>';
    return;
  }

  var data=channelState.data;
  var list=images();
  if(subtitle){
    subtitle.textContent=String(data.app||'')+' · '+String(data.image_count||list.length||0)+' '+channelText('Images','images');
  }
  if(!list.length){
    body.innerHTML='<div class="image-source-error">'+channelEsc(
      channelText('Keine Registry Images gefunden.','No registry images found.')
    )+'</div>';
    return;
  }

  if(!channelState.imageKey){
    channelState.imageKey=String(list[0].image_key||'');
  }
  var image=selectedImage();
  if(image&&!channelState.choice){
    var preferred=defaultChoice(image);
    channelState.choice=String(preferred&&preferred.option_id||'');
  }
  var options=image&&Array.isArray(image.options)?image.options:[];
  var selected=selectedOption();

  var imageCards='';
  list.forEach(function(entry,index){
    var key=String(entry.image_key||'');
    var selectedClass=key===String(channelState.imageKey||'')?' selected':'';
    var optionCount=(Array.isArray(entry.options)?entry.options:[]).filter(function(option){
      return option&&option.available;
    }).length;
    var countClass=optionCount?'':' none';
    imageCards+=
      '<button class="image-source-image-card'+selectedClass+'" type="button" data-image-channel-image="'+channelEsc(key)+'"'+
      (channelState.busy?' disabled':'')+'>'+
        '<div class="image-source-image-card-head">'+
          '<div class="image-source-image-service">'+channelEsc(String(entry.service_label||('Image '+(index+1))))+'</div>'+
          '<div class="image-source-image-count'+countClass+'">'+channelEsc(
            channelText(String(optionCount)+' Kanäle',String(optionCount)+' channels')
          )+'</div>'+
        '</div>'+
        '<div class="image-source-image-ref">'+channelEsc(String(entry.image_ref||'-'))+'</div>'+
        '<div class="image-source-image-meta"><span>'+channelEsc(channelText('Update Kanal','Update channel'))+
          '</span><strong>'+channelEsc(String(entry.current_tag||'-'))+'</strong></div>'+
        '<div class="image-source-image-meta"><span>'+channelEsc(channelText('Version','Version'))+
          '</span><strong>'+channelEsc(String(entry.current_version||'-'))+'</strong></div>'+
      '</button>';
  });

  var optionHtml='';
  options.forEach(function(option){
    var tag=String(option.tag||'');
    var optionId=String(option.option_id||'');
    var selectedClass=optionId===String(channelState.choice||'')?' selected':'';
    var level=option.available?'ok':'error';
    var currentBadge=option.current
      ?'<span class="image-source-current-badge">'+channelEsc(channelText('Aktuell eingetragen','Currently configured'))+'</span>'
      :'';
    var runtimeBadge=option.source_is_runtime
      ?'<span class="image-source-current-badge">'+channelEsc(channelText('Aktiver Docker Ursprung','Active Docker source'))+'</span>'
      :'';
    var status=option.available
      ?'<span class="image-source-status ok">✓ '+channelEsc(channelText('Registry Tag vorhanden','Registry tag available'))+'</span>'
      :'<span class="image-source-status error">✕ '+channelEsc(channelText('Tag nicht verfügbar','Tag unavailable'))+'</span>';
    optionHtml+=
      '<div class="image-source-option-wrap">'+
        '<button class="image-source-option '+level+selectedClass+'" type="button" data-image-channel-choice="'+channelEsc(optionId)+'"'+
        (channelState.busy?' disabled':'')+'>'+
          '<div class="image-source-option-top"><span class="image-source-radio"></span><strong>'+channelEsc(tag)+'</strong></div>'+
          '<small>'+channelEsc(String(option.source_label||option.source_repository||''))+' · '+channelEsc(String(option.target_image||''))+'</small>'+
          currentBadge+runtimeBadge+status+
        '</button>'+
      '</div>';
  });

  var registryError=String(image&&image.registry_error||'').trim();
  var currentRef=String(image&&image.image_ref||'-');
  var targetRef=String(selected&&selected.target_image||'-');
  var actionDisabled=channelState.busy||!selected||!selected.available||!!selected.current;
  try{
    if(typeof scanMutationIsLocked==='function'&&scanMutationIsLocked()) actionDisabled=true;
  }catch(e){}

  var progressClass=channelState.busy&&channelProgressInfo&&channelProgressInfo.determinate
    ?' image-source-switch-progress':'';
  var progressStyle='';
  if(progressClass&&Number.isFinite(Number(channelProgressInfo&&channelProgressInfo.progress))){
    progressStyle=' style="--image-source-progress:'+
      Math.max(0,Math.min(100,Math.round(Number(channelProgressInfo.progress))))+'%"';
  }
  var actionText=channelState.busy?progressLabel():channelText('Update Kanal übernehmen','Apply update channel');

  var html=
    '<div class="image-source-stack-overview">'+
      '<strong>'+channelEsc(channelText(
        String(Number(data.image_count||list.length))+' Images untersucht',
        String(Number(data.image_count||list.length))+' images inspected'
      ))+'</strong>'+
      '<span>'+channelEsc(channelText(
        String(Number(data.available_channel_count||0))+' Registry Kanäle gefunden',
        String(Number(data.available_channel_count||0))+' registry channels found'
      ))+'</span>'+
    '</div>'+
    '<div class="image-source-images">'+imageCards+'</div>'+
    '<div class="image-source-selected-title">'+channelEsc(channelText('Update Kanal auswählen','Select update channel'))+
      '<span>'+channelEsc(String(image&&image.service_label||''))+'</span></div>';

  if(registryError){
    html+='<div class="image-source-alert warn visible">'+channelEsc(registryError)+'</div>';
  }

  if(options.length){
    html+='<div class="image-source-options">'+optionHtml+'</div>';
  }else{
    html+='<div class="image-source-discovery-message">'+channelEsc(channelText(
      'Keine beweglichen Update Kanäle gefunden. Konkrete Versionen bleiben unter „Auf neue Version wechseln“.',
      'No moving update channels found. Concrete versions remain under “Switch to a new version”.'
    ))+'</div>';
  }

  if(selected){
    html+=
      '<div class="image-source-preview">'+
        '<div class="image-source-preview-title">'+channelEsc(channelText('Docker Compose Vorschau','Docker Compose preview'))+'</div>'+
        '<div class="image-source-compose">'+
          '<div class="image-source-compose-label">'+channelEsc(channelText('Aktuell','Current'))+'</div>'+
          '<div class="image-source-compose-value">image: '+channelEsc(currentRef)+'</div>'+
          '<div class="image-source-compose-label">'+channelEsc(channelText('Nach Wechsel','After switch'))+'</div>'+
          '<div class="image-source-compose-value target">image: '+channelEsc(targetRef)+'</div>'+
        '</div>'+
      '</div>';
  }

  html+='<div class="image-source-backup-note">✓ '+channelEsc(channelText(
    'Vor dem Wechsel wird die komplette App gesichert. Wenn nötig werden Image Quelle und Update Kanal gemeinsam geändert und anschließend der Ziel Digest geprüft.',
    'The complete app is backed up before switching. If necessary, the image source and update channel are changed together and the target digest is then verified.'
  ))+'</div>';

  if(channelState.error){
    html+='<div class="image-source-error">'+channelEsc(channelState.error)+'</div>';
  }

  html+=
    '<div class="image-source-actions">'+
      '<button class="image-source-action secondary" id="imageChannelCancel" type="button"'+
        (channelState.busy?' disabled':'')+'>'+channelEsc(channelText('Abbrechen','Cancel'))+'</button>'+
      '<button class="image-source-action primary'+progressClass+'" id="imageChannelApply" type="button"'+progressStyle+
        (actionDisabled?' disabled':'')+'>'+channelEsc(actionText)+'</button>'+
    '</div>';

  body.innerHTML=html;

  body.querySelectorAll('[data-image-channel-image]').forEach(function(button){
    button.onclick=function(){selectImage(String(button.dataset.imageChannelImage||''));};
  });
  body.querySelectorAll('[data-image-channel-choice]').forEach(function(button){
    button.onclick=function(){
      if(channelState.busy) return;
      channelState.choice=String(button.dataset.imageChannelChoice||'');
      channelState.error='';
      renderDialog();
    };
  });
  var cancel=document.getElementById('imageChannelCancel');
  if(cancel) cancel.onclick=function(){closeDialog(false);};
  var apply=document.getElementById('imageChannelApply');
  if(apply) apply.onclick=applySwitch;
}

async function openDialog(stackKey){
  stackKey=String(stackKey||'').trim();
  if(!stackKey) return;
  channelState={stackKey:stackKey,data:null,imageKey:'',choice:'',busy:false,error:''};
  document.getElementById('imageChannelHeading').textContent=channelText('Update Kanal','Update channel');
  var subtitle=document.getElementById('imageChannelSubtitle');
  if(subtitle) subtitle.textContent='';
  backdrop.classList.add('visible');
  backdrop.setAttribute('aria-hidden','false');
  lockBackground(true);
  renderDialog();

  try{
    var data=await api('/api/image-channel-options?stack_key='+encodeURIComponent(stackKey));
    channelState.data=data;
    var list=Array.isArray(data&&data.images)?data.images:[];
    var first=list[0]||null;
    channelState.imageKey=String(first&&first.image_key||'');
    var preferred=defaultChoice(first);
    channelState.choice=String(preferred&&preferred.option_id||'');
    channelState.error='';
  }catch(err){
    channelState.error=err&&err.message==='auth'?'':channelText(
      'Update Kanäle konnten nicht geladen werden: '+String(err&&err.message||'-'),
      'Update channels could not be loaded: '+String(err&&err.message||'-')
    );
  }
  renderDialog();
}

async function waitForInterruptedChannelAction(stackKey,timeoutMs=30*60*1000){
  var key=String(stackKey||'').trim();
  if(!key) return {state:'missing',info:null};

  var deadline=Date.now()+Math.max(30000,Number(timeoutMs)||0);
  var firstMissingAt=Date.now();
  var sawAction=false;

  while(Date.now()<deadline){
    var info=await refreshProgress(key);

    if(info&&info.kind==='update'){
      sawAction=true;
      if(info.finished){
        return {state:'finished',info:info};
      }
    }else if(!sawAction&&Date.now()-firstMissingAt>=15000){
      // A gateway error can also happen before the request reaches the
      // backend. Do not keep the dialog locked forever when no action record
      // ever appeared.
      return {state:'missing',info:null};
    }

    await new Promise(function(resolve){setTimeout(resolve,750);});
  }

  return {state:'timeout',info:channelProgressInfo};
}

async function applySwitch(){
  if(channelState.busy||!channelState.data) return;
  var image=selectedImage();
  var selected=selectedOption();
  if(!image||!selected||selected.current||!selected.available) return;

  try{
    if(typeof scanMutationIsLocked==='function'&&scanMutationIsLocked()){
      channelState.error=channelText(
        'Während einer laufenden Update Prüfung können Update Kanäle angesehen, aber nicht geändert werden.',
        'During an update check, update channels can be viewed but not changed.'
      );
      renderDialog();
      return;
    }
  }catch(e){}

  var stackKey=String(channelState.stackKey||'');
  var baseline=null;
  try{
    if(typeof verificationBaseline==='function') baseline=verificationBaseline();
  }catch(e){}
  channelState.busy=true;
  channelState.error='';
  channelProgressInfo=null;
  renderDialog();
  startProgress(stackKey);

  try{
    await api('/api/image-channel-switch',{
      method:'POST',
      body:JSON.stringify({
        stack_key:stackKey,
        image_key:String(image.image_key||''),
        tag:String(selected.tag||''),
        source_id:String(selected.source_id||'')||null,
        backup_mode:'full'
      })
    });
    await refreshProgress(stackKey);
    if(channelProgressInfo&&channelProgressInfo.determinate&&Number(channelProgressInfo.progress)>=100){
      await new Promise(function(resolve){setTimeout(resolve,350);});
    }
    closeDialog(true);
    if(typeof beginVerificationState==='function') beginVerificationState(stackKey,baseline);
    if(typeof waitForPostUpdateVerification==='function') await waitForPostUpdateVerification(stackKey);
  }catch(err){
    if(err&&err.message==='auth'){
      stopProgress();
      return;
    }

    var transportFailure=false;
    try{
      transportFailure=(
        typeof isUpdateTransportFailure==='function'
        &&isUpdateTransportFailure(err)
      );
    }catch(e){}

    if(transportFailure){
      // A reverse proxy can time out while the synchronous backend request
      // keeps running. Keep the dialog locked, follow the backend action
      // progress and let the targeted verification decide the real result.
      stopProgress();
      var recovery=await waitForInterruptedChannelAction(stackKey);
      var info=recovery&&recovery.info;

      if(
        recovery&&recovery.state==='finished'
        &&info&&info.success===true
      ){
        closeDialog(true);
        if(typeof beginVerificationState==='function'){
          beginVerificationState(stackKey,baseline);
        }
        if(typeof waitForPostUpdateVerification==='function'){
          await waitForPostUpdateVerification(stackKey);
        }
        return;
      }

      channelState.busy=false;
      channelProgressInfo=null;
      var recoveryError=(
        info&&String(info.error||'').trim()
      )||(
        recovery&&recovery.state==='timeout'
        ?channelText(
          'Die Docker Aktion läuft ungewöhnlich lange. Bitte erst nach Abschluss erneut prüfen.',
          'The Docker action is taking unusually long. Please check again only after it finishes.'
        )
        :String(err&&err.message||'-')
      );
      channelState.error=channelText(
        'Update Kanal konnte nicht gewechselt werden: '+recoveryError,
        'Update channel could not be switched: '+recoveryError
      );
      renderDialog();
      return;
    }

    stopProgress();
    channelState.busy=false;
    channelProgressInfo=null;
    channelState.error=channelText(
      'Update Kanal konnte nicht gewechselt werden: '+String(err&&err.message||'-'),
      'Update channel could not be switched: '+String(err&&err.message||'-')
    );
    renderDialog();
  }
}

document.addEventListener('click',function(event){
  var button=event.target&&event.target.closest?event.target.closest('[data-update-channel-open]'):null;
  if(!button) return;
  event.preventDefault();
  event.stopPropagation();
  openDialog(String(button.dataset.updateChannelOpen||''));
});

document.getElementById('imageChannelClose').onclick=function(){closeDialog(false);};
backdrop.addEventListener('click',function(event){
  if(event.target===backdrop) closeDialog(false);
});
document.addEventListener('keydown',function(event){
  if(event.key==='Escape'&&backdrop.classList.contains('visible')) closeDialog(false);
});

window.openImageChannelDialog=openDialog;
})();