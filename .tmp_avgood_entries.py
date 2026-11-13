#!/usr/bin/env python3
"""Extract entry titles + detail ids from the AvGood catalog list body."""
import re
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


cat = get("https://avgood.com/c/612.html")
# The list items: find blocks containing /c/<id>.html that are NOT in <head>
# Locate the <body> list container
body_start = cat.find("<body")
body = cat[body_start:] if body_start > 0 else cat
# Entry anchors with titles
for m in list(re.finditer(r'<a[^>]+href="(/c/\d+\.html)"[^>]*>([^<]{2,80})</a>', body))[:15]:
    print("ENTRY:", m.group(1), "|", m.group(2))
# Also capture the card/row structure around the first real entry
m = re.search(r'<a[^>]+href="/c/\d+\.html"', body)
if m:
    s = m.start()
    print("---- card context:")
    print(body[max(0, s - 200):s + 500].replace("\n", " ")[:700])
# pagination: total pages
pages = re.findall(r'href="[^"]*612\.html\?page=(\d+)"', cat)
print("page params found:", sorted(set(int(p) for p in pages))[:30])
