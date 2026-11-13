#!/usr/bin/env python3
"""Dump the /c/s/?q= results markup to learn the result-card structure."""
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
# show context around the first detail link
m = re.search(r'href="/c/\d+\.html"', body)
if m:
    s = m.start()
    print("FIRST-LINK-CTX:", body[max(0, s - 400):s + 600].replace("\n", " ")[:1000])
# show 3 sample contexts of detail links
for m in list(re.finditer(r'href="/c/\d+\.html"', body))[1:4]:
    s = m.start()
    print("CTX:", body[max(0, s - 300):s + 300].replace("\n", " ")[:600])
    print("======")
