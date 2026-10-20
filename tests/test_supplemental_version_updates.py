"""Regressions: a newer sidecar release is not a main-app update.

Run without Docker, secrets or importing the production server module.
"""
import ast
from pathlib import Path
import unittest


source = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
tree = ast.parse(source)
fn = next(
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "_split_app_version_updates"
)
namespace = {}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "<sidecar-updates>", "exec"), namespace)
classify = namespace["_split_app_version_updates"]


def img(image, status="CURRENT", tag="latest", newer=None):
    return {"image_ref": image, "tag": tag, "newer_tag": newer, "status": status}


class SidecarVersionTests(unittest.TestCase):
    def test_nametag_alpine_upgrade_is_informational(self):
        main = img("ghcr.io/mattgodoy/nametag:latest")
        alpine = img("alpine:3.20", "VERSION_UPDATE", "3.20", "3.24.2")
        postgres = img("postgres:16-alpine")
        main_count, sidecars = classify({
            "items": [alpine, main, postgres],
            "version_group": {"image_refs": [main["image_ref"]]},
        })
        self.assertEqual(main_count, 0)
        self.assertEqual(sidecars, [{
            "image_ref": "alpine:3.20",
            "installed_tag": "3.20",
            "available_tag": "3.24.2",
        }])

    def test_main_and_sidecar_updates_are_not_confused(self):
        main = img("ghcr.io/mattgodoy/nametag:latest", "VERSION_UPDATE", "latest", "0.65.0")
        alpine = img("alpine:3.20", "VERSION_UPDATE", "3.20", "3.24.2")
        n, sidecars = classify({
            "items": [alpine, main],
            "version_group": {"image_refs": [main["image_ref"]]},
        })
        self.assertEqual(n, 1)
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(sidecars[0]["image_ref"], alpine["image_ref"])

    def test_shared_release_family_keeps_all_main_updates(self):
        a = img("immich/server:1.0", "VERSION_UPDATE", "1.0", "1.1")
        b = img("immich/ml:1.0", "VERSION_UPDATE", "1.0", "1.1")
        n, sidecars = classify({
            "items": [a, b],
            "version_group": {"image_refs": [a["image_ref"], b["image_ref"]]},
        })
        self.assertEqual(n, 2)
        self.assertEqual(sidecars, [])

    def test_ambiguous_primary_keeps_legacy_count(self):
        a = img("example/service:1", "VERSION_UPDATE", "1", "2")
        n, sidecars = classify({"items": [a], "version_group": None})
        self.assertEqual(n, 1)
        self.assertEqual(sidecars, [])

    def test_same_tag_image_update_is_unaffected(self):
        a = img("ghcr.io/mattgodoy/nametag:latest", "IMAGE_UPDATE")
        b = img("alpine:3.20", "VERSION_UPDATE", "3.20", "3.24.2")
        n, sidecars = classify({
            "items": [a, b],
            "version_group": {"image_refs": [a["image_ref"]]},
        })
        self.assertEqual(n, 0)
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(a["status"], "IMAGE_UPDATE")


if __name__ == "__main__":
    unittest.main()
