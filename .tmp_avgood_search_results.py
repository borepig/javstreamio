#!/usr/bin/env python3
"""Extract AvGood search results for 色情男女 + check detail-page magnet."""
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


kw = quote("色情男女")
page = get(f"https://avgood.com/c/s/?q={kw}")
body = page[page.find("<body"):]
print("search results (title -> detail id):")
for m in list(re.finditer(r'<a[^>]+href="(/c/\d+\.html)"[^>]*>([^<]{2,60})</a>', body))[:24]:
    print("  ", m.group(2), "->", m.group(1))

# Does the static detail page carry a magnet?
det = get("https://avgood.com/c/409794.html")
mag = re.findall(r'magnet:\?xt=urn:btih:[A-Fa-f0-9]{40}', det)
print("static /c/409794.html magnets:", mag)
print("detail page size:", len(det))
# meta description (release date etc.)
m = re.search(r'<meta name="description" content="([^"]*)"', det)
print("meta desc:", (m.group(1) if m else None)[:200])
# poster
m = re.search(r'rel="poster" href="([^"]+)"', det)
print("poster:", m.group(1) if m else None)
