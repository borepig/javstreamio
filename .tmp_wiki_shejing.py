#!/usr/bin/env python3
"""Inspect the zh.wikipedia 色情男女 page structure (剧情简介 + cast)."""
import html as html_mod
import re
import urllib.parse
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


url = "https://zh.wikipedia.org/zh-cn/" + urllib.parse.quote("色情男女")
w = get(url)
# find the 剧情简介 section
i = w.find("剧情简介")
print("剧情简介 idx:", i)
if i > 0:
    seg = w[i:i + 1500]
    # print the section text between the heading and the next heading
    j = w.find("<h2", i)
    seg2 = w[i:j if j > 0 else i + 2000]
    text = re.sub(r"<[^>]+>", " ", seg2)
    text = re.sub(r"\s+", " ", html_mod.unescape(text)).strip()
    print("SECTION TEXT:", text[:400])
# cast table
m = re.search(r'<th[^>]*>(?:主演|演員|演员)</th>\s*<td[^>]*>(.*?)</td>', w, re.S)
print("cast:", re.findall(r'<a[^>]*>([^<]+)</a>', m.group(1)) if m else None)
# og:image
p = re.search(r'property="og:image"\s+content="([^"]+)"', w)
print("og:image:", html_mod.unescape(p.group(1)) if p else None)
