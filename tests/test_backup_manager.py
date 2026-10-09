"""Offline regression tests for all-Docker backup overview and deletion safety."""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile
import threading
import unittest


TREE = ast.parse(Path("app/main.py").read_text(encoding="utf-8"))
FUNCTIONS = {
    node.name: node for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def extract(name, scope):
    module = ast.Module(body=[FUNCTIONS[name]], type_ignores=[])
    ast.fix_missing_locations(module)
    local = dict(scope)
    exec(compile(module, "<backup-overview>", "exec"), local)
    return local[name]


class BackupManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "backups"
        self.root.mkdir()
        self.lock = threading.Lock()

    def tearDown(self):
        self.temp.cleanup()

    def add_backup(self, folder, backup_id, app_name, size=1024):
        path = self.root / folder / backup_id
        path.mkdir(parents=True)
        meta = {
            "backup_id": backup_id,
            "app_name": app_name,
            "compose_project": folder,
            "stack_key": folder + "-key",
            "created_at": "2026-10-09T12:00:00+00:00",
            "size_bytes": size,
            "mode": "full",
            "container_count": 1,
        }
        (path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        return path

    def common(self):
        return {
            "BACKUP_DIR": self.root,
            "BACKUP_LOCK": self.lock,
            "load_json": lambda path, default: json.loads(path.read_text(encoding="utf-8")),
            "re": re,
            "Path": Path,
            "shutil": shutil,
        }

    def inventory(self):
        scope = self.common()
        scope.update({
            "datetime": datetime,
            "timezone": timezone,
            "cleanup_expired_backups": lambda: None,
            "_parse_backup_timestamp": datetime.fromisoformat,
            "backup_expiry_for_created": lambda created: None,
            "_backup_directory_size_bytes": lambda root: 2048,
            "_normalize_backup_mode": lambda value, fallback: value or fallback,
            "_backup_container_count_from_metadata": lambda meta: meta.get("container_count", 0),
        })
        return extract("all_docker_backup_list", scope)()

    def deletion(self):
        return extract("delete_global_docker_backup", self.common())

    def test_lists_all_dockers_including_uninstalled_ones(self):
        self.add_backup("plex", "20261009T120000Z-aaaaaa", "Plex", 4096)
        self.add_backup("old-app", "20261009T120000Z-bbbbbb", "No longer installed", 0)
        staged = self.root / "plex" / "20261009T130000Z-cccccc.incomplete"
        staged.mkdir()
        (staged / "metadata.json").write_text("{}", encoding="utf-8")
        items = self.inventory()
        self.assertEqual(len(items), 2)
        self.assertEqual({r["name"] for r in items}, {"Plex", "No longer installed"})
        self.assertEqual({r["size_bytes"] for r in items}, {0, 4096})
        self.assertTrue(all("folder_id" in r and "backup_id" in r for r in items))

    def test_missing_size_falls_back_to_directory_measurement(self):
        path = self.add_backup("plex", "20261009T120000Z-aaaaaa", "Plex", None)
        items = self.inventory()
        self.assertEqual(items[0]["size_bytes"], 2048)

    def test_only_selected_backup_is_deleted(self):
        target = self.add_backup("plex", "20261009T120000Z-aaaaaa", "Plex")
        other = self.add_backup("plex", "20261009T130000Z-bbbbbb", "Plex")
        deleted = self.deletion()("plex", target.name)
        self.assertEqual(deleted, target.name)
        self.assertFalse(target.exists())
        self.assertTrue(other.exists())

    def test_traversal_is_rejected(self):
        delete = self.deletion()
        for folder, backup in [
            ("..", "something"), ("plex/../other", "something"),
            ("plex", "../../outside"), ("plex", "."),
            ("plex", "x/y"),
        ]:
            with self.subTest(folder=folder, backup=backup):
                with self.assertRaises(RuntimeError):
                    delete(folder, backup)

    def test_symlink_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "external"
            target.mkdir()
            (target / "keep.txt").write_text("preserve", encoding="utf-8")
            (self.root / "redirect").symlink_to(target, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                self.deletion()("redirect", "keep.txt")
            self.assertTrue((target / "keep.txt").is_file())

    def test_mismatched_metadata_refuses_deletion(self):
        backup = self.add_backup("plex", "20261009T120000Z-aaaaaa", "Plex")
        meta = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))
        meta["backup_id"] = "different"
        (backup / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "metadata"):
            self.deletion()("plex", backup.name)
        self.assertTrue(backup.exists())


if __name__ == "__main__":
    unittest.main()
