#!/usr/bin/env python3
"""Verify the AvGood detail + wiki page for 色情男女."""
import html as html_mod
import re
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


# 1) AvGood detail page for the search hit 611507
det = get("https://avgood.com/c/611507.html")
print("=== /c/611507.html ===")
mags = re.findall(r'magnet:\?xt=urn:btih:([A-Fa-f0-9]{40})', det)
print("magnets:", mags)
m = re.search(r'<meta name="description" content="([^"]*)"', det)
print("meta desc:", (m.group(1) if m else None)[:250])
m = re.search(r'rel="poster" href="([^"]+)"', det)
print("poster:", m.group(1) if m else None)
# video duration + size if present
m = re.search(r'(\d{1,2}:\d{2}:\d{2})', det)
print("duration:", m.group(1) if m else None)

# 2) wiki page
try:
    w = get("https://zh.wikipedia.org/zh-cn/色情男女")
    print("=== wiki 色情男女 ===")
    t = re.search(r'<title>(.*?)</title>', w, re.S)
    print("title:", t.group(1).split(" - ")[0] if t else None)
    p = re.search(r'property="og:image"\s+content="([^"]+)"', w)
    print("og:image:", html_mod.unescape(p.group(1)) if p else None)
except Exception as e:
    print("wiki fetch error:", e)
