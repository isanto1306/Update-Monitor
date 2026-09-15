from pathlib import Path

html_path = Path('static/index.html')
compose_path = Path('docker-compose.zimaos.yml')
readme_path = Path('README.md')

html = html_path.read_text(encoding='utf-8')
compose = compose_path.read_text(encoding='utf-8')
readme = readme_path.read_text(encoding='utf-8')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    return text.replace(old, new, 1)


html = replace_once(
    html,
    "const UPDATE_MONITOR_VERSION = 'v0.3.320';",
    "const UPDATE_MONITOR_VERSION = 'v0.3.321';",
    'frontend version',
)
compose = replace_once(compose, 'version: "0.3.320"', 'version: "0.3.321"', 'compose version')
readme = replace_once(readme, 'Current release: **v0.3.320**', 'Current release: **v0.3.321**', 'readme version')

html = replace_once(
    html,
    "  if(host==='gitlab.com'||host==='www.gitlab.com'||host.includes('gitlab'))return 'gitlab';\n  if(host.includes('forgejo'))return 'forgejo';",
    "  if(host==='gitlab.com'||host==='www.gitlab.com'||host.includes('gitlab'))return 'gitlab';\n  if(host==='hub.docker.com'||host==='www.docker.com'||host==='docker.com')return 'dockerhub';\n  if(host.includes('forgejo'))return 'forgejo';",
    'docker hub provider detection',
)

html = replace_once(
    html,
    "    case 'gitlab': return 'GitLab';\n    case 'forgejo': return 'Forgejo';",
    "    case 'gitlab': return 'GitLab';\n    case 'dockerhub': return 'Docker Hub';\n    case 'forgejo': return 'Forgejo';",
    'docker hub provider label',
)

label_block = """function projectProviderLabel(provider){
  switch(String(provider||'').toLowerCase()){
    case 'github': return 'GitHub';
    case 'codeberg': return 'Codeberg';
    case 'gitlab': return 'GitLab';
    case 'dockerhub': return 'Docker Hub';
    case 'forgejo': return 'Forgejo';
    case 'gitea': return 'Gitea';
    default: return state.language==='de'?'Projekt':'Project';
  }
}
"""

icon_function = r'''
function projectProviderIcon(provider){
  switch(String(provider||'').toLowerCase()){
    case 'github':
      return `<span class="update-project-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="#5fc7ff" d="M12 .7A12 12 0 0 0 8.2 24c.6.1.8-.2.8-.6v-2.1c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.8-1.3-1.8-1-.7.1-.7.1-.7 1.1.1 1.7 1.1 1.7 1.1 1 .1.7 2.6 3.5 1.9.1-.7.4-1.2.7-1.5-2.7-.3-5.6-1.4-5.6-6a4.7 4.7 0 0 1 1.2-3.3c-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.4 11.4 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.6.2 2.8.1 3.1a4.7 4.7 0 0 1 1.2 3.3c0 4.6-2.8 5.7-5.6 6 .4.4.8 1 .8 2.1v3c0 .4.2.7.8.6A12 12 0 0 0 12 .7Z"/></svg></span>`;
    case 'gitlab':
      return `<span class="update-project-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="#fc6d26" d="M12 22.4 16.4 9H7.6L12 22.4Z"/><path fill="#e24329" d="M12 22.4 7.6 9H1.7L12 22.4Z"/><path fill="#fc6d26" d="M1.7 9 .4 13a.9.9 0 0 0 .3 1L12 22.4 1.7 9Z"/><path fill="#fca326" d="M1.7 9h5.9L5 1.2a.5.5 0 0 0-1 0L1.7 9Z"/><path fill="#e24329" d="M12 22.4 16.4 9h5.9L12 22.4Z"/><path fill="#fc6d26" d="M22.3 9 23.6 13a.9.9 0 0 1-.3 1L12 22.4 22.3 9Z"/><path fill="#fca326" d="M22.3 9h-5.9L19 1.2a.5.5 0 0 1 1 0L22.3 9Z"/></svg></span>`;
    case 'codeberg':
      return `<span class="update-project-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="#2185d0" d="M4.2 18.2c1.8-5.3 4.4-9.2 7.8-12.1 3.4 2.9 6 6.8 7.8 12.1H4.2Z"/><path fill="#73c3f1" d="M7.2 18.2c1.4-3.5 3-6.2 4.8-8.3 1.8 2.1 3.4 4.8 4.8 8.3H7.2Z"/></svg></span>`;
    case 'dockerhub':
      return `<span class="update-project-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="#2496ed" d="M8.6 9.5h2.4V7.2H8.6v2.3Zm0 3h2.4v-2.3H8.6v2.3Zm3 0H14v-2.3h-2.4v2.3Zm-3-6h2.4V4.2H8.6v2.3Zm3 0H14V4.2h-2.4v2.3Zm3 3h2.4V7.2h-2.4v2.3Zm-3 0H14V7.2h-2.4v2.3Zm7.3 1.1c-.3-.2-1-.4-1.7-.3-.1-1-.9-1.8-1.9-2.1-.1 1.3-.8 2.4-2.2 2.4H4.4c-.2.9-.1 2 .4 3 .7 1.3 2 2 3.8 2 3.8 0 6.7-1.7 8.1-4.8.8 0 2.5 0 3.2-1.4.1-.3.2-.6.2-.8-.4-.2-.8-.3-1.2-.3Z"/></svg></span>`;
    default:
      return '';
  }
}
'''

