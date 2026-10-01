(function(){
'use strict';

var STORAGE_KEY='updateMonitorGridLayout';
var CUSTOM_KEY='updateMonitorGridCustomPercent';
var MODES={auto:true,'105':true,'110':true,'115':true,'120':true,'125':true,'130':true,manual:true};
var DEFAULT_MODE='auto';
var CUSTOM_MIN=105;
var CUSTOM_MAX=150;
var CUSTOM_DEFAULT=110;
var lastAppliedScale=null;

var texts={
  de:{label:'Darstellung',auto:'Automatisch',manual:'Manuell',custom:'Eigener Wert',hint:'Automatisch wird je nach Displaygröße angepasst.'},
  en:{label:'Layout',auto:'Automatic',manual:'Manual',custom:'Custom value',hint:'Automatic adjusts according to the display size.'},
  fr:{label:'Affichage',auto:'Automatique',manual:'Manuel',custom:'Valeur personnalisée',hint:'Automatique s’adapte à la taille de l’écran.'},
  pt:{label:'Disposição',auto:'Automático',manual:'Manual',custom:'Valor personalizado',hint:'Automático adapta-se ao tamanho do ecrã.'},
  es:{label:'Diseño',auto:'Automático',manual:'Manual',custom:'Valor personalizado',hint:'Automático se adapta al tamaño de la pantalla.'}
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

function clampCustom(value){
  var parsed=parseInt(value,10);
  if(!Number.isFinite(parsed))parsed=CUSTOM_DEFAULT;
  return Math.max(CUSTOM_MIN,Math.min(CUSTOM_MAX,parsed));
}

function readCustom(){
  return clampCustom(localStorage.getItem(CUSTOM_KEY));
}

function writeCustom(value){
  var normalized=clampCustom(value);
  localStorage.setItem(CUSTOM_KEY,String(normalized));
  return normalized;
}

function activeScale(){
  var mode=readMode();
  /* Automatic means: do not override the production layout at all. */
  if(mode==='auto')return '';
  if(mode==='manual')return String(readCustom());
  return mode;
}

function applyScale(){
  var scale=activeScale();
  var root=document.documentElement;
  if(scale){
    var numeric=clampCustom(scale);
    root.setAttribute('data-um-card-scale',String(numeric));
    root.style.setProperty('--um-manual-ui-scale',(numeric/100).toFixed(2));
    root.style.setProperty('--um-manual-main-width',(10000/numeric).toFixed(4)+'%');
  }else{
    root.removeAttribute('data-um-card-scale');
    root.style.removeProperty('--um-manual-ui-scale');
    root.style.removeProperty('--um-manual-main-width');
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
style.id='um-card-scale-options-v5';
style.textContent=[
  'html[data-um-card-scale] main{zoom:var(--um-manual-ui-scale);width:var(--um-manual-main-width) !important;}',
  'html[data-um-card-scale] [class*="-backdrop"]>[role="dialog"][aria-modal="true"]{zoom:var(--um-manual-ui-scale);}',
  '#gridLayoutSetting.um-card-scale-setting{height:auto !important;min-height:0 !important;flex-wrap:wrap !important;align-items:center !important;}',
  '#gridLayoutSetting .um-card-custom-wrap{flex:0 0 100%;width:100%;display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:8px;}',
  '#gridLayoutSetting .um-card-custom-wrap[hidden]{display:none !important;}',
  '#gridLayoutSetting .um-card-custom-label{font-size:12px;line-height:1.2;opacity:.78;}',
  '#gridLayoutSetting .um-card-custom-input-wrap{display:flex;align-items:center;gap:5px;}',
  '#gridLayoutSetting .um-card-custom-input{width:72px;height:32px;padding:0 8px;border:1px solid var(--border);border-radius:6px;background:rgba(13,21,29,.72);color:var(--text);font:inherit;text-align:right;outline:none;}',
  '#gridLayoutSetting .um-card-custom-input:focus{border-color:rgba(91,156,255,.72);box-shadow:0 0 0 2px rgba(91,156,255,.12);}',
  '#gridLayoutSetting .um-card-custom-unit{font-size:12px;opacity:.78;}',
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

function syncCustomVisibility(){
  var wrap=document.getElementById('gridLayoutCustomWrap');
  var input=document.getElementById('gridLayoutCustomInput');
  if(!wrap||!input)return;
  var manual=readMode()==='manual';
  wrap.hidden=!manual;
  input.value=String(readCustom());
}

function updateSettingText(){
  var select=document.getElementById('gridLayoutSelect');
  var label=document.getElementById('gridLayoutLabel');
  var hint=document.getElementById('gridLayoutHint');
  var customLabel=document.getElementById('gridLayoutCustomLabel');
  var customInput=document.getElementById('gridLayoutCustomInput');
  if(!select||!label)return;
  var copy=texts[language()];
  label.textContent=copy.label;
  if(hint)hint.textContent=copy.hint;
  if(customLabel)customLabel.textContent=copy.custom;
  if(customInput)customInput.setAttribute('aria-label',copy.custom);
  var auto=select.querySelector('option[value="auto"]');
  var manual=select.querySelector('option[value="manual"]');
  if(auto)auto.textContent=copy.auto;
  if(manual)manual.textContent=copy.manual;
  select.value=readMode();
  try{
    if(typeof window.syncSettingsCustomSelect==='function')window.syncSettingsCustomSelect(select);
  }catch(e){}
  fixLayoutSelectPopup();
  syncCustomVisibility();
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
      <option value="manual">Manuell</option>\
    </select>\
    <div id="gridLayoutCustomWrap" class="um-card-custom-wrap" hidden>\
      <span id="gridLayoutCustomLabel" class="um-card-custom-label">Eigener Wert</span>\
      <span class="um-card-custom-input-wrap">\
        <input id="gridLayoutCustomInput" class="um-card-custom-input" type="number" min="105" max="150" step="1" inputmode="numeric" value="110" aria-label="Eigener Wert">\
        <span class="um-card-custom-unit">%</span>\
      </span>\
    </div>\
    <div id="gridLayoutHint" class="um-card-scale-hint">Automatisch wird je nach Displaygröße angepasst.</div>';
  anchorSetting.insertAdjacentElement('afterend',setting);

  var select=document.getElementById('gridLayoutSelect');
  var input=document.getElementById('gridLayoutCustomInput');
  select.value=readMode();
  select.addEventListener('change',function(){
    var previous=readMode();
    var mode=String(select.value||DEFAULT_MODE);
    if(!MODES[mode])mode=DEFAULT_MODE;
    if(mode==='manual'&&!localStorage.getItem(CUSTOM_KEY)&&/^\d+$/.test(previous)){
      writeCustom(previous);
    }
    localStorage.setItem(STORAGE_KEY,mode);
    syncCustomVisibility();
    requestMainRender();
    updateSettingText();
  });

  if(input){
    input.value=String(readCustom());
    input.addEventListener('change',function(){
      input.value=String(writeCustom(input.value));
      if(readMode()==='manual'){
        lastAppliedScale=null;
        requestMainRender();
      }
    });
    input.addEventListener('keydown',function(event){
      if(event.key==='Enter'){
        event.preventDefault();
        input.blur();
      }
    });
  }

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
  if(!MODES[String(localStorage.getItem(STORAGE_KEY)||'').trim().toLowerCase()]){
    localStorage.setItem(STORAGE_KEY,DEFAULT_MODE);
  }
  if(!localStorage.getItem(CUSTOM_KEY))writeCustom(CUSTOM_DEFAULT);
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