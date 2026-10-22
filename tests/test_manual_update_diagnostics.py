"""Regression tests for authenticated manual-update diagnostics."""
import ast
import re
import threading
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {"update_action_progress", "finish_action_progress"}
NODES = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
BRIDGES = {}
for node in TREE.body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {
                "_INDEX_ASYNC_UPDATE_BRIDGE", "_INDEX_ERROR_LOCALIZATION_BRIDGE"
            }:
                BRIDGES[target.id] = ast.literal_eval(node.value)


class ManualUpdateDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.store = {"app": {
            "kind": "update",
            "phase": "starting",
            "current_container": None,
            "finished": False,
            "success": None,
            "error": None,
        }}
        self.env = {
            "action_progress_lock": threading.RLock(),
            "action_progress": self.store,
            "_clamp_progress": lambda value: int(value),
            "utc_now": lambda: "2026-10-22T09:00:00+00:00",
            "re": re,
        }
        exec(compile(ast.Module(body=NODES, type_ignores=[]), "<diagnostics>", "exec"), self.env)

    def test_recreate_failure_exposes_phase_container_and_original_reason(self):
        self.env["update_action_progress"]("app", phase="recreating", container_name="big-bear-portainer")
        self.env["finish_action_progress"]("app", False, "ZimaOS container recreate failed (HTTP 500)")
        info = self.store["app"]
        self.assertTrue(info["finished"])
        self.assertFalse(info["success"])
        self.assertEqual(info["phase"], "error")
        self.assertEqual(info["update_diagnostic"], {
            "step": "recreating",
            "container": "big-bear-portainer",
            "technical_detail": "ZimaOS container recreate failed (HTTP 500)",
        })

    def test_pull_error_handles_single_image_without_container(self):
        self.env["update_action_progress"]("app", phase="pulling", container_name="")
        self.env["finish_action_progress"]("app", False, "Docker pull failed (HTTP 429)")
        self.assertEqual(self.store["app"]["update_diagnostic"]["step"], "pulling")
        self.assertEqual(self.store["app"]["update_diagnostic"]["container"], "")

    def test_redacts_credentials_in_diagnostic(self):
        self.env["finish_action_progress"](
            "app", False, "password=topsecret token:abc123 https://me:password@example.test/api"
        )
        detail = self.store["app"]["update_diagnostic"]["technical_detail"]
        self.assertNotIn("topsecret", detail)
        self.assertNotIn("abc123", detail)
        self.assertNotIn("me:password", detail)
        self.assertIn("[redacted]", detail)

    def test_success_does_not_gain_error_diagnostic(self):
        self.env["finish_action_progress"]("app", True)
        self.assertTrue(self.store["app"]["success"])
        self.assertNotIn("update_diagnostic", self.store["app"])

    def test_existing_async_error_is_passed_to_localized_diagnostic(self):
        async_bridge = BRIDGES["_INDEX_ASYNC_UPDATE_BRIDGE"]
        localization = BRIDGES["_INDEX_ERROR_LOCALIZATION_BRIDGE"]
        self.assertIn("failure.updateMonitorDiagnostic=info.update_diagnostic", async_bridge)
        self.assertIn("formatManualUpdateDiagnostic(error.updateMonitorDiagnostic,currentLanguage())", localization)
        for language in ("de:", "en:", "fr:", "pt:", "es:"):
            self.assertIn(language, localization)
        self.assertIn("technical_detail", localization)


if __name__ == "__main__":
    unittest.main()