if label_block not in html:
    raise SystemExit('projectProviderLabel block not found after provider label patch')
html = html.replace(label_block, label_block + icon_function, 1)

old_render = """function renderProjectLink(item){
  const projectUrl=String((item||{}).project_url||'').trim();
  if(!/^https?:\\/\\//i.test(projectUrl))return '';
  const provider=projectProviderFromUrl(projectUrl,(item||{}).project_provider);
  const label=projectProviderLabel(provider);
  return `<a class="update-project-link provider-${esc(provider)}" href="${esc(projectUrl)}" target="_blank" rel="noopener noreferrer" title="${esc(projectUrl)}">${esc(label)} ↗</a>`;
}
"""
new_render = """function renderProjectLink(item){
  const projectUrl=String((item||{}).project_url||'').trim();
  if(!/^https?:\\/\\//i.test(projectUrl))return '';
  const provider=projectProviderFromUrl(projectUrl,(item||{}).project_provider);
  const label=projectProviderLabel(provider);
  const icon=projectProviderIcon(provider);
  return `<a class="update-project-link provider-${esc(provider)}" href="${esc(projectUrl)}" target="_blank" rel="noopener noreferrer" title="${esc(projectUrl)}">${icon}<span class="update-project-label">${esc(label)}</span><span class="update-project-external" aria-hidden="true">↗</span></a>`;
}
"""
html = replace_once(html, old_render, new_render, 'renderProjectLink')

css = r'''
<style id="um-provider-logo-buttons-v0321">
/* v0.3.321 — consistent provider badges with compact brand icons. */
.update-detail-head-right .update-project-link{
  width:max-content !important;
  min-width:0 !important;
  min-height:24px !important;
  display:inline-flex !important;
  align-items:center !important;
  justify-content:center !important;
  gap:5px !important;
  padding:0 9px !important;
  border:1px solid #376a85 !important;
  border-radius:7px !important;
  background:#102333 !important;
  color:#5fc7ff !important;
  font-size:12px !important;
  font-weight:700 !important;
  line-height:1 !important;
  white-space:nowrap !important;
}
.update-detail-head-right .update-project-link:hover{
  background:rgba(95,199,255,.10) !important;
}
.update-detail-head-right .update-project-link.provider-codeberg,
.update-detail-head-right .update-project-link.provider-gitlab,
.update-detail-head-right .update-project-link.provider-dockerhub{
  color:#5fc7ff !important;
  border-color:#376a85 !important;
}
.update-project-icon{
  width:14px;
  height:14px;
  min-width:14px;
  min-height:14px;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  overflow:hidden;
}
.update-project-icon svg{
  width:14px;
  height:14px;
  display:block;
}
.update-project-label{
  display:inline-block;
  line-height:1;
}
.update-project-external{
  display:inline-block;
  font-size:11px;
  line-height:1;
  transform:translateY(-.5px);
}
</style>
'''
if 'id="um-provider-logo-buttons-v0321"' in html:
    raise SystemExit('provider logo style already present')
html = replace_once(html, '</head>', css + '\n</head>', 'head closing tag')

html_path.write_text(html, encoding='utf-8')
compose_path.write_text(compose, encoding='utf-8')
readme_path.write_text(readme, encoding='utf-8')
