from pathlib import Path
import json, re, subprocess, tempfile, sys, base64, zlib

ROOT = Path('.')
index_path = ROOT / 'static' / 'index.html'
text = index_path.read_text(encoding='utf-8')
_PAYLOAD_B64 = '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'
payload = json.loads(zlib.decompress(base64.b64decode(_PAYLOAD_B64)).decode('utf-8'))
original = text


def fail(msg):
    raise SystemExit('TRANSLATION PATCH FAILED: ' + msg)

def replace_once(old, new, label):
    global text
    count = text.count(old)
    if count != 1:
        fail(f'{label}: expected 1 occurrence, found {count}')
    text = text.replace(old, new, 1)

def regex_sub_once(pattern, repl, label, flags=0):
    global text
    new_text, count = re.subn(pattern, lambda _m: repl, text, count=1, flags=flags)
    if count != 1:
        fail(f'{label}: expected 1 occurrence, found {count}')
    text = new_text

# 1. Verify that the current English/German dictionary is still the audited base.
marker = 'const translations = '
start = text.find(marker)
end = text.find('\nfunction t(', start)
if start < 0 or end < 0:
    fail('translation object markers not found')
expr = text[start + len(marker):end].strip().rstrip(';')
with tempfile.TemporaryDirectory() as td:
    js = Path(td) / 'audit.js'
    js.write_text('const translations = ' + expr + ';\nprocess.stdout.write(JSON.stringify({de:Object.keys(translations.de).sort(),en:Object.keys(translations.en).sort()}));\n', encoding='utf-8')
    try:
        parsed = json.loads(subprocess.check_output(['node', str(js)], text=True))
    except Exception as exc:
        fail('could not evaluate translation object: ' + str(exc))
expected = payload['expected_current_base_keys']
if parsed['de'] != expected or parsed['en'] != expected:
    missing_en = sorted(set(expected) - set(parsed['en']))
    extra_en = sorted(set(parsed['en']) - set(expected))
    fail(f'base translation keys changed; missing={missing_en}, extra={extra_en}')

# 2. Version: preserve the repository sequence and move frontend to v0.3.323.
regex_sub_once(r"const UPDATE_MONITOR_VERSION\s*=\s*'v0\.3\.\d+';", "const UPDATE_MONITOR_VERSION = 'v0.3.323';", 'frontend version')
regex_sub_once(r'(<div class="system-info-value" id="systemInfoVersion">)v0\.3\.\d+(</div>)', r'\1v0.3.323\2', 'visible version')
text = re.sub(r'((?:favicon\.ico|favicon-16x16\.png|favicon-32x32\.png|apple-touch-icon\.png|site\.webmanifest)\?v=)0\.3\.\d+', r'\g<1>0.3.323', text)

# 3. Language selector.
replace_once(
    '          <option id="languageEnglish" value="en">English</option>\n',
    '          <option id="languageEnglish" value="en">English</option>\n'
    '          <option id="languageFrench" value="fr">Français</option>\n'
    '          <option id="languagePortuguese" value="pt">Português</option>\n'
    '          <option id="languageSpanish" value="es">Español</option>\n',
    'language options'
)

# Current repository intentionally defaults new installations to English.
regex_sub_once(
    r"  language:\s*localStorage\.getItem\('updateMonitorLanguage'\)[^\n]*,",
    "  language: ['de','en','fr','pt','es'].includes(localStorage.getItem('updateMonitorLanguage')) ? localStorage.getItem('updateMonitorLanguage') : 'en',",
    'language state initialization'
)

