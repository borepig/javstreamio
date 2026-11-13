#!/usr/bin/env python3
"""Extract the 剧情简介 section body from the zh.wikipedia page."""
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
# heading with id
m = re.search(r'<h2[^>]*id="剧情简介"[^>]*>', w)
print("heading found:", bool(m))
if m:
    rest = w[m.end():]
    # take content up to the next <h2>
    nxt = re.search(r'<h2', rest)
    seg = rest[:nxt.start()] if nxt else rest[:3000]
    paras = re.findall(r'<p>(.*?)</p>', seg, re.S)
    text = " ".join(re.sub(r"<[^>]+>", "", html_mod.unescape(p)).strip() for p in paras)
    text = re.sub(r"\s+", " ", text).strip()
    print("SYNOPSIS:", text[:500])
    print("LEN:", len(text))
