#!/usr/bin/env python3
"""Probe AvGood api.php action names."""
import json
import re
from urllib.error import HTTPError
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


base = "https://avgood.com/app/vue/api.php"
tries = [
    f"{base}?action=boards&language=zh",
    f"{base}?action=list&type=612&page=1&pageSize=10",
    f"{base}?action=list&type=612&language=zh&page=1&pageSize=10",
    f"{base}?action=category&id=612",
    f"{base}?action=catalog&id=612",
    f"{base}?action=cat&language=zh",
    f"{base}?action=detail&id=409794",
]
for u in tries:
    out = get(u)
    tag = u.split("api.php")[1]
    if out.startswith("HTTP") or out.startswith("ERR"):
        print(tag, "->", out)
    else:
        # print compact JSON
        try:
            j = json.loads(out)
            print(tag, "->", json.dumps(j, ensure_ascii=False)[:300])
        except Exception:
            print(tag, "-> (non-json) ", out[:120].replace("\n", " "))
