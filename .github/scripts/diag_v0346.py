from pathlib import Path

TARGETS = {
    'app/main.py': [
        'def build_apps(',
        'def schedule_app_scan(',
        'def perform_version_update(',
        '@app.post("/api/app-update")',
        'def docker_information(',
        'def _verify_restore_runtime(',
        'def restore_app_backup(',
        'def apply_monitor_policy_fields(',
        'def casaos_compose_yaml(',
        'def casaos_apply_compose(',
    ],
    'static/index.html': [
        'Update wird überprüft',
        'Checking update',
        'Select version',
        'New version',
        'Uninstall',
        '>INSTALL<',
        'check_error',
        'update_status',
        'docker-info',
        'can_version_update',
        'can_image_update',
        'Prüffehler',
        'Check error',
        'Notices',
        'Hinweise',
    ],
}

out = []
for filename, needles in TARGETS.items():
    lines = Path(filename).read_text(encoding='utf-8').splitlines()
    out.append(f'===== {filename} ({len(lines)} lines) =====')
    for needle in needles:
        matches = [i for i, line in enumerate(lines) if needle in line]
        out.append(f'\n--- {needle!r}: {len(matches)} match(es) ---')
        for n, idx in enumerate(matches[:12], 1):
            start = max(0, idx - 14)
            end = min(len(lines), idx + 15)
            out.append(f'### match {n} at line {idx+1}')
            for j in range(start, end):
                out.append(f'{j+1:06d}: {lines[j]}')
            out.append('')
Path('diagnostics-v0346.txt').write_text('\n'.join(out), encoding='utf-8')
