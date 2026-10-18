"""Regression tests for app-level primary image selection."""
import ast
import re
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text()
TREE = ast.parse(SOURCE)
NAMES = {
    "_primary_group_explicit_service",
    "_primary_group_service_match",
    "_primary_group_auxiliary",
    "select_primary_version_group",
}
NODES = [
    node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name in NAMES
]

def item(repo, service, version):
    return {
        "image_ref": repo + ":" + version,
        "containers": [{"compose_service": service, "ports": []}],
        "available_version_tags": [version],
    }

class PrimaryGroupTests(unittest.TestCase):
    def setUp(self):
        self.groups = []
        self.ns = {
            "build_version_groups": lambda app: self.groups,
            "_version_group_is_infrastructure": lambda group: all(
                i["image_ref"].split(":")[0].rsplit("/", 1)[-1] in {"mongo", "redis", "postgres"}
                for i in group["items"]
            ),
            "_version_item_repo_key": lambda i: i["image_ref"].split(":")[0],
            "_version_group_name_tokens": lambda app: {
                app["name"].lower()
            },
            "_version_group_published_port_count": lambda group: 0,
            "_version_group_score": lambda group, app: 0,
            "re": re,
            "_version_group_has_direct_app_identity": lambda group, app: any(
                app["name"].lower() in i["image_ref"].lower()
                for i in group["items"]
            ),
        }
        exec(compile(ast.Module(body=NODES, type_ignores=[]), "<selection>", "exec"), self.ns)

    def test_scrypted_preferred_over_watchtower(self):
        main = {"items": [item("ghcr.io/koush/scrypted", "scrypted", "latest")]}
        extra = {"items": [item("nickfedor/watchtower", "scrypted-watchtower", "latest")]}
        self.groups = [extra, main]
        self.assertIs(self.ns["select_primary_version_group"]({"name": "Scrypted"}), main)

    def test_divinyl_preferred_over_mongo(self):
        main = {"items": [item("ghcr.io/kyonew/divinyl", "divinyl-app", "latest")]}
        extra = {"items": [item("mongo", "mongodb", "latest")]}
        self.groups = [extra, main]
        self.assertIs(self.ns["select_primary_version_group"]({"name": "Divinyl"}), main)

    def test_explicit_main_service_wins(self):
        left = {"items": [item("example/one", "worker", "latest")]}
        right = {"items": [item("example/two", "frontend", "latest")]}
        self.groups = [left, right]
        self.assertIs(self.ns["select_primary_version_group"]({
            "name": "Project", "main_service": "frontend"
        }), right)

    def test_install_monitor_main_before_registry_sidecar(self):
        registry = {"items": [item("registry", "registry", "2.8.3")]}
        main = {"items": [item("ghcr.io/isanto1306/install-monitor", "install-monitor", "0.2.56-beta.2")]}
        self.groups = [registry, main]
        app = {"name": "Install Monitor", "compose_project": "install-monitor"}
        self.assertIs(self.ns["select_primary_version_group"](app), main)

    def test_only_registry_sidecar_must_not_be_reported_as_install_monitor_version(self):
        registry = {"items": [item("registry", "registry", "2.8.3")]}
        self.groups = [registry]
        self.assertIsNone(self.ns["select_primary_version_group"]({
            "name": "Install Monitor", "compose_project": "install-monitor"
        }))

    def test_standalone_registry_is_still_selectable(self):
        registry = {"items": [item("registry", "registry", "2.8.3")]}
        self.groups = [registry]
        self.assertIs(self.ns["select_primary_version_group"]({
            "name": "Registry", "compose_project": "registry"
        }), registry)

    def test_hyphenated_name_requires_exact_match_not_substring(self):
        group = {"items": [item("ghcr.io/other/install-monitor-helper", "install-monitor-helper", "1.0")]}
        self.assertFalse(self.ns["_version_group_has_direct_app_identity"](
            group, {"name": "Install Monitor", "compose_project": "install-monitor"}
        ))

    def test_ambiguous_unchanged(self):
        self.groups = [
            {"items": [item("example/one", "worker", "latest")]},
            {"items": [item("example/two", "helper", "latest")]},
        ]
        self.assertIsNone(self.ns["select_primary_version_group"]({"name": "Project"}))


if __name__ == "__main__":
    unittest.main()