# 4. Add all dictionaries and localization helpers immediately after the existing de/en object.
compact = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
injection = f"""

Object.assign(translations.de,{compact(payload['de_extra'])});
Object.assign(translations.en,{compact(payload['en_extra'])});
translations.fr={compact(payload['fr'])};
translations.pt={compact(payload['pt'])};
translations.es={compact(payload['es'])};

const UI_LOCALES={{de:'de-DE',en:'en-US',fr:'fr-FR',pt:'pt-PT',es:'es-ES'}};
const INLINE_TRANSLATIONS={compact(payload['inline'])};
function getUiLocale(){{return UI_LOCALES[state.language]||'en-US';}}
function getDurationUnits(){{
  return ({{
    de:{{d:'T',h:'Std.',m:'Min.'}},
    en:{{d:'d',h:'h',m:'min'}},
    fr:{{d:'j',h:'h',m:'min'}},
    pt:{{d:'d',h:'h',m:'min'}},
    es:{{d:'d',h:'h',m:'min'}}
  }})[state.language]||{{d:'d',h:'h',m:'min'}};
}}
function inlineLocalizedText(de,en){{
  const german=String(de??'');
  const english=String(en??german);
  if(state.language==='de')return german||english;
  if(state.language==='en')return english||german;
  const map=INLINE_TRANSLATIONS[state.language]||{{}};
  if(Object.prototype.hasOwnProperty.call(map,english))return map[english];
  let m;
  const code=state.language;
  if((m=english.match(/^(\\d+) images inspected$/i))){{
    return code==='fr'?`${{m[1]}} images analysées`:code==='pt'?`${{m[1]}} imagens analisadas`:`${{m[1]}} imágenes analizadas`;
  }}
  if((m=english.match(/^(\\d+) alternative sources found$/i))){{
    return code==='fr'?`${{m[1]}} sources alternatives trouvées`:code==='pt'?`${{m[1]}} fontes alternativas encontradas`:`${{m[1]}} fuentes alternativas encontradas`;
  }}
  if((m=english.match(/^(\\d+) alternatives$/i))){{
    return code==='fr'?`${{m[1]}} alternatives`:code==='pt'?`${{m[1]}} alternativas`:`${{m[1]}} alternativas`;
  }}
  if((m=english.match(/^Image inspection failed:\\s*(.*)$/i))){{
    const prefix=code==='fr'?'Échec de l’analyse de l’image : ':code==='pt'?'Falha na análise da imagem: ':'Falló el análisis de la imagen: ';
    return prefix+m[1];
  }}
  if((m=english.match(/^Image source could not be switched:\\s*(.*)$/i))){{
    const prefix=code==='fr'?'Impossible de changer la source de l’image : ':code==='pt'?'Não foi possível alterar a fonte da imagem: ':'No se pudo cambiar la fuente de la imagen: ';
    return prefix+m[1];
  }}
  return english||german;
}}
function localizedBackendPair(obj,base,fallback=''){{
  const source=obj||{{}};
  const de=String(source[base+'_de']??source[base]??'').trim();
  const en=String(source[base+'_en']??source[base]??de??'').trim();
  if(state.language==='de')return de||en||fallback;
  if(state.language==='en')return en||de||fallback;
  return inlineLocalizedText(de||en||fallback,en||de||fallback);
}}
"""
replace_once('\nfunction t(k){return (translations[state.language]||translations.de)[k]||k;}', injection + "\nfunction t(k){const dict=translations[state.language]||translations.en; return dict[k]??translations.en[k]??translations.de[k]??k;}", 'translation helper insertion')

# 5. Main settings/system localization.
replace_once("  document.getElementById('systemInfoSupportLabel').textContent=state.language==='en'?'Support project':'Projekt unterstützen';", "  document.getElementById('systemInfoSupportLabel').textContent=t('supportProject');", 'support label')
replace_once(
    "  document.getElementById('languageGerman').textContent=t('german');\n  document.getElementById('languageEnglish').textContent=t('english');",
    "  document.getElementById('languageGerman').textContent='Deutsch';\n"
    "  document.getElementById('languageEnglish').textContent='English';\n"
    "  document.getElementById('languageFrench').textContent='Français';\n"
    "  document.getElementById('languagePortuguese').textContent='Português';\n"
    "  document.getElementById('languageSpanish').textContent='Español';",
    'language labels'
)
# Accessibility labels that were hardcoded in German.
accessibility = """
  const closeLabel=t('close');
  ['backupRestoreClose','dockerInfoClose','portConflictClose','backupPreviewClose','imageSourceClose'].forEach(id=>{
    const el=document.getElementById(id);
    if(el){el.setAttribute('aria-label',closeLabel);el.title=closeLabel;}
  });
  const scrollTopButton=document.getElementById('umScrollTopButtonV03251');
  if(scrollTopButton){scrollTopButton.setAttribute('aria-label',t('backToTop'));scrollTopButton.title=t('backToTop');}
  const paypalLink=document.querySelector('.system-info-paypal-link');
  if(paypalLink)paypalLink.setAttribute('aria-label',`${t('supportProject')} · PayPal`);
  const instagramLink=document.querySelector('.system-info-instagram-link');
  if(instagramLink)instagramLink.setAttribute('aria-label','iSanto1306 Apps · Instagram');
"""
replace_once("  document.getElementById('languageSelect').value=state.language;", "  document.getElementById('languageSelect').value=state.language;\n" + accessibility.rstrip(), 'accessibility localization')

