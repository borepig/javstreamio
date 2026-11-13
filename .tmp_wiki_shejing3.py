#!/usr/bin/env python3
"""Find the actual 剧情简介 section markup."""
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
# locate every occurrence of 剧情简介 and show context
for m in re.finditer(r'剧情简介', w):
    s = m.start()
    print("CTX:", w[max(0, s - 200):s + 300].replace("\n", " ")[:400])
    print("======")
