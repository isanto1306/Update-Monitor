(function(){
'use strict';

var STORAGE_KEY='updateMonitorGridLayout';
var CUSTOM_KEY='updateMonitorGridCustomPercent';
var MODES={auto:true,manual:true};
var DEFAULT_MODE='auto';
var CUSTOM_MIN=105;
var CUSTOM_MAX=150;
var CUSTOM_DEFAULT=110;
var SAFE_EDGE_PX=40;
var lastAppliedScale=null;

var texts={
  de:{label:'Darstellung',auto:'Automatisch',manual:'Manuell',custom:'Eigener Wert',increase:'Prozent erhöhen',decrease:'Prozent verringern',hint:'Automatisch wird je nach Displaygröße angepasst.'},
  en:{label:'Layout',auto:'Automatic',manual:'Manual',custom:'Custom value',increase:'Increase percentage',decrease:'Decrease percentage',hint:'Automatic adjusts according to the display size.'},
  fr:{label:'Affichage',auto:'Automatique',manual:'Manuel',custom:'Valeur personnalisée',increase:'Augmenter le pourcentage',decrease:'Diminuer le pourcentage',hint:'Automatique s’adapte à la taille de l’écran.'},
  pt:{label:'Disposição',auto:'Automático',manual:'Manual',custom:'Valor personalizado',increase:'Aumentar percentagem',decrease:'Diminuir percentagem',hint:'Automático adapta-se ao tamanho do ecrã.'},
  es:{label:'Diseño',auto:'Automático',manual:'Manual',custom:'Valor personalizado',increase:'Aumentar porcentaje',decrease:'Disminuir porcentaje',hint:'Automático se adapta al tamaño de la pantalla.'}
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
  var parsed=parseInt(String(value||'').replace(/[^0-9]/g,''),10);
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
  return String(readCustom());
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

function installManualColumnBoundary(){
  if(window.__umManualColumnBoundaryInstalled)return;
  var original=window.getUpdateColumnCount;
  if(typeof original!=='function')return;
  window.__umManualColumnBoundaryInstalled=true;
  window.getUpdateColumnCount=function(grid){
    if(readMode()!=='manual')return original(grid);
    var vv=window.visualViewport;
    var viewportWidth=Math.max(
      320,
      Math.round((vv&&vv.width)||document.documentElement.clientWidth||window.innerWidth||320)
    );
    if(viewportWidth<760)return 1;
    var scale=readCustom()/100;
    var available=Math.max(0,viewportWidth-(SAFE_EDGE_PX*2));
    var cardWidth=318*scale;
    var gap=14*scale;
    var fit=Math.floor((available+gap)/(cardWidth+gap));
    return Math.max(1,Math.min(5,fit));
  };
}

var style=document.createElement('style');
style.id='um-card-scale-options-v10';
style.textContent=[
  'html[data-um-card-scale] main{zoom:var(--um-manual-ui-scale);width:var(--um-manual-main-width) !important;}',
  'html[data-um-card-scale] [class*="-backdrop"]>[role="dialog"][aria-modal="true"]{zoom:var(--um-manual-ui-scale);}',
  '#gridLayoutSetting.um-card-scale-setting{height:auto !important;min-height:0 !important;}',
  '#gridLayoutSetting .settings-custom-select{width:100% !important;display:block !important;position:relative !important;}',
  '#gridLayoutSetting.manual-active .settings-custom-select-button{padding-right:164px !important;}',
  '#gridLayoutSetting .um-card-custom-wrap{position:absolute;right:38px;top:50%;transform:translateY(-50%);z-index:4;display:flex;align-items:center;height:30px;font:inherit;font-size:inherit;font-weight:inherit;}',
  '#gridLayoutSetting .um-card-custom-wrap[hidden]{display:none !important;}',
  '#gridLayoutSetting .um-card-custom-input{width:44px;height:30px;padding:0;border:0 !important;border-radius:0;background:transparent !important;color:var(--text);font:inherit;font-size:inherit;font-weight:inherit;line-height:30px;text-align:right;outline:none !important;box-shadow:none !important;-moz-appearance:textfield;appearance:textfield;}',
  '#gridLayoutSetting .um-card-custom-input::-webkit-inner-spin-button,#gridLayoutSetting .um-card-custom-input::-webkit-outer-spin-button{-webkit-appearance:none;margin:0;}',
  '#gridLayoutSetting .um-card-custom-unit{margin-left:2px;font:inherit;font-size:inherit;font-weight:inherit;line-height:1;color:var(--text);pointer-events:none;}',
  '#gridLayoutSetting .um-card-custom-spinner{display:flex;flex-direction:column;justify-content:center;gap:2px;margin-left:13px;height:28px;}',
  '#gridLayoutSetting .um-card-custom-step{width:18px;height:12px;padding:0;border:0;background:transparent;color:#d7e0e8;display:flex;align-items:center;justify-content:center;font-size:9px;line-height:1;cursor:pointer;opacity:.92;}',
  '#gridLayoutSetting .um-card-custom-step:hover{color:#ffffff;opacity:1;}',
  '#gridLayoutSetting .um-card-custom-step:focus-visible{outline:1px solid rgba(91,156,255,.65);outline-offset:1px;border-radius:2px;}',
  '#gridLayoutSetting .um-card-scale-hint{width:100%;box-sizing:border-box;margin-top:7px;font-size:12px;line-height:1.35;opacity:.68;}'
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

function attachCustomValueToSelector(){
  var select=document.getElementById('gridLayoutSelect');
  var wrap=document.getElementById('gridLayoutCustomWrap');
  if(!select||!wrap)return;
  var wrapper=select.nextElementSibling;
  if(wrapper&&wrapper.classList&&wrapper.classList.contains('settings-custom-select')&&wrap.parentElement!==wrapper){
    wrapper.appendChild(wrap);
  }
}

function syncCustomVisibility(){
  var setting=document.getElementById('gridLayoutSetting');
  var wrap=document.getElementById('gridLayoutCustomWrap');
  var input=document.getElementById('gridLayoutCustomInput');
  if(!setting||!wrap||!input)return;
  var manual=readMode()==='manual';
  setting.classList.toggle('manual-active',manual);
  wrap.hidden=!manual;
  if(document.activeElement!==input)input.value=String(readCustom());
}

function updateSettingText(){
  var select=document.getElementById('gridLayoutSelect');
  var label=document.getElementById('gridLayoutLabel');
  var hint=document.getElementById('gridLayoutHint');
  var customInput=document.getElementById('gridLayoutCustomInput');
  var increase=document.getElementById('gridLayoutCustomIncrease');
  var decrease=document.getElementById('gridLayoutCustomDecrease');
  if(!select||!label)return;
  var copy=texts[language()];
  label.textContent=copy.label;
  if(hint)hint.textContent=copy.hint;
  if(customInput)customInput.setAttribute('aria-label',copy.custom);
  if(increase){increase.setAttribute('aria-label',copy.increase);increase.title=copy.increase;}
  if(decrease){decrease.setAttribute('aria-label',copy.decrease);decrease.title=copy.decrease;}
  var auto=select.querySelector('option[value="auto"]');
  var manual=select.querySelector('option[value="manual"]');
  if(auto)auto.textContent=copy.auto;
  if(manual)manual.textContent=copy.manual;
  select.value=readMode();
  try{
    if(typeof window.syncSettingsCustomSelect==='function')window.syncSettingsCustomSelect(select);
  }catch(e){}
  fixLayoutSelectPopup();
  attachCustomValueToSelector();
  syncCustomVisibility();
}

function applyCustomValue(value){
  var input=document.getElementById('gridLayoutCustomInput');
  var normalized=writeCustom(value);
  if(input)input.value=String(normalized);
  if(readMode()==='manual'){
    lastAppliedScale=null;
    requestMainRender();
  }
  return normalized;
}

function ensureSetting(){
  if(document.getElementById('gridLayoutSelect')){
    updateSettingText();
    fixLayoutSelectPopup();
    attachCustomValueToSelector();
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
      <option value="manual">Manuell</option>\
    </select>\
    <span id="gridLayoutCustomWrap" class="um-card-custom-wrap" hidden>\
      <input id="gridLayoutCustomInput" class="um-card-custom-input" type="number" min="105" max="150" step="1" inputmode="numeric" value="110" aria-label="Eigener Wert">\
      <span class="um-card-custom-unit">%</span>\
      <span class="um-card-custom-spinner">\
        <button id="gridLayoutCustomIncrease" class="um-card-custom-step" type="button" aria-label="Prozent erhöhen">▲</button>\
        <button id="gridLayoutCustomDecrease" class="um-card-custom-step" type="button" aria-label="Prozent verringern">▼</button>\
      </span>\
    </span>\
    <div id="gridLayoutHint" class="um-card-scale-hint">Automatisch wird je nach Displaygröße angepasst.</div>';
  anchorSetting.insertAdjacentElement('afterend',setting);

  var select=document.getElementById('gridLayoutSelect');
  var input=document.getElementById('gridLayoutCustomInput');
  var increase=document.getElementById('gridLayoutCustomIncrease');
  var decrease=document.getElementById('gridLayoutCustomDecrease');
  select.value=readMode();
  select.addEventListener('change',function(){
    var mode=String(select.value||DEFAULT_MODE);
    if(!MODES[mode])mode=DEFAULT_MODE;
    localStorage.setItem(STORAGE_KEY,mode);
    attachCustomValueToSelector();
    syncCustomVisibility();
    lastAppliedScale=null;
    requestMainRender();
    updateSettingText();
  });

  if(input){
    input.value=String(readCustom());
    input.addEventListener('click',function(event){event.stopPropagation();});
    input.addEventListener('change',function(){applyCustomValue(input.value);});
    input.addEventListener('keydown',function(event){
      event.stopPropagation();
      if(event.key==='Enter'){
        event.preventDefault();
        input.blur();
      }
    });
  }

  function stepCustom(delta,event){
    if(event){event.preventDefault();event.stopPropagation();}
    applyCustomValue(readCustom()+delta);
  }
  if(increase)increase.addEventListener('click',function(event){stepCustom(1,event);});
  if(decrease)decrease.addEventListener('click',function(event){stepCustom(-1,event);});

  try{
    if(typeof window.createSettingsCustomSelect==='function'){
      window.createSettingsCustomSelect(select);
      fixLayoutSelectPopup();
      attachCustomValueToSelector();
    }else{
      select.style.display='block';
    }
  }catch(e){
    select.style.display='block';
  }
  updateSettingText();
}

function migrateLegacyMode(){
  var saved=String(localStorage.getItem(STORAGE_KEY)||'').trim().toLowerCase();
  if(MODES[saved])return;
  if(/^\d+$/.test(saved)){
    writeCustom(saved);
    localStorage.setItem(STORAGE_KEY,'manual');
    return;
  }
  localStorage.setItem(STORAGE_KEY,DEFAULT_MODE);
}

function initialize(){
  migrateLegacyMode();
  if(!localStorage.getItem(CUSTOM_KEY))writeCustom(CUSTOM_DEFAULT);
  installManualColumnBoundary();
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