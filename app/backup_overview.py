"""Central authenticated index and deletion of completed Update Monitor backups.

Installed and removed Compose apps are both included. Active .incomplete staging
directories and encryption key/configuration files are never listed or deleted.
"""
import shutil
from pathlib import Path
from fastapi import HTTPException, Request


def install_backup_overview(main):
    app = main["app"]
    require_auth = main["require_auth"]
    backup_dir = main["BACKUP_DIR"]
    backup_lock = main["BACKUP_LOCK"]
    action_lock = main["app_update_lock"]
    reject_during_scan = main["reject_mutation_during_scan"]
    load_json = main["load_json"]
    directory_size = main["_backup_directory_size_bytes"]

    def completed_entries():
        if not backup_dir.is_dir() or backup_dir.is_symlink():
            return []
        rows = []
        # The existing cleanup/retention workflow remains independent.
        with backup_lock:
            for app_dir in backup_dir.iterdir():
                if not app_dir.is_dir() or app_dir.is_symlink():
                    continue
                for folder in app_dir.iterdir():
                    if (not folder.is_dir() or folder.is_symlink()
                            or folder.name.endswith(".incomplete")):
                        continue
                    metadata_file = folder / "metadata.json"
                    if metadata_file.is_symlink() or not metadata_file.is_file():
                        continue
                    metadata = load_json(metadata_file, {})
                    if not isinstance(metadata, dict):
                        continue
                    created_at = str(metadata.get("created_at") or "")
                    if not created_at:
                        continue
                    size_bytes = metadata.get("size_bytes")
                    if type(size_bytes) is not int or size_bytes < 0:
                        size_bytes = directory_size(folder)
                    if type(size_bytes) is not int or size_bytes < 0:
                        size_bytes = None
                    rows.append({
                        "entry_id": app_dir.name + "/" + folder.name,
                        "backup_id": str(metadata.get("backup_id") or folder.name),
                        "app_name": str(metadata.get("app_name") or metadata.get("compose_project") or app_dir.name),
                        "compose_project": str(metadata.get("compose_project") or ""),
                        "created_at": created_at,
                        "mode": str(metadata.get("mode") or "quick"),
                        "size_bytes": size_bytes,
                        "encrypted": bool(
                            isinstance(metadata.get("encryption"), dict)
                            and metadata["encryption"].get("encrypted")
                        ),
                    })
        return rows

    @app.get("/api/backup-overview")
    def backup_overview(request: Request):
        require_auth(request)
        rows = completed_entries()
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        sizes = [row["size_bytes"] for row in rows if row["size_bytes"] is not None]
        return {
            "backups": rows,
            "total_count": len(rows),
            "total_size_bytes": sum(sizes),
            "unknown_size_count": len(rows) - len(sizes),
        }

    @app.delete("/api/backup-overview")
    def backup_overview_delete(entry_id: str, request: Request):
        require_auth(request)
        reject_during_scan()
        parts = str(entry_id or "").split("/")
        if len(parts) != 2 or any(
            not part or part in (".", "..") or "\\" in part
            or "\x00" in part or part.endswith(".incomplete")
            for part in parts
        ):
            raise HTTPException(status_code=400, detail="Invalid backup identifier")

        if not action_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="Another update, restore or uninstall is already running",
            )
        try:
            with backup_lock:
                if backup_dir.is_symlink():
                    raise HTTPException(status_code=400, detail="Invalid backup directory")
                app_dir = backup_dir / parts[0]
                folder = app_dir / parts[1]
                meta_file = folder / "metadata.json"
                if (app_dir.is_symlink() or folder.is_symlink() or
                        meta_file.is_symlink() or not meta_file.is_file()):
                    raise HTTPException(status_code=404, detail="Backup not found")
                meta = load_json(meta_file, {})
                if not isinstance(meta, dict) or not meta.get("created_at"):
                    raise HTTPException(status_code=404, detail="Backup not found")
                try:
                    root = backup_dir.resolve(strict=True)
                    resolved = folder.resolve(strict=True)
                except OSError as exc:
                    raise HTTPException(status_code=404, detail="Backup not found") from exc
                if root not in resolved.parents or resolved.parent != app_dir.resolve():
                    raise HTTPException(status_code=400, detail="Unsafe backup path")
                shutil.rmtree(folder)
                try:
                    if not any(app_dir.iterdir()):
                        app_dir.rmdir()
                except OSError:
                    pass
            return {"success": True, "deleted_entry_id": entry_id}
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Failed to delete backup") from exc
        finally:
            action_lock.release()
