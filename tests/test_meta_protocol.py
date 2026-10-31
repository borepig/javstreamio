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
CATALOG_DIR = ROOT / "addon" / "catalog" / "movie"
META_DIR = ROOT / "addon" / "meta" / "movie"
CATALOG_IDS = ("jav-censored", "jav-uncensored", "western", "others")


def load_build_addon():
    path = ROOT / "addon" / "build_addon.py"
    spec = importlib.util.spec_from_file_location("build_addon", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CatalogMetaFiles(unittest.TestCase):
    def test_each_catalog_id_has_a_wrapped_meta_object(self):
        for catalog_id in CATALOG_IDS:
            path = CATALOG_DIR / f"{catalog_id}.json"
            self.assertTrue(path.is_file(), f"missing catalog {path.name}")
            catalog = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("metas", catalog)
            self.assertTrue(catalog["metas"], f"{catalog_id} is empty")
            for item in catalog["metas"][:20]:
                meta_path = META_DIR / f"{item['id']}.json"
                self.assertTrue(meta_path.is_file(), f"missing meta file {meta_path.name}")
                body = json.loads(meta_path.read_text(encoding="utf-8"))
                self.assertIsInstance(body, dict)
                self.assertIn("meta", body, f"{meta_path.name} is a bare Meta Object, not a ResourceResponse")
                self.assertIsInstance(body["meta"], dict, f"{meta_path.name} wrapped meta as an array")
                self.assertNotIsInstance(body["meta"], list)
                self.assertEqual(body["meta"].get("id"), item["id"])
                self.assertEqual(body["meta"].get("type"), "movie")
                released = body["meta"].get("released")
                if released:
                    self.assertRegex(
                        released,
                        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                        f"{meta_path.name} released={released!r} is not RFC3339; "
                        "stremio-core rejects date-only strings and drops the whole meta",
                    )
                poster = item.get("poster") or ""
                self.assertFalse(
                    "vercel.app" in poster or "tu.djhdhs.us" in poster,
                    f"{item['id']} poster still depends on Vercel or the Cloudflare-gated host: {poster}",
                )
                if poster:
                    self.assertFalse(
                        poster.endswith("pl.jpg"),
                        f"{item['id']} catalog still uses fanart (pl.jpg) as the poster: {poster}",
                    )
                background = body["meta"].get("background") or ""
                if background:
                    self.assertFalse(
                        "vercel.app" in background or "tu.djhdhs.us" in background,
                        f"{item['id']} fanart still depends on Vercel or the forum host: {background}",
                    )


class MetaResourceHelper(unittest.TestCase):
    def test_wraps_bare_object_and_unwraps_array(self):
        build = load_build_addon()
        item = {"id": "jav:WAAA-629", "type": "movie", "name": "WAAA-629"}
        wrapped = build.as_meta_resource(item)
        self.assertEqual(wrapped["meta"]["id"], "jav:WAAA-629")
        self.assertEqual(build.as_meta_resource({"meta": item})["meta"]["id"], "jav:WAAA-629")
        self.assertEqual(build.as_meta_resource({"meta": [item]})["meta"]["id"], "jav:WAAA-629")

    def test_date_only_released_becomes_rfc3339(self):
        build = load_build_addon()
        wrapped = build.as_meta_resource(
            {"id": "jav:WAAA-629", "type": "movie", "name": "x", "released": "2026-05-06"}
        )
        self.assertEqual(wrapped["meta"]["released"], "2026-05-06T00:00:00.000Z")

    def test_dmm_poster_from_title_number(self):
        build = load_build_addon()
        self.assertEqual(
            build.dmm_poster("WAAA-629"),
            "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629pl.jpg",
        )
        self.assertEqual(
            build.dmm_poster("WAAA-629", "ps"),
            "https://pics.dmm.co.jp/digital/video/waaa00629/waaa00629ps.jpg",
        )
        self.assertEqual(
            build.dmm_poster("NPJS-263"),
            "https://pics.dmm.co.jp/digital/video/npjs00263/npjs00263pl.jpg",
        )
        self.assertEqual(build.dmm_poster("thread-123"), "")
        self.assertEqual(build.dmm_poster("FC2PPV-4781367"), "")
        self.assertEqual(build.fourhoi_poster("ABF-389"), "https://fourhoi.com/abf-389/cover-n.jpg")
        self.assertEqual(build.fourhoi_poster("AIAV-039"), "https://fourhoi.com/aiav-039/cover-n.jpg")
        self.assertEqual(build.fourhoi_poster("thread-123"), "")
        self.assertEqual(
            build.mgstage_poster("ABF-389"),
            "https://image.mgstage.com/images/prestige/abf/389/pb_e_abf-389.jpg",
        )
        self.assertEqual(build.mgstage_poster("WAAA-629"), "")
        self.assertEqual(
            build.dmm_mono_poster("DLDSS-557"),
            "https://pics.dmm.co.jp/mono/movie/adult/1dldss557/1dldss557ps.jpg",
        )
        self.assertEqual(build.dmm_mono_poster("WAAA-629"), "")
        import importlib.util
        probe_path = ROOT / "addon" / "probe_posters.py"
        spec = importlib.util.spec_from_file_location("probe_posters", probe_path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        self.assertEqual(
            probe.digital_ps("jav:NACT-147"),
            "https://pics.dmm.co.jp/digital/video/nact00147/nact00147ps.jpg",
        )
        self.assertEqual(probe.digital_ps("jav:thread-1"), "")

    def test_western_keeps_sehuatang_cover_for_proxy(self):
        build = load_build_addon()
        rec = {
            "titleno": "Blacked.17.05.30.Natalia.Starr",
            "出演女优": "Natalia.Starr",
            "preview_images": ["https://tu.djhdhs.us/cover.jpg"],
            "magnet": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
        title = build.build_title(rec, use_javdb=False)
        self.assertEqual(title.get("poster"), "")
        self.assertEqual(title.get("forumPoster"), "https://tu.djhdhs.us/cover.jpg")
        self.assertEqual(
            build.forum_cover(rec),
            "https://tu.djhdhs.us/cover.jpg",
        )

    def test_classify_forum_records(self):
        build = load_build_addon()
        self.assertEqual(
            build.classify_record({"titleno": "FC2PPV-4781367", "是否有码": "无码"}),
            "others",
        )
        self.assertEqual(
            build.classify_record({"titleno": "HMN-914", "是否有码": "有码"}),
            "jav-censored",
        )
        self.assertEqual(
            build.classify_record({"titleno": "WAAA-682", "是否有码": "无码"}),
            "jav-uncensored",
        )
        self.assertEqual(
            build.classify_record({
                "titleno": "Blacked.17.05.30.Natalia.Starr",
                "出演女优": "Natalia.Starr",
                "是否有码": "无码",
            }),
            "western",
        )
        self.assertEqual(
            build.classify_record({
                "titleno": "Vixen.26.10.19.Sami.Hayes",
                "出演女优": "Sami.Hayes",
                "是否有码": "无码",
            }),
            "western",
        )
        self.assertFalse(
            build.uses_online_meta({
                "titleno": "Blacked.17.05.30.Natalia.Starr",
                "出演女优": "Natalia.Starr",
            })
        )
        self.assertFalse(build.uses_online_meta({"titleno": "FC2PPV-4781367"}))
        self.assertTrue(build.uses_online_meta({"titleno": "HMN-914", "是否有码": "有码"}))

    def test_record_cast_from_sehuatang_actress(self):
        build = load_build_addon()
        self.assertEqual(build.record_cast({"出演女优": "Natalia.Starr"}), ["Natalia Starr"])
        self.assertEqual(
            build.record_cast({"出演女优": ".Kate.Dalia.And.Maddie.Wren"}),
            ["Kate Dalia", "Maddie Wren"],
        )
        self.assertEqual(
            build.record_cast({"出演女优": "三上悠亚 新有菜 相沢南"}),
            ["三上悠亚", "新有菜", "相沢南"],
        )
        self.assertEqual(build.record_cast({"出演女优": "铃之家铃"}), ["铃之家铃"])

    def test_catalog_extra_includes_search(self):
        build = load_build_addon()
        self.assertEqual(
            [item["name"] for item in build.catalog_extra()],
            ["search", "skip"],
        )

    def test_sqlite_catalog_is_searchable(self):
        import sqlite3
        import tempfile
        from pathlib import Path

        build = load_build_addon()
        titles = [
            {
                "id": "jav:WAAA-629",
                "category": "jav-censored",
                "name": "WAAA-629 title",
                "cast": ["田中"],
                "infoHash": "BBB",
                "posted_at": "2026-08-01",
            },
            {
                "id": "jav:JUR-093",
                "category": "jav-censored",
                "name": "JUR-093 無名の原石妻",
                "cast": ["宝生みさと"],
                "infoHash": "AAA",
                "posted_at": "2026-09-23",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "titles.sqlite"
            build.write_titles_sqlite(titles, dest)
            conn = sqlite3.connect(dest)
            rows = conn.execute(
                "SELECT id FROM titles WHERE category = ? AND search_text LIKE ? AND search_text LIKE ? ORDER BY posted_at DESC",
                ("jav-censored", "%waaa%", "%629%"),
            ).fetchall()
            conn.close()
            self.assertEqual([row[0] for row in rows], ["jav:WAAA-629"])
            self.assertIn("waaa629", build.title_search_text(titles[0]))


class JavdbCover(unittest.TestCase):
    def test_parse_jdbstatic_cover(self):
        import importlib.util
        path = ROOT / "addon" / "javdb.py"
        spec = importlib.util.spec_from_file_location("javdb", path)
        javdb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(javdb)
        html = """
        <html><body>
        <img class="video-cover" src="https://c0.jdbstatic.com/covers/wa/waaa-629.jpg">
        <div class="panel-block"><strong>日期:</strong><span class="value">2026-05-06</span></div>
        </body></html>
        """
        fields = javdb._parse_detail(html)
        self.assertEqual(fields.get("poster"), "https://c0.jdbstatic.com/covers/wa/waaa-629.jpg")
        self.assertEqual(fields.get("fanart"), "https://c0.jdbstatic.com/covers/wa/waaa-629.jpg")

    def test_cache_rejects_mismatched_western_hit(self):
        import importlib.util
        path = ROOT / "addon" / "javdb.py"
        spec = importlib.util.spec_from_file_location("javdb", path)
        javdb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(javdb)
        self.assertTrue(javdb.cache_fits("WAAA-629", {"number": "WAAA-629"}))
        self.assertFalse(
            javdb.cache_fits("Blacked.17.05.30.Natalia.Starr", {"number": "GAR-017"})
        )

    def test_compose_description_uses_forum_title_as_summary(self):
        import importlib.util
        path = ROOT / "addon" / "javdb.py"
        spec = importlib.util.spec_from_file_location("javdb", path)
        javdb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(javdb)
        text = javdb.compose_description(
            {"title": "新任美乳教师轮奸中出班级", "出演女优": "铃之家铃", "影片容量": "5.19G", "是否有码": "有码"},
            {
                "title": "新任美乳教師輪●中出し学級 鈴の家りん",
                "cast": ["鈴の家りん"],
                "genres": ["女教師", "中出"],
                "maker": "ワンズファクトリー",
                "runtime": "120m",
                "rating": "4.35",
            },
        )
        self.assertIn("新任美乳教師輪●中出し学級", text)
        self.assertIn("新任美乳教师轮奸中出班级", text)
        self.assertIn("ワンズファクトリー", text)
        self.assertIn("容量: 5.19G", text)
        self.assertNotIn("出演:", text)
        self.assertNotIn("類型:", text)
        self.assertNotIn("時長:", text)
        self.assertNotIn("JavDB評分", text)

    def test_parse_jav321_official_title(self):
        import importlib.util
        path = ROOT / "addon" / "javdb.py"
        spec = importlib.util.spec_from_file_location("javdb", path)
        javdb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(javdb)
        html = """
        <div class="panel">
          <div>新任美乳教師輪●中出し学級 鈴の家りん</div>
          <div>出演者 : 鈴の家りん</div>
          <div>メーカー : ワンズファクトリー</div>
          <div>配信開始日 : 2026-05-01</div>
        </div>
        """
        fields = javdb._parse_jav321(html)
        self.assertEqual(fields.get("title"), "新任美乳教師輪●中出し学級 鈴の家りん")
        self.assertEqual(fields.get("maker"), "ワンズファクトリー")
        self.assertEqual(fields.get("released"), "2026-05-01")


if __name__ == "__main__":
    unittest.main()
