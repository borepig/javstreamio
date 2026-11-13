#!/usr/bin/env python3
"""Check the AvGood detail page poster URL + public fetchability."""
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


det = get("https://avgood.com/c/409794.html")
# find poster image
for m in list(re.finditer(r'<img[^>]+src="([^"]+)"', det))[:6]:
    print("IMG:", m.group(1))
# og:image / meta
m = re.search(r'property="og:image"\s+content="([^"]+)"', det)
print("og:image:", m.group(1) if m else None)
# test public fetch of the poster
poster = None
for m in list(re.finditer(r'/remote/pic/[^"\s]+', det)):
    poster = "https://avgood.com" + m.group(0)
    break
if poster:
    try:
        req = Request(poster, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
        with urlopen(req, timeout=20) as r:
            print("poster fetch OK:", poster, "->", r.headers.get("Content-Type"), len(r.read()), "bytes")
    except HTTPError as e:
        print("poster fetch:", poster, "-> HTTP", e.code)
    except Exception as e:
        print("poster fetch:", poster, "-> ERR", e)
else:
    print("no /remote/pic poster found on 409794")
