#!/usr/bin/env python3
"""Inspect AvGood catalog page structure + pagination."""
import re
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


cat = get("https://avgood.com/c/612.html")
# find the entry list markup: detail links with surrounding context
for m in list(re.finditer(r'href="/c/\d+\.html"', cat))[:3]:
    s = m.start()
    print("ENTRY:", cat[max(0, s - 300):s + 400].replace("\n", " ")[:600])
    print("======")
# pagination links
print("pagination:", re.findall(r'href="([^"]*612[^"]*)"', cat)[:12])
# total entries hint
m = re.search(r'共\s*(\d+)\s*条', cat)
print("total hint:", m.group(1) if m else None)
# try page 2
p2 = get("https://avgood.com/c/612.html?page=2")
print("page2 links:", len(re.findall(r'href="/c/\d+\.html"', p2)))
