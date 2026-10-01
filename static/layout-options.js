(function(){
'use strict';

var STORAGE_KEY='updateMonitorGridLayout';
var MODES={auto:true,'6x4':true,'6x3':true};
var DEFAULT_MODE='auto';
var originalGetUpdateColumnCount=null;
var resizeTimer=null;

var texts={
  de:{label:'Darstellung',auto:'Automatisch'},
  en:{label:'Layout',auto:'Automatic'},
  fr:{label:'Affichage',auto:'Automatique'},
  pt:{label:'Disposição',auto:'Automático'},
  es:{label:'Diseño',auto:'Automático'}
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

function viewportWidth(){
  var vv=window.visualViewport;
  return Math.max(320,Math.round((vv&&vv.width)||document.documentElement.clientWidth||window.innerWidth||320));
}

function manualMode(){
  var mode=readMode();
  return (mode==='6x4'||mode==='6x3')&&viewportWidth()>=760?mode:'';
}

function applyRootMode(){
  var mode=manualMode();
  if(mode){
    document.documentElement.setAttribute('data-um-grid-layout',mode);
  }else{
    document.documentElement.removeAttribute('data-um-grid-layout');
  }
  return mode;
}

function applyCardMetrics(){
  var mode=applyRootMode();
  var root=document.documentElement;
  if(!mode){
    root.style.removeProperty('--um-manual-card-min-height');
    return;
  }

  var grid=document.querySelector('.update-grid');
  if(!grid){
    root.style.setProperty('--um-manual-card-min-height','220px');
    return;
  }

  var vv=window.visualViewport;
  var viewportHeight=Math.max(480,Math.round((vv&&vv.height)||document.documentElement.clientHeight||window.innerHeight||480));
  var top=Math.max(0,Math.round(grid.getBoundingClientRect().top));
  var available=Math.max(220,viewportHeight-top-18);
  var rows=mode==='6x3'?3:4;
  var gap=14;
  var height=Math.floor((available-gap*(rows-1))/rows);

  /* Never make a manual card smaller than the current production card. */
  height=Math.max(220,height);
  root.style.setProperty('--um-manual-card-min-height',height+'px');
}

function requestMainRender(){
  applyCardMetrics();
  try{
    if(typeof window.render==='function'){
      window.render();
      requestAnimationFrame(applyCardMetrics);
      return;
    }
  }catch(e){}

  /* The production page already recalculates its grid on resize. */
  try{window.dispatchEvent(new Event('resize'));}catch(e){}
  requestAnimationFrame(applyCardMetrics);
}

function installColumnOverride(){
  if(originalGetUpdateColumnCount)return;
  if(typeof window.getUpdateColumnCount!=='function')return;
  originalGetUpdateColumnCount=window.getUpdateColumnCount;
  window.getUpdateColumnCount=function(grid){
    if(manualMode())return 6;
    return originalGetUpdateColumnCount.call(this,grid);
  };
}

var style=document.createElement('style');
style.id='um-grid-layout-options-v1';
style.textContent=[
  'html[data-um-grid-layout="6x4"] .update-grid,html[data-um-grid-layout="6x3"] .update-grid{width:100% !important;max-width:100% !important;grid-template-columns:repeat(6,minmax(0,1fr)) !important;}',
  'html[data-um-grid-layout="6x4"] .update-column,html[data-um-grid-layout="6x3"] .update-column{min-width:0 !important;width:100% !important;}',
  'html[data-um-grid-layout="6x4"] .update-card,html[data-um-grid-layout="6x3"] .update-card{width:100% !important;max-width:none !important;min-height:var(--um-manual-card-min-height,220px) !important;}',
  '@media(max-width:759px){html[data-um-grid-layout] .update-grid{grid-template-columns:minmax(0,1fr) !important;width:100% !important;}}'
].join('');
document.head.appendChild(style);

function updateSettingText(){
  var select=document.getElementById('gridLayoutSelect');
  var label=document.getElementById('gridLayoutLabel');
  if(!select||!label)return;
  var copy=texts[language()];
  label.textContent=copy.label;
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
  setting.className='setting';
  setting.id='gridLayoutSetting';
  setting.innerHTML='\
    <label id="gridLayoutLabel" for="gridLayoutSelect">Darstellung</label>\
    <select id="gridLayoutSelect" class="settings-native-select">\
      <option value="auto">Automatisch</option>\
      <option value="6x4">6 × 4</option>\
      <option value="6x3">6 × 3</option>\
    </select>';
  anchorSetting.insertAdjacentElement('afterend',setting);

  var select=document.getElementById('gridLayoutSelect');
  select.value=readMode();
  select.addEventListener('change',function(){
    var mode=String(select.value||DEFAULT_MODE);
    if(!MODES[mode])mode=DEFAULT_MODE;
    localStorage.setItem(STORAGE_KEY,mode);
    applyRootMode();
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
  installColumnOverride();
  applyRootMode();
  ensureSetting();
  requestAnimationFrame(applyCardMetrics);

  var languageSelect=document.getElementById('languageSelect');
  if(languageSelect){
    languageSelect.addEventListener('change',function(){setTimeout(updateSettingText,0);});
  }

}

window.addEventListener('resize',function(){
  clearTimeout(resizeTimer);
  resizeTimer=setTimeout(function(){
    applyCardMetrics();
  },80);
},{passive:true});

if(window.visualViewport){
  window.visualViewport.addEventListener('resize',function(){
    clearTimeout(resizeTimer);
    resizeTimer=setTimeout(applyCardMetrics,80);
  },{passive:true});
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',initialize,{once:true});
}else{
  initialize();
}
})();