# 6. Weekday labels.
old_days = """function policyDayLabels(){
  return state.language==='en'
    ? ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
    : ['Mo','Di','Mi','Do','Fr','Sa','So'];
}"""
new_days = """function policyDayLabels(){
  const labels={
    de:['Mo','Di','Mi','Do','Fr','Sa','So'],
    en:['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],
    fr:['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'],
    pt:['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'],
    es:['Lun','Mar','Mié','Jue','Vie','Sáb','Dom']
  };
  return labels[state.language]||labels.en;
}"""
replace_once(old_days, new_days, 'weekday labels')

# 7. Translate backend/version error details for all supported languages.
new_localize = r'''function localizeVersionDetail(raw){
  const detail=String(raw||'').replace(/\s+/g,' ').trim();
  if(!detail||state.language==='en')return detail;

  const maps={
    de:[
      [/GitHub API rate limit reached/gi,'GitHub-API-Limit erreicht'],[/GitHub API HTTP (\d+)/gi,'GitHub-API-Fehler HTTP $1'],[/GitHub ([^ ]+) network error:/gi,'GitHub-$1-Netzwerkfehler:'],[/GitHub lookup failed:/gi,'GitHub-Abfrage fehlgeschlagen:'],[/Could not determine a registry for/gi,'Registry konnte nicht ermittelt werden für'],[/Could not determine a registry image for/gi,'Registry-Image konnte nicht ermittelt werden für'],[/Could not determine a versioned registry image for/gi,'Versioniertes Registry-Image konnte nicht ermittelt werden für'],[/Unsupported registry authentication/gi,'Registry-Authentifizierung wird nicht unterstützt'],[/No GitHub repository URL detected/gi,'Keine GitHub-Repository-Adresse erkannt'],[/Multiple independent version groups were detected; no safe primary version group could be selected/gi,'Mehrere unabhängige Versionsgruppen erkannt; keine sichere gemeinsame Versionsauswahl möglich'],[/No selectable version group is available/gi,'Keine sichere Versionsgruppe verfügbar'],[/The selected version is not available for every image in the version group/gi,'Die gewählte Version ist nicht für alle Images der Versionsgruppe verfügbar'],[/The selected version exists upstream but the exact Docker tag is not installable for/gi,'Die Version existiert im Projekt, aber der genaue Docker-Tag ist nicht installierbar für'],[/Registry temporarily unavailable or rate limited while pulling/gi,'Registry vorübergehend nicht verfügbar oder Anfragelimit erreicht beim Laden von'],[/Docker pull failed for/gi,'Docker-Pull fehlgeschlagen für'],[/Too Many Requests/gi,'zu viele Anfragen'],[/This app has no registry-backed image that can switch versions/gi,'Diese App hat kein Registry-Image mit auswählbaren Versionen'],[/This app does not use a fixed version tag/gi,'Diese App verwendet keinen festgelegten Versions-Tag'],[/App not found in current scan/gi,'App wurde in der aktuellen Prüfung nicht gefunden'],[/Remote server returned invalid JSON/gi,'Der entfernte Server hat ungültige Daten zurückgegeben'],[/network error/gi,'Netzwerkfehler'],[/Connection timed out/gi,'Zeitüberschreitung bei der Verbindung'],[/timed out/gi,'Zeitüberschreitung'],[/Name or service not known/gi,'Servername konnte nicht aufgelöst werden'],[/Temporary failure in name resolution/gi,'Temporärer DNS-Fehler'],[/Forbidden/gi,'Zugriff verweigert'],[/Unauthorized/gi,'Nicht autorisiert']
    ],
    fr:[
      [/GitHub API rate limit reached/gi,'Limite de l’API GitHub atteinte'],[/GitHub API HTTP (\d+)/gi,'Erreur API GitHub HTTP $1'],[/GitHub ([^ ]+) network error:/gi,'Erreur réseau GitHub $1 :'],[/GitHub lookup failed:/gi,'Échec de la requête GitHub :'],[/Could not determine a registry for/gi,'Impossible de déterminer un registre pour'],[/Could not determine a registry image for/gi,'Impossible de déterminer une image de registre pour'],[/Could not determine a versioned registry image for/gi,'Impossible de déterminer une image de registre versionnée pour'],[/Unsupported registry authentication/gi,'Authentification du registre non prise en charge'],[/No GitHub repository URL detected/gi,'Aucune URL de dépôt GitHub détectée'],[/Multiple independent version groups were detected; no safe primary version group could be selected/gi,'Plusieurs groupes de versions indépendants ont été détectés ; aucun groupe principal sûr ne peut être sélectionné'],[/No selectable version group is available/gi,'Aucun groupe de versions sélectionnable n’est disponible'],[/The selected version is not available for every image in the version group/gi,'La version sélectionnée n’est pas disponible pour toutes les images du groupe de versions'],[/The selected version exists upstream but the exact Docker tag is not installable for/gi,'La version existe dans le projet, mais le tag Docker exact ne peut pas être installé pour'],[/Registry temporarily unavailable or rate limited while pulling/gi,'Registre temporairement indisponible ou limité lors du téléchargement de'],[/Docker pull failed for/gi,'Échec du téléchargement Docker pour'],[/Too Many Requests/gi,'trop de requêtes'],[/This app has no registry-backed image that can switch versions/gi,'Cette app ne possède aucune image de registre permettant de changer de version'],[/This app does not use a fixed version tag/gi,'Cette app n’utilise pas de tag de version fixe'],[/App not found in current scan/gi,'App introuvable dans la vérification actuelle'],[/Remote server returned invalid JSON/gi,'Le serveur distant a renvoyé des données JSON invalides'],[/network error/gi,'erreur réseau'],[/Connection timed out/gi,'délai de connexion dépassé'],[/timed out/gi,'délai dépassé'],[/Name or service not known/gi,'nom du serveur introuvable'],[/Temporary failure in name resolution/gi,'erreur DNS temporaire'],[/Forbidden/gi,'accès refusé'],[/Unauthorized/gi,'non autorisé']
    ],
    pt:[
      [/GitHub API rate limit reached/gi,'Limite da API do GitHub atingido'],[/GitHub API HTTP (\d+)/gi,'Erro da API do GitHub HTTP $1'],[/GitHub ([^ ]+) network error:/gi,'Erro de rede do GitHub $1:'],[/GitHub lookup failed:/gi,'Falha na consulta ao GitHub:'],[/Could not determine a registry for/gi,'Não foi possível determinar um registry para'],[/Could not determine a registry image for/gi,'Não foi possível determinar uma imagem de registry para'],[/Could not determine a versioned registry image for/gi,'Não foi possível determinar uma imagem de registry com versão para'],[/Unsupported registry authentication/gi,'Autenticação do registry não suportada'],[/No GitHub repository URL detected/gi,'Não foi detetado nenhum URL de repositório GitHub'],[/Multiple independent version groups were detected; no safe primary version group could be selected/gi,'Foram detetados vários grupos de versões independentes; não foi possível selecionar um grupo principal seguro'],[/No selectable version group is available/gi,'Não existe nenhum grupo de versões selecionável'],[/The selected version is not available for every image in the version group/gi,'A versão selecionada não está disponível para todas as imagens do grupo de versões'],[/The selected version exists upstream but the exact Docker tag is not installable for/gi,'A versão existe no projeto, mas a tag Docker exata não pode ser instalada para'],[/Registry temporarily unavailable or rate limited while pulling/gi,'Registry temporariamente indisponível ou com limite de pedidos durante a transferência de'],[/Docker pull failed for/gi,'Falha no Docker pull para'],[/Too Many Requests/gi,'demasiados pedidos'],[/This app has no registry-backed image that can switch versions/gi,'Esta app não tem nenhuma imagem de registry que permita mudar de versão'],[/This app does not use a fixed version tag/gi,'Esta app não utiliza uma tag de versão fixa'],[/App not found in current scan/gi,'App não encontrada na verificação atual'],[/Remote server returned invalid JSON/gi,'O servidor remoto devolveu JSON inválido'],[/network error/gi,'erro de rede'],[/Connection timed out/gi,'tempo limite da ligação excedido'],[/timed out/gi,'tempo limite excedido'],[/Name or service not known/gi,'não foi possível resolver o nome do servidor'],[/Temporary failure in name resolution/gi,'erro DNS temporário'],[/Forbidden/gi,'acesso negado'],[/Unauthorized/gi,'não autorizado']
    ],
    es:[
      [/GitHub API rate limit reached/gi,'Se alcanzó el límite de la API de GitHub'],[/GitHub API HTTP (\d+)/gi,'Error de la API de GitHub HTTP $1'],[/GitHub ([^ ]+) network error:/gi,'Error de red de GitHub $1:'],[/GitHub lookup failed:/gi,'Falló la consulta a GitHub:'],[/Could not determine a registry for/gi,'No se pudo determinar un registro para'],[/Could not determine a registry image for/gi,'No se pudo determinar una imagen de registro para'],[/Could not determine a versioned registry image for/gi,'No se pudo determinar una imagen de registro con versión para'],[/Unsupported registry authentication/gi,'Autenticación del registro no compatible'],[/No GitHub repository URL detected/gi,'No se detectó ninguna URL de repositorio de GitHub'],[/Multiple independent version groups were detected; no safe primary version group could be selected/gi,'Se detectaron varios grupos de versiones independientes; no se pudo seleccionar un grupo principal seguro'],[/No selectable version group is available/gi,'No hay ningún grupo de versiones seleccionable'],[/The selected version is not available for every image in the version group/gi,'La versión seleccionada no está disponible para todas las imágenes del grupo de versiones'],[/The selected version exists upstream but the exact Docker tag is not installable for/gi,'La versión existe en el proyecto, pero la etiqueta Docker exacta no se puede instalar para'],[/Registry temporarily unavailable or rate limited while pulling/gi,'Registro temporalmente no disponible o limitado durante la descarga de'],[/Docker pull failed for/gi,'Falló la descarga de Docker para'],[/Too Many Requests/gi,'demasiadas solicitudes'],[/This app has no registry-backed image that can switch versions/gi,'Esta app no tiene ninguna imagen de registro que permita cambiar de versión'],[/This app does not use a fixed version tag/gi,'Esta app no utiliza una etiqueta de versión fija'],[/App not found in current scan/gi,'App no encontrada en la comprobación actual'],[/Remote server returned invalid JSON/gi,'El servidor remoto devolvió JSON no válido'],[/network error/gi,'error de red'],[/Connection timed out/gi,'se agotó el tiempo de conexión'],[/timed out/gi,'se agotó el tiempo de espera'],[/Name or service not known/gi,'no se pudo resolver el nombre del servidor'],[/Temporary failure in name resolution/gi,'error DNS temporal'],[/Forbidden/gi,'acceso denegado'],[/Unauthorized/gi,'no autorizado']
    ]
  };
  let translated=detail;
  for(const [pattern,value] of (maps[state.language]||[]))translated=translated.replace(pattern,value);
  if(translated===detail&&/[A-Za-z]{4,}\s+[A-Za-z]{4,}/.test(detail))return t('policyVersionsFailed');
  return translated;
}'''
regex_sub_once(r'function localizeVersionDetail\(raw\)\{[\s\S]*?\n\}\n(?=function syncPolicyVersionMenu)', new_localize, 'version detail localization')

