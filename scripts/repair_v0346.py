#!/usr/bin/env python3
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
INDEX = ROOT / "static" / "index.html"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise RuntimeError(f"Function not found: {name}")


def apply_patch() -> None:
    backend = MAIN.read_text(encoding="utf-8")
    ui = INDEX.read_text(encoding="utf-8", errors="strict")

    backend = replace_once(
        backend,
        'VERSION = "0.3.345"',
        'VERSION = "0.3.346"',
        "version bump",
    )

    # A post-update targeted scan may legitimately take a while, but its waiter
    # must never keep post_scan_pending alive forever if a scan thread wedges.
    schedule = function_source(backend, "schedule_app_scan")
    old_wait = '''                    while True:\n                        with scan_lock:\n                            thread = scan_thread\n                            scanning = scan_state.get("state") == "scanning"\n                        if not scanning and (not thread or not thread.is_alive()):\n                            break\n                        time.sleep(0.25)'''
    new_wait = '''                    while time.time() < deadline:\n                        with scan_lock:\n                            thread = scan_thread\n                            scanning = scan_state.get("state") == "scanning"\n                        if not scanning and (not thread or not thread.is_alive()):\n                            break\n                        time.sleep(0.25)'''
    if old_wait not in schedule:
        raise RuntimeError("schedule_app_scan: unbounded scan wait block not found")
    backend = backend.replace(old_wait, new_wait, 1)

    # Query ZimaOS once per app-list rebuild. Docker Compose labels alone do not
    # prove that a project is managed by the ZimaOS App Management API.
    helper_anchor = '''def casaos_compose_project_exists(compose_project):\n'''
    helper = '''def casaos_compose_project_names(timeout=5):\n    """Return managed ZimaOS Compose project names, or None if unavailable."""\n    try:\n        status, raw = casaos_request(\n            "/v2/app_management/compose",\n            method="GET",\n            accept="application/json",\n            timeout=max(1, int(timeout or 5)),\n        )\n        if status != 200:\n            return None\n        payload = json.loads(raw or "{}")\n        data = payload.get("data") or {}\n        if not isinstance(data, dict):\n            return None\n        return {str(value) for value in data.keys()}\n    except Exception:\n        return None\n\n\ndef compose_update_supported_for_app(app_item):\n    """Return whether Update Monitor can safely mutate this Compose project."""\n    app_item = app_item if isinstance(app_item, dict) else {}\n    explicit = app_item.get("compose_update_supported")\n    if isinstance(explicit, bool):\n        return explicit\n    project = str(app_item.get("compose_project") or "").strip()\n    if not project:\n        return False\n    managed = casaos_compose_project_names(timeout=5)\n    # Preserve existing ZimaOS behaviour if App Management is temporarily\n    # unavailable; actual mutations still fail safely through the API.\n    if managed is None:\n        return True\n    return project in managed\n\n\ndef compose_update_block_reason_for_app(app_item):\n    if compose_update_supported_for_app(app_item):\n        return None\n    if not str((app_item or {}).get("compose_project") or "").strip():\n        return "Compose project metadata is missing; update installation is disabled."\n    return (\n        "Compose app is outside ZimaOS App Management; "\n        "update installation is disabled."\n    )\n\n\n'''
    if "def casaos_compose_project_names(" not in backend:
        backend = replace_once(backend, helper_anchor, helper + helper_anchor, "compose support helpers")

    # Build the app list with a real ZimaOS-management capability flag and keep
    # the Compose labels that were already discovered by the Docker scanner.
    backend = replace_once(
        backend,
        '''def build_apps(results):\n    grouped = {}''',
        '''def build_apps(results):\n    managed_compose_projects = casaos_compose_project_names(timeout=5)\n    grouped = {}''',
        "build_apps managed project snapshot",
    )

    old_can_update = '''        can_image_update = any(\n            x.get("status") == "IMAGE_UPDATE"\n            and x.get("update_policy") == "follow_tag"\n            and x.get("compose_project")\n            for x in items\n        )'''
    new_can_update = '''        compose_project = str(app_item.get("compose_project") or "").strip()\n        if not compose_project:\n            compose_update_supported = False\n            compose_update_block_reason = (\n                "Compose project metadata is missing; update installation is disabled."\n            )\n        elif managed_compose_projects is None:\n            compose_update_supported = True\n            compose_update_block_reason = None\n        else:\n            compose_update_supported = compose_project in managed_compose_projects\n            compose_update_block_reason = (\n                None\n                if compose_update_supported\n                else (\n                    "Compose app is outside ZimaOS App Management; "\n                    "update installation is disabled."\n                )\n            )\n\n        can_image_update = any(\n            x.get("status") == "IMAGE_UPDATE"\n            and x.get("update_policy") == "follow_tag"\n            and x.get("compose_project")\n            for x in items\n        ) and compose_update_supported'''
    backend = replace_once(backend, old_can_update, new_can_update, "build_apps install capability")

    backend = replace_once(
        backend,
        '''            "compose_project": app_item.get("compose_project"),\n            "icon_url": app_item.get("icon_url"),''',
        '''            "compose_project": app_item.get("compose_project"),\n            "compose_working_dir": app_item.get("compose_working_dir"),\n            "compose_config_files": app_item.get("compose_config_files"),\n            "compose_update_supported": compose_update_supported,\n            "compose_update_block_reason": compose_update_block_reason,\n            "icon_url": app_item.get("icon_url"),''',
        "build_apps preserve compose metadata",
    )

    backend = replace_once(
        backend,
        '''    if app_item.get("compose_project") and group:\n        if mode == "follow":''',
        '''    if (\n        app_item.get("compose_project")\n        and compose_update_supported_for_app(app_item)\n        and group\n    ):\n        if mode == "follow":''',
        "policy install capability",
    )

    # Defense in depth: never call the ZimaOS update endpoint for an external
    # Compose project even if an old cached scan still contains an update flag.
    update_guard_anchor = '''    if not app_item:\n        raise HTTPException(status_code=404, detail="App not found in current scan")\n\n    if not app_item.get("can_image_update") and not app_item.get("can_version_update"):\n'''
    update_guard_new = '''    if not app_item:\n        raise HTTPException(status_code=404, detail="App not found in current scan")\n\n    if not compose_update_supported_for_app(app_item):\n        raise HTTPException(\n            status_code=400,\n            detail=(\n                compose_update_block_reason_for_app(app_item)\n                or "Update installation is not supported for this Compose app"\n            ),\n        )\n\n    if not app_item.get("can_image_update") and not app_item.get("can_version_update"):\n'''
    backend = replace_once(backend, update_guard_anchor, update_guard_new, "app update external compose guard")

    # Policy changes also need the same capability check. Notify/fixed remain
    # available, but installable follow/upgrade policies are blocked externally.
    policy_mode_anchor = '''    mode = str(data.mode or "").strip().lower()\n    if mode not in {"fixed", "upgrade", "follow", "notify"}:\n        raise HTTPException(status_code=400, detail="Unsupported update policy")\n\n    available_tags = policy_available_tags(app_item)\n'''
    policy_mode_new = '''    mode = str(data.mode or "").strip().lower()\n    if mode not in {"fixed", "upgrade", "follow", "notify"}:\n        raise HTTPException(status_code=400, detail="Unsupported update policy")\n\n    if mode in {"upgrade", "follow"} and not compose_update_supported_for_app(app_item):\n        raise HTTPException(\n            status_code=400,\n            detail=(\n                compose_update_block_reason_for_app(app_item)\n                or "Update installation is not supported for this Compose app"\n            ),\n        )\n\n    available_tags = policy_available_tags(app_item)\n'''
    backend = replace_once(backend, policy_mode_anchor, policy_mode_new, "app policy external compose guard")

    # Rebuild aggregate/effective fields after a policy change. Previously only
    # monitor_policy was refreshed, leaving stale effective_status/update counts.
    stale_policy_refresh = '''    with scan_lock:\n        for current in scan_state.get("apps") or []:\n            if current.get("stack_key") == data.stack_key:\n                apply_monitor_policy_fields(current)\n                break\n        save_json(SCAN_FILE, scan_state)\n'''
    rebuilt_policy_refresh = '''    with scan_lock:\n        current_results = list(scan_state.get("results") or [])\n        rebuilt_apps, rebuilt_summary = build_apps(current_results)\n        scan_state["apps"] = rebuilt_apps\n        scan_state["app_summary"] = rebuilt_summary\n        save_json(SCAN_FILE, scan_state)\n'''
    backend = replace_once(backend, stale_policy_refresh, rebuilt_policy_refresh, "policy aggregate refresh")

    # A successful data/image restore must not be reported as a failed restore
    # merely because the restored app's own healthcheck is currently unhealthy.
    restore_helper_anchor = '''def _verify_restore_runtime(meta, container_names):\n'''
    restore_helper = '''def _restore_health_validation_warning(exc):\n    message = str(exc or "").strip()\n    lowered = message.lower()\n    return bool(message) and (\n        "unhealthy" in lowered\n        or "health check" in lowered\n        or "healthcheck" in lowered\n        or "health=" in lowered\n    )\n\n\n'''
    if "def _restore_health_validation_warning(" not in backend:
        backend = replace_once(backend, restore_helper_anchor, restore_helper + restore_helper_anchor, "restore health helper")

    backend = replace_once(
        backend,
        '''        verified_images = _verify_restored_images(meta)\n        verified_runtime = _verify_restore_runtime(meta, names)\n        update_action_progress(stack_key, 99, determinate=True, phase="restore_verify")''',
        '''        verified_images = _verify_restored_images(meta)\n        validation_warning = None\n        try:\n            verified_runtime = _verify_restore_runtime(meta, names)\n        except RuntimeError as exc:\n            if not _restore_health_validation_warning(exc):\n                raise\n            verified_runtime = 0\n            validation_warning = str(exc)\n        update_action_progress(stack_key, 99, determinate=True, phase="restore_verify")''',
        "restore health validation warning",
    )
    backend = replace_once(
        backend,
        '''            "verified_runtime": verified_runtime,\n            "digest_pinned_images": pinned_images,''',
        '''            "verified_runtime": verified_runtime,\n            "validation_warning": validation_warning,\n            "digest_pinned_images": pinned_images,''',
        "restore warning response",
    )

    # Frontend: a full scan is authoritative for every app as well. The backend
    # already uses this rule; matching only app-scoped scans caused the Komga
    # button to remain stuck at "Update wird überprüft" after pending was cleared.
    ui = replace_once(
        ui,
        '''function matchingVerificationScan(scan,stackKey){\n  const key=String(stackKey||'');\n  return !!(\n    scan\n    &&String(scan.scan_scope||'')==='app'\n    &&String(scan.scan_stack_key||'')===key\n  );\n}''',
        '''function matchingVerificationScan(scan,stackKey){\n  const key=String(stackKey||'');\n  if(!scan)return false;\n  const scope=String(scan.scan_scope||'');\n  if(scope==='all')return true;\n  return scope==='app'&&String(scan.scan_stack_key||'')===key;\n}''',
        "frontend verification scope",
    )

    # Surface exact scan/check errors in the Notices popover.
    ui = replace_once(
        ui,
        '''  warnings.push(...dockerVersionWarnings());\n  return warnings;\n}''',
        '''  const apps=(state.data&&Array.isArray(state.data.apps))?state.data.apps:[];\n  for(const app of apps){\n    const items=Array.isArray(app&&app.items)?app.items:[];\n    for(const item of items){\n      if(String(item&&item.status||'')!=='ERROR')continue;\n      const detail=String(item&&item.detail||'').replace(/\\s+/g,' ').trim();\n      if(!detail)continue;\n      warnings.push({\n        kind:'check-error',\n        title:`${String(app&&app.name||'Docker')} · ${t('errorStatus')}`,\n        text:detail\n      });\n    }\n  }\n\n  warnings.push(...dockerVersionWarnings());\n  return warnings;\n}''',
        "notices check error details",
    )
    ui = replace_once(
        ui,
        '''  const important=warnings.filter(warning=>warning.kind==='scan-lock');''',
        '''  const important=warnings.filter(warning=>warning.kind==='scan-lock'||warning.kind==='check-error');''',
        "notices important filter",
    )

    # Also show the exact reason in the app's Docker image details instead of a
    # generic red "Check error" badge with no explanation.
    ui = replace_once(
        ui,
        '''      <div class="update-details-label">${esc(t('remoteDigest'))}</div><div class="update-details-value update-remote-digest-value"><span>${esc(remoteDigest)}</span><button class="docker-info-button" type="button" data-docker-info-stack="${esc(item.stack_key||'')}" data-docker-info-image="${esc(item.image_ref||'')}" title="${esc(t('dockerInformation'))}" aria-label="${esc(t('dockerInformation'))}"><span aria-hidden="true">i</span></button></div>\n    </div>''',
        '''      <div class="update-details-label">${esc(t('remoteDigest'))}</div><div class="update-details-value update-remote-digest-value"><span>${esc(remoteDigest)}</span><button class="docker-info-button" type="button" data-docker-info-stack="${esc(item.stack_key||'')}" data-docker-info-image="${esc(item.image_ref||'')}" title="${esc(t('dockerInformation'))}" aria-label="${esc(t('dockerInformation'))}"><span aria-hidden="true">i</span></button></div>\n      ${String(item.status||'')==='ERROR'&&String(item.detail||'').trim()\n        ? `<div class="update-details-label">${esc(t('errorStatus'))}</div><div class="update-details-value">${esc(String(item.detail).trim())}</div>`\n        : ''}\n    </div>''',
        "card check error details",
    )

    MAIN.write_text(backend, encoding="utf-8")
    INDEX.write_text(ui, encoding="utf-8")


