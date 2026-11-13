#!/usr/bin/env python3
"""Probe the AvGood /c/s/ search page (server-rendered?)."""
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


kw = quote("色情男女")
for u in (
    "https://avgood.com/c/s/",
    f"https://avgood.com/c/s/?kw={kw}",
    f"https://avgood.com/c/s/?q={kw}",
):
    out = get(u)
    n = len(re.findall(r'href="/c/\d+\.html"', out))
    print(u, "-> size", len(out), "| detail links:", n)
    for m in list(re.finditer(r'<a[^>]+href="(/c/\d+\.html)"[^>]*>([^<]{2,60})</a>', out))[:10]:
        print("   ", m.group(1), "|", m.group(2))