# 8. Replace remaining German/English-only branches.
replacements = [
("      const suffix=current?(state.language==='en'?' (installed)':' (installiert)'):'';", "      const suffix=current?t('installedSuffix'):'';", 'installed suffix'),
("    default: return state.language==='de'?'Projekt':'Project';", "    default: return t('projectGeneric');", 'generic project label'),
("title=\"${esc(state.language==='de'?'Docker Informationen':'Docker information')}\" aria-label=\"${esc(state.language==='de'?'Docker Informationen':'Docker information')}\"", "title=\"${esc(t('dockerInformation'))}\" aria-label=\"${esc(t('dockerInformation'))}\"", 'docker info title'),
("function imageSourceText(de,en){\n  return state.language==='en'?en:de;\n}", "function imageSourceText(de,en){\n  return inlineLocalizedText(de,en);\n}", 'image source text'),
("function dockerInfoText(de,en){\n  return state.language==='en'?en:de;\n}", "function dockerInfoText(de,en){\n  return inlineLocalizedText(de,en);\n}", 'docker info text'),
("    if(mode==='none')return state.language==='en'?'None':'Keines';", "    if(mode==='none')return t('noneLabel');", 'network none'),
("    <div class=\"update-card-stat-label\">${esc(network?(state.language==='en'?'Network':'Netzwerk'):t('port'))}</div>", "    <div class=\"update-card-stat-label\">${esc(network?t('networkLabel'):t('port'))}</div>", 'network label'),
("    :restart?(state.language==='en'?'Restart':'Neu starten'):runtimeControlLabel(app);", "    :restart?t('restart'):runtimeControlLabel(app);", 'restart label'),
("  const label=policy.auto_enabled?(state.language==='en'?'Automatic':'Automatisch')\n    :policy.mode==='follow'?(state.language==='en'?'Follow tag':'Tag folgen')", "  const label=policy.auto_enabled?t('automaticShort')\n    :policy.mode==='follow'?t('follow')", 'toolbar policy labels'),
("  const restartTitle=restartBlocked?selfProtectionTitle():(state.language==='en'?'Restart':'Neu starten');", "  const restartTitle=restartBlocked?selfProtectionTitle():t('restart');", 'restart title'),
("    const label=state.language==='en'\n      ? (allSelected?'Deselect all days':'Select all days')\n      : (allSelected?'Alle Wochentage abwählen':'Alle Wochentage auswählen');", "    const label=allSelected?t('deselectAllDays'):t('selectAllDays');", 'select days label'),
("function imageSourceCheckText(check,field){\n  const suffix=state.language==='en'?'_en':'_de';\n  return String((check||{})[field+suffix]||(check||{})[field]||'-');\n}", "function imageSourceCheckText(check,field){\n  return localizedBackendPair(check,field,'-');\n}", 'image check text'),
("  return date.toLocaleString(state.language==='en'?'en-US':'de-DE',{", "  return date.toLocaleString(getUiLocale(),{", 'date locale'),
("      const locale=state.language==='en'?'en-US':'de-DE';", "      const locale=getUiLocale();", 'number locale'),
("      if(scanMutationIsLocked())throw new Error(state.language==='en'?'Update check is running.':'Update Prüfung läuft.');", "      if(scanMutationIsLocked())throw new Error(t('scanLockedAction'));", 'scan lock error')
]
for old,new,label in replacements:
    replace_once(old,new,label)