def run_tests() -> None:
    backend = MAIN.read_text(encoding="utf-8")
    ui = INDEX.read_text(encoding="utf-8")
    ast.parse(backend)

    assert 'VERSION = "0.3.346"' in backend

    schedule = function_source(backend, "schedule_app_scan")
    assert "while True:" not in schedule
    assert "while time.time() < deadline:" in schedule
    assert "post_scan_pending.pop(stack_key, None)" in schedule

    build = function_source(backend, "build_apps")
    for token in [
        "compose_working_dir",
        "compose_config_files",
        "compose_update_supported",
        "compose_update_block_reason",
        "managed_compose_projects",
    ]:
        assert token in build, token

    policy_fields = function_source(backend, "apply_monitor_policy_fields")
    assert "compose_update_supported_for_app(app_item)" in policy_fields

    app_update = function_source(backend, "app_update")
    assert "compose_update_supported_for_app(app_item)" in app_update
    assert "schedule_app_scan(" in app_update

    app_policy = function_source(backend, "app_policy")
    assert "rebuilt_apps, rebuilt_summary = build_apps(current_results)" in app_policy
    assert "compose_update_supported_for_app(app_item)" in app_policy

    restore = function_source(backend, "restore_app_backup")
    assert "validation_warning" in restore
    assert "_restore_health_validation_warning" in restore

    # Frontend verification and diagnostics regression checks.
    assert "if(scope==='all')return true;" in ui
    assert "kind:'check-error'" in ui
    assert "warning.kind==='scan-lock'||warning.kind==='check-error'" in ui
    assert "String(item.status||'')==='ERROR'&&String(item.detail||'').trim()" in ui

    # Regression for the tester's English INSTALL report: the uninstall panel
    # must have its own label function and must never reuse installUpdate.
    uninstall_start = ui.index("function renderUninstallPanel")
    uninstall_end = ui.index("function ", uninstall_start + 20)
    uninstall_block = ui[uninstall_start:uninstall_end]
    assert "safeUninstallButtonLabel()" in uninstall_block
    assert "installUpdate" not in uninstall_block
    assert "en:'UNINSTALL'" in ui

    # Frozen summary/header geometry: this repair intentionally makes no CSS
    # replacements; only state/content logic above is touched.
    print("v0.3.346 regression checks: PASS")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--apply"
    if mode == "--apply":
        apply_patch()
        run_tests()
    elif mode == "--test":
        run_tests()
    else:
        raise SystemExit(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
