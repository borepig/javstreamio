#!/usr/bin/env python3
"""Check search result pagination + fix wiki URL encoding."""
import html as html_mod
import re
import urllib.parse
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_results(page: str) -> list[tuple[str, str, str]]:
    """Parse result cards: (id, title, description)."""
    out = []
    body = page[page.find("<body"):]
    # split on result cards
    for m in re.finditer(
            r'<a href="/c/(\d+)\.html"[^>]*>(.*?)</a>', body, re.S):
        title = re.sub(r'<[^>]+>', '', m.group(2))
        out.append((m.group(1), title.strip()))
    return out


for p in (1, 2, 3):
    url = "https://avgood.com/c/s/?q=" + urllib.parse.quote("色情男女") + f"&page={p}"
    page = get(url)
    res = parse_results(page)
    print(f"page {p}: {len(res)} results")
    for rid, title in res[:8]:
        print("   ", rid, "|", title[:80])
    if not res:
        break

# wiki with proper quoting
try:
    w = get("https://zh.wikipedia.org/zh-cn/" + urllib.parse.quote("色情男女"))
    t = re.search(r'<title>(.*?)</title>', w, re.S)
    print("wiki title:", t.group(1).split(" - ")[0] if t else None)
    p2 = re.search(r'property="og:image"\s+content="([^"]+)"', w)
    print("wiki og:image:", html_mod.unescape(p2.group(1)) if p2 else None)
    # cast
    c = re.search(r'<th[^>]*>(?:主演|演員|演员)</th>\s*<td[^>]*>(.*?)</td>', w, re.S)
    if c:
        print("wiki cast:", re.findall(r'<a[^>]*>([^<]+)</a>', c.group(1)))
except Exception as e:
    print("wiki fetch error:", e)