# Duration formatters: use proper abbreviations per locale (French uses 'j').
old_duration = """  if(state.language==='en'){
    if(days)return `${days} d ${hours} h`;
    if(hours)return `${hours} h ${minutes} min`;
    return `${minutes} min`;
  }
  if(days)return `${days} T ${hours} Std.`;
  if(hours)return `${hours} Std. ${minutes} Min.`;
  return `${minutes} Min.`;"""
new_duration = """  const units=getDurationUnits();
  if(days)return `${days} ${units.d} ${hours} ${units.h}`;
  if(hours)return `${hours} ${units.h} ${minutes} ${units.m}`;
  return `${minutes} ${units.m}`;"""
replace_once(old_duration,new_duration,'docker duration units')

old_backup_duration = """  if(state.language==='en'){
    if(days>0)return `${days} d${hours>0?` ${hours} h`:''}`;
    if(hours>0)return `${hours} h${minutes>0?` ${minutes} min`:''}`;
    return `${minutes} min`;
  }

  if(days>0)return `${days} T${hours>0?` ${hours} Std.`:''}`;
  if(hours>0)return `${hours} Std.${minutes>0?` ${minutes} Min.`:''}`;
  return `${minutes} Min.`;"""
new_backup_duration = """  const units=getDurationUnits();
  if(days>0)return `${days} ${units.d}${hours>0?` ${hours} ${units.h}`:''}`;
  if(hours>0)return `${hours} ${units.h}${minutes>0?` ${minutes} ${units.m}`:''}`;
  return `${minutes} ${units.m}`;"""
