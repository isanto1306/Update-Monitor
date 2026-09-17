from pathlib import Path

def extract(path, needles, radius=100):
    lines=Path(path).read_text(encoding='utf-8').splitlines()
    out=[]
    for needle in needles:
        idxs=[i for i,l in enumerate(lines) if needle in l]
        out.append(f'===== {needle!r} ({len(idxs)}) =====')
        for n,idx in enumerate(idxs[:8],1):
            out.append(f'--- match {n} line {idx+1} ---')
            for j in range(max(0,idx-radius),min(len(lines),idx+radius+1)):
                out.append(f'{j+1:06d}: {lines[j]}')
    return '\n'.join(out)

Path('fix-context-backend.txt').write_text(extract('app/main.py',[
 'def wait_for_image_source_runtime_stable(',
 '"compose_literal_refs": {},',
 'item = {',
 'def schedule_app_scan(',
 'def _verify_restore_runtime(',
 'def docker_information(',
],70),encoding='utf-8')
Path('fix-context-ui.txt').write_text(extract('static/index.html',[
 'function appAvailableVersionLabel(',
 'function renderHeaderWarningPopover(',
 'function updateHeaderWarningButton(',
 'function dockerInfoSection(',
 'data.check_detail',
 'function openDockerInfo',
 'const availablePrimary=',
 'policyVersionSelect',
 'state.pendingPostScans=',
],75),encoding='utf-8')
