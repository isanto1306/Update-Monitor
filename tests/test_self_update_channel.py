"""Self update/channel regression tests: no Docker/ZimaOS mutation is performed."""
import ast
import base64
import subprocess
import sys
import json
import re
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def load(name, bindings):
    module = ast.Module(body=[FUNCTIONS[name]], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = dict(bindings)
    exec(compile(module, "<self-update-tests>", "exec"), scope)
    return scope[name]


def item(version="0.3.423", extra=None):
    value = {
        "image_ref": "ghcr.io/isanto1306/update-monitor:" + version,
        "containers": [{"name": "update-monitor", "compose_service": "update-monitor"}],
    }
    if extra:
        value.update(extra)
    return value


class SelfUpdateRoutingTests(unittest.TestCase):
    def build(self, mode, target, group_items=None):
        own = item()
        group_items = [own] if group_items is None else group_items
        calls = []
        group = {"items": group_items, "available": {
            "display_tags": ["0.3.424", "0.3.423"], "per_alias": {}
        }, "current_tags": ["0.3.423"]}
        bindings = {
            "get_monitor_policy": lambda key: {"mode": mode, "target_tag": target},
            "select_primary_version_group": lambda app: group,
            "build_version_groups": lambda app: [group],
            "_version_group_installed_tag": lambda group: "0.3.423",
            "_version_alias": lambda value: value,
            "_follow_policy_tag": lambda policy, current_tags=None: "latest",
            "_version_group_target_map": lambda group, tag, field: {},
            "is_update_monitor_self_app": lambda app: True,
            "update_monitor_self_identity": lambda: {"container_name": "update-monitor"},
            "perform_self_compose_update_handoff": lambda app, member, tag, policy_mode: (
                calls.append((member, tag, policy_mode)) or {"self_update_handoff": True}
            ),
        }
        return load("perform_version_update", bindings), {
            "stack_key": "update-monitor", "compose_project": "update-monitor"
        }, calls

    def test_follow_tag_routes_to_external_helper(self):
        fn, app, calls = self.build("follow", "latest")
        self.assertTrue(fn(app)["self_update_handoff"])
        self.assertEqual(calls[0][1:], ("latest", "follow"))

    def test_concrete_manual_upgrade_routes_to_external_helper(self):
        fn, app, calls = self.build("upgrade", "0.3.424")
        self.assertTrue(fn(app)["self_update_handoff"])
        self.assertEqual(calls[0][1:], ("0.3.424", "upgrade"))

    def test_sidecar_rejected_before_self_mutation(self):
        fn, app, calls = self.build("follow", "latest", [
            item(), item("stable", {"containers": [
                {"name": "sidecar", "compose_service": "sidecar"}
            ]}),
        ])
        with self.assertRaisesRegex(RuntimeError, "one unambiguous"):
            fn(app)
        self.assertEqual(calls, [])


class SelfComposeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / "self-update.json"
        self.calls = []
        self.before = (
            "services:\\n  update-monitor:\\n"
            "    image: ghcr.io/isanto1306/update-monitor:0.3.423\\n"
            "    environment:\\n      SECRET: confidential-placeholder\\n"
        ).replace("\\n", "\n")
        self.policy = {"auto_enabled": False, "auto_time": "03:00", "auto_days": [],
                       "auto_timezone": "Europe/Berlin", "auto_backup_mode": "quick"}
        self.app = {
            "stack_key": "self", "compose_project": "update-monitor",
            "containers": [{"name": "update-monitor"}],
        }

    def run_update(self, tag="latest", mode="follow", current=None):
        self.calls = []
        saved = {}
        def mock_replace(yaml, services, target):
            self.calls.append(("replace", target))
            self.assertEqual(services, ["update-monitor"])
            return yaml.replace(":0.3.423", ":" + tag), 1, ["update-monitor"]
        def record_marker(path, payload):
            saved["marker"] = payload.copy()
        def start_helper(payload):
            self.calls.append(("launch", payload["image_ref"]))
            path = Path(payload["compose_payload_file"])
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            doc = json.loads(path.read_text())
            self.assertIn("confidential-placeholder", doc["original_yaml"])
            self.assertEqual(doc["target_ref"], "ghcr.io/isanto1306/update-monitor:" + tag)
            self.assertNotIn("original_yaml", payload)
            self.assertNotIn("target_yaml", payload)
            return "helper-123"
        def change_policy(key, mode_value, tag_value, **kwargs):
            self.calls.append(("policy", mode_value, tag_value))
            return {"mode": mode_value, "target_tag": tag_value}

        fn = load("perform_self_compose_update_handoff", {
            "is_update_monitor_self_app": lambda app: True,
            "_is_update_channel_tag": lambda s: s in ("latest", "stable"),
            "update_monitor_self_identity": lambda: {"container_name": "update-monitor",
                                                       "container_id": "original-id"},
            "docker_container_id": lambda name: "original-id",
            "docker_container_config_image": lambda name: current or "ghcr.io/isanto1306/update-monitor:0.3.423",
            "parse_image_ref": lambda ref: {
                "repo": ref.split(":")[0], "pinned_digest": "@sha256:" in ref,
            },
            "casaos_compose_yaml": lambda project: self.before,
            "replace_compose_service_images": mock_replace,
            "casaos_apply_compose": lambda project, yaml, dry_run: self.calls.append(("dry_run", dry_run)),
            "scan_lock": threading.RLock(),
            "scan_state": {"platform": "linux/amd64"},
            "update_action_progress": lambda *a, **kw: None,
            "docker_pull_verified": lambda ref, platform: {
                "image_ref": ref, "expected_digests": ["sha256:verified"],
            },
            "CACHE_DIR": self.root,
            "secrets": SimpleNamespace(token_hex=lambda n: "a" * (n * 2)),
            "os": os, "json": json,
            "docker_container_image_id": lambda name: "sha256:original",
            "utc_now": lambda: "2026-10-18T14:00:00Z",
            "SERVICE_STARTED_AT": "test",
            "SELF_UPDATE_STATE_FILE": self.marker,
            "save_json": record_marker,
            "_launch_self_update_helper": start_helper,
            "get_monitor_policy": lambda key: self.policy,
            "save_monitor_policy": change_policy,
        })
        result = fn(self.app, item(), tag, policy_mode=mode)
        return result, saved

    def test_follow_from_fixed_uses_dry_run_and_does_not_mutate_in_process(self):
        result, saved = self.run_update()
        self.assertTrue(result["self_update_handoff"])
        self.assertEqual(result["new_image_ref"], "ghcr.io/isanto1306/update-monitor:latest")
        self.assertIn(("dry_run", True), self.calls)
        self.assertNotIn(("dry_run", False), self.calls)
        self.assertLess(self.calls.index(("dry_run", True)), self.calls.index(("launch", result["new_image_ref"])))
        self.assertNotIn("confidential-placeholder", json.dumps(saved["marker"]))
        self.assertEqual(self.calls[-1], ("policy", "follow", "latest"))

    def test_manual_upgrade_ends_in_fixed_mode(self):
        result, _ = self.run_update(tag="0.3.424", mode="upgrade")
        self.assertTrue(result["self_update_handoff"])
        self.assertEqual(self.calls[-1], ("policy", "fixed", None))

    def test_unisolated_container_rejected(self):
        self.app["containers"].append({"name": "another-container"})
        with self.assertRaisesRegex(RuntimeError, "isolated"):
            self.run_update()
        self.assertEqual(self.calls, [])

    def test_digest_pinned_self_update_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Digest-pinned"):
            self.run_update(current="ghcr.io/isanto1306/update-monitor:0.3.423@sha256:abc")

    def test_no_real_update_when_target_is_already_in_compose(self):
        self.before = self.before.replace(":0.3.423", ":latest")
        with self.assertRaisesRegex(RuntimeError, "already configured"):
            self.run_update(tag="latest")


class ExternalHelperTests(unittest.TestCase):
    def test_script_parses_and_has_rollback_and_secret_cleanup(self):
        assignment = next(
            node for node in TREE.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_SELF_UPDATE_HELPER_SCRIPT"
                    for t in node.targets)
        )
        script = ast.literal_eval(assignment.value)
        compile(script, "<external-self-helper>", "exec")
        self.assertIn("compose_request(str(compose_data['target_yaml']))", script)
        self.assertIn("compose_request(str(compose_data['original_yaml']))", script)
        self.assertIn("compose_payload_path.unlink(missing_ok=True)", script)
        self.assertIn("repo_digests.intersection(expected)", script)
        self.assertIn("persisted Compose target disagrees", script)
        self.assertIn("current_id != old_id", script)

    def test_channel_policy_uses_handoff_after_validation(self):
        source = ast.get_source_segment(SOURCE, FUNCTIONS["perform_image_channel_switch"])
        self.assertIn("perform_self_compose_update_handoff(", source)
        self.assertLess(source.index("target_tag.casefold() not in registry_lookup"),
                        source.index("perform_self_compose_update_handoff("))
        self.assertLess(source.index("perform_self_compose_update_handoff("),
                        source.index("casaos_apply_compose(project, updated_yaml, dry_run=False)"))

    def test_switch_does_not_optimistically_mutate_scan_during_self_restart(self):
        endpoint = ast.get_source_segment(SOURCE, FUNCTIONS["image_channel_switch"])
        self.assertIn('result.get("self_update_handoff")', endpoint)
        self.assertIn("register_successful_image_source_switch", endpoint)