replace_once(old_backup_duration,new_backup_duration,'backup duration units')

# Image-source backend messages/labels.
replace_once("    const discoveryMessage=state.language==='en'\n      ?String(image.discovery_message_en||'')\n      :String(image.discovery_message_de||'');", "    const discoveryMessage=localizedBackendPair(image,'discovery_message','');", 'discovery message')
replace_once("    const imageWarning=state.language==='en'\n      ?String(image.warning_en||'')\n      :String(image.warning_de||'');\n    const imageError=state.language==='en'\n      ?String(image.error_en||'')\n      :String(image.error_de||'');", "    const imageWarning=localizedBackendPair(image,'warning','');\n    const imageError=localizedBackendPair(image,'error','');", 'image warning/error')
replace_once("      const label=state.language==='en'\n        ?String(option.label_en||option.label_de||sourceId)\n        :String(option.label_de||option.label_en||sourceId);", "      const label=localizedBackendPair(option,'label',sourceId);", 'image source option label')

# Runtime state localization.
regex_sub_once(r"function localizeRuntimeStateWord\(value\)\{[\s\S]*?\n\}", """function localizeRuntimeStateWord(value){
  const raw=String(value||'').trim().toLowerCase();
  const keys={running:'runtimeWordRunning',exited:'runtimeWordExited',stopped:'runtimeWordStopped',restarting:'runtimeWordRestarting',paused:'runtimeWordPaused',dead:'runtimeWordDead',created:'runtimeWordCreated'};
  return keys[raw]?t(keys[raw]):raw||'-';
}""", 'runtime state words')
replace_once("    const prefix=state.language==='en'?'Current state: ':'Aktueller Zustand: ';", "    const prefix=t('currentStatePrefix');", 'runtime state prefix')
replace_once("  return text?(state.language==='en'?'Current state: ':'Aktueller Zustand: ')+text:'';", "  return text?t('currentStatePrefix')+text:'';", 'runtime text prefix')

