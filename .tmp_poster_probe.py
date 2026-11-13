#!/usr/bin/env python3
"""Probe poster pipeline: SearXNG douban hits + douban og:image + HKMDB page."""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

SXNG = "http://192.168.68.83:8888"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
OG_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.I)

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def sxng(query, engines="google cse,wikipedia"):
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    url = f"{SXNG}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "javstreamio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  sxng '{query}' failed: {e}", file=sys.stderr)
        return []
    return [
        {"title": x.get("title") or "", "url": x.get("url") or "", "engine": x.get("engine") or ""}
        for x in data.get("results", [])
    ]

# 1) SearXNG: does `site:douban.com <title>` return douban hits via google cse?
for q in ["site:douban.com 情欲城巿 1992", "site:douban.com 蜜桃成熟时 1993", "douban 蜜桃成熟时"]:
    res = sxng(q)
    print(f"SearXNG '{q}': {len(res)} results")
    for r in res[:3]:
        print(f"  [{r['engine']}] {r['url']}")
    time.sleep(0.5)

# 2) Douban subject page → og:image
print("\n-- douban og:image probe --")
for u in [
    "https://movie.douban.com/subject/1295151/",  # 蜜桃成熟时 (1993) guess id — may differ
]:
    try:
        html = get(u)
        m = OG_RE.search(html)
        print(f"{u} -> og:image = {m.group(1) if m else 'NOT FOUND'}")
    except Exception as e:
        print(f"{u} fetch failed: {e}")

# 3) HKMDB page for id 11701 (欲望都市之3P诱惑 2006)
print("\n-- HKMDB probe --")
for u in ["https://hkmdb.com/movies/11701", "https://hkmdb.com/movie/11701"]:
    try:
        html = get(u)
        m = OG_RE.search(html)
        imgs = re.findall(r'<img[^>]+src="([^"]+)"', html)
        print(f"{u} -> og:image = {m.group(1) if m else 'NOT FOUND'}; first imgs = {imgs[:3]}")
    except Exception as e:
        print(f"{u} fetch failed: {e}")
