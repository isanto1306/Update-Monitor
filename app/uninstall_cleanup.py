"""Conservative ZimaOS AppData cleanup after an opted-in uninstall.

Only a uniquely attributable, direct /DATA/AppData child may be removed.
All other bind mounts, external shares and named Docker volumes stay untouched.
"""
import json
import os
import posixpath
import re


APPDATA_ROOT = "/DATA/AppData"
_IGNORED_NAMES = {
    "app", "apps", "data", "docker", "config", "files",
    "server", "service", "default", "database", "web", "root",
    "bear", "big", "zimaos", "casaos", "monitor",
}

# Runs ONLY in an isolated, ephemeral helper container with APPDATA_ROOT bind
# mounted at /um-appdata. Its arguments are individually prevalidated names.
_HELPER_SCRIPT = r'''
import json, os, shutil, stat, sys
from pathlib import Path

root = Path("/um-appdata")
names = sys.argv[1:]
result = {"deleted": [], "absent": [], "failed": []}

# Docker mounts can contain nested mountpoints. Refuse to traverse into a
# separate filesystem even if somebody later creates a mount under AppData.
mountpoints = set()
with open("/proc/self/mountinfo", encoding="utf-8") as f:
    for line in f:
        fields = line.split()
        if len(fields) >= 5:
            mountpoints.add(fields[4].replace(r"\040", " "))

if not shutil.rmtree.avoids_symlink_attacks:
    raise RuntimeError("Safe fd-based tree deletion unavailable")

for name in names:
    try:
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError("Invalid application directory")
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError("AppData root is not a real directory")
        path = root / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            result["absent"].append(name)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Target is not a normal directory")
        target = str(path)
        if any(m == target or m.startswith(target + "/") for m in mountpoints):
            raise RuntimeError("Directory contains a separate mounted filesystem")
        shutil.rmtree(path)
        if path.exists() or path.is_symlink():
            raise RuntimeError("Directory still exists after deletion")
        result["deleted"].append(name)
    except Exception as exc:
        result["failed"].append({"folder": name, "reason": str(exc)[:240]})

print(json.dumps(result))
'''


def _appdata_child(path):
    raw = str(path or "").strip()
    if not raw.startswith(APPDATA_ROOT + "/"):
        return None
    normalized = posixpath.normpath(raw)
    if not normalized.startswith(APPDATA_ROOT + "/"):
        return None
    child = normalized[len(APPDATA_ROOT) + 1:].split("/", 1)[0]
    if not child or child in {".", ".."}:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", child):
        return None
    return child


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _owner_names(app):
    project = str(app.get("compose_project") or "").strip()
    values = [project, app.get("name")]
    values.extend(c.get("name") for c in app.get("containers") or [] if isinstance(c, dict))
    names = set()
    for value in values:
        name = _slug(value)
        if len(name) >= 4 and name not in _IGNORED_NAMES:
            names.add(name)
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower()):
            if len(token) >= 4 and token not in _IGNORED_NAMES:
                names.add(token)
    return names


def _list_containers(run):
    rc, raw, err = run(["docker", "ps", "-aq", "--no-trunc"], timeout=30)
    if rc != 0:
        raise RuntimeError("Cannot verify other containers before AppData deletion: " + (err or raw or "docker ps failed")[:180])
    ids = [v.strip() for v in raw.splitlines() if v.strip()]
    rows = []
    for start in range(0, len(ids), 30):
        rc, raw, err = run(["docker", "inspect", *ids[start:start + 30]], timeout=60)
        if rc != 0:
            raise RuntimeError("Cannot inspect Docker mounts before AppData deletion: " + (err or raw or "docker inspect failed")[:180])
        try:
            chunk = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Could not parse Docker mount inventory") from exc
        if not isinstance(chunk, list) or len(chunk) != len(ids[start:start + 30]):
            raise RuntimeError("Incomplete Docker mount inventory; cleanup blocked")
        rows.extend(chunk)
    return rows