# Backup preview contained hardcoded German text.
replace_once("    continueButton.textContent='Weiter';", "    continueButton.textContent=t('backupContinue');", 'backup continue label')
replace_once("    version.textContent=available?`Verfügbar: ${available}`:'Update verfügbar';", "    version.textContent=available?t('policySelectedVersion').replace('{version}',available):t('updateAvailable');", 'backup version label')

# Language selector change handler, preserving English as the fallback for new/invalid settings.
replace_once("  state.language=e.target.value==='en'?'en':'de';", "  const selectedLanguage=String(e.target.value||'');\n  state.language=['de','en','fr','pt','es'].includes(selectedLanguage)?selectedLanguage:'en';", 'language onchange')

# 9. Final source-level checks before writing.
for forbidden in [
    "state.language==='en'?'Support project':'Projekt unterstützen'",
    "state.language==='en'?en:de",
    "continueButton.textContent='Weiter'",
    "version.textContent=available?`Verfügbar: ${available}`:'Update verfügbar'",
    "state.language=e.target.value==='en'?'en':'de'"
]:
    if forbidden in text:
        fail('unlocalized source remains: ' + forbidden)
for required in ['value="fr"','value="pt"','value="es"','translations.fr=','translations.pt=','translations.es=','const UI_LOCALES=','v0.3.323']:
    if required not in text:
        fail('required output missing: ' + required)

