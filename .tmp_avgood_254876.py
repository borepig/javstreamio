#!/usr/bin/env python3
"""Verify the user's example: /c/254876.html magnet + search-page coverage."""
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


det = get("https://avgood.com/c/254876.html")
mags = re.findall(r'magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})', det)
print("/c/254876.html magnets:", mags)
d = re.search(r'发行日期:(\d{4}-\d{2}-\d{2})', det)
print("发行日期:", d.group(1) if d else None)
m = re.search(r'<meta name="description" content="([^"]*)"', det)
print("meta desc:", (m.group(1) if m else None)[:200])

# Does the search page return 254876 for 色情男女?
page = get("https://avgood.com/c/s/?q=" + quote("色情男女"))
body = page[page.find("<body"):]
ids = re.findall(r'<a href="/c/(\d+)\.html"[^>]*>(.*?)</a>', body, re.S)
print("search result ids:", [i for i, t in ids])
print("254876 in results:", "254876" in [i for i, t in ids])
for i, t in ids:
    if "色情男女" in t:
        print("match:", i, re.sub(r"<[^>]+>", "", t).strip()[:80])
