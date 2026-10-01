(function(){
'use strict';

var STORAGE_KEY='updateMonitorGridLayout';
var MODES={auto:true,'100':true,'110':true,'125':true,'130':true};
var DEFAULT_MODE='auto';
var lastAppliedScale=null;

var texts={
  de:{label:'Darstellung',auto:'Automatisch',hint:'Automatisch wird je nach Displaygröße angepasst.'},
  en:{label:'Layout',auto:'Automatic',hint:'Automatic adjusts according to the display size.'},
  fr:{label:'Affichage',auto:'Automatique',hint:'Automatique s’adapte à la taille de l’écran.'},
  pt:{label:'Disposição',auto:'Automático',hint:'Automático adapta-se ao tamanho do ecrã.'},
  es:{label:'Diseño',auto:'Automático',hint:'Automático se adapta al tamaño de la pantalla.'}
};

function language(){
  var value=String(document.documentElement.lang||localStorage.getItem('updateMonitorLanguage')||'de')
    .trim().toLowerCase();
  if(value.indexOf('-')>0)value=value.split('-')[0];
  return texts[value]?value:'en';
}

function readMode(){
  var value=String(localStorage.getItem(STORAGE_KEY)||DEFAULT_MODE).trim().toLowerCase();
  return MODES[value]?value:DEFAULT_MODE;
}

function activeScale(){
  var mode=readMode();
  /* Automatic means: do not override the production grid at all. */
  return mode==='auto'?'':mode;
}

function applyScale(){
  var scale=activeScale();
  if(scale){
    document.documentElement.setAttribute('data-um-card-scale',scale);
  }else{
    document.documentElement.removeAttribute('data-um-card-scale');
  }
  if(lastAppliedScale===scale)return false;
  lastAppliedScale=scale;
  return true;
}

function requestMainRender(){
  var changed=applyScale();
  if(!changed)return;
  try{
    if(typeof window.render==='function')window.render();
  }catch(e){}
  /* Let the original Update Monitor responsive code recalculate its layout. */
  try{window.dispatchEvent(new Event('resize'));}catch(e){}
}

var style=document.createElement('style');
style.id='um-card-scale-options-v3';
style.textContent=[
  'html[data-um-card-scale="100"] .update-grid{zoom:1;width:100% !important;max-width:100% !important;}',
  'html[data-um-card-scale="110"] .update-grid{zoom:1.10;width:90.9091% !important;max-width:none !important;}',
  'html[data-um-card-scale="125"] .update-grid{zoom:1.25;width:80% !important;max-width:none !important;}',
  'html[data-um-card-scale="130"] .update-grid{zoom:1.30;width:76.9231% !important;max-width:none !important;}',
  '#gridLayoutSetting.um-card-scale-setting{height:auto !important;min-height:0 !important;flex-wrap:wrap !important;align-items:center !important;}',
  '#gridLayoutSetting .um-card-scale-hint{flex:0 0 100%;width:100%;box-sizing:border-box;margin-top:7px;font-size:12px;line-height:1.35;opacity:.68;}',
  '@media(max-width:759px){html[data-um-card-scale] .update-grid{zoom:1 !important;width:100% !important;max-width:100% !important;}}'
].join('');
document.head.appendChild(style);

function updateSettingText(){
  var select=document.getElementById('gridLayoutSelect');
  var label=document.getElementById('gridLayoutLabel');
  var hint=document.getElementById('gridLayoutHint');
  if(!select||!label)return;
  var copy=texts[language()];
  label.textContent=copy.label;
  if(hint)hint.textContent=copy.hint;
  var auto=select.querySelector('option[value="auto"]');
  if(auto)auto.textContent=copy.auto;
  select.value=readMode();
  try{
    if(typeof window.syncSettingsCustomSelect==='function')window.syncSettingsCustomSelect(select);
  }catch(e){}
}

function ensureSetting(){
  if(document.getElementById('gridLayoutSelect')){
    updateSettingText();
    return;
  }
  var anchor=document.getElementById('timeFormatSelect');
  if(!anchor)return;
  var anchorSetting=anchor.closest('.setting');
  if(!anchorSetting)return;

  var setting=document.createElement('div');
  setting.className='setting um-card-scale-setting';
  setting.id='gridLayoutSetting';
  setting.innerHTML='\
    <label id="gridLayoutLabel" for="gridLayoutSelect">Darstellung</label>\
    <select id="gridLayoutSelect" class="settings-native-select">\
      <option value="auto">Automatisch</option>\
      <option value="100">100 %</option>\
      <option value="110">110 %</option>\
      <option value="125">125 %</option>\
      <option value="130">130 %</option>\
    </select>\
    <div id="gridLayoutHint" class="um-card-scale-hint">Automatisch wird je nach Displaygröße angepasst.</div>';
  anchorSetting.insertAdjacentElement('afterend',setting);

  var select=document.getElementById('gridLayoutSelect');
  select.value=readMode();
  select.addEventListener('change',function(){
    var mode=String(select.value||DEFAULT_MODE);
    if(!MODES[mode])mode=DEFAULT_MODE;
    localStorage.setItem(STORAGE_KEY,mode);
    requestMainRender();
    updateSettingText();
  });

  try{
    if(typeof window.createSettingsCustomSelect==='function'){
      window.createSettingsCustomSelect(select);
    }else{
      select.style.display='block';
    }
  }catch(e){
    select.style.display='block';
  }
  updateSettingText();
}

function initialize(){
  /* Old/invalid values automatically fall back to the original automatic layout. */
  if(!MODES[String(localStorage.getItem(STORAGE_KEY)||'').trim().toLowerCase()]){
    localStorage.setItem(STORAGE_KEY,DEFAULT_MODE);
  }
  requestMainRender();
  ensureSetting();

  var languageSelect=document.getElementById('languageSelect');
  if(languageSelect){
    languageSelect.addEventListener('change',function(){setTimeout(updateSettingText,0);});
  }
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',initialize,{once:true});
}else{
  initialize();
}
})();