# Write patched file.
index_path.write_text(text, encoding='utf-8')

# 10. JavaScript syntax + dictionary parity + placeholder parity.
scripts = re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', text, flags=re.I)
for n, script in enumerate(scripts):
    if not script.strip():
        continue
    tmp = Path('/tmp') / f'update-monitor-script-{n}.js'
    tmp.write_text(script, encoding='utf-8')
    result = subprocess.run(['node','--check',str(tmp)], text=True, capture_output=True)
    if result.returncode != 0:
        fail('JavaScript syntax error: ' + result.stderr[:2000])

# Evaluate only the translation setup in a VM-safe standalone snippet.
tr_start = text.index('const translations = ')
tr_end = text.index('\nfunction getUiLocale()', tr_start)
setup = text[tr_start:tr_end]
# Convert const translations declaration and evaluate with a fake state only after setup.
audit_js = """
const vm=require('vm');
const src=process.argv[2];
const code=require('fs').readFileSync(src,'utf8');
const a=code.indexOf('const translations = ');
const b=code.indexOf('\\nfunction getUiLocale()',a);
const setup=code.slice(a,b).replace('const translations = ','globalThis.translations = ');
const ctx={}; vm.createContext(ctx); vm.runInContext(setup,ctx);
const tr=ctx.translations;
const langs=['de','en','fr','pt','es'];
const enKeys=Object.keys(tr.en).sort();
function placeholders(v){return [...String(v).matchAll(/\\{([A-Za-z0-9_]+)\\}/g)].map(m=>m[1]).sort();}
const errors=[];
for(const lang of langs){
 const keys=Object.keys(tr[lang]||{}).sort();
 if(JSON.stringify(keys)!==JSON.stringify(enKeys)) errors.push(lang+': key mismatch');
 for(const key of enKeys){
   if(!String((tr[lang]||{})[key]??'').trim()) errors.push(lang+': empty '+key);
   if(JSON.stringify(placeholders((tr[lang]||{})[key]))!==JSON.stringify(placeholders(tr.en[key]))) errors.push(lang+': placeholder mismatch '+key);
 }
}
if(errors.length){console.error(errors.join('\\n'));process.exit(2);} 
console.log(JSON.stringify({languages:langs,keys:enKeys.length}));
"""
audit_file = Path('/tmp/translation-final-audit.js')
audit_file.write_text(audit_js, encoding='utf-8')
result = subprocess.run(['node',str(audit_file),str(index_path)], text=True, capture_output=True)
if result.returncode != 0:
    fail('translation parity audit failed: ' + result.stderr[:4000])

# Duplicate HTML id check.
ids = re.findall(r'\bid="([^"]+)"', text)
duplicates = sorted({x for x in ids if ids.count(x) > 1})
if duplicates:
    fail('duplicate HTML ids: ' + ', '.join(duplicates[:20]))

# CSS must not be modified by translation patch except version-independent HTML/JS changes.
# We cannot compare to remote original after writing, but this patch never performs CSS replacements.

report = {
    'version':'v0.3.323',
    'languages':['de','en','fr','pt','es'],
    'translation_keys': len(payload['en_extra']) + len(payload['expected_current_base_keys']),
    'inline_translation_keys': {k:len(v) for k,v in payload['inline'].items()},
    'javascript_syntax':'ok',
    'dictionary_parity':'ok',
    'placeholder_parity':'ok',
    'duplicate_html_ids':'none',
    'css_touched':False,
}
Path('translation-audit-final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
