#!/usr/bin/env python3
"""Dump the /c/vue/ page structure to find the search app's JS bundle."""
import re
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


page = get("https://avgood.com/c/vue/")
print("page size:", len(page))
# all script src
print("script srcs:", re.findall(r'<script[^>]*src="([^"]+)"', page))
# inline module scripts / imports
mods = re.findall(r'import\(["\']([^"\']+)["\']', page)
print("imports:", mods[:10])
# sample of detail links + titles
for m in list(re.finditer(r'href="(/c/\d+\.html)"[^>]*>([^<]*)<', page))[:8]:
    print("link:", m.group(1), "|", m.group(2))
