(function(){
'use strict';

var STORAGE_KEY='updateMonitorGridLayout';
var MODES={auto:true,'105':true,'110':true,'115':true,'120':true,'125':true,'130':true};
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
  /* Automatic means: do not override the production layout at all. */
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
  try{window.dispatchEvent(new Event('resize'));}catch(e){}
}

var style=document.createElement('style');
style.id='um-card-scale-options-v4';
style.textContent=[
  'html[data-um-card-scale="105"]{--um-manual-ui-scale:1.05;--um-manual-main-width:95.2381%;}',
  'html[data-um-card-scale="110"]{--um-manual-ui-scale:1.10;--um-manual-main-width:90.9091%;}',
  'html[data-um-card-scale="115"]{--um-manual-ui-scale:1.15;--um-manual-main-width:86.9565%;}',
  'html[data-um-card-scale="120"]{--um-manual-ui-scale:1.20;--um-manual-main-width:83.3333%;}',
  'html[data-um-card-scale="125"]{--um-manual-ui-scale:1.25;--um-manual-main-width:80%;}',
  'html[data-um-card-scale="130"]{--um-manual-ui-scale:1.30;--um-manual-main-width:76.9231%;}',
  'html[data-um-card-scale] main{zoom:var(--um-manual-ui-scale);width:var(--um-manual-main-width) !important;}',
  'html[data-um-card-scale] [class*="-backdrop"]>[role="dialog"][aria-modal="true"]{zoom:var(--um-manual-ui-scale);}',
  '#gridLayoutSetting.um-card-scale-setting{height:auto !important;min-height:0 !important;flex-wrap:wrap !important;align-items:center !important;}',
  '#gridLayoutSetting .um-card-scale-hint{flex:0 0 100%;width:100%;box-sizing:border-box;margin-top:7px;font-size:12px;line-height:1.35;opacity:.68;}'
].join('');
document.head.appendChild(style);

function fixLayoutSelectPopup(){
  var select=document.getElementById('gridLayoutSelect');
  if(!select)return;
  var wrapper=select.nextElementSibling;
  if(wrapper&&wrapper.classList&&wrapper.classList.contains('settings-custom-select')){
    wrapper.classList.add('settings-interval-fixed');
  }
}

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
  fixLayoutSelectPopup();
}

function ensureSetting(){
  if(document.getElementById('gridLayoutSelect')){
    updateSettingText();
    fixLayoutSelectPopup();
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
      <option value="105">105 %</option>\
      <option value="110">110 %</option>\
      <option value="115">115 %</option>\
      <option value="120">120 %</option>\
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
      fixLayoutSelectPopup();
    }else{
      select.style.display='block';
    }
  }catch(e){
    select.style.display='block';
  }
  updateSettingText();
}

function initialize(){
  /* Old/invalid values, including the removed 100% option, fall back to Automatic. */
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