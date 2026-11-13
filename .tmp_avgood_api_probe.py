#!/usr/bin/env python3
"""Probe the AvGood Vue bundle for the search API call shape."""
import re
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


js = get("https://avgood.com/app/vue/assets/index-i40yzTQY.js")

# 1) context around baseUrl / api.php usage
for m in list(re.finditer(r'baseUrl', js))[:10]:
    s = m.start()
    print("BASEURL-CTX:", js[max(0, s - 250):s + 250].replace("\n", " ")[:520])
    print("======")

# 2) context around `action` (any, not just literals)
for m in list(re.finditer(r'[^a-zA-Z]action', js))[:12]:
    s = m.start()
    print("ACTION-CTX:", js[max(0, s - 200):s + 200].replace("\n", " ")[:420])
    print("======")

# 3) look for the search param key: try common names
for kw in ("keyword", "keywords", "q", "word", "search"):
    hits = list(re.finditer(r'["\']' + kw + r'["\']', js))
    print(kw, "hits:", len(hits))
    for m in hits[:4]:
        s = m.start()
        print("   ", js[max(0, s - 150):s + 150].replace("\n", " ")[:320])
