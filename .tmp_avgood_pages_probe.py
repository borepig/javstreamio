#!/usr/bin/env python3
"""Probe the AvGood /c/vue/ search page + catalog page structure."""
import re
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


# 1) /c/vue/ search page: find its JS bundle
page = get("https://avgood.com/c/vue/")
scripts = re.findall(r'src="(/app/vue/assets/[^"]+\.js)"', page)
print("vue-page scripts:", scripts)
links = re.findall(r'href="(/c/\d+\.html)"', page)
print("detail links on /c/vue/:", len(links))

# 2) /c/612 catalog page structure
cat = get("https://avgood.com/c/612")
print("---- /c/612 size:", len(cat))
print("detail links on /c/612:", len(re.findall(r'href="(/c/\d+\.html)"', cat)))
titles = re.findall(r'<title>(.*?)</title>', cat, re.S)
print("title:", titles[:1])
# show a sample of the page around links
m = re.search(r'href="/c/\d+\.html"[^>]*>([^<]*)<', cat)
print("sample link text:", m.group(1) if m else None)
