/* Update Monitor — global Docker backup management, v0.3.403 */
(function () {
  'use strict';
  if (window.__umBackupOverviewInstalled) return;
  window.__umBackupOverviewInstalled = true;

  var labels = {
    de: {title:'Backup-Verwaltung',subtitle:'Gespeicherte Backups aller Docker-Apps',sort:'Sortieren nach',name:'Name',date:'Datum',size:'Größe',type:'Typ',actions:'Aktion',close:'Schließen',empty:'Keine gespeicherten Backups vorhanden.',loading:'Backups werden geladen …',count:'Backups',total:'Belegter Speicher',unknown:'zzgl. unbekannter Größen',delete:'Backup löschen',confirm:'Dieses Backup endgültig löschen?',deleted:'Backup wurde gelöscht.',error:'Die Backup-Liste konnte nicht geladen werden.',deleteError:'Das Backup konnte nicht gelöscht werden.',quick:'Schnell',full:'Vollständig',encrypted:'Verschlüsselt',ascending:'Aufsteigend',descending:'Absteigend',cancel:'Abbrechen',deleteConfirm:'Endgültig löschen'},
    en: {title:'Backup management',subtitle:'Saved backups from all Docker apps',sort:'Sort by',name:'Name',date:'Date',size:'Size',type:'Type',actions:'Action',close:'Close',empty:'No saved backups found.',loading:'Loading backups …',count:'Backups',total:'Storage used',unknown:'plus unknown sizes',delete:'Delete backup',confirm:'Permanently delete this backup?',deleted:'Backup deleted.',error:'Could not load the backup list.',deleteError:'Could not delete the backup.',quick:'Quick',full:'Full',encrypted:'Encrypted',ascending:'Ascending',descending:'Descending',cancel:'Cancel',deleteConfirm:'Delete permanently'},
    fr: {title:'Gestion des sauvegardes',subtitle:'Sauvegardes de toutes les applications Docker',sort:'Trier par',name:'Nom',date:'Date',size:'Taille',type:'Type',actions:'Action',close:'Fermer',empty:'Aucune sauvegarde enregistrée.',loading:'Chargement des sauvegardes…',count:'Sauvegardes',total:'Espace utilisé',unknown:'plus tailles inconnues',delete:'Supprimer la sauvegarde',confirm:'Supprimer définitivement cette sauvegarde ?',deleted:'Sauvegarde supprimée.',error:'Impossible de charger les sauvegardes.',deleteError:'Impossible de supprimer la sauvegarde.',quick:'Rapide',full:'Complète',encrypted:'Chiffrée',ascending:'Croissant',descending:'Décroissant',cancel:'Annuler',deleteConfirm:'Supprimer définitivement'},
    pt: {title:'Gestão de backups',subtitle:'Backups guardados de todas as aplicações Docker',sort:'Ordenar por',name:'Nome',date:'Data',size:'Tamanho',type:'Tipo',actions:'Ação',close:'Fechar',empty:'Não existem backups guardados.',loading:'A carregar backups…',count:'Backups',total:'Espaço utilizado',unknown:'mais tamanhos desconhecidos',delete:'Eliminar backup',confirm:'Eliminar este backup definitivamente?',deleted:'Backup eliminado.',error:'Não foi possível carregar os backups.',deleteError:'Não foi possível eliminar o backup.',quick:'Rápido',full:'Completo',encrypted:'Encriptado',ascending:'Ascendente',descending:'Descendente',cancel:'Cancelar',deleteConfirm:'Eliminar definitivamente',cancel:'Cancelar',deleteConfirm:'Eliminar definitivamente'},
    es: {title:'Gestión de copias',subtitle:'Copias guardadas de todas las aplicaciones Docker',sort:'Ordenar por',name:'Nombre',date:'Fecha',size:'Tamaño',type:'Tipo',actions:'Acción',close:'Cerrar',empty:'No hay copias guardadas.',loading:'Cargando copias…',count:'Copias',total:'Espacio utilizado',unknown:'más tamaños desconocidos',delete:'Eliminar copia',confirm:'¿Eliminar esta copia definitivamente?',deleted:'Copia eliminada.',error:'No se pudieron cargar las copias.',deleteError:'No se pudo eliminar la copia.',quick:'Rápida',full:'Completa',encrypted:'Cifrada',ascending:'Ascendente',descending:'Descendente'}
  };
  function lang() {
    var v;
    try { v = localStorage.getItem('updateMonitorLanguage'); } catch (_) {}
    return labels[v] ? v : 'en';
  }
  function t(key) { return labels[lang()][key] || labels.en[key] || key; }
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function byteText(value) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return new Intl.NumberFormat(lang(), {maximumFractionDigits: unit ? 2 : 0}).format(value) + ' ' + units[unit];
  }
  function timeText(value) {
    var date = new Date(value);
    if (!Number.isFinite(date.valueOf())) return '—';
    try { return new Intl.DateTimeFormat(lang(), {day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(date); }
    catch (_) { return date.toLocaleString(); }
  }

  var css = document.createElement('style');
  css.id = 'um-backup-overview-styles';
  css.textContent = [
    '#umBackupOverviewButton{width:40px;height:40px;padding:0;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;}',
    '#umBackupOverviewButton svg{width:28px;height:28px;display:block;stroke:#d7e0e8;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;}',
    '#umBackupOverviewButton:hover svg{stroke:#8fcaff;}',
    '#umBackupOverviewBackdrop{position:fixed;inset:0;z-index:23000;display:none;align-items:center;justify-content:center;padding:18px;background:rgba(3,9,17,.79);backdrop-filter:blur(5px);}',
    '#umBackupOverviewBackdrop.visible{display:flex;}',
    '#umBackupOverviewDialog{box-sizing:border-box;width:min(1010px,100%);max-height:min(860px,calc(100dvh - 36px));overflow:hidden;display:flex;flex-direction:column;border:1px solid rgba(112,143,179,.36);border-radius:13px;background:#141e29;color:#e2eaf2;box-shadow:0 25px 95px rgba(0,0,0,.66);font-family:inherit;}',
    '.umbo-top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px 24px 14px;}',
    '.umbo-top h2{margin:0;font-size:20px;line-height:1.3;font-weight:800;color:#eaf3ff;}',
    '.umbo-subtitle{margin:5px 0 0;color:#91a4b7;font-size:12px;}',
    '.umbo-x{width:34px;height:34px;flex:0 0 34px;border:1px solid rgba(129,152,175,.3);border-radius:8px;background:transparent;color:#d9e6f2;font-size:23px;line-height:1;cursor:pointer;}',
    '.umbo-x:hover{border-color:#8ec4fa;color:#fff;}',
    '.umbo-stats{display:flex;gap:10px;flex-wrap:wrap;padding:0 24px 17px;}',
    '.umbo-stat{background:#1a2837;border:1px solid rgba(95,136,175,.24);border-radius:9px;padding:10px 15px;min-width:115px;}',
    '.umbo-stat small{display:block;font-size:10px;color:#9cadbd;margin-bottom:4px;}',
    '.umbo-stat strong{font-size:16px;color:#dcecff;font-variant-numeric:tabular-nums;}',
    '.umbo-controls{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:11px 24px;border-top:1px solid rgba(112,143,179,.18);border-bottom:1px solid rgba(112,143,179,.2);}',
    '.umbo-controls label{font-size:12px;color:#a7b7c7;}',
    '.umbo-controls select,.umbo-controls button{border:1px solid rgba(122,153,181,.39);border-radius:8px;background:#1d2b3a;color:#e0ecf7;min-height:34px;padding:6px 10px;font:inherit;font-size:12px;cursor:pointer;}',
    '.umbo-controls button:hover{border-color:#71aff2;}',
    '.umbo-content{min-height:130px;max-height:60vh;overflow:auto;padding:0 24px 20px;scrollbar-color:#38516b #141e29;}',
    '.umbo-table{width:100%;border-collapse:collapse;text-align:left;font-size:12px;}',
    '.umbo-table th{position:sticky;top:0;background:#141e29;color:#93a6ba;font-weight:700;z-index:1;padding:13px 9px 11px;border-bottom:1px solid rgba(120,147,179,.3);white-space:nowrap;}',
    '.umbo-table td{padding:12px 9px;border-bottom:1px solid rgba(120,147,179,.15);vertical-align:middle;}',
    '.umbo-table tr:hover td{background:rgba(69,134,199,.055);}',
    '.umbo-app{font-weight:700;color:#e1ebf8;overflow-wrap:anywhere;}',
    '.umbo-id{font-size:10px;color:#839bb1;margin-top:3px;overflow-wrap:anywhere;}',
    '.umbo-date,.umbo-size{font-variant-numeric:tabular-nums;white-space:nowrap;}',
    '.umbo-tag{font-size:10px;color:#bcd1e3;}',
    '.umbo-lock{color:#dfbe7c;font-size:10px;margin-top:4px;}',
    '.umbo-delete{height:28px;width:28px;display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(224,94,94,.43);border-radius:7px;color:#ff9797;background:rgba(163,48,48,.08);cursor:pointer;font:inherit;font-size:19px;line-height:1;}',
    '.umbo-delete:hover{background:rgba(183,55,55,.18);border-color:#fd7777;color:#fff;}',
    '.umbo-delete:disabled,.umbo-controls button:disabled{opacity:.45;cursor:wait;}',
    '.umbo-message{padding:22px 8px;text-align:center;color:#aabbca;font-size:12px;}',
    '.umbo-message.error{color:#ffaaaa;}',
    '#umBackupOverviewDialog button:focus-visible,#umBackupOverviewDialog select:focus-visible,#umBackupOverviewButton:focus-visible{outline:2px solid #77baff;outline-offset:2px;}',
    '@media(max-width:650px){.umbo-top{padding:17px 15px 11px;}.umbo-stats{padding:0 15px 13px;}.umbo-controls{padding:11px 15px;}.umbo-content{padding:0 12px 14px;}.umbo-table{min-width:530px;}}'
  ].join('\n');
  css.textContent += '\n' + [
    "#umBackupOverviewButton,#umBackupOverviewButton:hover,#umBackupOverviewButton:active{border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;appearance:none!important;}",
    "#umBackupOverviewButton::before,#umBackupOverviewButton::after{display:none!important;}",
    ".umbo-top-actions{display:flex;align-items:center;gap:14px;flex:0 0 auto;}",
    ".umbo-sort-button{display:inline-flex;align-items:center;gap:7px;background:none;border:0;color:inherit;padding:4px 0;font:inherit;font-weight:inherit;white-space:nowrap;cursor:pointer;}",
    ".umbo-sort-button:hover,.umbo-sort-button.active{color:#a6d1fc;}",
    ".umbo-sort-arrow{display:inline-block;min-width:13px;opacity:.6;font-size:13px;}",
    ".umbo-sort-button.active .umbo-sort-arrow{opacity:1;}",
    "#umBackupOverviewDialog button:focus-visible,#umBackupOverviewButton:focus-visible,#umBackupDeleteDialog button:focus-visible{outline:2px solid #77baff;outline-offset:2px;}",
    "#umBackupDeleteBackdrop{position:fixed;inset:0;z-index:23010;display:none;align-items:center;justify-content:center;padding:18px;background:rgba(3,9,17,.82);backdrop-filter:blur(4px);}",
    "#umBackupDeleteBackdrop.visible{display:flex;}",
    "#umBackupDeleteDialog{box-sizing:border-box;width:min(430px,100%);border:1px solid rgba(112,143,179,.42);border-radius:13px;background:#172636;padding:23px;color:#e2eaf2;box-shadow:0 24px 85px rgba(0,0,0,.7);}",
    "#umBackupDeleteTitle{margin:0 0 12px;font-size:19px;font-weight:800;}",
    "#umBackupDeleteMessage{margin:0 0 16px;color:#c0d2e4;font-size:13px;line-height:1.5;}",
    "#umBackupDeleteDetail{padding:10px 12px;margin:0 0 20px;border-radius:8px;background:#101d29;color:#b4c8dc;overflow-wrap:anywhere;font-size:12px;}",
    ".umbo-delete-actions{display:flex;justify-content:flex-end;gap:10px;}",
    ".umbo-delete-actions button{border:1px solid rgba(122,153,181,.42);border-radius:8px;background:#27394c;color:#e7eff8;padding:9px 14px;font:inherit;font-size:12px;cursor:pointer;}",
    ".umbo-delete-actions #umboDeleteConfirm{background:#983c3c;border-color:#c25b5b;color:white;}",
    ".umbo-delete-actions button:disabled{opacity:.45;cursor:wait;}",
    "@media(max-width:650px){.umbo-top-actions{gap:8px;}}"
  ].join('\n');
  css.textContent += '\n' + "/* Position the warning pointer beneath the warning symbol, not the newly inserted backup button. */\n#headerWarningPopover::after{right:var(--um-warning-pointer-right,17px)!important;}";
  css.textContent += '\n' + [
    'html.um-backup-scroll-lock,html.um-backup-scroll-lock body{overflow:hidden!important;overscroll-behavior:none!important;}',
    '#umBackupOverviewBackdrop,#umBackupDeleteBackdrop{overscroll-behavior:contain;}',
    '#umBackupOverviewBackdrop .umbo-content{overscroll-behavior:contain;}',
    '#umboClose{font-size:28px!important;line-height:1!important;}',
    '.umbo-top{align-items:center;gap:24px;padding:20px 24px 20px;}',
    '.umbo-top-heading{min-width:0;}',
    '.umbo-top-actions{gap:18px;}',
    '.umbo-top-actions .umbo-stats{box-sizing:border-box;display:grid;grid-template-columns:1fr 1fr;align-items:stretch;flex:0 0 auto;gap:0;width:310px;min-width:0;min-height:48px;margin-right:60px;padding:0;border:1px solid rgba(105,137,169,.32);border-radius:10px;background:rgba(17,27,39,.50);}',
    '.umbo-top-actions .umbo-stat{box-sizing:border-box;display:flex;align-items:center;min-width:0;min-height:46px;padding:5px 12px;border:0;border-radius:0;background:transparent;}',
    '.umbo-top-actions .umbo-stat+.umbo-stat{border-left:1px solid rgba(105,137,169,.30);}',
    '.umbo-stat-copy{display:flex;flex-direction:column;gap:3px;min-width:0;}',
    '.umbo-top-actions .umbo-stat small{display:block;margin:0;font-size:11px;font-weight:400;color:#93a5b7;white-space:nowrap;}',
    '.umbo-top-actions .umbo-stat strong{display:block;font-size:15px;font-weight:800;white-space:nowrap;color:#e4edf7;}',
    '.umbo-top-actions #umboClose{flex:0 0 auto;}',
    '#umBackupOverviewBackdrop .umbo-content{scrollbar-width:thin;margin-bottom:14px;}',
    '#umBackupOverviewBackdrop .umbo-content::-webkit-scrollbar{width:6px;height:6px;}',
    '#umBackupOverviewBackdrop .umbo-content::-webkit-scrollbar-track{background:transparent;}',
    '#umBackupOverviewBackdrop .umbo-content::-webkit-scrollbar-thumb{background:#38516b;border-radius:6px;}',
    '@media(max-width:860px){.umbo-top{position:relative;flex-direction:column;align-items:stretch;gap:14px;padding:18px 16px 14px;}.umbo-top-heading{padding-right:48px;}.umbo-top-actions{justify-content:flex-end;width:100%;}.umbo-top-actions .umbo-stats{width:min(100%,310px);margin-right:0;}.umbo-top-actions #umboClose{position:absolute;right:16px;top:16px;}}',
    '@media(max-width:480px){.umbo-top-actions .umbo-stats{width:100%;}.umbo-top-actions .umbo-stat{padding:6px 8px;}.umbo-top-actions .umbo-stat small{font-size:10px;}.umbo-top-actions .umbo-stat strong{font-size:14px;}}'
  ].join('\n');
  document.head.appendChild(css);

  function init() {
    var settingsButton = document.getElementById('settingsButton');
    if (!settingsButton || !settingsButton.parentNode) return;
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = 'umBackupOverviewButton';
    trigger.className = 'icon-button';
    trigger.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="4" rx="1.5"></rect><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 13h4"></path></svg>';
    settingsButton.parentNode.insertBefore(trigger, settingsButton);

    // The header warning popover previously used a fixed offset from the
    // settings button. Inserting the backup button moves its trigger without
    // moving the popover's arrow; derive alignment from real screen geometry.
    (function alignHeaderWarningPopover() {
      var warningButton = document.getElementById('headerWarningButton');
      var popover = document.getElementById('headerWarningPopover');
      if (!warningButton || !popover) return;

      function align() {
        if (popover.hidden || warningButton.hidden || !popover.offsetWidth) return;
        var buttonBox = warningButton.getBoundingClientRect();
        var buttonCenter = (buttonBox.left + buttonBox.right) / 2;
        var panelBox = popover.getBoundingClientRect();
        var scale = panelBox.width / popover.offsetWidth;
        if (!Number.isFinite(scale) || scale <= 0) return;

        // On wide layouts, move the panel with its trigger. On narrow
        // viewports, preserve the panel's on-screen position and move just
        // the small pointer instead of clipping the left side.
        var position = window.getComputedStyle(popover).position;
        var parent = popover.offsetParent;
        if (position === 'absolute' && parent) {
          var viewport = document.documentElement.clientWidth || window.innerWidth;
          if (panelBox.width <= viewport - 16) {
            var desiredRight = buttonCenter + 24 * scale;
            var panelRight = Math.max(panelBox.width + 8,
              Math.min(viewport - 8, desiredRight));
            var parentRight = parent.getBoundingClientRect().right;
            popover.style.right = ((parentRight - panelRight) / scale).toFixed(2) + 'px';
            panelBox = popover.getBoundingClientRect();
          }
        }
        // The pointer is a 14px rotated square; its center is 7px in.
        var pointerRight = (panelBox.right - buttonCenter) / scale - 7;
        pointerRight = Math.max(8, Math.min(popover.offsetWidth - 22, pointerRight));
        popover.style.setProperty('--um-warning-pointer-right', pointerRight.toFixed(2) + 'px');
      }

      function scheduleAlign() { window.requestAnimationFrame(align); }
      var observer = new MutationObserver(scheduleAlign);
      observer.observe(popover, {attributes:true,attributeFilter:['hidden']});
      warningButton.addEventListener('click', scheduleAlign);
      window.addEventListener('resize', scheduleAlign, {passive:true});
      if ('ResizeObserver' in window) {
        var resizeObserver = new ResizeObserver(scheduleAlign);
        resizeObserver.observe(warningButton);
        resizeObserver.observe(popover);
      }
      scheduleAlign();
    })();

    var backdrop = document.createElement('div');
    backdrop.id = 'umBackupOverviewBackdrop';
    backdrop.setAttribute('aria-hidden', 'true');
    backdrop.innerHTML =
      '<section id="umBackupOverviewDialog" role="dialog" aria-modal="true" aria-labelledby="umboTitle">' +
        '<div class="umbo-top">' +
          '<div class="umbo-top-heading"><h2 id="umboTitle"></h2><p class="umbo-subtitle" id="umboSubtitle"></p></div>' +
          '<div class="umbo-top-actions">' +
            '<div class="umbo-stats">' +
              '<div class="umbo-stat"><span class="umbo-stat-copy"><small id="umboCountLabel"></small><strong id="umboCount">0</strong></span></div>' +
              '<div class="umbo-stat"><span class="umbo-stat-copy"><small id="umboTotalLabel"></small><strong id="umboTotal">—</strong></span></div>' +
            '</div>' +
            '<button type="button" class="image-source-close" id="umboClose" aria-label="Close">×</button>' +
          '</div>' +
        '</div>' +
        '<div class="umbo-content" id="umboContent" aria-live="polite"></div>' +
      '</section>';
    document.body.appendChild(backdrop);
    var deleteBackdrop = document.createElement('div');
    deleteBackdrop.id = 'umBackupDeleteBackdrop';
    deleteBackdrop.setAttribute('aria-hidden', 'true');
    deleteBackdrop.innerHTML =
      '<section id="umBackupDeleteDialog" role="alertdialog" aria-modal="true" aria-labelledby="umBackupDeleteTitle" aria-describedby="umBackupDeleteMessage">' +
      '<h3 id="umBackupDeleteTitle"></h3><p id="umBackupDeleteMessage"></p>' +
      '<div id="umBackupDeleteDetail"></div><div class="umbo-delete-actions">' +
      '<button type="button" id="umboDeleteCancel"></button><button type="button" id="umboDeleteConfirm"></button></div></section>';
    document.body.appendChild(deleteBackdrop);

    var data = [];
    var total = 0;
    var unknown = 0;
    var sort = 'date';
    var direction = -1;
    var pending = false;
    var message = null;
    var previousFocus = null;
    var selectedDelete = null;
    var deleteFocus = null;
    var content = document.getElementById('umboContent');

    // Only the backup table may scroll while the overview is visible.
    // Prevent wheel/touch scrolling over the dark side margins or modal header,
    // including when the delete confirmation is stacked above the overview.
    function preventOutsideScroll(event) {
      if (selectedDelete || !content.contains(event.target)) event.preventDefault();
    }
    function lockBackgroundScroll() {
      document.documentElement.classList.add('um-backup-scroll-lock');
      document.addEventListener('wheel', preventOutsideScroll, {capture:true,passive:false});
      document.addEventListener('touchmove', preventOutsideScroll, {capture:true,passive:false});
    }
    function unlockBackgroundScroll() {
      document.removeEventListener('wheel', preventOutsideScroll, true);
      document.removeEventListener('touchmove', preventOutsideScroll, true);
      document.documentElement.classList.remove('um-backup-scroll-lock');
    }

    function localize() {
      trigger.setAttribute('aria-label', t('title'));
      trigger.title = t('title');
      document.getElementById('umboTitle').textContent = t('title');
      document.getElementById('umboSubtitle').textContent = t('subtitle');
      document.getElementById('umboCountLabel').textContent = t('count');
      document.getElementById('umboTotalLabel').textContent = t('total');
      document.getElementById('umboClose').setAttribute('aria-label', t('close'));
      document.getElementById('umBackupDeleteTitle').textContent = t('delete');
      document.getElementById('umBackupDeleteMessage').textContent = t('confirm');
      document.getElementById('umboDeleteCancel').textContent = t('cancel');
      document.getElementById('umboDeleteConfirm').textContent = t('deleteConfirm');
    }

    function showMessage(text, isError) {
      content.innerHTML = '<div class="umbo-message' + (isError ? ' error' : '') + '">' + esc(text) + '</div>';
    }

    function sortHeading(key) {
      var active = sort === key;
      var next = active ? -direction : (key === 'name' ? 1 : -1);
      var label = t(next < 0 ? 'descending' : 'ascending');
      return '<th aria-sort="' + (active ? (direction < 0 ? 'descending' : 'ascending') : 'none') + '">' +
        '<button type="button" class="umbo-sort-button' + (active ? ' active' : '') +
        '" data-umbo-sort="' + key + '" title="' + esc(label) + '" aria-label="' + esc(t(key) + ': ' + label) + '">' +
        esc(t(key)) + '<span class="umbo-sort-arrow" aria-hidden="true">' +
        (active ? (direction < 0 ? '↓' : '↑') : '↕') + '</span></button></th>';
    }
    function render() {
      localize();
      document.getElementById('umboCount').textContent = String(data.length);
      document.getElementById('umboTotal').textContent = byteText(total) +
        (unknown ? ' (' + t('unknown') + ': ' + unknown + ')' : '');
      if (pending) { showMessage(t('loading'), false); return; }
      if (message) { showMessage(message.text, message.error); return; }
      if (!data.length) { showMessage(t('empty'), false); return; }
      var rows = data.slice().sort(function(a,b) {
        var cmp = 0;
        if (sort === 'name') cmp = String(a.app_name||'').localeCompare(String(b.app_name||''), lang(), {sensitivity:'base'});
        if (sort === 'date') cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
        if (sort === 'size') {
          if (a.size_bytes == null && b.size_bytes != null) return 1;
          if (b.size_bytes == null && a.size_bytes != null) return -1;
          cmp = (a.size_bytes||0) - (b.size_bytes||0);
        }
        return cmp ? cmp * direction : String(a.entry_id).localeCompare(String(b.entry_id));
      });
      var html = '<table class="umbo-table"><thead><tr>' + sortHeading('name') + sortHeading('date') +
        '<th>' + esc(t('type')) + '</th>' + sortHeading('size') +
        '<th>' + esc(t('actions')) + '</th></tr></thead><tbody>';
      rows.forEach(function(row) {
        html += '<tr><td><div class="umbo-app">' + esc(row.app_name) +
          '</div><div class="umbo-id">' + esc(row.backup_id) + '</div></td>' +
          '<td class="umbo-date">' + esc(timeText(row.created_at)) + '</td>' +
          '<td><div class="umbo-tag">' + (row.mode === 'full' ? t('full') : t('quick')) + '</div>' +
          (row.encrypted ? '<div class="umbo-lock">◆ ' + t('encrypted') + '</div>' : '') +
          '</td><td class="umbo-size">' + esc(byteText(row.size_bytes)) + '</td>' +
          '<td><button type="button" class="umbo-delete" data-umbo-id="' + esc(row.entry_id) +
          '" aria-label="' + esc(t('delete')) + '" title="' + esc(t('delete')) + '">×</button></td></tr>';
      });
      content.innerHTML = html + '</tbody></table>';
    }

    async function requestJson(url, options) {
      var response = await fetch(url, Object.assign({credentials:'same-origin',cache:'no-store'}, options || {}));
      var json = await response.json().catch(function() { return {}; });
      if (!response.ok) throw new Error(String(json.detail || 'HTTP ' + response.status));
      return json;
    }
    async function load() {
      if (pending) return;
      pending = true;
      message = null;
      render();
      try {
        var result = await requestJson('/api/backup-overview');
        data = Array.isArray(result.backups) ? result.backups : [];
        total = Number(result.total_size_bytes) || 0;
        unknown = Number(result.unknown_size_count) || 0;
      } catch (err) {
        message = {text:t('error') + ' ' + (err.message || ''),error:true};
      } finally {
        pending = false;
        render();
      }
    }
    function openDelete(row, opener) {
      if (!row || pending || selectedDelete) return;
      selectedDelete = row;
      deleteFocus = opener;
      document.getElementById('umBackupDeleteDetail').textContent =
        String(row.app_name || '') + ' · ' + timeText(row.created_at) + ' · ' + byteText(row.size_bytes);
      localize();
      deleteBackdrop.classList.add('visible');
      deleteBackdrop.setAttribute('aria-hidden', 'false');
      document.getElementById('umboDeleteCancel').focus();
    }
    function closeDelete() {
      if (!selectedDelete || pending) return;
      selectedDelete = null;
      deleteBackdrop.classList.remove('visible');
      deleteBackdrop.setAttribute('aria-hidden', 'true');
      if (deleteFocus && deleteFocus.isConnected) deleteFocus.focus();
      else document.getElementById('umboClose').focus();
      deleteFocus = null;
    }
    async function confirmDelete() {
      if (!selectedDelete || pending) return;
      var row = selectedDelete;
      pending = true;
      document.getElementById('umboDeleteConfirm').disabled = true;
      document.getElementById('umboDeleteCancel').disabled = true;
      try {
        await requestJson('/api/backup-overview?entry_id=' + encodeURIComponent(row.entry_id), {method:'DELETE'});
        data = data.filter(function(item) { return item.entry_id !== row.entry_id; });
        selectedDelete = null;
        deleteBackdrop.classList.remove('visible');
        deleteBackdrop.setAttribute('aria-hidden', 'true');
        deleteFocus = null;
        pending = false;
        await load();
        document.getElementById('umboClose').focus();
      } catch (err) {
        selectedDelete = null;
        deleteBackdrop.classList.remove('visible');
        deleteBackdrop.setAttribute('aria-hidden', 'true');
        deleteFocus = null;
        pending = false;
        message = {text:t('deleteError') + ' ' + (err.message || ''),error:true};
        render();
        document.getElementById('umboClose').focus();
      } finally {
        document.getElementById('umboDeleteConfirm').disabled = false;
        document.getElementById('umboDeleteCancel').disabled = false;
      }
    }
    function close() {
      if (pending || selectedDelete) return;
      backdrop.classList.remove('visible');
      backdrop.setAttribute('aria-hidden', 'true');
      unlockBackgroundScroll();
      if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
    }
    trigger.addEventListener('click', function() {
      previousFocus = document.activeElement;
      backdrop.classList.add('visible');
      backdrop.setAttribute('aria-hidden', 'false');
      lockBackgroundScroll();
      localize();
      document.getElementById('umboClose').focus();
      load();
    });
    document.getElementById('umboClose').addEventListener('click', close);
    backdrop.addEventListener('click', function(evt) { if (evt.target === backdrop) close(); });
    deleteBackdrop.addEventListener('click', function(evt) { if (evt.target === deleteBackdrop) closeDelete(); });
    document.getElementById('umboDeleteCancel').addEventListener('click', closeDelete);
    document.getElementById('umboDeleteConfirm').addEventListener('click', confirmDelete);
    document.addEventListener('keydown', function(evt) {
      if (!backdrop.classList.contains('visible')) return;
      if (evt.key === 'Escape') { evt.preventDefault(); if (selectedDelete) closeDelete(); else close(); return; }
      if (evt.key === 'Tab') {
        var focusRoot = selectedDelete ? deleteBackdrop : backdrop;
        var focusables = Array.prototype.filter.call(focusRoot.querySelectorAll('button:not(:disabled)'), function(el){return el.getClientRects().length;});
        if (!focusables.length) return;
        var index = focusables.indexOf(document.activeElement);
        if (evt.shiftKey && index <= 0) { evt.preventDefault(); focusables[focusables.length - 1].focus(); }
        else if (!evt.shiftKey && index === focusables.length - 1) { evt.preventDefault(); focusables[0].focus(); }
      }
    });
    content.addEventListener('click', function(evt) {
      var sortButton = evt.target.closest('[data-umbo-sort]');
      if (sortButton && !pending && !selectedDelete) {
        var nextSort = sortButton.getAttribute('data-umbo-sort');
        if (nextSort === 'name' || nextSort === 'date' || nextSort === 'size') {
          if (sort === nextSort) direction *= -1;
          else { sort = nextSort; direction = nextSort === 'name' ? 1 : -1; }
          render();
        }
        return;
      }
      var button = evt.target.closest('[data-umbo-id]');
      if (button && !selectedDelete) {
        var row = data.find(function(item) {return item.entry_id === button.getAttribute('data-umbo-id');});
        openDelete(row, button);
      }
    });
    var languageSelect = document.getElementById('languageSelect');
    if (languageSelect) languageSelect.addEventListener('change', function() { setTimeout(render, 0); });
    localize();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
})();
