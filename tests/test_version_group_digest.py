"""Regression tests for digest-pinned ZimaOS version activation.

These run against the production function bodies extracted from main.py while
using simulated Docker/ZimaOS state. No real containers are modified.
"""
import ast
from pathlib import Path
import unittest


SOURCE = Path("app/main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def load_function(name, bindings):
    node = FUNCTIONS[name]
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = dict(bindings)
    exec(compile(module, "<digest-pin-regression>", "exec"), scope)
    return scope[name]


class Clock:
    def __init__(self):
        self.now = 0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class VersionGroupDigestTests(unittest.TestCase):
    target = "triliumnext/trilium:v0.106.0@sha256:be93"
    old = "triliumnext/trilium:v0.105.0"
    new = "triliumnext/trilium:v0.106.0"

    def build_waiter(self, runtime_is_new=False, allow_recreate=True):
        clock = Clock()
        state = {"new": runtime_is_new, "recreates": []}

        def recreate(container_id, project, **kwargs):
            state["recreates"].append((container_id, project, kwargs))
            if allow_recreate:
                state["new"] = True

        def digest_match(name, ref, expected, expected_image_id=None):
            self.assertEqual(name, "big-bear-trilium")
            self.assertEqual(ref, self.target)
            self.assertEqual(expected, ["sha256:be93"])
            self.assertEqual(expected_image_id, "sha256:image-106")
            return state["new"], ({"sha256:be93"} if state["new"] else {"sha256:old"})

        def same_ref(left, right):
            return left == right

        fn = load_function("wait_for_version_group_compose_update", {
            "time": clock,
            "casaos_compose_yaml": lambda project: "saved compose",
            "compose_service_image_map_from_yaml": lambda raw: {"app": self.target},
            "image_refs_semantically_equal": same_ref,
            "docker_container_config_image": lambda name: self.new if state["new"] else self.old,
            "docker_container_id": lambda name: "container-123",
            "container_matches_target_digest": digest_match,
            "casaos_recreate_container": recreate,
        })
        kwargs = {
            "compose_project": "big-bear-trilium",
            "expected_by_container": {"big-bear-trilium": self.target},
            "timeout": 40,
            "target_verification": {self.target: {
                "expected_digests": ["sha256:be93"],
                "image_id": "sha256:image-106",
            }},
            "original_container_ids": {"big-bear-trilium": "container-123"},
            "recreate_after": 10,
        }
        return fn, clock, state, kwargs

    def test_accepts_verified_digest_with_tag_only_docker_config_image(self):
        fn, clock, state, kw = self.build_waiter(runtime_is_new=True)
        actual = fn(**kw)
        self.assertEqual(actual, {"big-bear-trilium": self.new})
        self.assertFalse(state["recreates"])
        self.assertEqual(clock.now, 0)

    def test_recreates_old_container_once_then_accepts_target_digest(self):
        fn, clock, state, kw = self.build_waiter(runtime_is_new=False)
        actual = fn(**kw)
        self.assertEqual(actual, {"big-bear-trilium": self.new})
        self.assertEqual(len(state["recreates"]), 1)
        self.assertGreaterEqual(clock.now, 10)

    def test_does_not_report_success_for_old_image_after_failed_recreate(self):
        fn, clock, state, kw = self.build_waiter(runtime_is_new=False, allow_recreate=False)
        with self.assertRaisesRegex(RuntimeError, "did not reach the verified target"):
            fn(**kw)
        self.assertEqual(len(state["recreates"]), 1)

    def test_requires_verified_target_evidence(self):
        fn, clock, state, kw = self.build_waiter()
        kw["target_verification"] = {}
        with self.assertRaisesRegex(RuntimeError, "No verified target"):
            fn(**kw)
        self.assertFalse(state["recreates"])

    def test_runtime_wait_accepts_verified_digest_even_without_pin_in_config(self):
        clock = Clock()
        fn = load_function("wait_for_container_target_digest", {
            "time": clock,
            "docker_container_config_image": lambda name: self.new,
            "container_matches_target_digest": lambda *args, **kwargs: (True, {"sha256:be93"}),
            "parse_image_ref": lambda ref: {"pinned_digest": ref.split("@", 1)[1] if "@" in ref else None},
            "image_refs_semantically_equal": lambda left, right: left == right,
            "docker_container_image_id": lambda name: "sha256:image-106",
        })
        result = fn(
            "big-bear-trilium", self.target, ["sha256:be93"],
            timeout=20, expected_image_id="sha256:image-106",
        )
        self.assertEqual(result["image_ref"], self.new)

    def test_runtime_wait_never_accepts_unverified_digest(self):
        clock = Clock()
        fn = load_function("wait_for_container_target_digest", {
            "time": clock,
            "docker_container_config_image": lambda name: self.new,
            "container_matches_target_digest": lambda *args, **kwargs: (False, {"sha256:old"}),
            "parse_image_ref": lambda ref: {"pinned_digest": ref.split("@", 1)[1] if "@" in ref else None},
            "image_refs_semantically_equal": lambda left, right: left == right,
            "docker_container_image_id": lambda name: "sha256:oldimage",
        })
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            fn("big-bear-trilium", self.target, ["sha256:be93"], timeout=8)


if __name__ == "__main__":
    unittest.main()
