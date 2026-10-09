/* All-Docker backup overview. Kept separate from existing restore dialogs. */
(function(){
  'use strict';
  const I18N={
    de:{open:'Backup Verwaltung',title:'Backup Verwaltung',description:'Alle vorhandenen Docker Backups',sort:'Sortieren nach',name:'Name',date:'Datum',size:'Größe',type:'Typ',actions:'Aktionen',latest:'Neueste zuerst',direction:'Sortierreihenfolge wechseln',close:'Schließen',loading:'Backups werden geladen …',empty:'Keine Backups vorhanden.',count:'Backups',total:'Gesamtgröße',quick:'Schnellbackup',full:'Vollbackup',encrypted:'Verschlüsselt',delete:'Backup löschen',confirm:'Dieses Backup von {name} vom {date} wirklich dauerhaft löschen?',failed:'Backups konnten nicht geladen werden:',deleteFailed:'Backup konnte nicht gelöscht werden:',deleted:'Backup wurde gelöscht.',locked:'Während einer Prüfung können keine Backups gelöscht werden.',unknown:'Nicht ermittelt'},
    en:{open:'Backup manager',title:'Backup manager',description:'All available Docker backups',sort:'Sort by',name:'Name',date:'Date',size:'Size',type:'Type',actions:'Actions',latest:'Newest first',direction:'Reverse sort order',close:'Close',loading:'Loading backups…',empty:'No backups available.',count:'Backups',total:'Total size',quick:'Quick backup',full:'Full backup',encrypted:'Encrypted',delete:'Delete backup',confirm:'Permanently delete the backup of {name} from {date}?',failed:'Could not load backups:',deleteFailed:'Could not delete backup:',deleted:'Backup deleted.',locked:'Backups cannot be deleted during a scan.',unknown:'Unknown'},
    fr:{open:'Gestion des sauvegardes',title:'Gestion des sauvegardes',description:'Toutes les sauvegardes Docker',sort:'Trier par',name:'Nom',date:'Date',size:'Taille',type:'Type',actions:'Actions',latest:'Plus récentes',direction:'Inverser le tri',close:'Fermer',loading:'Chargement des sauvegardes…',empty:'Aucune sauvegarde disponible.',count:'Sauvegardes',total:'Taille totale',quick:'Sauvegarde rapide',full:'Sauvegarde complète',encrypted:'Chiffrée',delete:'Supprimer',confirm:'Supprimer définitivement la sauvegarde de {name} du {date} ?',failed:'Impossible de charger les sauvegardes :',deleteFailed:'Impossible de supprimer la sauvegarde :',deleted:'Sauvegarde supprimée.',locked:'Suppression impossible pendant une analyse.',unknown:'Inconnu'},
    pt:{open:'Gestão de backups',title:'Gestão de backups',description:'Todos os backups Docker disponíveis',sort:'Ordenar por',name:'Nome',date:'Data',size:'Tamanho',type:'Tipo',actions:'Ações',latest:'Mais recentes',direction:'Inverter ordem',close:'Fechar',loading:'A carregar backups…',empty:'Não há backups.',count:'Backups',total:'Tamanho total',quick:'Backup rápido',full:'Backup completo',encrypted:'Encriptado',delete:'Eliminar backup',confirm:'Eliminar definitivamente o backup de {name} de {date}?',failed:'Não foi possível carregar os backups:',deleteFailed:'Não foi possível eliminar o backup:',deleted:'Backup eliminado.',locked:'Não é possível eliminar backups durante uma verificação.',unknown:'Desconhecido'},
    es:{open:'Gestión de copias',title:'Gestión de copias',description:'Todas las copias de seguridad de Docker',sort:'Ordenar por',name:'Nombre',date:'Fecha',size:'Tamaño',type:'Tipo',actions:'Acciones',latest:'Más recientes',direction:'Invertir orden',close:'Cerrar',loading:'Cargando copias…',empty:'No hay copias de seguridad.',count:'Copias',total:'Tamaño total',quick:'Copia rápida',full:'Copia completa',encrypted:'Cifrada',delete:'Eliminar copia',confirm:'¿Eliminar definitivamente la copia de {name} del {date}?',failed:'No se pudieron cargar las copias:',deleteFailed:'No se pudo eliminar la copia:',deleted:'Copia eliminada.',locked:'No se pueden eliminar copias durante una revisión.',unknown:'Desconocido'}
  };
  const $=id=>document.getElementById(id);
  const locale=()=>{try{return (typeof state!=='undefined'&&state.language)||'de';}catch(_){return 'de';}};
  const tr=key=>(I18N[locale()]||I18N.en)[key]||I18N.en[key]||key;
  const safe=value=>String(value==null?'':value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const sizeOf=value=>{const n=Number(value);if(value==null||!Number.isFinite(n)||n<0)return tr('unknown');let x=n,i=0;const units=['B','KB','MB','GB','TB'];while(x>=1024&&i<4){x/=1024;i++;}return (i===0?x.toFixed(0):x.toFixed(x>=100?0:1))+' '+units[i];};
  const dateOf=value=>{const d=new Date(value||'');return Number.isNaN(d.getTime())?'-':d.toLocaleString(({de:'de-DE',en:'en-US',fr:'fr-FR',pt:'pt-PT',es:'es-ES'})[locale()]||'de-DE',{dateStyle:'short',timeStyle:'short'});};
  let rows=[],sort='date',asc=false,opened=false,busy=false,notice='';
  function scanLocked(){return document.body.classList.contains('scan-ui-locked');}
  const css=document.createElement('style');
  css.id='umBackupManagerStyle';
  css.textContent='#umBackupManagerButton{display:inline-flex;align-items:center;justify-content:center;cursor:pointer;min-width:43px;min-height:37px}#umBackupManagerButton svg{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}#umBackupManagerBackdrop{position:fixed;inset:0;z-index:20080;background:rgba(2,7,11,.76);display:none;align-items:center;justify-content:center;padding:14px;box-sizing:border-box}#umBackupManagerBackdrop.visible{display:flex}#umBackupManagerDialog{box-sizing:border-box;width:min(890px,100%);max-height:calc(100vh - 28px);display:flex;flex-direction:column;min-height:220px;background:#101922;color:#e3ecf2;border:1px solid rgba(91,156,255,.38);border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.48);padding:18px;gap:12px}#umBackupManagerDialog button,#umBackupManagerDialog select{font:inherit}#umBackupManagerTop{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}#umBackupManagerTitle{font-size:17px;font-weight:800;margin:0}#umBackupManagerSubtitle{font-size:12px;color:#91a5b4;margin:4px 0 0}#umBackupManagerClose{background:transparent;border:0;border-radius:6px;color:#afc0ce;font-size:25px;width:30px;height:30px;cursor:pointer}#umBackupManagerClose:hover{background:rgba(91,156,255,.16);color:white}#umBackupManagerToolbar{display:flex;align-items:center;flex-wrap:wrap;gap:10px;justify-content:space-between}#umBackupManagerStats{font-size:12px;color:#a6bdcf}#umBackupManagerControls{display:flex;align-items:center;gap:8px}#umBackupManagerControls label{font-size:12px;color:#a6bdcf}#umBackupManagerControls select,#umBackupManagerReverse{height:33px;border:1px solid rgba(108,147,179,.34);border-radius:6px;background:#182532;color:#e3ecf2;padding:0 9px}#umBackupManagerReverse{cursor:pointer;min-width:34px}#umBackupManagerScroll{min-height:100px;overflow:auto;overscroll-behavior:contain}#umBackupManagerTable{width:100%;border-collapse:collapse;font-size:12px}#umBackupManagerTable th{text-align:left;color:#91a5b4;font-weight:650;padding:10px 9px;border-bottom:1px solid rgba(112,137,160,.25);white-space:nowrap;position:sticky;top:0;background:#101922}#umBackupManagerTable td{padding:11px 9px;border-bottom:1px solid rgba(112,137,160,.16);vertical-align:middle}#umBackupManagerTable td:nth-child(3),#umBackupManagerTable td:nth-child(4){white-space:nowrap}#umBackupManagerTable .um-bm-name{font-weight:740;color:#dceaf3;overflow-wrap:anywhere}#umBackupManagerTable .um-bm-meta{font-size:10px;color:#8da3b5;margin-top:4px}#umBackupManagerTable .um-bm-delete{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;border:1px solid rgba(209,111,111,.35);background:transparent;color:#ef9f9b;font-size:19px;cursor:pointer}#umBackupManagerTable .um-bm-delete:hover:not(:disabled){background:rgba(173,53,53,.22);color:#ffd0cc}#umBackupManagerTable .um-bm-delete:disabled{opacity:.3;cursor:not-allowed}#umBackupManagerEmpty{padding:30px 5px;color:#96abba;text-align:center;font-size:13px}#umBackupManagerNotice{font-size:12px;min-height:16px;color:#f1ada5}#umBackupManagerNotice.ok{color:#7cd9a8}@media(max-width:650px){#umBackupManagerDialog{padding:12px;max-height:calc(100dvh - 20px)}#umBackupManagerTable th,#umBackupManagerTable td{padding:8px 5px;font-size:11px}#umBackupManagerTable th:nth-child(4),#umBackupManagerTable td:nth-child(4){display:none}#umBackupManagerControls label{display:none}}';
  document.head.appendChild(css);

  function sortedRows(){
    const arr=rows.slice(),factor=asc?1:-1;
    const collator=new Intl.Collator(locale(),{numeric:true,sensitivity:'base'});
    return arr.sort((a,b)=>{
      if(sort==='name')return factor*collator.compare(a.name||'',b.name||'')||collator.compare(a.backup_id||'',b.backup_id||'');
      if(sort==='size'){
        const na=a.size_bytes==null?null:Number(a.size_bytes),nb=b.size_bytes==null?null:Number(b.size_bytes);
        if(na===null)return nb===null?0:1;
        if(nb===null)return -1;
        return factor*(na-nb)||collator.compare(a.name||'',b.name||'');
      }
      return factor*(Date.parse(a.created_at||'')-Date.parse(b.created_at||''))||collator.compare(a.name||'',b.name||'');
    });
  }
  function render(){
    if(!opened)return;
    $('umBackupManagerTitle').textContent=tr('title');
    $('umBackupManagerSubtitle').textContent=tr('description');
    $('umBackupManagerClose').setAttribute('aria-label',tr('close'));
    $('umBackupManagerSortLabel').textContent=tr('sort');
    $('umBackupManagerReverse').setAttribute('aria-label',tr('direction'));
    $('umBackupManagerReverse').title=tr('direction');
    $('umBackupManagerReverse').textContent=asc?'↑':'↓';
    [['name','name'],['date','date'],['size','size']].forEach(([value,key])=>{
      const option=$('umBackupManagerSort').querySelector('option[value="'+value+'"]');
      if(option)option.textContent=tr(key);
    });
    $('umBackupManagerSort').value=sort;
    const known=rows.filter(r=>r.size_bytes!=null&&Number.isFinite(Number(r.size_bytes)));
    const total=known.reduce((sum,r)=>sum+Number(r.size_bytes),0);
    $('umBackupManagerStats').textContent=tr('count')+': '+rows.length+' · '+tr('total')+': '+(known.length===rows.length?sizeOf(total):tr('unknown'));
    const table=$('umBackupManagerTable');
    table.querySelectorAll('thead th').forEach((th,index)=>{th.textContent=tr(['name','date','size','type','actions'][index]);});
    const body=$('umBackupManagerRows');
    const ordered=sortedRows();
    body.innerHTML=ordered.map((r,index)=>{
      const detail=[r.compose_project&&r.compose_project!==r.name?r.compose_project:'',r.encrypted?tr('encrypted'):''].filter(Boolean).join(' · ');
      return '<tr><td><div class="um-bm-name">'+safe(r.name||'-')+'</div><div class="um-bm-meta">'+safe(detail)+'</div></td>'
        +'<td>'+safe(dateOf(r.created_at))+'</td><td>'+safe(sizeOf(r.size_bytes))+'</td><td>'+safe(r.mode==='full'?tr('full'):tr('quick'))+'</td>'
        +'<td><button type="button" class="um-bm-delete" data-row="'+index+'" '+(busy||scanLocked()?'disabled':'')+' aria-label="'+safe(tr('delete'))+'" title="'+safe(tr('delete'))+'">×</button></td></tr>';
    }).join('');
    $('umBackupManagerEmpty').hidden=ordered.length>0;
    $('umBackupManagerEmpty').textContent=tr('empty');
    $('umBackupManagerNotice').textContent=notice;
    body.querySelectorAll('[data-row]').forEach(btn=>{
      btn.addEventListener('click',()=>removeOne(ordered[Number(btn.dataset.row)]));
    });
  }
  async function load(){
    notice='';
    $('umBackupManagerEmpty').hidden=false;
    $('umBackupManagerEmpty').textContent=tr('loading');
    $('umBackupManagerRows').innerHTML='';
    try{
      const response=await api('/api/backups');
      if(!opened)return;
      rows=Array.isArray(response.backups)?response.backups:[];
      render();
    }catch(err){
      if(!opened)return;
      rows=[];
      notice=err&&err.message==='auth'?'':tr('failed')+' '+String(err.message||'-');
      render();
    }
  }
  async function removeOne(row){
    if(!row||busy)return;
    if(scanLocked()){notice=tr('locked');render();return;}
    const question=tr('confirm').replace('{name}',row.name||'-').replace('{date}',dateOf(row.created_at));
    if(!window.confirm(question))return;
    busy=true;notice='';render();
    try{
      await api('/api/backups?folder_id='+encodeURIComponent(row.folder_id)+'&backup_id='+encodeURIComponent(row.backup_id),{method:'DELETE'});
      rows=rows.filter(item=>!(item.folder_id===row.folder_id&&item.backup_id===row.backup_id));
      notice=tr('deleted');
      $('umBackupManagerNotice').classList.add('ok');
    }catch(err){
      if(err&&err.message!=='auth')notice=tr('deleteFailed')+' '+String(err.message||'-');
      $('umBackupManagerNotice').classList.remove('ok');
    }finally{busy=false;render();}
  }
  function close(){
    if(busy)return;
    opened=false;
    $('umBackupManagerBackdrop').classList.remove('visible');
    $('umBackupManagerBackdrop').setAttribute('aria-hidden','true');
    $('umBackupManagerButton').focus();
  }
  async function open(){
    opened=true;rows=[];notice='';
    $('umBackupManagerNotice').classList.remove('ok');
    $('umBackupManagerBackdrop').classList.add('visible');
    $('umBackupManagerBackdrop').setAttribute('aria-hidden','false');
    $('umBackupManagerButton').title=tr('open');
    $('umBackupManagerButton').setAttribute('aria-label',tr('open'));
    render();$('umBackupManagerClose').focus();
    await load();
  }
  function init(){
    const settings=$('settingsButton');
    if(!settings||$('umBackupManagerButton'))return;
    const button=document.createElement('button');
    button.id='umBackupManagerButton';button.className='icon-button';button.type='button';
    button.title=tr('open');button.setAttribute('aria-label',tr('open'));
    button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="5" rx="1.5"></rect><path d="M5 9v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"></path></svg>';
    settings.before(button);
    const backdrop=document.createElement('div');
    backdrop.id='umBackupManagerBackdrop';backdrop.setAttribute('aria-hidden','true');
    backdrop.innerHTML='<section id="umBackupManagerDialog" role="dialog" aria-modal="true" aria-labelledby="umBackupManagerTitle">'
      +'<div id="umBackupManagerTop"><div><h2 id="umBackupManagerTitle"></h2><p id="umBackupManagerSubtitle"></p></div><button id="umBackupManagerClose" type="button">×</button></div>'
      +'<div id="umBackupManagerToolbar"><span id="umBackupManagerStats"></span><div id="umBackupManagerControls"><label id="umBackupManagerSortLabel" for="umBackupManagerSort"></label><select id="umBackupManagerSort"><option value="name"></option><option value="date"></option><option value="size"></option></select><button id="umBackupManagerReverse" type="button"></button></div></div>'
      +'<div id="umBackupManagerScroll"><table id="umBackupManagerTable"><thead><tr><th></th><th></th><th></th><th></th><th></th></tr></thead><tbody id="umBackupManagerRows"></tbody></table><div id="umBackupManagerEmpty"></div></div>'
      +'<div id="umBackupManagerNotice" role="status"></div></section>';
    document.body.appendChild(backdrop);
    button.addEventListener('click',open);
    $('umBackupManagerClose').addEventListener('click',close);
    backdrop.addEventListener('click',event=>{if(event.target===backdrop)close();});
    document.addEventListener('keydown',event=>{if(opened&&event.key==='Escape'){event.preventDefault();close();}});
    $('umBackupManagerSort').addEventListener('change',event=>{sort=event.target.value;render();});
    $('umBackupManagerReverse').addEventListener('click',()=>{asc=!asc;render();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();
