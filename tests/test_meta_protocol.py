"""Stremio /meta must return ResourceResponse.Meta: { "meta": <object> }.

A bare Meta Object cannot be deserialized by stremio-core, which is the
"No metadata was found!" screen. Wrapping as { "meta": [object] } also fails
because `meta` must be an object, not an array.
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "addon" / "catalog" / "movie" / "jav103.json"
META_DIR = ROOT / "addon" / "meta" / "movie"


def load_build_addon():
    path = ROOT / "addon" / "build_addon.py"
    spec = importlib.util.spec_from_file_location("build_addon", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CatalogMetaFiles(unittest.TestCase):
    def test_each_catalog_id_has_a_wrapped_meta_object(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertIn("metas", catalog)
        self.assertTrue(catalog["metas"], "catalog is empty")
        for item in catalog["metas"]:
            path = META_DIR / f"{item['id']}.json"
            self.assertTrue(path.is_file(), f"missing meta file {path.name}")
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(body, dict)
            self.assertIn("meta", body, f"{path.name} is a bare Meta Object, not a ResourceResponse")
            self.assertIsInstance(body["meta"], dict, f"{path.name} wrapped meta as an array")
            self.assertNotIsInstance(body["meta"], list)
            self.assertEqual(body["meta"].get("id"), item["id"])
            self.assertEqual(body["meta"].get("type"), "movie")


class MetaResourceHelper(unittest.TestCase):
    def test_wraps_bare_object_and_unwraps_array(self):
        build = load_build_addon()
        item = {"id": "jav:WAAA-629", "type": "movie", "name": "WAAA-629"}
        wrapped = build.as_meta_resource(item)
        self.assertEqual(wrapped, {"meta": item})
        self.assertEqual(build.as_meta_resource({"meta": item}), {"meta": item})
        self.assertEqual(build.as_meta_resource({"meta": [item]}), {"meta": item})


if __name__ == "__main__":
    unittest.main()
