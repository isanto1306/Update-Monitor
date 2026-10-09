"""Offline safety tests: AppData cleanup must never touch shared NAS data."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "uninstall_cleanup", Path(__file__).resolve().parents[1] / "app" / "uninstall_cleanup.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(name, project, sources):
    return {
        "Name": "/" + name,
        "Config": {"Labels": {"com.docker.compose.project": project}},
        "Mounts": [{"Type": "bind", "Source": source} for source in sources],
    }


class FakeDocker:
    def __init__(self, containers=None, helper=None):
        self.containers = list(containers or [])
        self.helper = helper or {"deleted": [], "absent": [], "failed": []}
        self.commands = []
        self.fail_inventory = False

    def __call__(self, cmd, timeout=None):
        self.commands.append(list(cmd))
        if cmd[:3] == ["docker", "ps", "-aq"]:
            if self.fail_inventory:
                return 1, "", "Docker unavailable"
            return 0, "\n".join(str(i) for i in range(len(self.containers))), ""
        if cmd[:2] == ["docker", "inspect"] and len(cmd) > 3:
            return 0, json.dumps([self.containers[int(i)] for i in cmd[2:]]), ""
        if cmd[:3] == ["docker", "inspect", "--format"]:
            return 0, "sha256:helper-image", ""
        if cmd[:2] == ["docker", "run"]:
            return 0, json.dumps(self.helper), ""
        raise AssertionError(cmd)


class UninstallCleanupTests(unittest.TestCase):
    app = {
        "name": "Trilium",
        "compose_project": "big-bear-trilium",
        "containers": [{"name": "big-bear-trilium"}],
    }

    def test_only_own_appdata_directory_is_selected(self):
        docker = FakeDocker([
            row("big-bear-trilium", "big-bear-trilium", [
                "/DATA/AppData/trilium/config",
                "/DATA/AppData/trilium/data",
                "/DATA/Media/Music",
                "/DATA/AppData/shared-database/db",
            ]),
        ])
        snapshot = module.snapshot_appdata_candidates(self.app, docker)
        self.assertEqual(snapshot["candidates"], ["trilium"])
        self.assertEqual(snapshot["not_attributable"], ["shared-database"])

    def test_does_not_select_parent_or_other_storage(self):
        for source in (
            "/DATA/AppData", "/DATA/AppData/../storage/photos",
            "/DATA/storage", "/DATA/AppData/.", "/media/storage",
        ):
            self.assertIsNone(module._appdata_child(source))
        self.assertEqual(module._appdata_child("/DATA/AppData/trilium/config"), "trilium")

    def test_shared_bind_mount_blocks_deletion(self):
        docker = FakeDocker([row("other-app", "other", ["/DATA/AppData/trilium/db"])])
        result = module.cleanup_appdata_after_uninstall(
            {"candidates": ["trilium"], "not_attributable": []},
            docker, "update-monitor"
        )
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["deleted"])
        self.assertTrue(result["skipped"])
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in docker.commands))

    def test_successful_clean_runs_one_local_isolated_helper(self):
        docker = FakeDocker([], {"deleted": ["trilium"], "absent": [], "failed": []})
        result = module.cleanup_appdata_after_uninstall(
            {"candidates": ["trilium"], "not_attributable": []},
            docker, "update-monitor"
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["deleted"], ["trilium"])
        command = next(cmd for cmd in docker.commands if cmd[:2] == ["docker", "run"])
        for fragment in ("--pull", "never", "--network", "none",
                         "--read-only", "--cap-drop", "ALL", "--security-opt",
                         "no-new-privileges"):
            self.assertIn(fragment, command)
        self.assertIn("sha256:helper-image", command)
        self.assertNotIn("/DATA/storage", " ".join(command))

    def test_errors_never_get_reported_as_complete(self):
        docker = FakeDocker([])
        docker.fail_inventory = True
        result = module.cleanup_appdata_after_uninstall(
            {"candidates": ["trilium"]}, docker, "update-monitor"
        )
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["failed"])

    def test_missing_ownership_is_not_assumed(self):
        docker = FakeDocker([row("foo", "another-project", ["/DATA/AppData/trilium"])])
        with self.assertRaisesRegex(RuntimeError, "Cannot identify live Compose"):
            module.snapshot_appdata_candidates(self.app, docker)

    def test_unattributed_paths_do_not_get_deleted(self):
        docker = FakeDocker([])
        result = module.cleanup_appdata_after_uninstall(
            {"candidates": [], "not_attributable": ["shared-database"]},
            docker, "update-monitor"
        )
        self.assertEqual(result["status"], "unverified")
        self.assertTrue(result["skipped"])
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in docker.commands))

    def test_helper_never_follows_symlink(self):
        with tempfile.TemporaryDirectory() as rootdir, tempfile.TemporaryDirectory() as outside:
            root = Path(rootdir)
            external = Path(outside) / "do-not-delete.txt"
            external.write_text("protected", encoding="utf-8")
            (root / "trilium").mkdir()
            (root / "trilium" / "outside").symlink_to(outside, target_is_directory=True)
            script = module._HELPER_SCRIPT.replace('Path("/um-appdata")', "Path(" + repr(str(root)) + ")")
            completed = subprocess.run(
                [sys.executable, "-c", script, "trilium"],
                capture_output=True, text=True, check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["deleted"], ["trilium"])
            self.assertTrue(external.exists())
            self.assertFalse((root / "trilium").exists())


if __name__ == "__main__":
    unittest.main()
