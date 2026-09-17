from pathlib import Path
p=Path('static/index.html')
lines=p.read_text(encoding='utf-8').splitlines()
needles=['error_count','warningTitle','warningButton','environment_values_hidden','scan_warning','newer_tag','version_group_state','installUpdate','uninstall:',"data-uninstall",'dockerInfo','renderWarnings','warningItems','Select version','New version','Prüffehler','Check error','verificationPhase']
out=[f'index lines={len(lines)}']
for needle in needles:
    matches=[i for i,line in enumerate(lines) if needle in line]
    out.append(f'\n=== {needle!r} {len(matches)} ===')
    for idx in matches[:20]:
        out.append(f'--- line {idx+1} ---')
        for j in range(max(0,idx-18),min(len(lines),idx+19)):
            out.append(f'{j+1:06d}: {lines[j]}')
Path('diagnostics-v0346-ui2.txt').write_text('\n'.join(out),encoding='utf-8')