def snapshot_appdata_candidates(app, run):
    """Capture owned AppData paths BEFORE ZimaOS removes the containers."""
    project = str(app.get("compose_project") or "").strip()
    if not project:
        raise RuntimeError("AppData cleanup requires an identified Compose project")
    rows = _list_containers(run)
    own = [
        row for row in rows
        if str(((row.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project") or "").strip() == project
    ]
    if not own:
        raise RuntimeError("Cannot identify live Compose containers to verify their AppData ownership")

    owners = _owner_names(app)
    candidates = set()
    skipped = set()
    for row in own:
        for mount in row.get("Mounts") or []:
            if str(mount.get("Type") or "").lower() != "bind":
                continue
            source = str(mount.get("Source") or "")
            child = _appdata_child(source)
            if child is None:
                continue
            if _slug(child) in owners:
                candidates.add(child)
            else:
                skipped.add(child)

    return {
        "project": project,
        "candidates": sorted(candidates),
        "not_attributable": sorted(skipped - candidates),
    }


def cleanup_appdata_after_uninstall(snapshot, run, self_container_name):
    """Remove only verified unshared children after ZimaOS/Docker removal."""
    candidates = list((snapshot or {}).get("candidates") or [])
    skipped = [{"folder": name, "reason": "AppData directory could not be attributed uniquely to this app"}
               for name in (snapshot or {}).get("not_attributable") or []]
    result = {
        "status": "unverified",
        "deleted": [],
        "already_absent": [],
        "skipped": skipped,
        "failed": [],
        "verified_folders": len(candidates),
    }

    if not candidates:
        result["status"] = "unverified"
        return result

    try:
        other_containers = _list_containers(run)
    except RuntimeError as exc:
        result["failed"].append({"reason": str(exc)})
        result["status"] = "partial"
        return result

    safe = []
    for name in candidates:
        target = APPDATA_ROOT + "/" + name
        conflict = False
        for row in other_containers:
            for mount in row.get("Mounts") or []:
                source = posixpath.normpath(str(mount.get("Source") or ""))
                # A different container using any part of this app's directory
                # retains ownership, including stopped containers and volumes.
                if source == target or source.startswith(target + "/"):
                    conflict = True
                    break
            if conflict:
                break
        if conflict:
            result["skipped"].append({"folder": name, "reason": "Another container still uses this directory"})
        else:
            safe.append(name)

    if safe:
        container = str(self_container_name or "").strip()
        if not container:
            result["failed"].append({"reason": "Cannot identify Update Monitor cleanup helper image"})
        else:
            rc, image_id, err = run(
                ["docker", "inspect", "--format", "{{.Image}}", container],
                timeout=15,
            )
            if rc != 0 or not str(image_id or "").strip().startswith("sha256:"):
                result["failed"].append({"reason": "Cannot identify the running Update Monitor image: " + str(err or "")[:180]})
            else:
                command = [
                    "docker", "run", "--rm",
                    "--pull", "never",
                    "--network", "none",
                    "--read-only",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "--pids-limit", "64",
                    "--memory", "128m",
                    "--entrypoint", "python",
                    "--mount", f"type=bind,source={APPDATA_ROOT},target=/um-appdata",
                    str(image_id).strip(),
                    "-c", _HELPER_SCRIPT,
                    *safe,
                ]
                rc, output, stderr = run(command, timeout=300)
                if rc != 0:
                    result["failed"].append({"reason": "Isolated AppData cleanup helper failed: " + str(stderr or output or f"exit {rc}")[-240:]})
                else:
                    try:
                        payload = json.loads(str(output or "").strip().splitlines()[-1])
                        result["deleted"] = list(payload["deleted"])
                        result["already_absent"] = list(payload["absent"])
                        result["failed"].extend(payload["failed"])
                        reported = set(result["deleted"]) | set(result["already_absent"]) | {
                            entry.get("folder") for entry in payload["failed"]
                        }
                        if reported != set(safe):
                            result["failed"].append({
                                "reason": "Cleanup helper did not account for every selected folder"
                            })
                    except (KeyError, IndexError, TypeError, ValueError):
                        result["failed"].append({"reason": "Cleanup helper returned no verifiable result"})

    result["status"] = (
        "complete" if not result["skipped"] and not result["failed"]
        else "partial"
    )
    return result
