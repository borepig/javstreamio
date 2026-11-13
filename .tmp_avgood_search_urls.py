#!/usr/bin/env python3
"""Probe AvGood search URL patterns + valid api list types."""
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (javstreamio)"})
    try:
        with urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:
        return f"ERR {e}"


kw = quote("色情男女")
urls = [
    f"https://avgood.com/search.php?kw={kw}",
    f"https://avgood.com/search?kw={kw}",
    f"https://avgood.com/?s={kw}",
    "https://avgood.com/app/vue/api.php?action=list&type=view&page=1&pageSize=10",
    "https://avgood.com/app/vue/api.php?action=list&type=latest&page=1&pageSize=10",
    "https://avgood.com/zh/c/612",
    "https://avgood.com/c/612.html",
]
for u in urls:
    out = get(u)
    if out.startswith("HTTP") or out.startswith("ERR"):
        print(u.split("avgood.com", 1)[1][:80], "->", out)
        continue
    if "api.php" in u:
        try:
            j = json.loads(out)
            data = j.get("data") or []
            print(u.split("avgood.com", 1)[1][:80], "->", json.dumps(j, ensure_ascii=False)[:220])
            if isinstance(data, list) and data:
                print("  sample:", json.dumps(data[0], ensure_ascii=False)[:220])
            continue
        except Exception:
            pass
    # HTML search results: count detail links
    import re as _re
    n = len(_re.findall(r'href="/c/\d+\.html"', out))
    print(u.split("avgood.com", 1)[1][:80], "->", out[:60].replace("\n", " "), "| detail links:", n)