class SimulatedHelperExecutionTests(unittest.TestCase):
    """Exercise the actual standalone helper against fake local Docker/ZimaOS."""

    def test_external_helper_applies_verifies_and_rolls_back(self):
        assignment = next(
            node for node in TREE.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_SELF_UPDATE_HELPER_SCRIPT"
                    for t in node.targets)
        )
        source = ast.literal_eval(assignment.value)

        for scenario in ("success", "rollback"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                marker = root / "self-update.json"
                base_url = root / "app-management.url"
                compose_payload = root / "self-update-compose-test.json"
                old_yaml = "services:\\n  update-monitor:\\n    image: example/app:0.3.423\\n"
                new_yaml = "services:\\n  update-monitor:\\n    image: example/app:latest\\n"
                compose_payload.write_text(json.dumps({
                    "original_yaml": old_yaml, "target_yaml": new_yaml,
                    "target_ref": "example/app:latest",
                }), encoding="utf-8")
                base_url.write_text("http://127.0.0.1:9999", encoding="utf-8")
                payload = {
                    "old_container_id": "old-id", "container_name": "update-monitor",
                    "compose_project": "update-monitor", "stack_key": "self",
                    "image_ref": "example/app:latest",
                    "expected_digests": ["sha256:newimage"],
                    "compose_payload_file": str(compose_payload),
                }
                mocked_script = source.replace("/app/cache", str(root)).replace(
                    "/host/run/casaos/app-management.url", str(base_url)
                )
                prelude = """
import json, os, subprocess, time, types, urllib.request
scenario = os.environ['TEST_SCENARIO']
mock_state = {'persisted': 'services:\\\\n  update-monitor:\\\\n    image: example/app:0.3.423\\\\n',
         'new': False, 'rolled_back': False}
class Response:
    status = 200
    def __init__(self, body=b''): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n=-1): return self.body
def fake_open(request, timeout=None):
    url = request.full_url
    method = request.get_method()
    if '/compose/' in url:
        if method == 'PUT':
            body = request.data.decode('utf-8')
            mock_state['persisted'] = body
            if 'example/app:latest' in body: mock_state['new'] = True
            else:
                mock_state['new'] = False
                mock_state['rolled_back'] = True
            return Response(b'{}')
        if method == 'GET':
            if scenario == 'rollback' and mock_state['new']:
                return Response(b'wrong persisted compose')
            return Response(mock_state['persisted'].encode())
    if '/container/' in url and method == 'PATCH':
        mock_state['new'] = True
        return Response(b'{}')
    raise RuntimeError('Unexpected ZimaOS request: ' + method + ' ' + url)
def fake_run(args, **kwargs):
    if args[:2] == ['docker', 'inspect']:
        new = mock_state['new']
        rows = [{'Id': 'new-id' if new else 'old-id',
                 'Image': 'sha256:new' if new else 'sha256:old',
                 'State': {'Running': True, 'Restarting': False,
                           'Health': {'Status': 'healthy'}}}]
    elif args[:3] == ['docker', 'image', 'inspect']:
        rows = [{'RepoDigests': ['example/app@sha256:newimage']}]
    else:
        raise RuntimeError('Unexpected Docker command: ' + repr(args))
    return types.SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr='')
urllib.request.urlopen = fake_open
subprocess.run = fake_run
time.sleep = lambda n: None
"""
                env = os.environ.copy()
                env["UM_SELF_UPDATE_PAYLOAD"] = base64.b64encode(
                    json.dumps(payload).encode("utf-8")
                ).decode("ascii")
                env["TEST_SCENARIO"] = scenario
                result = subprocess.run(
                    [sys.executable, "-c", prelude + "\n" + mocked_script],
                    capture_output=True, text=True, timeout=15, env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(marker.read_text(encoding="utf-8"))
                if scenario == "success":
                    self.assertEqual(status["status"], "restarted-verified", status)
                    self.assertEqual(status["matched_digests"], ["sha256:newimage"])
                else:
                    self.assertEqual(status["status"], "failed")
                    self.assertEqual(status["rollback"], "original-compose-restored")
                self.assertFalse(compose_payload.exists(), "helper left credentials on disk")

if __name__ == "__main__":
    unittest.main()
