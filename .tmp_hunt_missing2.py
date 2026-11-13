"""Find the English title / poster source for 欲望都市之3P诱惑 (2006)."""
import json
import urllib.parse
import urllib.request

SEARXNG = "http://192.168.68.83:8888"

def sxng(query, engines):
    url = f"{SEARXNG}/search?q={urllib.parse.quote(query, safe='')}&format=json&pageno=1&engines={engines}"
    req = urllib.request.Request(url, headers={"User-Agent": "javstreamio-hkcat3/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data.get("results") or []

# Web engines for the English title + metadata.
for q in ["欲望都市之3P诱惑 2006", "欲望都市之3P诱惑 imdb", "3P诱惑 2006 电影"]:
    for res in sxng(q, "google,bing,brave")[:8]:
        print(" ", (res.get("title") or "")[:110], "|", (res.get("url") or "")[:90])
    print("---")
